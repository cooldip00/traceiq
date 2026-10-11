import networkx as nx

from backend.graph_builder import build_graph, get_impacted_documents, RANK
from backend.models import DocType
from backend.parser import parse_documents
from tests.conftest import doc, req


def _rank(G, node):
    data = G.nodes[node]
    return RANK[DocType(data["doc_type"])] if data.get("doc_type") else None


def test_sample_graph_is_acyclic(sample_graph):
    assert nx.is_directed_acyclic_graph(sample_graph)


def test_every_link_points_downstream(sample_graph):
    for u, v, data in sample_graph.edges(data=True):
        if data["edge_type"] in ("links_to", "traces_to"):
            assert _rank(sample_graph, u) <= _rank(sample_graph, v), (u, v)


def test_trace_declared_by_both_sides_becomes_one_edge(sample_graph):
    edge = sample_graph.edges["BRD-001/R-01", "PRD-001/F-01"]
    assert sorted(edge["declared_by"]) == ["BRD-001", "PRD-001"]
    two_sided = [e for e in sample_graph.edges(data=True) if len(e[2].get("declared_by", [])) == 2]
    assert len(two_sided) == 8


def test_brd_change_reaches_jira_items(sample_graph):
    impact = get_impacted_documents(sample_graph, "BRD-001")
    assert impact["downstream_docs"] == [
        "ADD-001", "CDD-001", "EP-001", "EP-002", "PRD-001",
        "SDD-001", "US-001", "US-002", "US-003", "US-004",
    ]
    assert impact["upstream_docs"] == ["LVC-001", "TIA-001"]
    assert impact["total_impacted"] == 10


def test_epic_and_story_impact(sample_graph):
    assert get_impacted_documents(sample_graph, "EP-001")["downstream_docs"] == ["US-001", "US-002"]
    leaf = get_impacted_documents(sample_graph, "US-001")
    assert leaf["downstream_docs"] == [] and leaf["total_impacted"] == 0


def test_unknown_document():
    assert get_impacted_documents(nx.DiGraph(), "NOPE")["total_impacted"] == 0


def test_ghost_node_keeps_author_direction():
    docs, _ = parse_documents([doc("PRD-001", "PRD", req("F-01", traces=["BRD-009/R-01"]))])
    G = build_graph(docs)
    assert G.has_edge("PRD-001/F-01", "BRD-009/R-01")
    assert G.nodes["BRD-009/R-01"] == {}  # ghost: no attributes
