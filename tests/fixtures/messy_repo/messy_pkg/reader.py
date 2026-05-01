"""File reader."""
from pathlib import Path


class Reader:
    def __init__(self, path: str) -> None:
        self.path = Path(path)

    def read(self) -> str:
        return self.path.read_text()
