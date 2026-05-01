"""Distractor — named similarly but does something different. Should NOT be flagged as a duplicate of any pair above."""


def parse_csv_header(line: str) -> dict[str, int]:
    """Parse a header row into a name → column-index dict."""
    cols = line.strip().split(",")
    return {name.strip(): idx for idx, name in enumerate(cols)}
