"""Application entry point bridging math and io."""
from .geom import distance
from .vec import Vec
from .reader import Reader


def run(path: str) -> float:
    raw = Reader(path).read().strip().split(",")
    a = Vec(float(raw[0]), float(raw[1]))
    b = Vec(float(raw[2]), float(raw[3]))
    return distance(a, b)
