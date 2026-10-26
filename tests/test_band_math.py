import numpy as np
import pytest
import struct
import zlib
import rasterio
from rasterio.transform import from_bounds
from fastapi.testclient import TestClient
from calc.band_math import BandAlgebraEngine
from tile.tms import TileMath
from service.app import app, simulate_bands


def test_ndvi_band_algebra():
    b4 = np.array([[0.1, 0.2], [0.3, 0.4]])
    b8 = np.array([[0.5, 0.6], [0.7, 0.8]])
    engine = BandAlgebraEngine()
    res = engine.evaluate_expression("(B8 - B4) / (B8 + B4 + 1e-6)", {"B4": b4, "B8": b8})
    assert res.shape == (2, 2)
    assert np.all(res >= -1.0) and np.all(res <= 1.0)


def test_malicious_expression_prevention():
    engine = BandAlgebraEngine()
    with pytest.raises(ValueError):
        engine.evaluate_expression("__import__('os').system('ls')", {"B4": np.zeros((2, 2))})


def test_tile_coordinate_math():
    bounds = TileMath.tile_to_latlon_bounds(x=0, y=0, z=0)
    assert bounds[0] == -180.0
    assert bounds[2] == 180.0


def test_expression_preserves_actual_values_and_nodata():
    b4 = np.array([[2.0, -9999.0], [np.nan, 4.0]])
    result = BandAlgebraEngine.evaluate_expression("B4 * 2", {"B4": b4}, nodata=-9999)
    np.testing.assert_allclose(result, [[4, np.nan], [np.nan, 8]], equal_nan=True)


def test_division_by_zero_and_masked_input_stay_nodata():
    b4 = np.ma.array([[1.0, 2.0], [3.0, 4.0]], mask=[[0, 0], [1, 0]])
    b8 = np.array([[0.0, 2.0], [3.0, np.inf]])
    result = BandAlgebraEngine.evaluate_expression("B4 / B8", {"B4": b4, "B8": b8})
    np.testing.assert_allclose(result, [[np.nan, 1], [np.nan, np.nan]], equal_nan=True)


@pytest.mark.parametrize("expression", [
    "", "B4 +", "B4 ** 2", "B4[0]", "B4.real", "abs(B4)",
    "B4 + True", "B4 + 'x'", "B4 + 1e999", "1 + 2", "B9 + 1",
    "B4" + " + 1" * 80,
])
def test_rejects_unsupported_expressions(expression):
    with pytest.raises(ValueError):
        BandAlgebraEngine.evaluate_expression(expression, {"B4": np.ones((2, 2))})


@pytest.mark.parametrize("bands", [
    {"B4": np.ones((2, 2))},
    {"B4": np.ones((2, 2)), "B8": np.ones((2, 1))},
    {"B4": np.ones(2), "B8": np.ones(2)},
    {"B4": np.ones((0, 2)), "B8": np.ones((0, 2))},
])
def test_rejects_missing_or_misaligned_bands(bands):
    with pytest.raises(ValueError):
        BandAlgebraEngine.evaluate_expression("B4 + B8", bands)


@pytest.mark.parametrize("x,y,z", [(-1, 0, 1), (2, 0, 1), (0, 2, 1), (0, 0, -1),
                                      (0, 0, 23), (True, 0, 1), (0.5, 0, 1)])
def test_rejects_invalid_xyz_coordinates(x, y, z):
    with pytest.raises(ValueError):
        TileMath.tile_to_latlon_bounds(x, y, z)


def test_xyz_polar_bounds_and_shared_edges():
    west = TileMath.tile_to_latlon_bounds(0, 0, 1)
    east = TileMath.tile_to_latlon_bounds(1, 0, 1)
    south = TileMath.tile_to_latlon_bounds(0, 1, 1)
    assert west[3] == pytest.approx(85.0511287798066)
    assert west[2] == east[0] == 0
    assert west[1] == south[3] == 0
    assert south[1] == pytest.approx(-85.0511287798066)


