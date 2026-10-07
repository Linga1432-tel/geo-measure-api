import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import config
from app.db import get_db
from app.models import Feature, UploadedFile
from app.schemas import (FeaturesResponse, FileOut, MeasurementsResponse,
                         MeasurementSummary)
from app.services.processing import process_file

router = APIRouter(prefix="/api/files", tags=["files"])


def _get_file_or_404(db: Session, file_id: str) -> UploadedFile:
    f = db.get(UploadedFile, file_id)
    if not f:
        raise HTTPException(404, "File not found")
    return f


def _require_completed(f: UploadedFile) -> None:
    if f.status != "COMPLETED":
        raise HTTPException(409, f"File status is {f.status}: {f.error or 'not ready'}")


def _page(db: Session, file_id: str, limit: int, offset: int):
    return db.scalars(select(Feature).where(Feature.file_id == file_id)
                      .order_by(Feature.index).limit(limit).offset(offset)).all()


@router.post("/", response_model=FileOut, status_code=201)
async def upload_file(file: UploadFile = File(...), db: Session = Depends(get_db)):
    name = Path(file.filename or "").name
    ext = Path(name).suffix.lower()
    if ext not in config.ALLOWED_EXTENSIONS:
        raise HTTPException(415, "Only .zip (containing a Shapefile) or .kml files are supported")

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / f"upload{ext}"
        size = 0
        with dest.open("wb") as out:  # stream to disk, enforce size limit
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > config.MAX_UPLOAD_BYTES:
                    raise HTTPException(413, "File too large")
                out.write(chunk)
        if size == 0:
            raise HTTPException(400, "Empty file")

        record = UploadedFile(filename=name, file_type="shapefile" if ext == ".zip" else "kml")
        db.add(record)
        db.commit()
        record = process_file(db, record, dest)

    result = FileOut.model_validate(record)
    if record.status == "FAILED":
        return JSONResponse(result.model_dump(mode="json"), status_code=422)
    return result


@router.get("/{file_id}/", response_model=FileOut)
def get_file(file_id: str, db: Session = Depends(get_db)):
    return _get_file_or_404(db, file_id)


@router.get("/{file_id}/features/", response_model=FeaturesResponse)
def get_features(file_id: str, limit: int = Query(100, ge=1, le=1000),
                 offset: int = Query(0, ge=0), db: Session = Depends(get_db)):
    f = _get_file_or_404(db, file_id)
    _require_completed(f)
    return FeaturesResponse(file_id=file_id, total=f.feature_count, limit=limit,
                            offset=offset, results=_page(db, file_id, limit, offset))


@router.get("/{file_id}/measurements/", response_model=MeasurementsResponse)
def get_measurements(file_id: str, limit: int = Query(100, ge=1, le=1000),
                     offset: int = Query(0, ge=0), db: Session = Depends(get_db)):
    f = _get_file_or_404(db, file_id)
    _require_completed(f)
    area, length, measured, skipped = db.execute(
        select(func.coalesce(func.sum(Feature.area_sq_m), 0.0),
               func.coalesce(func.sum(Feature.length_m), 0.0),
               func.count().filter(Feature.supported.is_(True)),
               func.count().filter(Feature.supported.is_(False)))
        .where(Feature.file_id == file_id)).one()
    return MeasurementsResponse(
        file_id=file_id, total=f.feature_count, limit=limit, offset=offset,
        summary=MeasurementSummary(total_area_sq_m=area, total_length_m=length,
                                   measured_features=measured, skipped_features=skipped),
        results=[{"index": r.index, "geometry_type": r.geometry_type, "supported": r.supported,
                  "area_sq_m": r.area_sq_m, "length_m": r.length_m,
                  "measurement_crs": r.measurement_crs, "warning": r.warning}
                 for r in _page(db, file_id, limit, offset)])
