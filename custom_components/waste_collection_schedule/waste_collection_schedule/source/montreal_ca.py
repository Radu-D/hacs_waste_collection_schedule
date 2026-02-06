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
    },
    "fr": {
        "latitude": "Remplacement facultatif de la latitude",
        "longitude": "Remplacement facultatif de la longitude",
        "sector": "Remplacement manuel du secteur des déchets",
        "recycling": "Remplacement manuel du secteur du recyclage",
        "bulky": "Remplacement manuel du secteur des encombrants",
        "food": "Remplacement manuel du secteur alimentaire",
        "green": "Remplacement manuel du secteur vert",
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
    },
    "fr": {
        "latitude": "Latitude",
        "longitude": "Longitude",
        "sector": "Secteur des déchets",
        "recycling": "Secteur du recyclage",
        "bulky": "Secteur des encombrants",
        "food": "Secteur alimentaire",
        "green": "Secteur vert",
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

CACHE_MAX_AGE = 7 * 24 * 3600
_GEO_CACHE: dict[str, list] = {}
_DATASET_URLS = None


def _cache_dir():
    base = Path(".waste_cache")
    base.mkdir(exist_ok=True)
    return base


def _discover_datasets():
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
    lats, lons = [], []
    for ring in polygon:
        for lon, lat in ring:
            lats.append(lat)
            lons.append(lon)
    return min(lats), min(lons), max(lats), max(lons)


def _bbox_contains(lat, lon, bbox):
    min_lat, min_lon, max_lat, max_lon = bbox
    return min_lat <= lat <= max_lat and min_lon <= lon <= max_lon


def _point_in_polygon(lat, lon, polygon):
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


def _load_geojson(url):
    if url in _GEO_CACHE:
        return _GEO_CACHE[url]

    cache_path = _cache_dir() / url.split("/")[-1]

    if not cache_path.exists() or time.time() - cache_path.stat().st_mtime > CACHE_MAX_AGE:
        LOGGER.info("Downloading Montreal dataset %s", url)
        r = requests.get(url, timeout=60)
        r.raise_for_status()
        cache_path.write_bytes(r.content)

    data = json.loads(cache_path.read_text())
    prepared = _prepare_features(data["features"])
    _GEO_CACHE[url] = prepared
    return prepared


def _resolve_sector(lat, lon, features):
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
    def __init__(
        self,
        sector=None,
        recycling=None,
        bulky=None,
        food=None,
        green=None,
        latitude=None,
        longitude=None,
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

    def parse_collection(self, source_type, message):
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
        features = _load_geojson(url)

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
                entries.extend(self.parse_collection(
                    source_type.capitalize(), msg))

        return entries

    def fetch(self):
        urls = _discover_datasets()
        entries = []

        for key, url in urls.items():
            entries.extend(self.get_data_by_source(key, url))

        return entries
