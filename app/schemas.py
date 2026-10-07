from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class FileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    filename: str
    file_type: str
    feature_count: int
    crs: str | None
    status: str
    error: str | None = None
    created_at: datetime


class FeatureOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    index: int
    geometry_type: str | None
    geometry: dict[str, Any] | None
    crs: str | None
    properties: dict[str, Any]


class MeasurementOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    index: int
    geometry_type: str | None
    supported: bool
    area_sq_m: float | None = None
    length_m: float | None = None
    measurement_crs: str | None = None
    warning: str | None = None


class MeasurementSummary(BaseModel):
    total_area_sq_m: float
    total_length_m: float
    measured_features: int
    skipped_features: int


class MeasurementsResponse(BaseModel):
    file_id: str
    total: int
    limit: int
    offset: int
    summary: MeasurementSummary
    results: list[MeasurementOut]


class FeaturesResponse(BaseModel):
    file_id: str
    total: int
    limit: int
    offset: int
    results: list[FeatureOut]
