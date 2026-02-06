import json
import time
import logging
import re
from pathlib import Path
from datetime import datetime

import requests
from waste_collection_schedule import Collection
from waste_collection_schedule.exceptions import SourceArgumentException

LOGGER = logging.getLogger(__name__)

TITLE = "Montreal (QC)"
DESCRIPTION = "Official Montreal waste collection using GIS datasets"
URL = "https://montreal.ca/info-collectes"
COUNTRY = "ca"

TEST_CASES = {
    "Downtown": {"latitude": 45.5017, "longitude": -73.5673},
    "Plateau": {"latitude": 45.525, "longitude": -73.58},
}

CKAN_PACKAGE_URL = "https://donnees.montreal.ca/api/3/action/package_show?id=info-collectes"

RESOURCE_KEYWORDS = {
    "waste": "ordures",
    "recycling": "recyclables",
    "food": "alimentaires",
    "green": "verts",
    "bulky": "encombrants",
}

ICON_MAP = {
    "Waste": "mdi:trash-can",
    "Recycling": "mdi:recycle",
    "Food": "mdi:food-apple",
    "Green": "mdi:leaf",
    "Bulky": "mdi:sofa",
}

HOW_TO_GET_ARGUMENTS_DESCRIPTION = {
    "en": "Leave latitude/longitude empty to auto-detect sectors from Home Assistant location.",
    "fr": "Laissez la latitude/longitude vide pour détecter automatiquement les secteurs à partir de l'emplacement de Home Assistant.",
}

PARAM_DESCRIPTIONS = {
    "en": {
        "latitude": "Optional latitude override",
        "longitude": "Optional longitude override",
        "sector": "Manual waste sector override",
        "recycling": "Manual recycling sector override",
        "bulky": "Manual bulky sector override",
        "food": "Manual food sector override",
        "green": "Manual green sector override",
        "cache_hours": "Dataset cache lifetime expiration in hours",
    },
    "fr": {
        "latitude": "Remplacement facultatif de la latitude",
        "longitude": "Remplacement facultatif de la longitude",
        "sector": "Remplacement manuel du secteur des déchets",
        "recycling": "Remplacement manuel du secteur du recyclage",
        "bulky": "Remplacement manuel du secteur des encombrants",
        "food": "Remplacement manuel du secteur alimentaire",
        "green": "Remplacement manuel du secteur vert",
        "cache_hours": "Durée de vie du cache du jeu de données en heures",
    },
}

