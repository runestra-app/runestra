"""Restricted conditions: literals, comparisons, booleans and dotted references."""

import ast
from typing import Any

_ALLOWED = (
    ast.Expression,
    ast.BoolOp,
    ast.And,
    ast.Or,
    ast.UnaryOp,
    ast.Not,
    ast.Compare,
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
    ast.Name,
    ast.Attribute,
    ast.Load,
    ast.Constant,
)


def parse_condition(expression: str) -> ast.Expression:
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as error:
        raise ValueError(f"Invalid condition: {error.msg}") from error
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED):
            raise ValueError(f"Forbidden condition syntax: {ast.dump(node)}")  # noqa: TRY004
        if isinstance(node, ast.Name) and node.id not in {"steps", "inputs", "failure"}:
            raise ValueError(f"Unknown condition root: {node.id}")
        if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            raise ValueError("Private attributes are forbidden")
        if isinstance(node, ast.Constant) and not isinstance(
            node.value, (str, int, float, bool, type(None))
        ):
            raise ValueError("Forbidden condition literal")  # noqa: TRY004
    return tree


def evaluate_condition(expression: str, context: dict[str, Any]) -> bool:
    def visit(node: ast.AST) -> Any:
        match node:
            case ast.Expression(body=body):
                return visit(body)
            case ast.Constant(value=value):
                return value
            case ast.Name(id=name):
                return context[name]
            case ast.Attribute(value=value, attr=attr):
                return visit(value)[attr]
            case ast.UnaryOp(op=ast.Not(), operand=operand):
                return not visit(operand)
            case ast.BoolOp(op=ast.And(), values=values):
                return all(visit(value) for value in values)
            case ast.BoolOp(op=ast.Or(), values=values):
                return any(visit(value) for value in values)
            case ast.Compare(left=left, ops=ops, comparators=rights):
                current = visit(left)
                for op, right in zip(ops, rights):
                    other = visit(right)
                    match op:
                        case ast.Eq():
                            result = current == other
                        case ast.NotEq():
                            result = current != other
                        case ast.Lt():
                            result = current < other
                        case ast.LtE():
                            result = current <= other
                        case ast.Gt():
                            result = current > other
                        case ast.GtE():
                            result = current >= other
                    if not result:
                        return False
                    current = other
                return True
        raise ValueError("Unsupported condition")

    return bool(visit(parse_condition(expression)))
