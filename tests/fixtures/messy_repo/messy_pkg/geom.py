"""Geometry helpers built on Vec and Mat."""
from .vec import Vec
from .mat import Mat


def distance(a: Vec, b: Vec) -> float:
    diff = a.add(Vec(-b.x, -b.y))
    return (diff.x ** 2 + diff.y ** 2) ** 0.5


def transform(m: Mat, v: Vec) -> Vec:
    return m.apply(v)
