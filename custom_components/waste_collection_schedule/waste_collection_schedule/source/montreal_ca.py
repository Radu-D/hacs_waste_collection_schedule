import json
import time
import logging
import re
from pathlib import Path
from datetime import datetime, date
from typing import Any
from calendar import monthrange
import requests
from waste_collection_schedule import Collection
from waste_collection_schedule.exceptions import SourceArgumentException

LOGGER = logging.getLogger(__name__)

TITLE = "Montreal (QC)"
DESCRIPTION = "Official Montreal waste collection using GIS datasets"
URL = "https://montreal.ca/info-collectes"
COUNTRY = "ca"

TEST_CASES = {
    # "Downtown": {"latitude": 45.61355223813583, "longitude": -73.62396224886224},
    # "Plateau": {"latitude": 45.5238970487704, "longitude": -73.5720096031592},
    # edge case where there are delegated organic collections and biweekly types
    "Hochelaga": {"latitude": 45.55267663199481, "longitude": -73.53461468484242},
}

CKAN_PACKAGE_URL = "https://donnees.montreal.ca/api/3/action/package_show?id=info-collectes"

RESOURCE_KEYWORDS = {
    "waste": "ordures",
    "recycling": "recyclables",
    "food": "alimentaires",
    "green": "verts",
    "bulky": "encombrants",
    "organic": "organiques",
}

ICON_MAP = {
    "Waste": "mdi:trash-can",
    "Recycling": "mdi:recycle",
    "Food": "mdi:food-apple",
    "Green": "mdi:leaf",
    "Bulky": "mdi:sofa",
    "Organic": "mdi:compost",
}

WEEKDAYS = {
    "Monday": 0,
    "Tuesday": 1,
    "Tuesay": 1,
    "Wednesday": 2,
    "Thursday": 3,
    "Friday": 4,
    "Saturday": 5,
    "Sunday": 6,
}

DEFAULT_CACHE_HOURS = 24
MIN_CACHE_HOURS = 1
# 7 days should be more than enough to cover any temporary issues with the CKAN API or dataset updates
MAX_CACHE_HOURS = 168

_GEO_CACHE: dict[str, list[dict[str, Any]]] = {}
_DATASET_URLS: dict[str, str] | None = None


def _cache_dir() -> Path:
    """
    Returns the cache directory path for waste collection schedule data.
    Creates the directory if it does not exist.

    Returns:
        Path: Path object for the cache directory.
    """
    base = Path.home() / ".waste_cache"
    base.mkdir(exist_ok=True)
    return base


def _discover_datasets() -> dict[str, str]:
    """
    Discovers and caches Montreal waste collection datasets via CKAN API.

    Returns:
        dict[str, str]: Mapping of resource keys to their GeoJSON URLs.

    Raises:
        Exception: If fewer than 5 datasets are found.
    """
    global _DATASET_URLS

    if _DATASET_URLS:
        return _DATASET_URLS

    LOGGER.info("Discovering Montreal datasets via CKAN")

    r = requests.get(CKAN_PACKAGE_URL, timeout=60)
    r.raise_for_status()
    data = r.json()["result"]

    urls: dict[str, str] = {}

    for resource in data.get("resources", []):
        name = str(resource.get("name", "")).lower()
        fmt = str(resource.get("format", "")).lower()
        url = resource.get("url")
        if not url or fmt != "geojson":
            continue

        for key, keyword in RESOURCE_KEYWORDS.items():
            if keyword in name:
                urls[key] = url

    if len(urls) < 5:
        raise Exception("Could not discover Montreal datasets")

    _DATASET_URLS = urls
    return urls


def _compute_bbox(polygon: list) -> tuple[float, float, float, float]:
    """
    Computes the bounding box for a polygon.

    Args:
        polygon (list): List of rings, each a list of (longitude, latitude) tuples.

    Returns:
        tuple: (min_lat, min_lon, max_lat, max_lon)
    """
    lats, lons = [], []
    for ring in polygon:
        for lon, lat in ring:
            lats.append(lat)
            lons.append(lon)
    return min(lats), min(lons), max(lats), max(lons)


def _bbox_contains(lat: float, lon: float, bbox: tuple[float, float, float, float]) -> bool:
    """
    Checks if a point is inside a bounding box.

    Args:
        lat (float): Latitude.
        lon (float): Longitude.
        bbox (tuple): Bounding box (min_lat, min_lon, max_lat, max_lon).

    Returns:
        bool: True if point is inside the bounding box.
    """
    min_lat, min_lon, max_lat, max_lon = bbox
    return min_lat <= lat <= max_lat and min_lon <= lon <= max_lon


