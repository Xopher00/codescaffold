"""God module mixing math and io concerns. Should be split."""
from .vec import Vec
from .reader import Reader


def vec_from_pair(a: float, b: float) -> Vec:
    """Math helper: belongs in math cluster."""
    return Vec(a, b).scale(1.0)


def read_first_line(path: str) -> str:
    """IO helper: belongs in io cluster."""
    return Reader(path).read().splitlines()[0]
