import networkx as nx
from backend.models import ParsedDocument


def build_graph(documents: list[ParsedDocument]) -> nx.DiGraph:
    """
    Build a directed traceability graph from parsed documents.

    Nodes:
        - Document nodes (doc_id)
        - Requirement nodes (doc_id/req_id)

    Edges:
        - Document → Document (from links_to in frontmatter)
        - Requirement → Requirement (from traces_to in requirement body)
        - Document → Requirement (ownership)
    """
    G = nx.DiGraph()

    # Add all document nodes
    for doc in documents:
        G.add_node(
            doc.doc_id,
            node_type="document",
            doc_type=doc.doc_type.value,
            title=doc.title,
            version=doc.version,
        )

        # Add requirement nodes under this document
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
            # Document owns its requirements
            G.add_edge(doc.doc_id, node_id, edge_type="contains")

    # Add document-to-document edges (from frontmatter links_to)
    doc_ids = {doc.doc_id for doc in documents}
    for doc in documents:
        for linked_id in doc.links_to:
            G.add_edge(
                doc.doc_id,
                linked_id,
                edge_type="links_to",
                exists=linked_id in doc_ids,
            )

    # Add requirement-to-requirement edges (from traces_to in requirements)
    for doc in documents:
        for req in doc.requirements:
            source_node = f"{doc.doc_id}/{req.req_id}"
            for trace in req.traces_to:
                # traces_to format: "DOC-001/R-01" or just "DOC-001"
                G.add_edge(source_node, trace, edge_type="traces_to")

    return G


def get_document_node_ids(G: nx.DiGraph) -> list[str]:
    return [n for n, d in G.nodes(data=True) if d.get("node_type") == "document"]


def get_requirement_node_ids(G: nx.DiGraph) -> list[str]:
    return [n for n, d in G.nodes(data=True) if d.get("node_type") == "requirement"]


def get_downstream_documents(G: nx.DiGraph, doc_id: str) -> list[str]:
    """Get all documents reachable downstream from a given document."""
    try:
        descendants = nx.descendants(G, doc_id)
        return [
            n for n in descendants
            if G.nodes[n].get("node_type") == "document"
        ]
    except nx.NetworkXError:
        return []
