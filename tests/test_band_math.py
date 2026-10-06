import numpy as np
import pytest
from calc.band_math import BandAlgebraEngine
from tile.tms import TileMath


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
