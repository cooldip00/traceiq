from backend.graph_builder import build_graph
from backend.models import IssueType, IssueSeverity
from backend.parser import parse_documents
from backend.validator import validate_structural, get_semantic_pairs
from tests.conftest import doc, req


def _run(*files):
    docs, _ = parse_documents(list(files))
    return validate_structural(docs, build_graph(docs))


def test_sample_docs_find_exactly_the_seeded_structural_conflicts(sample_docs, sample_graph):
    found = {(i.issue_type, i.source_doc, i.source_req) for i in validate_structural(sample_docs, sample_graph)}
    assert found == {
        (IssueType.COVERAGE_GAP, "BRD-001", "R-07"),                # R-07 not covered in CDD
        (IssueType.ORPHANED_REQUIREMENT, "PRD-001", "F-04"),        # F-04 has no Epic
        (IssueType.VERSION_MISMATCH, "SDD-001", None),              # SDD written against BRD v1.0
        (IssueType.MISSING_ACCEPTANCE_CRITERIA, "US-004", "US-04"),  # US-004 has no AC
    }


def test_semantic_pairs_are_unique_and_point_downstream(sample_docs, sample_graph):
    pairs = get_semantic_pairs(sample_docs, sample_graph)
    keys = [(a, ra.req_id, b, rb.req_id) for a, ra, b, rb in pairs]
    assert len(keys) == len(set(keys)) == 34
    unordered = {frozenset([(a, x), (b, y)]) for a, x, b, y in keys}
    assert len(unordered) == len(keys)
    # The seeded SLA conflict pair is sent to the AI, upstream requirement first
    assert ("BRD-001", "R-03", "US-002", "US-02") in keys


def test_prd_feature_without_epic_is_found_by_rule_not_by_id():
    issues = _run(
        doc("PRD-001", "PRD", req("F-01") + req("F-02"), links=["EP-001"]),
        doc("EP-001", "EPIC", req("EP-01", traces=["PRD-001/F-01"])),
    )
    orphans = [i for i in issues if i.issue_type == IssueType.ORPHANED_REQUIREMENT]
    assert [(i.source_req, i.severity) for i in orphans] == [("F-02", IssueSeverity.HIGH)]


def test_requirement_coverage_skipped_when_whole_doc_type_missing():
    issues = _run(doc("BRD-001", "BRD", req("R-01")))
    assert not any(i.source_req for i in issues if i.issue_type == IssueType.COVERAGE_GAP)
    titles = {i.title for i in issues}
    assert "Missing downstream: BRD-001 has no CDD" in titles
    assert "Missing downstream: BRD-001 has no PRD" in titles


def test_version_check_works_for_any_document_type():
    issues = _run(
        doc("PRD-001", "PRD", version="2.0"),
        doc("EP-001", "EPIC", "Scoped from PRD-001 v1.4 features.", links=["PRD-001"]),
    )
    mismatches = [i for i in issues if i.issue_type == IssueType.VERSION_MISMATCH]
    assert len(mismatches) == 1 and "PRD-001 v1.4" in mismatches[0].title


def test_matching_version_reference_is_fine():
    issues = _run(doc("BRD-001", "BRD", version="1.2"), doc("SDD-001", "SDD", "Based on BRD v1.2."))
    assert not any(i.issue_type == IssueType.VERSION_MISMATCH for i in issues)


def test_broken_trace_is_reported():
    issues = _run(doc("PRD-001", "PRD", req("F-01", traces=["BRD-009/R-01"])))
    broken = [i for i in issues if i.issue_type == IssueType.BROKEN_REFERENCE]
    assert len(broken) == 1 and broken[0].target_doc == "BRD-009"


def test_same_rank_cycle_is_reported():
    issues = _run(
        doc("EP-001", "EPIC", req("EP-01", traces=["EP-002/EP-02"])),
        doc("EP-002", "EPIC", req("EP-02", traces=["EP-001/EP-01"])),
    )
    assert any(i.issue_type == IssueType.CIRCULAR_TRACE for i in issues)
