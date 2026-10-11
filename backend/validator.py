import re
import networkx as nx
from backend.models import (
    ParsedDocument, Requirement, Issue, IssueType, IssueSeverity, DocType, make_issue,
)

# Document types that MUST have a direct downstream document link
REQUIRED_LINKS = {
    DocType.LVC: [DocType.BRD, DocType.TIA],
    DocType.BRD: [DocType.CDD, DocType.PRD],
    DocType.PRD: [DocType.SDD, DocType.EPIC],
    DocType.SDD: [DocType.ADD],
    DocType.EPIC: [DocType.USER_STORY],
}

# Requirements of type X that MUST be traced by at least one requirement of
# each downstream type: (downstream type, issue type, severity, consequence)
REQUIRED_TRACE_COVERAGE = {
    DocType.BRD: [
        (DocType.CDD, IssueType.COVERAGE_GAP, IssueSeverity.MEDIUM,
         "This business requirement has no concept design."),
        (DocType.PRD, IssueType.COVERAGE_GAP, IssueSeverity.HIGH,
         "This business requirement is not specified as a product feature."),
    ],
    DocType.PRD: [
        (DocType.EPIC, IssueType.ORPHANED_REQUIREMENT, IssueSeverity.HIGH,
         "This feature will not be tracked or delivered."),
    ],
}

# "BRD v1.0", "BRD-001 v1.2", "SDD-v2.0"
VERSION_REF = re.compile(r"\b([A-Z][A-Z_]*(?:-\d+)?)[ -]v(\d+(?:\.\d+)+)\b")
DOC_TYPE_NAMES = {t.value for t in DocType}


def _version_key(v: str) -> tuple:
    return tuple(int(p) if p.isdigit() else p for p in re.split(r"[.\-]", v.strip()))


def _downstream_doc_types(G: nx.DiGraph, node: str, edge_type: str) -> set[str]:
    """Doc types of the direct downstream neighbours of `node` via `edge_type`."""
    return {
        G.nodes[v].get("doc_type")
        for v in G.successors(node)
        if G.edges[node, v].get("edge_type") == edge_type
    }


