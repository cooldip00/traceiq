import pytest
import requests

import backend.analyzer as analyzer
from backend.models import IssueType, IssueSeverity, Requirement

R_A = Requirement(req_id="R-03", title="Welcome email", description="Email within 2 minutes.")
R_B = Requirement(req_id="US-02", title="Welcome email", description="Email within 60 minutes.")


def fake_replies(monkeypatch, *replies):
    """Make _call_qwen return (or raise) the given replies in order."""
    calls = iter(replies)

    def fake(_msg):
        r = next(calls)
        if isinstance(r, Exception):
            raise r
        return r
    monkeypatch.setattr(analyzer, "_call_qwen", fake)


@pytest.mark.parametrize("raw, expected", [
    ('{"status": "CONFLICT"}', {"status": "CONFLICT"}),
    ('```json\n{"status": "CONFLICT"}\n```', {"status": "CONFLICT"}),
    ('Sure! {"status": "CONFLICT"}', {"status": "CONFLICT"}),
    ('{"status": "CONFLICT"} (see {note})', {"status": "CONFLICT"}),
])
def test_extract_json(raw, expected):
    assert analyzer.extract_json(raw) == expected


def test_extract_json_rejects_garbage():
    with pytest.raises(analyzer.AnalysisError):
        analyzer.extract_json("no json here")


@pytest.mark.parametrize("value, expected", [
    (0.9, 0.9), ("0.9", 0.9), ("90%", 0.9), (90, 0.9), ("high", 0.9), (-0.2, 0.0),
    ("unsure", None), (None, None), (True, None),
])
def test_parse_confidence(value, expected):
    result = analyzer.parse_confidence(value)
    assert result == pytest.approx(expected) if expected is not None else result is None


def test_conflict_is_always_high(monkeypatch):
    fake_replies(monkeypatch, {"status": "conflict", "confidence": "high", "reason": "2 vs 60", "severity": "LOW"})
    status, issue = analyzer.analyze_pair("BRD-001", R_A, "US-002", R_B)
    assert status == "CONFLICT"
    assert issue.issue_type == IssueType.SEMANTIC_CONFLICT
    assert issue.severity == IssueSeverity.HIGH
    assert issue.confidence == 0.9


def test_partial_has_its_own_type(monkeypatch):
    fake_replies(monkeypatch, {"status": "PARTIAL", "confidence": 0.6, "severity": "LOW"})
    _, issue = analyzer.analyze_pair("BRD-001", R_A, "US-002", R_B)
    assert issue.issue_type == IssueType.PARTIAL_MATCH and issue.severity == IssueSeverity.LOW


def test_consistent_returns_no_issue(monkeypatch):
    fake_replies(monkeypatch, {"status": "CONSISTENT", "confidence": 0.95})
    assert analyzer.analyze_pair("BRD-001", R_A, "US-002", R_B) == ("CONSISTENT", None)


def test_retry_then_success(monkeypatch):
    fake_replies(monkeypatch, requests.Timeout("slow"), {"status": "CONSISTENT"})
    assert analyzer.analyze_pair("BRD-001", R_A, "US-002", R_B)[0] == "CONSISTENT"


def test_two_failures_raise(monkeypatch):
    fake_replies(monkeypatch, {"status": "MAYBE"}, requests.ConnectionError("down"))
    with pytest.raises(analyzer.AnalysisError):
        analyzer.analyze_pair("BRD-001", R_A, "US-002", R_B)


def test_failed_pairs_are_counted_not_hidden(monkeypatch):
    fake_replies(
        monkeypatch,
        {"status": "CONSISTENT"},
        {"status": "CONFLICT", "confidence": 0.9},
        requests.ConnectionError("down"), requests.ConnectionError("down"),
    )
    pair = ("BRD-001", R_A, "US-002", R_B)
    progress = []
    result = analyzer.analyze_all_pairs([pair, pair, pair], lambda *a: progress.append(a[:2]))
    assert (result.total, result.consistent, len(result.issues), result.failed) == (3, 1, 1, 1)
    assert progress == [(1, 3), (2, 3), (3, 3)]


def test_check_ollama_unreachable(monkeypatch):
    def boom(*a, **k):
        raise requests.ConnectionError("refused")
    monkeypatch.setattr(analyzer.requests, "get", boom)
    ok, msg = analyzer.check_ollama()
    assert not ok and "not reachable" in msg


def test_check_ollama_model_missing(monkeypatch):
    class Resp:
        def raise_for_status(self): pass
        def json(self): return {"models": [{"name": "llama3:8b"}]}
    monkeypatch.setattr(analyzer.requests, "get", lambda *a, **k: Resp())
    ok, msg = analyzer.check_ollama()
    assert not ok and "ollama pull" in msg
