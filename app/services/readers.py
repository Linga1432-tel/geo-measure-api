"""Read Shapefile (.zip) and KML into a GeoDataFrame, safely."""
import zipfile
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pyogrio

from app import config


class ProcessingError(Exception):
    """Raised for invalid / unreadable uploads (client error)."""


def _safe_extract(zip_path: Path, dest: Path) -> None:
    try:
        with zipfile.ZipFile(zip_path) as zf:
            total = sum(i.file_size for i in zf.infolist())
            if total > config.MAX_UNZIPPED_BYTES:
                raise ProcessingError("Archive is too large when uncompressed")
            dest_root = dest.resolve()
            for info in zf.infolist():
                target = (dest / info.filename).resolve()
                if dest_root not in target.parents and target != dest_root:  # zip-slip guard
                    raise ProcessingError("Archive contains unsafe paths")
            zf.extractall(dest)
    except zipfile.BadZipFile as exc:
        raise ProcessingError("Not a valid zip archive") from exc


def read_shapefile_zip(zip_path: Path, workdir: Path) -> gpd.GeoDataFrame:
    _safe_extract(zip_path, workdir)
    shps = [p for p in workdir.rglob("*.shp") if not p.name.startswith(".")]
    if not shps:
        raise ProcessingError("No .shp file found in the archive")
    return _combine([_read(p) for p in shps])


def read_kml(path: Path) -> gpd.GeoDataFrame:
    try:
        layers = pyogrio.list_layers(path)
    except Exception as exc:
        raise ProcessingError(f"Unable to read KML: {exc}") from exc
    frames = [_read(path, layer=str(name)) for name, _ in layers]
    frames = [f for f in frames if len(f)]
    if not frames:
        return gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")
    return _combine(frames)


def _read(path: Path, **kw) -> gpd.GeoDataFrame:
    try:
        gdf = gpd.read_file(path, engine="pyogrio", **kw)
    except Exception as exc:
        raise ProcessingError(f"Unable to read geospatial data: {exc}") from exc
    if gdf.crs is None and path.suffix.lower() == ".kml":
        gdf = gdf.set_crs("EPSG:4326")  # KML is always WGS84 per spec
    return gdf


def _combine(frames: list[gpd.GeoDataFrame]) -> gpd.GeoDataFrame:
    if frames[0].crs is None:
        raise ProcessingError("Dataset has no CRS (e.g. shapefile is missing its .prj file)")
    frames = [f.to_crs(frames[0].crs) if f.crs else f for f in frames]
    gdf = pd.concat(frames, ignore_index=True)
    return gpd.GeoDataFrame(gdf, geometry="geometry", crs=frames[0].crs)