def test_simulation_is_repeatable_without_mutating_global_rng():
    np.random.seed(42)
    expected = np.random.random()
    np.random.seed(42)
    first = simulate_bands(0, 0, 1)
    second = simulate_bands(0, 0, 1)
    other = simulate_bands(1, 0, 1)
    assert np.random.random() == expected
    np.testing.assert_equal(first["B4"], second["B4"])
    assert not np.array_equal(first["B4"][8:], other["B4"][8:])


@pytest.fixture
def client():
    with TestClient(app) as value:
        yield value


def test_supplied_arrays_return_exact_stats_and_null_nodata(client):
    response = client.post("/calculate", json={
        "expression": "B8 - B4", "nodata": -9999,
        "bands": {"B4": [[1, -9999], [None, 4]], "B8": [[5, 3], [6, 10]]},
    })
    assert response.status_code == 200
    data = response.json()
    assert data["source"] == "supplied_arrays"
    assert data["pixels"] == [[4, None], [None, 6]]
    assert data["stats"] == {"mean": 5, "min": 4, "max": 6, "std": 1}
    assert data["valid_count"] == data["nodata_count"] == 2


def test_all_nodata_has_null_statistics(client):
    data = client.post("/calculate", json={
        "expression": "B4 / 0", "bands": {"B4": [[1, 2]]},
    }).json()
    assert data["valid_count"] == 0
    assert all(value is None for value in data["stats"].values())
    assert data["pixels"] == [[None, None]]


def test_large_finite_values_do_not_overflow_statistics(client):
    response = client.post("/calculate", json={
        "expression": "B4", "bands": {"B4": [[-1e308, 1e308]]},
    })
    assert response.status_code == 200
    assert response.json()["stats"]["mean"] == 0
    assert response.json()["stats"]["std"] == 1e308


@pytest.mark.parametrize("payload", [
    {"expression": "B4", "bands": {"B4": [[1, 2], [3]]}},
    {"expression": "B4", "bands": {"B4": [[]]}},
    {"expression": "B4", "bands": {"B4": [[1]], "B99": [[1]]}},
    {"expression": "B8", "bands": {"B4": [[1]]}},
    {"expression": "B4", "bands": {"B4": [[1]]}, "path": "ignored.tif"},
])
def test_array_api_rejects_invalid_payloads(client, payload):
    assert client.post("/calculate", json=payload).status_code in (400, 422)


def test_tile_api_rejects_invalid_coordinates_and_expressions(client):
    assert client.get("/tiles/1/2/0/bounds").status_code == 400
    assert client.get("/tiles/-1/0/0/calc").status_code == 400
    assert client.get("/tiles/0/0/0/calc", params={"expr": "B4.__class__"}).status_code == 400
    assert client.get("/tiles/0/0/0/calc", params={"source": "arbitrary.tif"}).status_code == 422


def test_simulated_tile_stats_match_returned_pixels(client):
    response = client.get("/tiles/0/0/0/calc", params={"expr": "B4 * 10", "include_pixels": True})
    assert response.status_code == 200
    data = response.json()
    pixels = np.asarray(data["pixels"], dtype=float)
    assert data["source"] == "simulated_bands"
    assert data["nodata_count"] == 64
    assert data["stats"]["max"] > 1
    assert data["stats"]["mean"] == pytest.approx(np.nanmean(pixels), abs=1e-6)


@pytest.fixture
def configured_raster(tmp_path, monkeypatch):
    path = tmp_path / "self_created_bands.tif"
    b4 = np.full((32, 32), 0.2, dtype=np.float32)
    b8 = np.full((32, 32), 0.6, dtype=np.float32)
    b8[:, 16:] = 0.8
    b4[:4, :4] = b8[:4, :4] = -9999
    with rasterio.open(path, "w", driver="GTiff", width=32, height=32, count=2,
                       dtype="float32", crs="EPSG:4326", nodata=-9999,
                       transform=from_bounds(-180, 0, 0, 85.0511287798066, 32, 32)) as dataset:
        dataset.write(b4, 1)
        dataset.write(b8, 2)
        dataset.set_band_description(1, "B4")
        dataset.set_band_description(2, "B8")
    monkeypatch.setenv("TILE_RASTER_PATH", str(path))
    return path


