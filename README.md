# Geospatial File Measurement API

A FastAPI backend that accepts a **Shapefile (.zip)** or **KML**, extracts every feature
(index, geometry type, geometry, CRS, properties) and computes **area** (polygons) and
**length** (lines) in metres, using a properly projected CRS.

## Setup

Requires Python 3.10+ (GeoPandas wheels bundle GDAL, no system install needed).

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

- Interactive docs (Swagger): http://127.0.0.1:8000/docs
- Run tests: `pytest -q`
- Config via env vars: `DATA_DIR` (default `data/`), `DATABASE_URL`, `MAX_UPLOAD_MB` (50), `MAX_UNZIPPED_MB` (200)

## API

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/files/` | Upload `.zip` (Shapefile) or `.kml` (multipart field `file`) and process it |
| GET | `/api/files/{id}/` | File info / status |
| GET | `/api/files/{id}/measurements/?limit=&offset=` | Per-feature measurements + totals |
| GET | `/api/files/{id}/features/?limit=&offset=` | Features with geometry (GeoJSON) and properties |
| GET | `/health` | Health check |

### Upload
```bash
curl -F "file=@survey.kml" http://127.0.0.1:8000/api/files/
```
```json
{"id":"3f9c...","filename":"survey.kml","file_type":"kml","feature_count":3,
 "crs":"EPSG:4326","status":"COMPLETED","error":null,"created_at":"2026-10-07T10:00:00"}
```
Errors: `415` unsupported extension, `413` too large, `400` empty, `422` unreadable/invalid
content (response still contains the file `id` with `status: FAILED` and an `error` message),
`404` unknown id, `409` asking for results of a file that is not `COMPLETED`.

### Measurements
```bash
curl http://127.0.0.1:8000/api/files/3f9c.../measurements/
```
```json
{
  "file_id": "3f9c...", "total": 3, "limit": 100, "offset": 0,
  "summary": {"total_area_sq_m": 1203541.2, "total_length_m": 1085.7,
              "measured_features": 3, "skipped_features": 0},
  "results": [
    {"index":0,"geometry_type":"Polygon","supported":true,"area_sq_m":1203541.2,
     "length_m":null,"measurement_crs":"EPSG:32643","warning":null},
    {"index":1,"geometry_type":"LineString","supported":true,"area_sq_m":null,
     "length_m":1085.7,"measurement_crs":"EPSG:32643","warning":null},
    {"index":2,"geometry_type":"Point","supported":true,"area_sq_m":null,
     "length_m":null,"measurement_crs":null,"warning":null}
  ]
}
```
Unsupported or empty geometries (e.g. `GeometryCollection`, null geometry) return
`"supported": false` with a `warning` instead of failing the request.

## Architecture

```
app/
  main.py            FastAPI app, router registration, table creation
  config.py          Env-driven settings and limits
  db.py / models.py  SQLAlchemy 2.0 (SQLite by default): UploadedFile, Feature
  schemas.py         Pydantic response models
  routers/files.py   HTTP layer only (validation, status codes, pagination)
  services/
    readers.py       Safe zip extraction + Shapefile/KML reading (GeoPandas/pyogrio)
    crs.py           UTM zone selection + cached pyproj transformers
    measurements.py  Pure functions: geometry (EPSG:4326) -> Measurement
    processing.py    Orchestrates read -> extract -> measure -> persist
tests/test_api.py    End-to-end + unit tests
```

**File-processing flow**
1. Validate extension, stream upload to a temp file while enforcing the size limit.
2. Create an `UploadedFile` row (`PROCESSING`).
3. Read: for `.zip`, extract safely (zip-slip and zip-bomb checks) and read every `.shp`;
   for KML, read every layer (KML folders) via GDAL.
4. For each feature store index, GeoJSON geometry (original CRS), geometry type, CRS, properties.
5. Status becomes `COMPLETED`, or `FAILED` with an error message. Nothing raises to the client as a 500.

**Measurement flow**
For each feature: reproject to EPSG:4326 -> choose a UTM zone from the feature centroid ->
reproject to that UTM CRS -> `area` (Polygon/MultiPolygon) or `length` (LineString/MultiLineString).
Multi-part geometries are summed. Points need no measurement. Z values are dropped (2D measurement).
Results are computed once at upload and stored, so GET requests are cheap.

**CRS handling**
Area/length are never computed in degrees. Source CRS is read from the file (`.prj` for
shapefiles; KML is always WGS84 by spec). The UTM zone is picked **per feature**
(UPS beyond 84°N / 80°S), so datasets spanning several zones are still measured accurately.
A shapefile without `.prj` is rejected with a clear error rather than guessing.
Projected inputs (e.g. EPSG:3857) are also re-measured in UTM, because Web Mercator
heavily distorts area away from the equator. The test suite checks 4326 vs 3857 input agree.

## Design Decisions

- **FastAPI over Django/DRF**: smaller surface, async upload streaming, automatic OpenAPI docs; no admin/ORM-heavy needs.
- **GeoPandas + pyogrio**: one reader for Shapefile and KML, bundled GDAL wheels (no system GDAL). Alternatives: `fiona` (slower, older), `fastkml`/`pyshp` (more code, more format gaps).
- **Per-feature UTM vs. a single CRS per file vs. geodesic (`pyproj.Geod`)**: per-feature UTM is accurate to ~0.1% in-zone and easy to explain and audit. Geodesic is the most accurate globally and is a good future cross-check.
- **Measure at upload, persist results**: simple and fast reads. Trade-off: algorithm changes need re-processing.
- **Synchronous processing** with a status field: fine for small/medium files and keeps the API contract (`PROCESSING/COMPLETED/FAILED`) ready for async workers. Alternative: Celery/RQ.
- **SQLite + JSON columns**: zero setup for reviewers. PostGIS would be the production choice.
- **Pagination** on features/measurements; summary totals computed in SQL.
- **Graceful failures**: unsupported/empty geometry flagged, invalid polygons warned, bad files return 422.

## Learnings

- Why degrees can't be used for area, and how UTM zones/EPSG codes (326xx/327xx) work.
- `always_xy=True` matters: axis order differs between EPSG:4326 definitions and GDAL/KML.
- KML folders appear as separate GDAL layers, so reading only the default layer drops data.
- Untrusted zip files need zip-slip and size checks.

## Future Scope

- Background processing (Celery/RQ) with progress, and polling/webhooks.
- PostGIS storage and spatial queries (bbox filter, intersects).
- Geodesic (ellipsoidal) measurements as a cross-check or option; perimeter and centroid.
- More formats: GeoJSON, GPKG; KMZ support.
- Auto-repair invalid geometry (`make_valid`), CSV/GeoJSON export of measurements.
- Auth, rate limiting, Docker + CI, Alembic migrations, S3 file storage.
