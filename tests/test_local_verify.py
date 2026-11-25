import pytest

from freenerf.agents.local_verify import calculate, check_equal, verify_result, compare_expressions


@pytest.mark.parametrize(
    "expression,expected",
    [
        ("6*4*3", 72),
        ("2**282", 2**282),
        ("sqrt(144)", 12),
        ("log(8, 2)", 3),
        ("ceil(log(1000, 10))", 3),
        ("-5 + 2*3", 1),
    ],
)
def test_calculate_valid_expressions(expression, expected):
    assert calculate(expression) == expected


def test_check_equal_match():
    result = check_equal("6*4*3", 72)
    assert result["match"] is True


def test_check_equal_mismatch():
    result = check_equal("6*4*3", 73)
    assert result["match"] is False


@pytest.mark.parametrize(
    "expression",
    [
        "__import__('os').system('id')",
        "open('/etc/passwd')",
        "2**10001",
        "1/0",
        "sqrt(-1)",
    ],
)
def test_reject_unsafe_or_invalid_expressions(expression):
    with pytest.raises(Exception):
        calculate(expression)


def test_verify_result_verified():
    result = verify_result("6*4*3", 72)
    assert result["status"] == "verified"
    assert result["actual"] == 72


def test_verify_result_mismatch():
    result = verify_result("6*4*3", 73)
    assert result["status"] == "mismatch"
    assert result["actual"] == 72


def test_verify_result_unsupported():
    result = verify_result("2**100000000", 1)
    assert result["status"] == "unsupported"


def test_compare_expressions_verified():
    result = compare_expressions("6*4*3", "72")
    assert result["status"] == "verified"
    assert result["match"] is True


def test_compare_expressions_mismatch():
    result = compare_expressions("2**3", "3**2")
    assert result["status"] == "mismatch"
    assert result["result_a"] == 8
    assert result["result_b"] == 9


def test_compare_expressions_unsupported():
    result = compare_expressions("2**100000000", "5")
    assert result["status"] == "unsupported"


def test_expression_complexity_limit():
    expression = "+".join(["1"] * 150)
    with pytest.raises(ValueError, match="Expression too complex"):
        calculate(expression)


def test_expression_depth_limit():
    expression = "sqrt(" * 40 + "1" + ")" * 40
    with pytest.raises(ValueError, match="(nesting too deep|Expression too complex)"):
        calculate(expression)