def test_raster_mode_reads_reprojects_and_preserves_nodata(client, configured_raster):
    data = client.get("/tiles/1/0/0/calc", params={"source": "raster"}).json()
    assert data["source"] == "configured_raster"
    assert data["shape"] == [256, 256]
    assert 0 < data["nodata_count"] < 65536
    assert data["stats"]["min"] == pytest.approx(0.5, abs=1e-5)
    assert data["stats"]["max"] == pytest.approx(0.6, abs=1e-5)


def test_raster_xyz_windows_select_different_source_regions(client, configured_raster):
    left = client.get("/tiles/2/0/0/calc", params={"source": "raster"}).json()
    right = client.get("/tiles/2/1/0/calc", params={"source": "raster"}).json()
    assert left["stats"]["mean"] == pytest.approx(0.5, abs=1e-5)
    assert right["stats"]["mean"] == pytest.approx(0.6, abs=1e-5)


def test_raster_outside_coverage_is_nodata(client, configured_raster):
    response = client.get("/tiles/1/1/0/calc", params={"source": "raster"})
    assert response.status_code == 200
    assert response.json()["valid_count"] == 0
    assert response.json()["stats"]["mean"] is None


def test_raster_mode_reports_missing_configuration(client, monkeypatch):
    monkeypatch.delenv("TILE_RASTER_PATH", raising=False)
    assert client.get("/sources").json()["raster_configured"] is False
    response = client.get("/tiles/0/0/0/calc", params={"source": "raster"})
    assert response.status_code == 400
    assert "TILE_RASTER_PATH" in response.json()["detail"]


def test_raster_proj_failure_gives_actionable_error(client, configured_raster, monkeypatch):
    def incompatible_proj(*args, **kwargs):
        raise rasterio.errors.CRSError("PROJ: proj.db DATABASE.LAYOUT.VERSION mismatch")

    monkeypatch.setattr("service.app.rasterio.open", incompatible_proj)
    response = client.get("/tiles/1/0/0/calc", params={"source": "raster"})
    assert response.status_code == 400
    assert "PROJ_DATA" in response.json()["detail"]
    assert "proj.db" in response.json()["detail"]


def test_raster_without_crs_is_rejected(client, tmp_path, monkeypatch):
    path = tmp_path / "no_crs.tif"
    with rasterio.open(path, "w", driver="GTiff", width=2, height=2, count=1,
                       dtype="float32", transform=from_bounds(0, 0, 1, 1, 2, 2)) as dataset:
        dataset.write(np.ones((2, 2), dtype=np.float32), 1)
    monkeypatch.setenv("TILE_RASTER_PATH", str(path))
    response = client.get("/tiles/0/0/0/calc", params={"source": "raster", "expr": "B1"})
    assert response.status_code == 400
    assert "坐标参考系" in response.json()["detail"]


def test_png_contains_valid_rgba_pixels_and_transparent_nodata(client, configured_raster):
    response = client.get("/tiles/1/0/0/image.png", params={"source": "raster"})
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    content = response.content
    assert content.startswith(b"\x89PNG\r\n\x1a\n")
    assert struct.unpack_from(">II", content, 16) == (256, 256)
    offset = 8
    compressed = b""
    while offset < len(content):
        length = struct.unpack_from(">I", content, offset)[0]
        kind = content[offset + 4:offset + 8]
        data = content[offset + 8:offset + 8 + length]
        crc = struct.unpack_from(">I", content, offset + 8 + length)[0]
        assert crc == zlib.crc32(kind + data)
        if kind == b"IDAT":
            compressed += data
        offset += length + 12
    rows = np.frombuffer(zlib.decompress(compressed), dtype=np.uint8).reshape(256, 1025)
    assert np.all(rows[:, 0] == 0)
    alpha = rows[:, 1:].reshape(256, 256, 4)[..., 3]
    assert np.any(alpha == 0) and np.any(alpha == 255)


def test_viewer_and_local_leaflet_are_served(client):
    page = client.get("/")
    assert page.status_code == 200
    assert "Math.random" not in page.text
    assert "URLSearchParams" in page.text
    assert client.get("/vendor/leaflet.js").status_code == 200
