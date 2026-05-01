"""2D vector primitive."""


class Vec:
    def __init__(self, x: float, y: float) -> None:
        self.x = x
        self.y = y

    def add(self, other: "Vec") -> "Vec":
        return Vec(self.x + other.x, self.y + other.y)

    def scale(self, k: float) -> "Vec":
        return Vec(self.x * k, self.y * k)
