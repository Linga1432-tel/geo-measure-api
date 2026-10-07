import io
import os
import tempfile
import zipfile
from pathlib import Path

os.environ["DATA_DIR"] = tempfile.mkdtemp()

import shapefile  # noqa: E402
from pyproj import CRS, Transformer  # noqa: E402
from shapely.ops import transform  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from shapely.geometry import LineString, Point, Polygon  # noqa: E402

from app.main import app  # noqa: E402

client = TestClient(app)

# ~0.01 deg square near Bengaluru (~1.2 km x 1.1 km  =>  ~1.2 km^2)
SQUARE = Polygon([(77.59, 12.97), (77.60, 12.97), (77.60, 12.98), (77.59, 12.98)])

KML = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document>
<Placemark><name>plot</name><Polygon><outerBoundaryIs><LinearRing><coordinates>
77.59,12.97,0 77.60,12.97,0 77.60,12.98,0 77.59,12.98,0 77.59,12.97,0
</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>
<Placemark><name>road</name><LineString><coordinates>77.59,12.97,0 77.60,12.97,0</coordinates></LineString></Placemark>
<Placemark><name>pin</name><Point><coordinates>77.59,12.97,0</coordinates></Point></Placemark>
</Document></kml>"""


def _zip_shapefile(geom, epsg=4326) -> bytes:
    """Write a one-polygon shapefile (+ .prj) with pyshp, in the given CRS, and zip it."""
    if epsg != 4326:
        t = Transformer.from_crs(4326, epsg, always_xy=True)
        geom = transform(t.transform, geom)
    with tempfile.TemporaryDirectory() as d:
        base = str(Path(d) / "data")
        with shapefile.Writer(base, shapeType=shapefile.POLYGON) as w:
            w.field("id", "N")
            w.poly([list(geom.exterior.coords)])
            w.record(1)
        Path(base + ".prj").write_text(CRS.from_epsg(epsg).to_wkt())
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            for p in Path(d).iterdir():
                zf.write(p, p.name)
        return buf.getvalue()


def _upload(name, data):
    return client.post("/api/files/", files={"file": (name, data)})


def test_kml_flow():
    r = _upload("survey.kml", KML.encode())
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "COMPLETED" and body["feature_count"] == 3
    assert body["crs"] == "EPSG:4326"

    assert client.get(f"/api/files/{body['id']}/").json()["filename"] == "survey.kml"

    m = client.get(f"/api/files/{body['id']}/measurements/").json()
    by_type = {x["geometry_type"]: x for x in m["results"]}
    assert by_type["Polygon"]["measurement_crs"] == "EPSG:32643"
    assert 1.1e6 < by_type["Polygon"]["area_sq_m"] < 1.3e6
    assert 1000 < by_type["LineString"]["length_m"] < 1150
    assert by_type["Point"]["supported"] and by_type["Point"]["area_sq_m"] is None

    feats = client.get(f"/api/files/{body['id']}/features/").json()
    assert feats["results"][0]["properties"]["name"] == "plot"


def test_shapefile_projected_crs_matches_wgs84():
    a = _upload("a.zip", _zip_shapefile(SQUARE)).json()
    b = _upload("b.zip", _zip_shapefile(SQUARE, 3857)).json()
    assert a["status"] == b["status"] == "COMPLETED", (a, b)
    assert b["crs"] == "EPSG:3857"
    area = lambda i: client.get(f"/api/files/{i}/measurements/").json()["results"][0]["area_sq_m"]
    assert area(a["id"]) == pytest.approx(area(b["id"]), rel=1e-3)


def test_multi_types_and_unsupported():
    from app.services.measurements import measure
    from shapely.geometry import GeometryCollection
    assert not measure(GeometryCollection([Point(0, 0), LineString([(0, 0), (1, 1)])])).supported
    assert not measure(None).supported


def test_bad_inputs():
    assert _upload("x.txt", b"hi").status_code == 415
    assert _upload("x.zip", b"not a zip").status_code == 422
    assert _upload("x.kml", b"<not-kml/>").status_code == 422
    assert client.get("/api/files/nope/").status_code == 404


def test_shapefile_without_prj_rejected():
    buf = io.BytesIO()
    with tempfile.TemporaryDirectory() as d:
        base = str(Path(d) / "x")
        with shapefile.Writer(base, shapeType=shapefile.POINT) as w:
            w.field("id", "N"); w.point(1, 1); w.record(1)
        with zipfile.ZipFile(buf, "w") as zf:
            for ext in ("shp", "shx", "dbf"):
                zf.write(f"{base}.{ext}", f"x.{ext}")
    r = _upload("x.zip", buf.getvalue())
    assert r.status_code == 422 and ".prj" in r.json()["error"]
