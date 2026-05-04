from __future__ import annotations

import networkx as nx

from refactor_plan.naming.namer import (
    _build_cluster_context,
    _format_cluster_block,
    _strip_json_fence,
)


# ---------------------------------------------------------------------------
# helpers to build minimal graphs
# ---------------------------------------------------------------------------

def _make_graph(nodes: list[dict], edges: list[tuple[str, str, dict]]) -> nx.Graph:
    G = nx.Graph()
    for n in nodes:
        G.add_node(n["id"], **{k: v for k, v in n.items() if k != "id"})
    for src, dst, attrs in edges:
        G.add_edge(src, dst, **attrs)
    return G


def _node(nid: str, label: str, source_file: str) -> dict:
    return {"id": nid, "label": label, "source_file": source_file, "file_type": "code"}


# ---------------------------------------------------------------------------
# _strip_json_fence
# ---------------------------------------------------------------------------

def test_strip_json_fence_bare() -> None:
    assert _strip_json_fence('{"a": "b"}') == '{"a": "b"}'


def test_strip_json_fence_with_json_tag() -> None:
    raw = '```json\n{"a": "b"}\n```'
    assert _strip_json_fence(raw) == '{"a": "b"}'


def test_strip_json_fence_without_tag() -> None:
    raw = '```\n{"a": "b"}\n```'
    assert _strip_json_fence(raw) == '{"a": "b"}'


# ---------------------------------------------------------------------------
# _build_cluster_context — symbol extraction
# ---------------------------------------------------------------------------

def test_extracts_classes_and_functions() -> None:
    sf = "/repo/pkg/auth.py"
    G = _make_graph(
        nodes=[
            _node("file_auth", "auth.py", sf),
            _node("login_service", "LoginService", sf),
            _node("hash_pw", "hash_password()", sf),
            _node("method_auth", ".authenticate()", sf),
        ],
        edges=[
            ("file_auth", "login_service", {"relation": "contains", "weight": 1.0}),
            ("file_auth", "hash_pw", {"relation": "contains", "weight": 1.0}),
            ("login_service", "method_auth", {"relation": "method", "weight": 1.0}),
        ],
    )
    ctx = _build_cluster_context(1, [sf], G, {1: [sf]})
    assert "LoginService" in ctx["classes"]
    assert "hash_password" in ctx["functions"]
    assert ".authenticate" not in str(ctx)  # methods suppressed


def test_file_nodes_excluded() -> None:
    sf = "/repo/pkg/auth.py"
    G = _make_graph(
        nodes=[_node("file_auth", "auth.py", sf)],
        edges=[],
    )
    ctx = _build_cluster_context(1, [sf], G, {1: [sf]})
    assert ctx["classes"] == []
    assert ctx["functions"] == []


# ---------------------------------------------------------------------------
# _build_cluster_context — cross-cluster edges
# ---------------------------------------------------------------------------

def test_cross_cluster_uses_edge() -> None:
    sf_a = "/repo/pkg/auth.py"
    sf_b = "/repo/pkg/events.py"
    G = _make_graph(
        nodes=[
            _node("file_auth", "auth.py", sf_a),
            _node("login_svc", "LoginService", sf_a),
            _node("file_events", "events.py", sf_b),
            _node("emit_fn", "emit()", sf_b),
        ],
        edges=[
            ("file_auth", "login_svc", {"relation": "contains", "weight": 1.0}),
            ("file_events", "emit_fn", {"relation": "contains", "weight": 1.0}),
            ("login_svc", "emit_fn", {"relation": "uses", "weight": 0.8}),
        ],
    )
    all_comms = {1: [sf_a], 2: [sf_b]}
    ctx = _build_cluster_context(1, [sf_a], G, all_comms)
    assert any("emit" in dep for dep in ctx["external_deps"])
    assert any("pkg_002" in dep for dep in ctx["external_deps"])


def test_intra_cluster_edges_not_in_deps() -> None:
    sf = "/repo/pkg/auth.py"
    G = _make_graph(
        nodes=[
            _node("login_svc", "LoginService", sf),
            _node("hash_fn", "hash_password()", sf),
        ],
        edges=[
            ("login_svc", "hash_fn", {"relation": "calls", "weight": 1.0}),
        ],
    )
    ctx = _build_cluster_context(1, [sf], G, {1: [sf]})
    assert ctx["external_deps"] == []


def test_external_calls_edge() -> None:
    sf_a = "/repo/pkg/core.py"
    sf_b = "/repo/pkg/utils.py"
    G = _make_graph(
        nodes=[
            _node("processor", "Processor", sf_a),
            _node("helper_fn", "helper()", sf_b),
        ],
        edges=[
            ("processor", "helper_fn", {"relation": "calls", "weight": 1.0}),
        ],
    )
    all_comms = {1: [sf_a], 2: [sf_b]}
    ctx = _build_cluster_context(1, [sf_a], G, all_comms)
    assert any("helper" in dep for dep in ctx["external_deps"])


# ---------------------------------------------------------------------------
# _format_cluster_block
# ---------------------------------------------------------------------------

def test_format_block_full() -> None:
    ctx = {
        "classes": ["LoginService", "User"],
        "functions": ["hash_password"],
        "external_deps": ["emit() [pkg_002]"],
    }
    block = _format_cluster_block("pkg_001", ["auth.py", "models.py"], ctx)
    assert "pkg_001:" in block
    assert "Files: auth.py, models.py" in block
    assert "Classes: LoginService, User" in block
    assert "Functions: hash_password" in block
    assert "Uses from other clusters: emit() [pkg_002]" in block


def test_format_block_omits_empty_sections() -> None:
    ctx = {"classes": [], "functions": ["emit"], "external_deps": []}
    block = _format_cluster_block("pkg_002", ["events.py"], ctx)
    assert "Classes" not in block
    assert "Uses from other clusters" not in block
    assert "Functions: emit" in block
