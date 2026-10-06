import math
from typing import Tuple


class TileMath:
    @staticmethod
    def tile_to_latlon_bounds(x: int, y: int, z: int) -> Tuple[float, float, float, float]:
        n = 2.0 ** z
        lon_min = x / n * 360.0 - 180.0
        lat_rad_max = math.atan(math.sinh(math.pi * (1 - 2 * y / n)))
        lat_max = math.degrees(lat_rad_max)

        lon_max = (x + 1) / n * 360.0 - 180.0
        lat_rad_min = math.atan(math.sinh(math.pi * (1 - 2 * (y + 1) / n)))
        lat_min = math.degrees(lat_rad_min)

        return (round(lon_min, 6), round(lat_min, 6), round(lon_max, 6), round(lat_max, 6))
