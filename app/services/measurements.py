"""Area / length calculation. Input geometry MUST be in EPSG:4326 (lon/lat)."""
from dataclasses import dataclass

import shapely
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform

from app.services.crs import utm_epsg_for, wgs84_to

AREA_TYPES = {"Polygon", "MultiPolygon"}
LENGTH_TYPES = {"LineString", "MultiLineString"}
NO_MEASURE_TYPES = {"Point", "MultiPoint"}


@dataclass
class Measurement:
    supported: bool
    area_sq_m: float | None = None
    length_m: float | None = None
    measurement_crs: str | None = None
    warning: str | None = None


def measure(geom: BaseGeometry | None) -> Measurement:
    if geom is None or geom.is_empty:
        return Measurement(False, warning="Missing or empty geometry")

    gtype = geom.geom_type
    if gtype in NO_MEASURE_TYPES:
        return Measurement(True)  # supported, nothing to measure
    if gtype not in AREA_TYPES | LENGTH_TYPES:
        return Measurement(False, warning=f"Measurement not supported for {gtype}")

    geom = shapely.force_2d(geom)
    warning = None
    if gtype in AREA_TYPES and not geom.is_valid:
        warning = "Invalid polygon geometry (e.g. self-intersection); area may be inaccurate"

    c = geom.centroid
    epsg = utm_epsg_for(c.x, c.y)
    projected = transform(wgs84_to(epsg).transform, geom)
    crs = f"EPSG:{epsg}"

    if gtype in AREA_TYPES:
        return Measurement(True, area_sq_m=projected.area, measurement_crs=crs, warning=warning)
    return Measurement(True, length_m=projected.length, measurement_crs=crs, warning=warning)
