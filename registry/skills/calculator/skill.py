definition = {
    "name": "calculator",
    "description": "Safely evaluate simple arithmetic expressions.",
    "parameters": [
        {"name": "expression", "type": "string", "required": True},
    ],
}

import ast
import math
import statistics


_ALLOWED_NAMES = {
    "abs": abs,
    "min": min,
    "max": max,
    "round": round,
    "sum": sum,
    "pow": pow,
    "math": math,
    "statistics": statistics,
}
_ALLOWED_NODES = (
    ast.Expression,
    ast.BinOp,
    ast.UnaryOp,
    ast.BoolOp,
    ast.Compare,
    ast.Call,
    ast.Load,
    ast.Name,
    ast.Constant,
    ast.List,
    ast.Tuple,
    ast.Dict,
    ast.Set,
    ast.Subscript,
    ast.Slice,
    ast.Attribute,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.FloorDiv,
    ast.Mod,
    ast.Pow,
    ast.USub,
    ast.UAdd,
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
)


def _validate(node):
    if not isinstance(node, _ALLOWED_NODES):
        raise ValueError(f"Unsupported expression node: {type(node).__name__}")
    if isinstance(node, ast.Name) and node.id not in _ALLOWED_NAMES:
        raise ValueError(f"Unsupported name: {node.id}")
    if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
        raise ValueError("Private attributes are not allowed")
    for child in ast.iter_child_nodes(node):
        _validate(child)


def execute(params):
    expression = str(params.get("expression", "")).strip()
    if not expression:
        raise ValueError("expression is required")
    tree = ast.parse(expression, mode="eval")
    _validate(tree)
    value = eval(compile(tree, "<calculator>", "eval"), {"__builtins__": {}}, _ALLOWED_NAMES)
    return {"expression": expression, "value": value}
