import math
from typing import Tuple


class TileMath:
    @staticmethod
    def validate_coordinates(x: int, y: int, z: int) -> None:
        if any(type(value) is not int for value in (x, y, z)):
            raise ValueError("瓦片坐标必须为整数")
        if not 0 <= z <= 22:
            raise ValueError("缩放层级 z 必须在 0 至 22 之间")
        if not (0 <= x < 2 ** z and 0 <= y < 2 ** z):
            raise ValueError("瓦片 x/y 必须在 0 至 2^z-1 之间")

    @staticmethod
    def tile_to_latlon_bounds(x: int, y: int, z: int) -> Tuple[float, float, float, float]:
        TileMath.validate_coordinates(x, y, z)
        n = 2.0 ** z
        lon_min = x / n * 360.0 - 180.0
        lat_rad_max = math.atan(math.sinh(math.pi * (1 - 2 * y / n)))
        lat_max = math.degrees(lat_rad_max)

        lon_max = (x + 1) / n * 360.0 - 180.0
        lat_rad_min = math.atan(math.sinh(math.pi * (1 - 2 * (y + 1) / n)))
        lat_min = math.degrees(lat_rad_min)

        return (lon_min, lat_min, lon_max, lat_max)
