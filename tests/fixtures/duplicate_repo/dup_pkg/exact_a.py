"""EXACT pair member A — byte-identical body to exact_b.parse_csv_row."""


def parse_csv_row(line: str) -> list[str]:
    parts = line.strip().split(",")
    return [p.strip() for p in parts if p.strip()]