PARAM_TRANSLATIONS = {
    "en": {
        "latitude": "Latitude",
        "longitude": "Longitude",
        "sector": "Waste sector",
        "recycling": "Recycling sector",
        "bulky": "Bulky sector",
        "food": "Food sector",
        "green": "Green sector",
        "cache_hours": "Cache hours",
    },
    "fr": {
        "latitude": "Latitude",
        "longitude": "Longitude",
        "sector": "Secteur des déchets",
        "recycling": "Secteur du recyclage",
        "bulky": "Secteur des encombrants",
        "food": "Secteur alimentaire",
        "green": "Secteur vert",
        "cache_hours": "Heures de cache",
    },
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

_GEO_CACHE: dict[str, list] = {}
_DATASET_URLS = None


def _cache_dir():
    """Return the cache directory for waste collection schedule data.

    Creates a hidden cache directory at '.waste_cache' in the current working directory
    if it does not already exist. Used to store cached data to avoid redundant API calls.

    Returns:
        Path: Path object pointing to the '.waste_cache' directory.
    """
    base = Path(".waste_cache")
    base.mkdir(exist_ok=True)
    return base


def _discover_datasets():
    """Discover and cache Montreal waste collection datasets via CKAN API.

    Fetches a list of available datasets from the Montreal CKAN package endpoint
    and filters for GeoJSON resources matching predefined keywords. Caches the
    discovered URLs globally to avoid repeated API calls.

    Returns:
        dict: Dictionary mapping resource keys to their download URLs (5 entries expected).

    Raises:
        requests.exceptions.RequestException: If the HTTP request to CKAN fails.
        Exception: If fewer than 5 datasets matching the expected keywords and format are found.
    """
    global _DATASET_URLS

    if _DATASET_URLS:
        return _DATASET_URLS

    LOGGER.info("Discovering Montreal datasets via CKAN")

    r = requests.get(CKAN_PACKAGE_URL, timeout=60)
    r.raise_for_status()
    data = r.json()["result"]

    urls = {}

    for resource in data["resources"]:
        name = resource["name"].lower()

        for key, keyword in RESOURCE_KEYWORDS.items():
            if keyword in name and resource["format"].lower() == "geojson":
                urls[key] = resource["url"]

    if len(urls) != 5:
        raise Exception("Could not discover all Montreal datasets")

    _DATASET_URLS = urls
    return urls


def _compute_bbox(polygon):
    """Compute the bounding box of a polygon.

    Args:
        polygon (list): List of rings, each a list of (longitude, latitude) tuples.

    Returns:
        tuple: (min_lat, min_lon, max_lat, max_lon) bounding box coordinates.
    """
    lats, lons = [], []
    for ring in polygon:
        for lon, lat in ring:
            lats.append(lat)
            lons.append(lon)
    return min(lats), min(lons), max(lats), max(lons)


def _bbox_contains(lat, lon, bbox):
    """Check if a geographic point is inside a rectangular bounding box.

    Args:
        lat (float): Latitude of the point.
        lon (float): Longitude of the point.
        bbox (tuple): (min_lat, min_lon, max_lat, max_lon).

    Returns:
        bool: True if the point is within or on the edges of the bounding box.
    """
    min_lat, min_lon, max_lat, max_lon = bbox
    return min_lat <= lat <= max_lat and min_lon <= lon <= max_lon


def _point_in_polygon(lat, lon, polygon):
    """Determine if a point is inside a polygon using the ray casting algorithm.

    Args:
        lat (float): Latitude of the point.
        lon (float): Longitude of the point.
        polygon (list): List of rings; first is outer boundary, others are holes. Each ring is a list of (lon, lat) tuples.

    Returns:
        bool: True if the point is inside the polygon (and outside any holes).
    """
    def point_in_ring(lat, lon, ring):
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

    if not point_in_ring(lat, lon, polygon[0]):
        return False
    for hole in polygon[1:]:
        if point_in_ring(lat, lon, hole):
            return False
    return True


def _prepare_features(features):
    """Prepare geographic features by extracting and computing bounding boxes for polygons.

    Args:
        features (list): List of GeoJSON feature objects, each with a "geometry" key.

    Returns:
        list: Features with an added "_prepared" key containing (polygon, bounding_box) tuples.
    """
    prepared = []
    for feature in features:
        geom = feature["geometry"]

        if geom["type"] == "Polygon":
            polys = [geom["coordinates"]]
        elif geom["type"] == "MultiPolygon":
            polys = geom["coordinates"]
        else:
            continue

        feature["_prepared"] = [
            (poly, _compute_bbox(poly)) for poly in polys
        ]
        prepared.append(feature)

    return prepared


def _load_geojson(url, max_age):
    """Load and cache GeoJSON data from a URL.

    Retrieves GeoJSON features from the specified URL and caches them locally.
    If a cached version exists and is within the max_age threshold, the cached
    version is returned. Otherwise, the data is downloaded and cached to disk.

    Args:
        url (str): URL of the GeoJSON file to load.
        max_age (int): Maximum age of cached data in seconds.

    Returns:
        list: Prepared GeoJSON features.

    Raises:
        requests.exceptions.HTTPError: If the HTTP request fails.
    """
    if url in _GEO_CACHE:
        return _GEO_CACHE[url]

    cache_path = _cache_dir() / url.split("/")[-1]

    if not cache_path.exists() or time.time() - cache_path.stat().st_mtime > max_age:
        LOGGER.info("Downloading Montreal dataset %s", url)
        r = requests.get(url, timeout=60)
        r.raise_for_status()
        cache_path.write_bytes(r.content)

    data = json.loads(cache_path.read_text())
    prepared = _prepare_features(data["features"])
    _GEO_CACHE[url] = prepared
    return prepared


def _resolve_sector(lat, lon, features):
    """Resolve the waste collection sector for a given geographic location.

    Args:
        lat (float): Latitude coordinate.
        lon (float): Longitude coordinate.
        features (list): List of features with "_prepared" and "properties" keys.

    Returns:
        str: The sector identifier ("SECTEUR") for the location.

    Raises:
        SourceArgumentException: If the location is outside Montreal and no matching sector is found.
    """
    for feature in features:
        for poly, bbox in feature["_prepared"]:
            if not _bbox_contains(lat, lon, bbox):
                continue
            if _point_in_polygon(lat, lon, poly):
                return feature["properties"]["SECTEUR"]

    raise SourceArgumentException(
        "latitude",
        "Location is outside Montreal. Use manual sector override."
    )


class Source:
    """Source for Montreal waste collection schedule using GIS datasets.

    Fetches and parses waste collection schedule data for Montreal using GIS data from the city's open data portal.
    Supports automatic sector detection via GPS coordinates or manual sector specification.
    """

    def __init__(
        self,
        sector=None,
        recycling=None,
        bulky=None,
        food=None,
        green=None,
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
        }

        self._lat = latitude
        self._lon = longitude

        if cache_hours is None:
            cache_hours = DEFAULT_CACHE_HOURS

        cache_hours = max(MIN_CACHE_HOURS, min(
            MAX_CACHE_HOURS, int(cache_hours)))
        # Convert cache hours to seconds for internal use
        self._cache_max_age = cache_hours * 3600

    def parse_collection(self, source_type, message):
        """Parse a collection schedule message and generate Collection entries for a specific weekday.

        Extracts the day of the week from the message text, then generates Collection entries for every occurrence of that weekday throughout the current year.

        Args:
            source_type (str): Type of waste collection (e.g., 'organic', 'recyclable').
            message (str): Collection schedule message containing a day of the week.

        Returns:
            list[Collection]: Collection objects for each occurrence of the specified weekday in the current year. Empty if no valid weekday found.
        """
        entries = []

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
                    if d.weekday() == weekday:
                        entries.append(
                            Collection(
                                date=d.date(),
                                t=source_type,
                                icon=ICON_MAP.get(source_type),
                            )
                        )
                except ValueError:
                    pass

        return entries

    def get_data_by_source(self, source_type, url):
        """Retrieve waste collection schedule data for a specific source type and sector.

        Loads GeoJSON data from the provided URL, determines the sector either from manual configuration or by resolving coordinates to a sector, then extracts and parses collection schedule messages for that sector.

        Args:
            source_type (str): Type of waste collection source (e.g., 'garbage', 'recycling').
            url (str): URL to the GeoJSON file containing sector and collection schedule data.

        Returns:
            list: Parsed collection schedule entries for the resolved sector.

        Raises:
            SourceArgumentException: If sector is not manually provided and latitude/longitude are not available for automatic sector resolution.
        """
        features = _load_geojson(url, self._cache_max_age)

        sector = self._manual_sector[source_type]

        if sector is None:
            if self._lat is None or self._lon is None:
                raise SourceArgumentException(
                    "latitude",
                    "Latitude/longitude required when sector not provided.",
                )
            sector = _resolve_sector(self._lat, self._lon, features)
            LOGGER.info("%s auto sector: %s", source_type, sector)

        entries = []
        for feature in features:
            if feature["properties"]["SECTEUR"] != sector:
                continue

            msg = feature["properties"].get("MESSAGE_EN")
            if msg:
                entries.extend(
                    self.parse_collection(source_type.capitalize(), msg)
                )

        return entries

    def fetch(self):
        """Fetch waste collection schedule data from all Montreal GIS datasets.

        Discovers all available datasets and retrieves waste collection entries from each source.

        Returns:
            list: Waste collection entries aggregated from all discovered datasets.
        """
        urls = _discover_datasets()
        entries = []

        for key, url in urls.items():
            entries.extend(self.get_data_by_source(key, url))

        return entries
