import ast
import math
import operator


MAX_EXPONENT = 1000
MAX_INTEGER_BITS = 4096
MAX_AST_NODES = 100
MAX_DEPTH = 30

_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
}

_UNARY_OPS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}

_FUNCTIONS = {
    "sqrt": math.sqrt,
    "log": math.log,
    "log2": math.log2,
    "log10": math.log10,
    "ceil": math.ceil,
    "floor": math.floor,
    "abs": abs,
}


def _check_limits(tree):
    nodes = list(ast.walk(tree))

    if len(nodes) > MAX_AST_NODES:
        raise ValueError("Expression too complex")

    def depth(node, current=1):
        if current > MAX_DEPTH:
            raise ValueError("Expression nesting too deep")
        for child in ast.iter_child_nodes(node):
            depth(child, current + 1)

    depth(tree)


def calculate(expression):
    """Evaluate a restricted arithmetic expression without eval()."""
    if not isinstance(expression, str):
        raise TypeError("Expression must be a string")

    if len(expression) > 1000:
        raise ValueError("Expression too long")

    tree = ast.parse(expression, mode="eval")
    _check_limits(tree)

    def visit(node):
        if isinstance(node, ast.Expression):
            return visit(node.body)

        if isinstance(node, ast.Constant):
            if isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
                if isinstance(node.value, int) and node.value.bit_length() > MAX_INTEGER_BITS:
                    raise ValueError("Integer input too large")
                if isinstance(node.value, float) and not math.isfinite(node.value):
                    raise ValueError("Non-finite input")
                return node.value
            raise ValueError("Unsupported constant")

        if isinstance(node, ast.BinOp):
            op = _BIN_OPS.get(type(node.op))
            if op is None:
                raise ValueError("Unsupported operator")

            left = visit(node.left)
            right = visit(node.right)

            if isinstance(node.op, ast.Pow):
                if abs(right) > MAX_EXPONENT:
                    raise ValueError("Exponent too large")

                if isinstance(left, int) and isinstance(right, int) and right >= 0:
                    if left not in (-1, 0, 1) and left.bit_length() * right > MAX_INTEGER_BITS:
                        raise ValueError("Integer result too large")

            result = op(left, right)

            if isinstance(result, complex):
                raise ValueError("Complex numbers are not supported")

            if isinstance(result, int) and result.bit_length() > MAX_INTEGER_BITS:
                raise ValueError("Integer result too large")

            if isinstance(result, float) and not math.isfinite(result):
                raise ValueError("Non-finite result")

            return result

        if isinstance(node, ast.UnaryOp):
            op = _UNARY_OPS.get(type(node.op))
            if op is None:
                raise ValueError("Unsupported unary operator")
            return op(visit(node.operand))

        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name):
                raise ValueError("Unsupported function call")

            func = _FUNCTIONS.get(node.func.id)
            if func is None:
                raise ValueError("Unsupported function")

            if node.keywords:
                raise ValueError("Keyword arguments not supported")

            args = [visit(arg) for arg in node.args]
            result = func(*args)

            if isinstance(result, float) and not math.isfinite(result):
                raise ValueError("Non-finite result")

            return result

        raise ValueError(f"Unsupported expression: {type(node).__name__}")

    return visit(tree)


def check_equal(expression, expected, rel_tol=1e-9, abs_tol=1e-12):
    """Compare a locally calculated value with an expected result."""
    actual = calculate(expression)
    matched = math.isclose(
        float(actual),
        float(expected),
        rel_tol=rel_tol,
        abs_tol=abs_tol,
    )
    return {
        "expression": expression,
        "actual": actual,
        "expected": expected,
        "match": matched,
    }


def verify_result(expression, expected):
    """Return verified, mismatch, or unsupported without hiding errors."""
    try:
        result = check_equal(expression, expected)
        return {
            "status": "verified" if result["match"] else "mismatch",
            **result,
        }
    except (ValueError, TypeError, ArithmeticError, SyntaxError, OverflowError) as exc:
        return {
            "status": "unsupported",
            "expression": expression,
            "expected": expected,
            "reason": str(exc),
        }


def compare_expressions(expression_a, expression_b, rel_tol=1e-9, abs_tol=1e-12):
    """Calculate two expressions locally and compare their results."""
    try:
        a = calculate(expression_a)
        b = calculate(expression_b)
        matched = math.isclose(
            float(a),
            float(b),
            rel_tol=rel_tol,
            abs_tol=abs_tol,
        )
        return {
            "status": "verified" if matched else "mismatch",
            "expression_a": expression_a,
            "result_a": a,
            "expression_b": expression_b,
            "result_b": b,
            "match": matched,
        }
    except (ValueError, TypeError, ArithmeticError, SyntaxError, OverflowError) as exc:
        return {
            "status": "unsupported",
            "expression_a": expression_a,
            "expression_b": expression_b,
            "reason": str(exc),
        }
