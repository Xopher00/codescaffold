"""EXACT pair member B — byte-identical body to exact_a.parse_csv_row."""


def parse_csv_row(line: str) -> list[str]:
    parts = line.strip().split(",")
    return [p.strip() for p in parts if p.strip()]
