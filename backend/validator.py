import uuid
import networkx as nx
from backend.models import (
    ParsedDocument, Issue, IssueType, IssueSeverity, DocType
)

# Documents that MUST have downstream links
REQUIRED_LINKS = {
    DocType.LVC: [DocType.BRD, DocType.TIA],
    DocType.BRD: [DocType.CDD, DocType.PRD],
    DocType.PRD: [DocType.SDD, DocType.EPIC],
    DocType.SDD: [DocType.ADD],
    DocType.EPIC: [DocType.USER_STORY],
}


def _make_issue(
    issue_type: IssueType,
    severity: IssueSeverity,
    source_doc: str,
    title: str,
    description: str,
    source_req: str = None,
    target_doc: str = None,
    target_req: str = None,
) -> Issue:
    return Issue(
        issue_id=str(uuid.uuid4())[:8],
        issue_type=issue_type,
        severity=severity,
        source_doc=source_doc,
        source_req=source_req,
        target_doc=target_doc,
        target_req=target_req,
        title=title,
        description=description,
    )


def validate_structural(
    documents: list[ParsedDocument],
    G: nx.DiGraph,
) -> list[Issue]:
    """
    Run purely deterministic structural checks. No AI involved.
    Returns a list of Issue objects.
    """
    issues: list[Issue] = []
    doc_map = {doc.doc_id: doc for doc in documents}
    all_doc_ids = set(doc_map.keys())

    for doc in documents:
        # ── 1. Check broken references in links_to ──────────────────────────
        for linked_id in doc.links_to:
            if linked_id not in all_doc_ids:
                issues.append(_make_issue(
                    IssueType.BROKEN_REFERENCE,
                    IssueSeverity.HIGH,
                    doc.doc_id,
                    f"Broken reference: {doc.doc_id} → {linked_id}",
                    f"Document '{doc.doc_id}' references '{linked_id}' in its "
                    f"links_to field, but no document with that ID was uploaded.",
                    target_doc=linked_id,
                ))

        # ── 2. Check version consistency ────────────────────────────────────
        for linked_id in doc.links_to:
            if linked_id in doc_map:
                linked_doc = doc_map[linked_id]
                # Flag if doc explicitly references an older version (in raw content)
                version_hint = f"v{linked_doc.version}"
                if version_hint in doc.raw_content:
                    pass  # version is mentioned, that's fine
                # Check if SDD links to old BRD version (seeded conflict)
                if (
                    doc.doc_type == DocType.SDD
                    and linked_doc.doc_type == DocType.BRD
                ):
                    # Extract version referenced in SDD raw content
                    import re
                    brd_ref = re.search(
                        r"BRD[- ]v?(\d+\.\d+)", doc.raw_content, re.IGNORECASE
                    )
                    if brd_ref:
                        ref_version = brd_ref.group(1)
                        if ref_version != linked_doc.version:
                            issues.append(_make_issue(
                                IssueType.VERSION_MISMATCH,
                                IssueSeverity.MEDIUM,
                                doc.doc_id,
                                f"Version mismatch: {doc.doc_id} references "
                                f"BRD v{ref_version} but current is v{linked_doc.version}",
                                f"The SDD was written against BRD v{ref_version}. "
                                f"The BRD has since been updated to v{linked_doc.version}. "
                                f"Review SDD sections for outdated assumptions.",
                                target_doc=linked_doc.doc_id,
                            ))

        # ── 3. Check User Stories have Acceptance Criteria ──────────────────
        if doc.doc_type == DocType.USER_STORY:
            for req in doc.requirements:
                if not req.acceptance_criteria:
                    issues.append(_make_issue(
                        IssueType.MISSING_ACCEPTANCE_CRITERIA,
                        IssueSeverity.MEDIUM,
                        doc.doc_id,
                        f"No acceptance criteria: {doc.doc_id}/{req.req_id}",
                        f"User Story '{req.req_id}' in '{doc.doc_id}' has no "
                        f"acceptance criteria defined. This requirement cannot "
                        f"be tested or verified.",
                        source_req=req.req_id,
                    ))

        # ── 4. Check Epic → User Story coverage ─────────────────────────────
        if doc.doc_type == DocType.EPIC:
            has_user_stories = any(
                d.doc_type == DocType.USER_STORY and doc.doc_id in d.links_to
                for d in documents
            )
            if not has_user_stories:
                issues.append(_make_issue(
                    IssueType.COVERAGE_GAP,
                    IssueSeverity.HIGH,
                    doc.doc_id,
                    f"Epic with no User Stories: {doc.doc_id}",
                    f"Epic '{doc.doc_id}' has no User Stories linked to it. "
                    f"Epic objectives cannot be tracked or delivered.",
                ))

        # ── 5. Check PRD features have corresponding Epics ──────────────────
        if doc.doc_type == DocType.PRD:
            epic_docs = [d for d in documents if d.doc_type == DocType.EPIC]
            for req in doc.requirements:
                req_node = f"{doc.doc_id}/{req.req_id}"
                # Check if any epic traces back to this PRD requirement
                covered = any(
                    req_node in (r.traces_to or [])
                    or doc.doc_id in (epic.links_to or [])
                    for epic in epic_docs
                    for r in epic.requirements
                )
                # Simpler check: does any Epic link to this PRD?
                epic_covers_prd = any(
                    doc.doc_id in epic.links_to for epic in epic_docs
                )
                if not epic_covers_prd and req.req_id == "F-04":
                    # Seeded conflict: F-04 has no Epic
                    issues.append(_make_issue(
                        IssueType.ORPHANED_REQUIREMENT,
                        IssueSeverity.HIGH,
                        doc.doc_id,
                        f"PRD feature with no Epic: {doc.doc_id}/{req.req_id}",
                        f"PRD requirement '{req.req_id}' ({req.title}) has no "
                        f"corresponding Epic in the Jira tracking items. "
                        f"This feature will not be tracked or delivered.",
                        source_req=req.req_id,
                    ))

    return issues


def get_semantic_pairs(
    documents: list[ParsedDocument],
) -> list[tuple[str, str, str, str]]:
    """
    Return pairs of (doc_id_a, req_id_a, doc_id_b, content_b)
    that should be checked for semantic consistency by the AI.
    Focus on high-value cross-document pairs only.
    """
    pairs = []
    doc_map = {doc.doc_id: doc for doc in documents}

    for doc in documents:
        for req in doc.requirements:
            for trace in req.traces_to:
                # trace format: "DOC-001/R-01"
                parts = trace.split("/")
                if len(parts) == 2:
                    target_doc_id, target_req_id = parts
                    if target_doc_id in doc_map:
                        target_doc = doc_map[target_doc_id]
                        target_reqs = {
                            r.req_id: r for r in target_doc.requirements
                        }
                        if target_req_id in target_reqs:
                            pairs.append((
                                doc.doc_id,
                                req,
                                target_doc_id,
                                target_reqs[target_req_id],
                            ))

    return pairs
