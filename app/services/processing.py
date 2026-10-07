"""Orchestrates: read file -> extract features -> measure -> persist."""
import json
import tempfile
from pathlib import Path

from sqlalchemy.orm import Session

from app.models import Feature, UploadedFile
from app.services import readers
from app.services.measurements import measure


def process_file(db: Session, record: UploadedFile, upload_path: Path) -> UploadedFile:
    try:
        with tempfile.TemporaryDirectory() as tmp:
            if record.file_type == "shapefile":
                gdf = readers.read_shapefile_zip(upload_path, Path(tmp))
            else:
                gdf = readers.read_kml(upload_path)

        record.crs = _epsg_label(gdf.crs)

        # Measurements need lon/lat so we can choose a local UTM zone per feature.
        gdf_wgs84 = gdf.to_crs("EPSG:4326") if len(gdf) else gdf
        geojson = json.loads(gdf.to_json(drop_id=True)) if len(gdf) else {"features": []}

        rows = []
        for i, (feat, geom4326) in enumerate(zip(geojson["features"], gdf_wgs84.geometry)):
            geom = feat.get("geometry")
            m = measure(geom4326)
            rows.append(
                Feature(
                    file_id=record.id,
                    index=i,
                    geometry_type=geom["type"] if geom else None,
                    geometry=geom,
                    crs=record.crs,
                    properties=feat.get("properties") or {},
                    supported=m.supported,
                    area_sq_m=m.area_sq_m,
                    length_m=m.length_m,
                    measurement_crs=m.measurement_crs,
                    warning=m.warning,
                )
            )
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


def _epsg_label(crs) -> str | None:
    if crs is None:
        return None
    epsg = crs.to_epsg()
    return f"EPSG:{epsg}" if epsg else crs.name
