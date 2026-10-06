from fastapi import FastAPI, HTTPException
import numpy as np
from calc.band_math import BandAlgebraEngine
from tile.tms import TileMath

app = FastAPI(title="TiTile-Stream Service")
engine = BandAlgebraEngine()


@app.get("/tiles/{z}/{x}/{y}/bounds")
def get_tile_bounds(z: int, x: int, y: int):
    bounds = TileMath.tile_to_latlon_bounds(x, y, z)
    return {
        "z": z, "x": x, "y": y,
        "bbox": {"west": bounds[0], "south": bounds[1], "east": bounds[2], "north": bounds[3]}
    }


@app.get("/tiles/{z}/{x}/{y}/calc")
def calculate_tile_index(z: int, x: int, y: int, expr: str = "(B8 - B4) / (B8 + B4 + 1e-6)"):
    try:
        np.random.seed(x + y + z)
        b4 = np.random.uniform(0.1, 0.4, (256, 256))
        b8 = np.random.uniform(0.3, 0.9, (256, 256))
        index_array = engine.evaluate_expression(expr, {"B4": b4, "B8": b8})

        mean_val = float(np.mean(index_array))
        max_val = float(np.max(index_array))
        min_val = float(np.min(index_array))

        return {
            "z": z, "x": x, "y": y,
            "expression": expr,
            "stats": {"mean": round(mean_val, 4), "max": round(max_val, 4), "min": round(min_val, 4)}
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
