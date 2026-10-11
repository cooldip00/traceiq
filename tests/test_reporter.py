from backend.models import IssueType, IssueSeverity, AIStatus, make_issue
from backend.reporter import generate_report


def _issue(severity, issue_type=IssueType.COVERAGE_GAP):
    return make_issue(issue_type, severity, "DOC-001", "t", "d")


def test_conflicts_are_high_severity_only():
    r = generate_report("j", [_issue(IssueSeverity.HIGH), _issue(IssueSeverity.MEDIUM),
                              _issue(IssueSeverity.LOW)], 1, 1, 1)
    assert len(r.conflicts) == 1 and len(r.warnings) == 2


def test_ok_count_is_consistent_pairs():
    r = generate_report("j", [], 13, 32, 16, pairs_total=34, pairs_consistent=30)
    assert r.ok_count == 30 and r.ai_status == AIStatus.OK


def test_ai_unavailable_never_claims_all_clear():
    r = generate_report("j", [], 13, 32, 16, pairs_total=34, ai_available=False,
                        ai_message="Ollama is not reachable")
    assert r.ai_status == AIStatus.UNAVAILABLE
    assert r.ok_count == 0 and r.pairs_failed == 34
    assert "did not run" in r.summary
    assert "No issues found" not in r.summary


def test_partial_ai_failure_is_reported():
    r = generate_report("j", [], 1, 1, 1, pairs_total=10, pairs_consistent=7, pairs_failed=3)
    assert r.ai_status == AIStatus.PARTIAL and "3 of 10" in r.summary


def test_clean_run():
    r = generate_report("j", [], 1, 1, 1, pairs_total=5, pairs_consistent=5)
    assert r.summary == "No issues found: all checks passed."
