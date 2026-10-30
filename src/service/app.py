from pathlib import Path
import os
import struct
import zlib
from typing import Literal
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.transform import from_bounds
from rasterio.vrt import WarpedVRT
from rasterio.warp import transform_bounds
from calc.band_math import BandAlgebraEngine
from tile.tms import TileMath

app = FastAPI(title="TiTile-Stream 波段计算原型")
engine = BandAlgebraEngine()
service_directory = Path(__file__).resolve().parent
app.mount("/vendor", StaticFiles(directory=service_directory / "vendor"), name="vendor")


class BandCalculationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    expression: str = Field(min_length=1, max_length=512)
    bands: dict[str, list[list[float | None]]] = Field(min_length=1, max_length=7)
    nodata: float | None = None


def summarize_array(array: np.ndarray, include_pixels: bool = False) -> dict:
    valid = np.isfinite(array)
    values = array[valid]
    stats = dict.fromkeys(("mean", "max", "min", "std"))
    if values.size:
        scale = float(np.abs(values).max()) or 1.0
        normalized = values / scale
        stats = {
            "mean": round(float(np.mean(normalized)) * scale, 6),
            "std": round(float(np.std(normalized)) * scale, 6),
            "max": round(float(values.max()), 6),
            "min": round(float(values.min()), 6),
        }
    output = {
        "shape": list(array.shape),
        "valid_count": int(valid.sum()),
        "nodata_count": int(array.size - valid.sum()),
        "stats": stats,
    }
    if include_pixels:
        pixels = array.astype(object)
        pixels[~valid] = None
        output["pixels"] = pixels.tolist()
    return output


def simulate_bands(x: int, y: int, z: int) -> dict[str, np.ndarray]:
    TileMath.validate_coordinates(x, y, z)
    rng = np.random.default_rng(np.random.SeedSequence([z, x, y]))
    bands = {}
    for name in sorted(engine.ALLOWED_VARS):
        array = rng.uniform(0.1, 0.9, (256, 256))
        array[:8, :8] = np.nan
        bands[name] = array
    return bands


def raster_path() -> Path:
    configured = os.environ.get("TILE_RASTER_PATH", "")
    if not configured:
        raise ValueError("未配置 TILE_RASTER_PATH，请选择模拟模式或配置服务端栅格路径")
    path = Path(configured).expanduser().resolve()
    if not path.is_file():
        raise ValueError("服务端配置的栅格文件不存在")
    return path


def raster_band_map(dataset) -> dict[str, int]:
    mapping = {}
    for index, description in enumerate(dataset.descriptions, start=1):
        name = description or f"B{index}"
        if name in engine.ALLOWED_VARS:
            if name in mapping:
                raise ValueError("栅格波段描述包含重复名称")
            mapping[name] = index
    return mapping


def raster_failure(exc: Exception) -> ValueError:
    if "proj" in str(exc).lower():
        return ValueError(
            "栅格坐标转换失败：请检查 PROJ_DATA／PROJ_LIB 是否指向当前 Rasterio 兼容的 proj.db"
        )
    return ValueError("配置的栅格无法读取或重投影")


def describe_raster() -> dict:
    try:
        path = raster_path()
        with rasterio.open(path) as dataset:
            if dataset.crs is None:
                raise ValueError("栅格缺少坐标参考系")
            band_map = raster_band_map(dataset)
            if not band_map:
                raise ValueError("栅格没有可识别的波段")
            bounds = transform_bounds(dataset.crs, "EPSG:4326", *dataset.bounds, densify_pts=21)
            west, south, east, north = bounds
            if (not np.isfinite(bounds).all() or west >= east or south >= north
                    or west < -180 or east > 180 or south < -90 or north > 90):
                raise ValueError("栅格覆盖范围无效或跨越日期变更线，当前地图暂不支持")
            if south >= 85.0511287798066 or north <= -85.0511287798066:
                raise ValueError("栅格覆盖范围位于 Web Mercator XYZ 支持范围之外")
            return {
                "source": "configured_raster", "name": path.name,
                "crs": dataset.crs.to_string(), "width": dataset.width, "height": dataset.height,
                "bbox_crs": "EPSG:4326",
                "bbox": dict(zip(("west", "south", "east", "north"), bounds)),
                "bands": [
                    {"name": name, "index": index, "dtype": dataset.dtypes[index - 1]}
                    for name, index in band_map.items()
                ],
                "data_kind": "synthetic_demo" if dataset.tags().get("data_kind") == "synthetic_demo"
                else "user_configured",
            }
    except (rasterio.errors.RasterioError, rasterio.errors.CRSError) as exc:
        raise raster_failure(exc) from exc


