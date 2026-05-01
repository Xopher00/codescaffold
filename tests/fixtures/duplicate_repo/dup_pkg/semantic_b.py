"""SEMANTIC pair member B — recursive Fibonacci. Same purpose, different structure."""


def fib(n: int) -> int:
    if n < 2:
        return n
    return fib(n - 1) + fib(n - 2)
