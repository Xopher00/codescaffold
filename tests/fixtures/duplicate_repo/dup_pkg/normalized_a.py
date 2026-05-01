"""NORMALIZED pair member A — same logic as normalized_b, different locals + comments."""


def factorial(n: int) -> int:
    """Compute n! iteratively."""
    result = 1
    for i in range(1, n + 1):
        result *= i
    return result