def read_raster_tile(x: int, y: int, z: int) -> dict[str, np.ndarray]:
    TileMath.validate_coordinates(x, y, z)
    limit = 20037508.342789244
    span = 2 * limit / (2 ** z)
    bounds = (-limit + x * span, limit - (y + 1) * span,
              -limit + (x + 1) * span, limit - y * span)
    try:
        with rasterio.open(raster_path()) as dataset:
            if dataset.crs is None:
                raise ValueError("栅格缺少坐标参考系")
            band_map = raster_band_map(dataset)
            if not band_map:
                raise ValueError("栅格没有可识别的波段")
            # VRT 按目标 XYZ 范围读取并重投影，范围外及源无数据保留掩码。
            with WarpedVRT(
                dataset, crs="EPSG:3857", transform=from_bounds(*bounds, 256, 256),
                width=256, height=256, dtype="float64", nodata=np.nan,
                resampling=Resampling.nearest,
            ) as vrt:
                return {
                    name: vrt.read(index, masked=True).filled(np.nan)
                    for name, index in band_map.items()
                }
    except (rasterio.errors.RasterioError, rasterio.errors.CRSError) as exc:
        raise raster_failure(exc) from exc


def calculate_tile_array(x: int, y: int, z: int, expr: str, source: str):
    TileMath.validate_coordinates(x, y, z)
    if source == "raster":
        bands = read_raster_tile(x, y, z)
        description = f"服务端栅格 {raster_path().name}；最近邻重投影至 256×256 XYZ 网格"
    else:
        bands = simulate_bands(x, y, z)
        description = "确定性模拟波段，不代表真实卫星影像；左上 8×8 像元为无数据"
    return engine.evaluate_expression(expr, bands), description


def encode_png(array: np.ndarray, colormap: str) -> bytes:
    valid = np.isfinite(array)
    values = array[valid]
    scaled = np.zeros(array.shape)
    if values.size:
        scale = float(np.abs(values).max()) or 1.0
        minimum = float(values.min()) / scale
        span = float(values.max()) / scale - minimum
        scaled[valid] = (values / scale - minimum) / span if span > 0 else 0.5
    rgba = np.zeros((*array.shape, 4), dtype=np.uint8)
    if colormap == "water":
        rgba[..., 0] = 10 + scaled * 50
        rgba[..., 1] = 80 + scaled * 120
        rgba[..., 2] = 180 + scaled * 70
    elif colormap == "turbo":
        rgba[..., 0] = scaled * 255
        rgba[..., 1] = (1 - np.abs(2 * scaled - 1)) * 255
        rgba[..., 2] = (1 - scaled) * 255
    else:
        rgba[..., 0] = (1 - scaled) * 230
        rgba[..., 1] = scaled * 230
        rgba[..., 2] = 40
    rgba[..., 3] = valid * 255

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    height, width = array.shape
    rows = b"".join(b"\x00" + row.tobytes() for row in rgba)
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


@app.get("/sources")
def get_sources():
    configured = bool(os.environ.get("TILE_RASTER_PATH"))
    return {"simulated": True, "raster_configured": configured}


@app.get("/health")
def get_health():
    return {"status": "ok", "service": "titile-stream"}


@app.get("/sources/raster")
def get_raster_metadata():
    try:
        return describe_raster()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/")
def get_viewer():
    return FileResponse(service_directory / "viewer.html")


@app.get("/tiles/{z}/{x}/{y}/bounds")
def get_tile_bounds(z: int, x: int, y: int):
    try:
        bounds = TileMath.tile_to_latlon_bounds(x, y, z)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "z": z, "x": x, "y": y,
        "bbox": {"west": bounds[0], "south": bounds[1], "east": bounds[2], "north": bounds[3]}
    }


@app.get("/tiles/{z}/{x}/{y}/calc")
def calculate_tile_index(
    z: int, x: int, y: int,
    expr: str = Query("(B8 - B4) / (B8 + B4 + 1e-6)", min_length=1, max_length=512),
    include_pixels: bool = False,
    source: Literal["simulated", "raster"] = "simulated",
):
    try:
        index_array, description = calculate_tile_array(x, y, z, expr, source)
        return {
            "z": z, "x": x, "y": y,
            "source": "simulated_bands" if source == "simulated" else "configured_raster",
            "source_description": description,
            "expression": expr,
            **summarize_array(index_array, include_pixels),
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/tiles/{z}/{x}/{y}/image.png")
def get_tile_image(
    z: int, x: int, y: int,
    expr: str = Query("(B8 - B4) / (B8 + B4 + 1e-6)", min_length=1, max_length=512),
    source: Literal["simulated", "raster"] = "simulated",
    colormap: Literal["ndvi", "water", "turbo"] = "ndvi",
):
    try:
        array, _ = calculate_tile_array(x, y, z, expr, source)
        return Response(encode_png(array, colormap), media_type="image/png",
                        headers={"X-Tile-Source": source})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/calculate")
def calculate_supplied_bands(request: BandCalculationRequest):
    try:
        if set(request.bands) - engine.ALLOWED_VARS:
            raise ValueError("包含不支持的波段名称")
        if any(
            not rows or len(rows) > 512 or any(not row or len(row) > 512 for row in rows)
            for rows in request.bands.values()
        ):
            raise ValueError("每个波段须为 1 至 512 行、每行 1 至 512 列")
        arrays = {name: np.asarray(rows, dtype=np.float64) for name, rows in request.bands.items()}
        result = engine.evaluate_expression(request.expression, arrays, request.nodata)
        return {
            "source": "supplied_arrays",
            "expression": request.expression,
            **summarize_array(result, include_pixels=True),
        }
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
