"""Pure-Python readers for Shapefile (.zip) and KML (no GDAL required).

Both readers return ``(features, crs)`` where each feature is a ``RawFeature``.
"""
import datetime as dt
import decimal
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import shapefile  # pyshp
from defusedxml import ElementTree as ET
from pyproj import CRS
from shapely.geometry import (GeometryCollection, LinearRing, LineString, MultiLineString,
                              MultiPoint, MultiPolygon, Point, Polygon, shape)
from shapely.geometry.base import BaseGeometry

from app import config

WGS84 = CRS.from_epsg(4326)


class ProcessingError(Exception):
    """Raised for invalid / unreadable uploads (client error)."""


@dataclass
class RawFeature:
    geometry: BaseGeometry | None
    properties: dict[str, Any] = field(default_factory=dict)


# ----------------------------------------------------------------- Shapefile
def _safe_extract(zip_path: Path, dest: Path) -> None:
    try:
        with zipfile.ZipFile(zip_path) as zf:
            if sum(i.file_size for i in zf.infolist()) > config.MAX_UNZIPPED_BYTES:
                raise ProcessingError("Archive is too large when uncompressed")
            root = dest.resolve()
            for info in zf.infolist():
                target = (dest / info.filename).resolve()
                if root not in target.parents and target != root:  # zip-slip guard
                    raise ProcessingError("Archive contains unsafe paths")
            zf.extractall(dest)
    except zipfile.BadZipFile as exc:
        raise ProcessingError("Not a valid zip archive") from exc


def _json_safe(v: Any) -> Any:
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, bytes):
        return v.decode("utf-8", errors="replace")
    if isinstance(v, float) and v != v:  # NaN
        return None
    return v


def read_shapefile_zip(zip_path: Path, workdir: Path) -> tuple[list[RawFeature], CRS]:
    _safe_extract(zip_path, workdir)
    shps = [p for p in workdir.rglob("*.shp") if not p.name.startswith(".")]
    if not shps:
        raise ProcessingError("No .shp file found in the archive")

    features: list[RawFeature] = []
    crs: CRS | None = None
    for shp in shps:
        prj = shp.with_suffix(".prj")
        if not prj.exists():
            raise ProcessingError(f"{shp.name}: missing .prj file, cannot determine CRS")
        try:
            layer_crs = CRS.from_wkt(prj.read_text(errors="replace"))
        except Exception as exc:
            raise ProcessingError(f"{shp.name}: unreadable .prj ({exc})") from exc
        if crs is None:
            crs = layer_crs
        elif layer_crs != crs:
            raise ProcessingError("Archive contains shapefiles with different CRS")
        try:
            with shapefile.Reader(str(shp)) as sf:
                for sr in sf.iterShapeRecords():
                    features.append(RawFeature(_shp_geom(sr.shape),
                                               {k: _json_safe(v) for k, v in sr.record.as_dict().items()}))
        except ProcessingError:
            raise
        except Exception as exc:
            raise ProcessingError(f"Unable to read {shp.name}: {exc}") from exc
    return features, crs


def _shp_geom(shp) -> BaseGeometry | None:
    if shp.shapeType == shapefile.NULL:
        return None
    try:
        return shape(shp.__geo_interface__)
    except Exception:
        return None


# ----------------------------------------------------------------------- KML
def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _children(el, name: str):
    return [c for c in el if _local(c.tag) == name]


def _child(el, name: str):
    found = _children(el, name)
    return found[0] if found else None


def _coords(text: str | None) -> list[tuple[float, ...]]:
    pts = []
    for tok in (text or "").split():
        parts = tok.split(",")
        if len(parts) < 2:
            raise ValueError(f"bad coordinate '{tok}'")
        pts.append(tuple(float(p) for p in parts[:3]))
    return pts


def _kml_geom(el) -> BaseGeometry | None:
    name = _local(el.tag)
    if name == "Point":
        pts = _coords(_child(el, "coordinates").text if _child(el, "coordinates") is not None else "")
        return Point(pts[0]) if pts else None
    if name in ("LineString", "LinearRing"):
        pts = _coords(_child(el, "coordinates").text if _child(el, "coordinates") is not None else "")
        return LineString(pts) if len(pts) >= 2 else None
    if name == "Polygon":
        def ring(boundary):
            lr = _child(boundary, "LinearRing")
            c = _child(lr, "coordinates") if lr is not None else None
            return _coords(c.text) if c is not None else []
        outer = _child(el, "outerBoundaryIs")
        if outer is None:
            return None
        shell = ring(outer)
        holes = [ring(b) for b in _children(el, "innerBoundaryIs")]
        return Polygon(shell, [h for h in holes if h]) if len(shell) >= 3 else None
    if name == "MultiGeometry":
        parts = [g for g in (_kml_geom(c) for c in el if _local(c.tag) in
                             ("Point", "LineString", "LinearRing", "Polygon", "MultiGeometry")) if g is not None]
        if not parts:
            return None
        kinds = {type(p) for p in parts}
        if kinds == {Point}:
            return MultiPoint(parts)
        if kinds == {LineString}:
            return MultiLineString(parts)
        if kinds == {Polygon}:
            return MultiPolygon(parts)
        return GeometryCollection(parts)  # mixed: stored, but flagged unsupported for measurement
    return None


def _kml_props(pm) -> dict[str, Any]:
    props: dict[str, Any] = {}
    for key in ("name", "description"):
        c = _child(pm, key)
        if c is not None and c.text:
            props[key] = c.text.strip()
    ext = _child(pm, "ExtendedData")
    if ext is not None:
        for d in ext.iter():
            if _local(d.tag) == "Data" and d.get("name"):
                v = _child(d, "value")
                props[d.get("name")] = v.text.strip() if v is not None and v.text else None
            elif _local(d.tag) == "SimpleData" and d.get("name"):
                props[d.get("name")] = d.text.strip() if d.text else None
    return props


def read_kml(path: Path) -> tuple[list[RawFeature], CRS]:
    try:
        root = ET.parse(str(path)).getroot()
    except Exception as exc:
        raise ProcessingError(f"Unable to read KML: {exc}") from exc
    if _local(root.tag) != "kml":
        raise ProcessingError("Not a KML document (root element is not <kml>)")

    features = []
    for pm in root.iter():
        if _local(pm.tag) != "Placemark":
            continue
        geom = None
        for child in pm:
            if _local(child.tag) in ("Point", "LineString", "LinearRing", "Polygon", "MultiGeometry"):
                try:
                    geom = _kml_geom(child)
                except ValueError as exc:
                    raise ProcessingError(f"Invalid coordinates in KML: {exc}") from exc
                break
        features.append(RawFeature(geom, _kml_props(pm)))
    return features, WGS84  # KML is always WGS84 per spec
