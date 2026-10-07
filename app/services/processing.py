"""Orchestrates: read file -> extract features -> measure -> persist."""
import json
import tempfile
from pathlib import Path

from pyproj import CRS, Transformer
from shapely.geometry import mapping
from shapely.ops import transform
from sqlalchemy.orm import Session

from app.models import Feature, UploadedFile
from app.services import readers
from app.services.measurements import measure


def _epsg_label(crs: CRS) -> str:
    epsg = crs.to_epsg()
    return f"EPSG:{epsg}" if epsg else crs.name


def process_file(db: Session, record: UploadedFile, upload_path: Path) -> UploadedFile:
    try:
        with tempfile.TemporaryDirectory() as tmp:
            if record.file_type == "shapefile":
                raw, crs = readers.read_shapefile_zip(upload_path, Path(tmp))
            else:
                raw, crs = readers.read_kml(upload_path)

        label = _epsg_label(crs)
        record.crs = label
        # Measurements need lon/lat so a local UTM zone can be chosen per feature.
        to_wgs84 = None if crs == readers.WGS84 else Transformer.from_crs(
            crs, readers.WGS84, always_xy=True).transform

        rows = []
        for i, rf in enumerate(raw):
            g = rf.geometry
            m = measure(transform(to_wgs84, g) if (g is not None and to_wgs84) else g)
            geojson = json.loads(json.dumps(mapping(g))) if g is not None else None
            rows.append(Feature(
                file_id=record.id, index=i,
                geometry_type=g.geom_type if g is not None else None,
                geometry=geojson, crs=label, properties=rf.properties,
                supported=m.supported, area_sq_m=m.area_sq_m, length_m=m.length_m,
                measurement_crs=m.measurement_crs, warning=m.warning))
        db.add_all(rows)
        record.feature_count = len(rows)
        record.status = "COMPLETED"
    except readers.ProcessingError as exc:
        db.rollback()
        record.status, record.error = "FAILED", str(exc)
    except Exception as exc:  # unexpected - never crash the request
        db.rollback()
        record.status, record.error = "FAILED", f"Unexpected processing error: {exc}"
    db.add(record)
    db.commit()
    return record
