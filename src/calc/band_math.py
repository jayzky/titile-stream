import ast
import math
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
    def _validate_ast(cls, node: ast.AST, depth: int = 0):
        if depth > 20:
            raise ValueError("表达式嵌套过深")
        if not isinstance(node, cls.ALLOWED_NODES):
            raise ValueError(f"安全拦截: 不支持的操作类型 '{type(node).__name__}'")
        if isinstance(node, ast.Name) and node.id not in cls.ALLOWED_VARS:
            raise ValueError(f"非法波段参数: '{node.id}'")
        if isinstance(node, ast.Constant):
            if type(node.value) not in (int, float):
                raise ValueError("表达式常量必须是有限数值")
            try:
                finite = math.isfinite(float(node.value))
            except OverflowError:
                finite = False
            if not finite:
                raise ValueError("表达式常量必须是有限数值")
        for child in ast.iter_child_nodes(node):
            cls._validate_ast(child, depth + 1)

    @classmethod
    def _evaluate_node(cls, node: ast.AST, bands: Dict[str, np.ndarray]):
        if isinstance(node, ast.Expression):
            return cls._evaluate_node(node.body, bands)
        if isinstance(node, ast.Name):
            return bands[node.id]
        if isinstance(node, ast.Constant):
            return float(node.value)
        if isinstance(node, ast.UnaryOp):
            value = cls._evaluate_node(node.operand, bands)
            return -value if isinstance(node.op, ast.USub) else value
        left = cls._evaluate_node(node.left, bands)
        right = cls._evaluate_node(node.right, bands)
        if isinstance(node.op, ast.Add):
            return np.add(left, right)
        if isinstance(node.op, ast.Sub):
            return np.subtract(left, right)
        if isinstance(node.op, ast.Mult):
            return np.multiply(left, right)
        return np.divide(left, right)

    @classmethod
    def evaluate_expression(
        cls, expr: str, bands: Dict[str, np.ndarray], nodata: float | None = None
    ) -> np.ndarray:
        if not isinstance(expr, str) or not expr.strip() or len(expr) > 512:
            raise ValueError("表达式须为 1 至 512 个字符")
        try:
            parsed = ast.parse(expr.strip(), mode="eval")
        except (SyntaxError, RecursionError) as exc:
            raise ValueError("表达式语法错误") from exc
        if sum(1 for _ in ast.walk(parsed)) > 128:
            raise ValueError("表达式节点过多")
        cls._validate_ast(parsed)
        names = {node.id for node in ast.walk(parsed) if isinstance(node, ast.Name)}
        if not names:
            raise ValueError("表达式至少引用一个波段")
        missing = names - bands.keys()
        if missing:
            raise ValueError(f"缺少波段: {', '.join(sorted(missing))}")

        arrays = {}
        shape = None
        invalid = None
        for name in sorted(names):
            values = np.ma.asarray(bands[name], dtype=np.float64)
            array = np.asarray(values.filled(np.nan))
            if array.ndim != 2 or not array.size or array.size > 1_048_576:
                raise ValueError("波段须为非空二维数组，且像元数不超过 1048576")
            if shape is not None and array.shape != shape:
                raise ValueError("参与运算的波段形状必须一致")
            shape = array.shape
            mask = ~np.isfinite(array)
            if nodata is not None:
                mask |= array == nodata
            invalid = mask if invalid is None else invalid | mask
            arrays[name] = array

        # 无数据像元始终保留掩码，禁止用零或区间裁剪伪造有效值。
        with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
            result = np.asarray(cls._evaluate_node(parsed, arrays), dtype=np.float64)
        return np.where(invalid | ~np.isfinite(result), np.nan, result)
