import ast
from typing import Dict
import numpy as np


class BandAlgebraEngine:
    ALLOWED_VARS = {"B1", "B2", "B3", "B4", "B8", "B11", "B12"}
    ALLOWED_NODES = (
        ast.Expression,
        ast.BinOp,
        ast.UnaryOp,
        ast.Add,
        ast.Sub,
        ast.Mult,
        ast.Div,
        ast.USub,
        ast.UAdd,
        ast.Constant,
        ast.Name,
        ast.Load,
    )

    @classmethod
    def _validate_ast(cls, node: ast.AST):
        if not isinstance(node, cls.ALLOWED_NODES):
            raise ValueError(f"安全拦截: 不支持的操作类型 '{type(node).__name__}'")
        if isinstance(node, ast.Name):
            if node.id not in cls.ALLOWED_VARS:
                raise ValueError(f"非法波段参数: '{node.id}'")
        for child in ast.iter_child_nodes(node):
            cls._validate_ast(child)

    @classmethod
    def evaluate_expression(cls, expr: str, bands: Dict[str, np.ndarray]) -> np.ndarray:
        expr_clean = expr.strip()
        parsed = ast.parse(expr_clean, mode="eval")
        cls._validate_ast(parsed)

        safe_env = {k: v.astype(np.float32) for k, v in bands.items() if k in cls.ALLOWED_VARS}
        compiled_code = compile(parsed, "<string>", "eval")
        result = eval(compiled_code, {"__builtins__": {}}, safe_env)
        return np.clip(np.nan_to_num(result, nan=0.0), -1.0, 1.0)
