"""2x2 matrix using Vec."""
from .vec import Vec


class Mat:
    def __init__(self, a: Vec, b: Vec) -> None:
        self.a = a
        self.b = b

    def apply(self, v: Vec) -> Vec:
        return Vec(self.a.x * v.x + self.b.x * v.y,
                   self.a.y * v.x + self.b.y * v.y)
