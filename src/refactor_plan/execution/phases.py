

from pathlib import Path

def _path_to_module(
    path: Path,
    repo_root: Path,
    src_root: Path | None = None,
) -> str | None:
    try:
        if src_root is not None:
            try:
                parts = list(path.relative_to(src_root).parts)
            except ValueError:
                parts = list(path.relative_to(repo_root).parts)
        else:
            rel = path.relative_to(repo_root)
            parts = list(rel.parts)
            if parts and parts[0] == "src":
                parts = parts[1:]
    except ValueError:
        return None

    if not parts:
        return None
    if parts[-1].endswith(".py"):
        parts[-1] = parts[-1][:-3]
        if parts[-1] == "__init__":
            parts = parts[:-1]
    return ".".join(parts) if parts else None