def _point_in_polygon(lat: float, lon: float, polygon: list) -> bool:
    """
    Determines if a point is inside a polygon using the ray casting algorithm.

    Args:
        lat (float): Latitude.
        lon (float): Longitude.
        polygon (list): List of rings; first is outer boundary, others are holes.

    Returns:
        bool: True if point is inside the polygon.
    """
    def point_in_ring(lat: float, lon: float, ring: list) -> bool:
        inside = False
        j = len(ring) - 1
        for i in range(len(ring)):
            lon1, lat1 = ring[i]
            lon2, lat2 = ring[j]
            intersect = ((lat1 > lat) != (lat2 > lat)) and (
                lon < (lon2 - lon1) * (lat - lat1) /
                (lat2 - lat1 + 1e-12) + lon1
            )
            if intersect:
                inside = not inside
            j = i
        return inside

    if not polygon:
        return False

    if not point_in_ring(lat, lon, polygon[0]):
        return False

    for hole in polygon[1:]:
        if point_in_ring(lat, lon, hole):
            return False

    return True


def _prepare_features(features: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Prepares GeoJSON features by extracting polygons and bounding boxes.

    Args:
        features (list): List of GeoJSON feature dicts.

    Returns:
        list: Features with '_prepared' key containing (polygon, bbox) pairs.
    """
    prepared: list[dict[str, Any]] = []

    for feature in features:
        geom = feature.get("geometry") or {}
        gtype = geom.get("type")

        if gtype == "Polygon":
            polys = [geom.get("coordinates")]
        elif gtype == "MultiPolygon":
            polys = geom.get("coordinates")
        else:
            continue

        if not polys:
            continue

        pairs = []
        for poly in polys:
            if not poly:
                continue
            pairs.append((poly, _compute_bbox(poly)))

        if not pairs:
            continue

        # precompute bbox + polygon pairs once to avoid recomputing on every lookup
        feature["_prepared"] = pairs
        prepared.append(feature)

    return prepared


def _load_geojson(url: str, max_age: int) -> list[dict[str, Any]]:
    """
    Loads and caches GeoJSON data from a URL.

    Args:
        url (str): GeoJSON URL.
        max_age (int): Maximum cache age in seconds.

    Returns:
        list: Prepared GeoJSON features.
    """
    if url in _GEO_CACHE:
        return _GEO_CACHE[url]

    cache_path = _cache_dir() / url.split("/")[-1]

    if not cache_path.exists() or time.time() - cache_path.stat().st_mtime > max_age:
        LOGGER.info("Downloading Montreal dataset %s", url)
        r = requests.get(url, timeout=60)
        r.raise_for_status()
        cache_path.write_bytes(r.content)

    try:
        data = json.loads(cache_path.read_text())
    except Exception:
        LOGGER.warning("Corrupted cache, redownloading %s", url)
        cache_path.unlink(missing_ok=True)
        return _load_geojson(url, max_age)

    prepared = _prepare_features(data.get("features", []))
    _GEO_CACHE[url] = prepared
    return prepared


def _resolve_sector(lat: float, lon: float, features: list[dict[str, Any]]) -> str:
    """
    Resolves the waste collection sector for a given location.

    Args:
        lat (float): Latitude.
        lon (float): Longitude.
        features (list): List of prepared GeoJSON features.

    Returns:
        str: Sector identifier.

    Raises:
        SourceArgumentException: If location is outside Montreal.
    """
    for feature in features:
        for poly, bbox in feature.get("_prepared", []):
            # fast bbox reject before expensive polygon math
            if not _bbox_contains(lat, lon, bbox):
                continue
            if _point_in_polygon(lat, lon, poly):
                props = feature.get("properties") or {}
                sector = props.get("SECTEUR")
                if sector:
                    return sector

    raise SourceArgumentException(
        "latitude",
        "Location is outside Montreal. Use manual sector override.",
    )


def _is_delegation_message(msg: str) -> bool:
    """
    Checks if a message indicates delegated collection services.

    Args:
        msg (str): Message string.

    Returns:
        bool: True if message indicates delegation.
    """
    # some organic sectors delegate to food+green instead of having their own schedule
    m = msg.lower()
    return ("offered via the collections" in m) or ("offerte via les collectes" in m)


def _merge_notes(a: str | None, b: str | None) -> str | None:
    """
    Merges two note strings, combining if both are present and different.

    Args:
        a (str | None): First note.
        b (str | None): Second note.

    Returns:
        str | None: Merged note or None if both are empty.
    """
    a = (a or "").strip()
    b = (b or "").strip()
    if not a and not b:
        return None
    if a and not b:
        return a
    if b and not a:
        return b
    if a == b:
        return a
    return f"{a}\n\n{b}"


def _parse_explicit_dates(source_type: str, message: str) -> list[Collection]:
    entries = []

    date_lines = re.findall(
        r"-\s*([A-Za-z]+)\s+([\d,\sand]+)\s*(\d{4})?",
        message
    )

    if not date_lines:
        return entries

    year = datetime.now().year

    for month_name, days_blob, maybe_year in date_lines:

        if maybe_year:
            year = int(maybe_year)

        try:
            month = datetime.strptime(month_name, "%B").month
        except ValueError:
            continue

        max_day = monthrange(year, month)[1]

        days = re.findall(r"\d+", days_blob)

        for d in days:
            d = int(d)

            # guard against bad values like 2026 being treated as a day
            if d < 1 or d > max_day:
                continue

            entries.append(
                Collection(
                    date=date(year, month, d),
                    t=source_type,
                    icon=ICON_MAP.get(source_type),
                )
            )

    return entries


class Source:
    def __init__(
        self,
        sector=None,
        recycling=None,
        bulky=None,
        food=None,
        green=None,
        organic=None,
        latitude=None,
        longitude=None,
        cache_hours=None,
    ):
        self._manual_sector = {
            "waste": sector,
            "recycling": recycling,
            "bulky": bulky,
            "food": food,
            "green": green,
            "organic": organic,
        }

        self._lat = latitude
        self._lon = longitude

        if cache_hours is None:
            cache_hours = DEFAULT_CACHE_HOURS

        cache_hours = max(MIN_CACHE_HOURS, min(
            MAX_CACHE_HOURS, int(cache_hours)))
        self._cache_max_age = cache_hours * 3600

    def parse_collection(self, source_type: str, message: str) -> list[Collection]:
        """
        Parses a collection schedule message and generates Collection entries for a weekday.

        Args:
            source_type (str): Waste stream type.
            message (str): Schedule message containing weekday info.

        Returns:
            list[Collection]: Collection entries for each occurrence of the weekday in the current year.
        """
        explicit = _parse_explicit_dates(source_type, message)
        if explicit:
            return explicit

        entries: list[Collection] = []

        weekday = None
        for day, idx in WEEKDAYS.items():
            if re.search(day, message, re.IGNORECASE):
                weekday = idx
                break

        if weekday is None:
            return entries

        year = datetime.now().year

        for month in range(1, 13):
            for day in range(1, 32):
                try:
                    d = datetime(year, month, day)
                except ValueError:
                    continue

                if d.weekday() == weekday:
                    entries.append(
                        Collection(
                            date=d.date(),
                            t=source_type,
                            icon=ICON_MAP.get(source_type),
                        )
                    )

        return entries

    def get_data_by_source(self, source_type: str, url: str) -> tuple[list[Collection], bool]:
        """
        Retrieves waste collection schedule data for a specific source and sector.

        Args:
            source_type (str): Waste stream type.
            url (str): GeoJSON URL.

        Returns:
            tuple[list[Collection], bool]: Parsed collection entries and delegation flag.
        """
        features = _load_geojson(url, self._cache_max_age)

        sector = self._manual_sector.get(source_type)

        if sector is None:
            if self._lat is None or self._lon is None:
                raise SourceArgumentException(
                    "latitude",
                    "Latitude/longitude required when sector not provided.",
                )
            sector = _resolve_sector(
                float(self._lat), float(self._lon), features)
            LOGGER.info("%s auto sector: %s", source_type, sector)

        entries: list[Collection] = []
        delegated = False

        for feature in features:
            props = feature.get("properties") or {}
            if props.get("SECTEUR") != sector:
                continue

            msg = str(props.get("MESSAGE_EN") or props.get("MESSAGE_FR") or "")

            if _is_delegation_message(msg):
                delegated = True
                continue

            parsed = self.parse_collection(source_type.capitalize(), msg)

            note = _merge_notes(
                props.get("EXCEPTION_EN") or props.get("EXCEPTION_FR"),
                None,
            )
            if note:
                for e in parsed:
                    # attach exception text as note metadata
                    try:
                        e.note = note
                    except Exception:
                        pass

            entries.extend(parsed)

        return entries, delegated

    def fetch(self) -> list[Collection]:
        """
        Fetches waste collection schedule data from all Montreal GIS datasets.

        Returns:
            list[Collection]: Aggregated waste collection entries.
        """
        urls = _discover_datasets()

        stream_entries: dict[str, list[Collection]] = {}
        delegation_flags: dict[str, bool] = {}

        for key, url in urls.items():
            entries, delegated = self.get_data_by_source(key, url)
            stream_entries[key] = entries
            delegation_flags[key] = delegated

        organic_notes: dict[date, str | None] = {}

        # organic can be delegated to food+green, we merge dates instead of reusing objects
        if delegation_flags.get("organic"):
            for e in stream_entries.get("green", []):
                organic_notes.setdefault(e.date, getattr(e, "note", None))
            for e in stream_entries.get("food", []):
                organic_notes.setdefault(e.date, getattr(e, "note", None))

        # explicit organic always wins over delegated ones
        for e in stream_entries.get("organic", []):
            organic_notes[e.date] = getattr(e, "note", None)

        final: list[Collection] = []

        for key, entries in stream_entries.items():
            if key == "organic":
                continue
            final.extend(entries)

        for d, n in organic_notes.items():
            final.append(
                Collection(
                    date=d,
                    t="Organic",
                    icon=ICON_MAP["Organic"],
                    note=n,
                )
            )

        return final
