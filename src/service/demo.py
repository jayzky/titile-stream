"""生成不含真实遥感观测的确定性演示栅格。"""

import argparse
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_bounds


def create_demo_raster(output: Path, size: int = 256) -> Path:
    if not 32 <= size <= 2048:
        raise ValueError("size 必须介于 32 与 2048 之间")
    output = Path(output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    # 排他创建先占用输出，避免覆盖用户已有文件；失败文件供用户检查。
    with output.open("xb"):
        pass
    rows, columns = np.mgrid[0:size, 0:size] / (size - 1)
    b4 = (0.15 + 0.15 * columns).astype("float32")
    b8 = (0.4 + 0.3 * rows).astype("float32")
    b4[:size // 8, :size // 8] = b8[:size // 8, :size // 8] = -9999
    with rasterio.open(
        output, "w", driver="GTiff", width=size, height=size, count=2,
        dtype="float32", crs="EPSG:4326", nodata=-9999,
        transform=from_bounds(114.2, 30.3, 114.6, 30.7, size, size), compress="deflate",
    ) as dataset:
        dataset.write(b4, 1)
        dataset.write(b8, 2)
        dataset.set_band_description(1, "B4")
        dataset.set_band_description(2, "B8")
        dataset.update_tags(data_kind="synthetic_demo", source="deterministic gradients; not satellite data")
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("data/demo_bands.tif"))
    parser.add_argument("--size", type=int, default=256)
    args = parser.parse_args()
    try:
        output = create_demo_raster(args.output, args.size)
    except (OSError, ValueError, rasterio.errors.RasterioError) as exc:
        parser.exit(1, f"生成失败：{exc}\n")
    print(f"已生成自建演示栅格（非卫星影像）：{output}")


if __name__ == "__main__":
    main()