def validate_structural(
    documents: list[ParsedDocument],
    G: nx.DiGraph,
) -> list[Issue]:
    """
    Run purely deterministic structural checks. No AI involved.
    Expects G from build_graph (edges point upstream → downstream).
    Returns a list of Issue objects.
    """
    issues: list[Issue] = []
    doc_map = {doc.doc_id: doc for doc in documents}
    uploaded_types = {doc.doc_type for doc in documents}
    docs_by_type: dict[DocType, list[ParsedDocument]] = {}
    for doc in documents:
        docs_by_type.setdefault(doc.doc_type, []).append(doc)

    for doc in documents:
        # ── 1. Broken references in links_to ────────────────────────────────
        for linked_id in doc.links_to:
            if linked_id not in doc_map:
                issues.append(make_issue(
                    IssueType.BROKEN_REFERENCE,
                    IssueSeverity.HIGH,
                    doc.doc_id,
                    f"Broken reference: {doc.doc_id} → {linked_id}",
                    f"Document '{doc.doc_id}' references '{linked_id}' in its "
                    f"links_to field, but no document with that ID was uploaded.",
                    target_doc=linked_id,
                ))

        # ── 2. Version references in the text ("written against BRD v1.0") ─
        reported = set()
        for name, ref_version in VERSION_REF.findall(doc.raw_content):
            if name in doc_map:
                target = doc_map[name]
            elif name in DOC_TYPE_NAMES and len(docs_by_type.get(DocType(name), [])) == 1:
                target = docs_by_type[DocType(name)][0]
            else:
                continue  # unknown name, or ambiguous type with several documents
            if target.doc_id == doc.doc_id or (target.doc_id, ref_version) in reported:
                continue
            reported.add((target.doc_id, ref_version))
            if _version_key(ref_version) != _version_key(target.version):
                issues.append(make_issue(
                    IssueType.VERSION_MISMATCH,
                    IssueSeverity.MEDIUM,
                    doc.doc_id,
                    f"Version mismatch: {doc.doc_id} references "
                    f"{target.doc_id} v{ref_version} but current is v{target.version}",
                    f"'{doc.doc_id}' was written against {target.doc_id} "
                    f"v{ref_version}. {target.doc_id} is now v{target.version}. "
                    f"Review '{doc.doc_id}' for outdated assumptions.",
                    target_doc=target.doc_id,
                ))

        # ── 3. User Stories have Acceptance Criteria ────────────────────────
        if doc.doc_type == DocType.USER_STORY:
            for req in doc.requirements:
                if not req.acceptance_criteria:
                    issues.append(make_issue(
                        IssueType.MISSING_ACCEPTANCE_CRITERIA,
                        IssueSeverity.MEDIUM,
                        doc.doc_id,
                        f"No acceptance criteria: {doc.doc_id}/{req.req_id}",
                        f"User Story '{req.req_id}' in '{doc.doc_id}' has no "
                        f"acceptance criteria defined. This requirement cannot "
                        f"be tested or verified.",
                        source_req=req.req_id,
                    ))

        # ── 4. Missing downstream documents (direct links_to edges) ─────────
        for expected in REQUIRED_LINKS.get(doc.doc_type, []):
            if expected.value not in _downstream_doc_types(G, doc.doc_id, "links_to"):
                issues.append(make_issue(
                    IssueType.COVERAGE_GAP,
                    IssueSeverity.HIGH if doc.doc_type == DocType.EPIC else IssueSeverity.MEDIUM,
                    doc.doc_id,
                    f"Missing downstream: {doc.doc_id} has no {expected.value}",
                    f"Document '{doc.doc_id}' ({doc.doc_type.value}) is expected "
                    f"to link to a downstream {expected.value} document, but "
                    f"none is linked.",
                ))

        # ── 5. Requirement-level coverage (every BRD req → CDD + PRD, ...) ──
        for expected, issue_type, severity, consequence in REQUIRED_TRACE_COVERAGE.get(doc.doc_type, []):
            if expected not in uploaded_types:
                continue  # whole document missing; check 4 already reports it
            for req in doc.requirements:
                node = f"{doc.doc_id}/{req.req_id}"
                if expected.value not in _downstream_doc_types(G, node, "traces_to"):
                    issues.append(make_issue(
                        issue_type,
                        severity,
                        doc.doc_id,
                        f"{doc.doc_type.value} requirement with no {expected.value}: {node}",
                        f"No {expected.value} requirement traces to '{req.req_id}' "
                        f"({req.title}). {consequence}",
                        source_req=req.req_id,
                    ))

    # ── 6. Broken traces_to (target was never uploaded → ghost node) ─────────
    for doc in documents:
        for req in doc.requirements:
            source_node = f"{doc.doc_id}/{req.req_id}"
            for trace in req.traces_to:
                if G.nodes[trace].get("node_type") is None:
                    issues.append(make_issue(
                        IssueType.BROKEN_REFERENCE,
                        IssueSeverity.HIGH,
                        doc.doc_id,
                        f"Broken trace: {source_node} → {trace}",
                        f"Requirement '{req.req_id}' in '{doc.doc_id}' traces to "
                        f"'{trace}', but that node does not exist in the "
                        f"traceability graph.",
                        source_req=req.req_id,
                        target_doc=trace.split("/")[0],
                    ))

    # ── 7. Cycles (only possible between same-rank or unknown documents) ─────
    try:
        cycle = nx.find_cycle(G)
    except nx.NetworkXNoCycle:
        cycle = None
    if cycle:
        path = " → ".join([u for u, _ in cycle] + [cycle[0][0]])
        issues.append(make_issue(
            IssueType.CIRCULAR_TRACE,
            IssueSeverity.LOW,
            cycle[0][0].split("/")[0],
            "Circular traceability chain",
            f"These items trace to each other in a loop: {path}. Impact "
            f"analysis treats every item in the loop as affecting the others.",
        ))

    return issues


def get_semantic_pairs(
    documents: list[ParsedDocument],
    G: nx.DiGraph,
) -> list[tuple[str, Requirement, str, Requirement]]:
    """
    Return (upstream_doc_id, upstream_req, downstream_doc_id, downstream_req)
    for every traces_to edge between two existing requirements.
    Reading the edges of the graph means a trace declared by both sides is
    checked once, not twice.
    """
    req_map = {
        f"{doc.doc_id}/{req.req_id}": (doc.doc_id, req)
        for doc in documents
        for req in doc.requirements
    }
    pairs = []
    for u, v, data in sorted(G.edges(data=True), key=lambda e: (e[0], e[1])):
        if data.get("edge_type") == "traces_to" and u in req_map and v in req_map:
            pairs.append((*req_map[u], *req_map[v]))
    return pairs
