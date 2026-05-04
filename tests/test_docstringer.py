from __future__ import annotations

import networkx as nx
from refactor_plan.naming.docstringer import (
    _build_symbol_context,
    _extract_existing_docstring,
    _get_symbol_type,
    _insert_or_replace_docstring,
)


def _make_graph(nodes: list[dict], edges: list[tuple[str, str, dict]]) -> nx.Graph:
    G = nx.Graph()
    for n in nodes:
        G.add_node(n["id"], **{k: v for k, v in n.items() if k != "id"})
    for src, dst, attrs in edges:
        G.add_edge(src, dst, **attrs)
    return G


# ---------------------------------------------------------------------------
# _get_symbol_type
# ---------------------------------------------------------------------------

def test_get_symbol_type_function() -> None:
    assert _get_symbol_type("def foo():\n    pass\n", "foo") == "function"


def test_get_symbol_type_async_function() -> None:
    assert _get_symbol_type("async def fetch():\n    pass\n", "fetch") == "function"


def test_get_symbol_type_class() -> None:
    assert _get_symbol_type("class Foo:\n    pass\n", "Foo") == "class"


def test_get_symbol_type_missing() -> None:
    assert _get_symbol_type("def foo():\n    pass\n", "bar") is None


# ---------------------------------------------------------------------------
# _extract_existing_docstring
# ---------------------------------------------------------------------------

def test_extract_existing_triple_double() -> None:
    src = 'def foo():\n    """Does a thing."""\n    pass\n'
    assert _extract_existing_docstring(src, "foo") == "Does a thing."


def test_extract_existing_triple_single() -> None:
    src = "def foo():\n    '''Does a thing.'''\n    pass\n"
    assert _extract_existing_docstring(src, "foo") == "Does a thing."


def test_extract_existing_class() -> None:
    src = 'class MyClass:\n    """Represents something."""\n    pass\n'
    assert _extract_existing_docstring(src, "MyClass") == "Represents something."


def test_extract_no_docstring() -> None:
    assert _extract_existing_docstring("def foo():\n    return 1\n", "foo") is None


def test_extract_wrong_symbol() -> None:
    src = 'def foo():\n    """Doc."""\n    pass\n'
    assert _extract_existing_docstring(src, "bar") is None


# ---------------------------------------------------------------------------
# _insert_or_replace_docstring
# ---------------------------------------------------------------------------

def test_insert_into_empty_body() -> None:
    src = "def helper():\n    return 42\n"
    result = _insert_or_replace_docstring(src, "helper", "Return the answer.")
    assert '"""Return the answer."""' in result
    assert "return 42" in result


def test_insert_preserves_indentation() -> None:
    src = "def helper():\n    return 42\n"
    result = _insert_or_replace_docstring(src, "helper", "Return the answer.")
    lines = result.splitlines()
    doc_line = next(l for l in lines if "Return the answer" in l)
    assert doc_line.startswith("    ")


def test_replace_existing_docstring() -> None:
    src = 'def helper():\n    """Old doc."""\n    return 42\n'
    result = _insert_or_replace_docstring(src, "helper", "New doc.")
    assert '"""New doc."""' in result
    assert "Old doc" not in result
    assert "return 42" in result


def test_insert_into_class() -> None:
    src = "class Foo:\n    def bar(self):\n        pass\n"
    result = _insert_or_replace_docstring(src, "Foo", "Represents Foo.")
    assert '"""Represents Foo."""' in result


def test_only_modifies_named_symbol() -> None:
    src = (
        "def foo():\n    return 1\n\n"
        "def bar():\n    return 2\n"
    )
    result = _insert_or_replace_docstring(src, "foo", "Does foo.")
    assert '"""Does foo."""' in result
    # bar unchanged — no docstring inserted there
    bar_start = result.index("def bar")
    assert '"""' not in result[bar_start:]


def test_escapes_triple_quotes_in_text() -> None:
    src = "def helper():\n    return 42\n"
    result = _insert_or_replace_docstring(src, "helper", 'Has """quotes""".')
    # Should use ''' as escape
    assert "Has '''quotes'''." in result


# ---------------------------------------------------------------------------
# _build_symbol_context
# ---------------------------------------------------------------------------

def test_context_extracts_methods() -> None:
    sf = "/repo/auth.py"
    G = _make_graph(
        nodes=[
            {"id": "svc", "label": "LoginService", "source_file": sf},
            {"id": "auth_m", "label": ".authenticate()", "source_file": sf},
        ],
        edges=[
            ("svc", "auth_m", {"relation": "method", "_src": "svc", "_tgt": "auth_m", "weight": 1.0}),
        ],
    )
    ctx = _build_symbol_context("LoginService", sf, G)  # type: ignore[arg-type]
    assert ".authenticate()" in ctx["methods"]


def test_context_calls_direction() -> None:
    sf_a = "/repo/auth.py"
    sf_b = "/repo/events.py"
    G = _make_graph(
        nodes=[
            {"id": "svc", "label": "LoginService", "source_file": sf_a},
            {"id": "emit_fn", "label": "emit()", "source_file": sf_b},
        ],
        edges=[
            ("svc", "emit_fn", {"relation": "calls", "_src": "svc", "_tgt": "emit_fn", "weight": 1.0}),
        ],
    )
    from pathlib import Path
    ctx = _build_symbol_context("LoginService", Path(sf_a), G)
    assert "emit()" in ctx["calls"]
    assert ctx["called_by"] == []


def test_context_called_by_direction() -> None:
    sf_a = "/repo/main.py"
    sf_b = "/repo/auth.py"
    G = _make_graph(
        nodes=[
            {"id": "run_fn", "label": "run()", "source_file": sf_a},
            {"id": "auth_fn", "label": "authenticate()", "source_file": sf_b},
        ],
        edges=[
            ("run_fn", "auth_fn", {"relation": "calls", "_src": "run_fn", "_tgt": "auth_fn", "weight": 1.0}),
        ],
    )
    from pathlib import Path
    ctx = _build_symbol_context("authenticate", Path(sf_b), G)
    assert "run()" in ctx["called_by"]
    assert ctx["calls"] == []


def test_context_empty_when_no_node() -> None:
    G = nx.Graph()
    from pathlib import Path
    ctx = _build_symbol_context("Nonexistent", Path("/repo/mod.py"), G)
    assert all(v == [] for v in ctx.values())
