import io
import os
import tempfile
import zipfile
from pathlib import Path

os.environ["DATA_DIR"] = tempfile.mkdtemp()

import geopandas as gpd  # noqa: E402
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


def _zip_shapefile(gdf: gpd.GeoDataFrame) -> bytes:
    with tempfile.TemporaryDirectory() as d:
        gdf.to_file(Path(d) / "data.shp")
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
    assert feats["results"][0]["properties"]["Name"] == "plot"


def test_shapefile_projected_crs_matches_wgs84():
    gdf = gpd.GeoDataFrame({"id": [1]}, geometry=[SQUARE], crs="EPSG:4326")
    a = _upload("a.zip", _zip_shapefile(gdf)).json()
    b = _upload("b.zip", _zip_shapefile(gdf.to_crs("EPSG:3857"))).json()
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
