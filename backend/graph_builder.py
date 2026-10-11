import networkx as nx
from backend.models import ParsedDocument, DocType

# Position of each document type in the PDM chain (upstream → downstream).
# Every links_to / traces_to edge is stored pointing from the lower rank to the
# higher rank, whichever side declared it. This keeps the graph acyclic, so
# descendants = "affected by a change" and ancestors = "what this depends on".
#
# Simplification: after PRD the real process forks into a design branch
# (SDD → ADD) and a delivery branch (EPIC → USER_STORY). A single number line
# treats them as one chain, which is enough for edge direction.
RANK = {
    DocType.LVC: 0,
    DocType.TIA: 1,
    DocType.BRD: 2,
    DocType.CDD: 3,
    DocType.PRD: 4,
    DocType.SDD: 5,
    DocType.ADD: 6,
    DocType.EPIC: 7,
    DocType.USER_STORY: 8,
}


def build_graph(documents: list[ParsedDocument]) -> nx.DiGraph:
    """
    Build a directed traceability graph from parsed documents.

    Nodes:
        - Document nodes (doc_id)
        - Requirement nodes (doc_id/req_id)
        - "Ghost" nodes for references to things that were not uploaded
          (no attributes; the validator reports them as broken references)

    Edges (all point upstream → downstream):
        - contains:  Document → its Requirements
        - links_to:  Document ↔ Document (from frontmatter links_to)
        - traces_to: Requirement ↔ Requirement (from **Traces To:**)

    Each links_to / traces_to edge records `declared_by`: the document(s)
    that declared it. Two documents declaring the same link become one edge
    with two declarers.
    """
    G = nx.DiGraph()
    rank_of_doc = {doc.doc_id: RANK[doc.doc_type] for doc in documents}

    def rank_of(node_id: str) -> int | None:
        # "BRD-001/R-01" → rank of BRD-001; unknown (ghost) nodes → None
        return rank_of_doc.get(node_id.split("/")[0])

    def add_oriented(a: str, b: str, edge_type: str, declared_by: str, **attrs):
        ra, rb = rank_of(a), rank_of(b)
        # Flip to point downstream. Ghost nodes and same-rank pairs have no
        # order, so the author's direction is kept.
        if ra is not None and rb is not None and ra > rb:
            a, b = b, a
        if G.has_edge(a, b):
            declarers = G.edges[a, b]["declared_by"]
            if declared_by not in declarers:
                declarers.append(declared_by)
        else:
            G.add_edge(a, b, edge_type=edge_type, declared_by=[declared_by], **attrs)

    # Pass 1: document + requirement nodes, ownership edges
    for doc in documents:
        G.add_node(
            doc.doc_id,
            node_type="document",
            doc_type=doc.doc_type.value,
            title=doc.title,
            version=doc.version,
        )
        for req in doc.requirements:
            node_id = f"{doc.doc_id}/{req.req_id}"
            G.add_node(
                node_id,
                node_type="requirement",
                req_id=req.req_id,
                title=req.title,
                doc_id=doc.doc_id,
                doc_type=doc.doc_type.value,
                has_ac=len(req.acceptance_criteria) > 0,
            )
            G.add_edge(doc.doc_id, node_id, edge_type="contains")

    # Pass 2: document-to-document edges (frontmatter links_to)
    for doc in documents:
        for linked_id in doc.links_to:
            add_oriented(
                doc.doc_id, linked_id, "links_to", doc.doc_id,
                exists=linked_id in rank_of_doc,
            )

    # Pass 3: requirement-to-requirement edges ("DOC-001/R-01" or "DOC-001")
    for doc in documents:
        for req in doc.requirements:
            for trace in req.traces_to:
                add_oriented(f"{doc.doc_id}/{req.req_id}", trace, "traces_to", doc.doc_id)

    return G


def get_impacted_documents(
    G: nx.DiGraph, doc_id: str
) -> dict:
    """
    Given a document ID, find what a change to that document affects.

    Returns a dict with:
        - upstream_docs:    documents doc_id depends on (ancestors)
        - downstream_docs:  documents that depend on doc_id (descendants);
                            these may be stale after a change
        - affected_requirements: requirement nodes downstream of doc_id
        - impact_chain:     list of {source, target, edge_type} dicts
                            forming the downstream propagation paths
        - total_impacted:   number of downstream documents
    """
    if doc_id not in G:
        return {
            "upstream_docs": [],
            "downstream_docs": [],
            "affected_requirements": [],
            "impact_chain": [],
            "total_impacted": 0,
        }

    descendants = nx.descendants(G, doc_id)
    ancestors = nx.ancestors(G, doc_id)

    def docs_in(nodes):
        return sorted(n for n in nodes if G.nodes[n].get("node_type") == "document")

    downstream_docs = docs_in(descendants)
    affected_requirements = sorted(
        n for n in descendants
        if G.nodes[n].get("node_type") == "requirement"
        and G.nodes[n].get("doc_id") != doc_id
    )

    reachable = descendants | {doc_id}
    impact_chain = [
        {"source": u, "target": v, "edge_type": data.get("edge_type", "unknown")}
        for u, v, data in G.edges(data=True)
        if u in reachable and v in reachable
    ]

    return {
        "upstream_docs": docs_in(ancestors),
        "downstream_docs": downstream_docs,
        "affected_requirements": affected_requirements,
        "impact_chain": impact_chain,
        "total_impacted": len(downstream_docs),
    }
