import json
import numpy as np
import pytest
import rasterio
from fastapi.testclient import TestClient
from rasterio.transform import from_bounds

from service.app import app
from service.demo import create_demo_raster


@pytest.fixture
def client():
    with TestClient(app) as value:
        yield value


def test_demo_raster_is_deterministic_labelled_and_preserves_nodata(tmp_path):
    first = create_demo_raster(tmp_path / "first.tif", size=64)
    second = create_demo_raster(tmp_path / "nested" / "second.tif", size=64)
    with rasterio.open(first) as left, rasterio.open(second) as right:
        assert left.descriptions == ("B4", "B8")
        assert left.crs.to_epsg() == 4326
        assert left.tags()["data_kind"] == "synthetic_demo"
        np.testing.assert_array_equal(left.read(), right.read())
        assert left.read(1, masked=True).mask.sum() == 64
        assert left.read(1)[-1, -1] == pytest.approx(0.3)
        assert left.read(2)[-1, -1] == pytest.approx(0.7)


def test_demo_refuses_existing_file_without_changing_it(tmp_path):
    output = tmp_path / "existing.tif"
    output.write_bytes(b"must remain unchanged")
    with pytest.raises(FileExistsError):
        create_demo_raster(output)
    assert output.read_bytes() == b"must remain unchanged"


@pytest.mark.parametrize("size", [0, 31, 2049])
def test_invalid_demo_size_does_not_create_output(tmp_path, size):
    output = tmp_path / "not_created" / "demo.tif"
    with pytest.raises(ValueError):
        create_demo_raster(output, size)
    assert not output.parent.exists()


def test_demo_metadata_to_raster_calculation_flow(client, tmp_path, monkeypatch):
    path = create_demo_raster(tmp_path / "demo.tif")
    monkeypatch.setenv("TILE_RASTER_PATH", str(path))
    assert client.get("/health").json() == {"status": "ok", "service": "titile-stream"}
    response = client.get("/sources/raster")
    assert response.status_code == 200
    data = response.json()
    assert str(tmp_path) not in json.dumps(data)
    assert data["name"] == "demo.tif"
    assert data["data_kind"] == "synthetic_demo"
    assert data["width"] == data["height"] == 256
    assert data["bbox"] == pytest.approx({"west": 114.2, "south": 30.3, "east": 114.6, "north": 30.7})
    assert [band["name"] for band in data["bands"]] == ["B4", "B8"]
    # 覆盖范围中心对应的实际 XYZ 瓦片，验证演示数据不只停留在文件生成。
    result = client.get("/tiles/10/837/420/calc", params={"source": "raster"})
    assert result.status_code == 200
    assert result.json()["valid_count"] > 0
    assert 0 < result.json()["stats"]["mean"] < 1
    image = client.get("/tiles/10/837/420/image.png", params={"source": "raster"})
    assert image.status_code == 200 and image.content.startswith(b"\x89PNG\r\n\x1a\n")


def test_metadata_reprojects_bounds_to_longitude_latitude(client, tmp_path, monkeypatch):
    path = tmp_path / "projected.tif"
    with rasterio.open(path, "w", driver="GTiff", width=16, height=16, count=1,
                       dtype="float32", crs="EPSG:3857",
                       transform=from_bounds(0, 0, 111319.490793, 111325.142866, 16, 16)) as dataset:
        dataset.write(np.ones((16, 16), dtype="float32"), 1)
    monkeypatch.setenv("TILE_RASTER_PATH", str(path))
    data = client.get("/sources/raster").json()
    assert data["crs"] == "EPSG:3857"
    assert data["bbox_crs"] == "EPSG:4326"
    assert data["bbox"] == pytest.approx({"west": 0, "south": 0, "east": 1, "north": 1})
    assert data["bands"] == [{"name": "B1", "index": 1, "dtype": "float32"}]


def test_metadata_rejects_absent_file_and_health_is_independent(client, tmp_path, monkeypatch):
    monkeypatch.setenv("TILE_RASTER_PATH", str(tmp_path / "private_missing.tif"))
    response = client.get("/sources/raster")
    assert response.status_code == 400
    assert "private_missing" not in response.text
    assert client.get("/health").status_code == 200


def test_metadata_read_errors_do_not_disclose_paths(client, tmp_path, monkeypatch):
    path = tmp_path / "private_corrupt.tif"
    path.write_text("not a raster", encoding="utf8")
    monkeypatch.setenv("TILE_RASTER_PATH", str(path))
    response = client.get("/sources/raster")
    assert response.status_code == 400
    assert str(tmp_path) not in response.text
    assert "private_corrupt" not in response.text


def test_metadata_rejects_missing_crs(client, tmp_path, monkeypatch):
    path = tmp_path / "missing_crs.tif"
    with rasterio.open(path, "w", driver="GTiff", width=8, height=8, count=1,
                       dtype="float32", transform=from_bounds(0, 0, 1, 1, 8, 8)) as dataset:
        dataset.write(np.ones((8, 8), dtype="float32"), 1)
    monkeypatch.setenv("TILE_RASTER_PATH", str(path))
    response = client.get("/sources/raster")
    assert response.status_code == 400
    assert "坐标参考系" in response.json()["detail"]
