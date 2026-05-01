"""Line-oriented parser using Reader."""
from .reader import Reader


def parse_lines(path: str) -> list[str]:
    return Reader(path).read().splitlines()
