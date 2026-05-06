## This is placeholder code, taken from the plan tender-imgining-bird.md. It is not finished and may need modification. Do not take as gospel

# @dataclass(frozen=True)
# class RopeResolution:
#    status: Literal["resolved", "ambiguous", "not_found", "not_top_level", "not_python", "error"]
#    symbol_kind: Literal["class", "function", "variable"] | None = None
#    line: int | None = None
#    candidates: tuple[str, ...] = ()   # near-misses for "not_found"
#    reason: str | None = None
