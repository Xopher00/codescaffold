"""File writer that round-trips through Reader."""
from .reader import Reader


class Writer:
    def __init__(self, path: str) -> None:
        self.path = path

    def write(self, content: str) -> None:
        from pathlib import Path
        Path(self.path).write_text(content)

    def echo(self) -> str:
        return Reader(self.path).read()
