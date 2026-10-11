import asyncio
import json

import pytest
from fastapi.testclient import TestClient

import backend.analyzer as analyzer
import backend.main as main
import backend.orchestrator as orch
from backend.models import AIStatus


def run(files):
    """Run the pipeline; return (report, events, stored_reports)."""
    async def go():
        queue, stored = asyncio.Queue(), []
        report = await orch.run_pipeline("job", files, queue, on_report=stored.append)
        events = []
        while not queue.empty():
            events.append(queue.get_nowait())
        return report, events, stored
    return asyncio.run(go())


@pytest.fixture
def ollama_down(monkeypatch):
    monkeypatch.setattr(orch, "check_ollama", lambda: (False, "Ollama is not reachable"))


@pytest.fixture
def ollama_fake(monkeypatch):
    """Ollama 'up'; the model flags only the seeded SLA pair as a conflict."""
    monkeypatch.setattr(orch, "check_ollama", lambda: (True, "ok"))

    def fake_call(msg):
        if "(BRD-001, R-03)" in msg and "(US-002, US-02)" in msg:
            return {"status": "CONFLICT", "confidence": 0.95, "reason": "2 min vs 60 min", "severity": "HIGH"}
        return {"status": "CONSISTENT", "confidence": 0.9}
    monkeypatch.setattr(analyzer, "_call_qwen", fake_call)


def test_full_sample_run_finds_all_five_seeded_conflicts(sample_files, ollama_fake):
    report, events, stored = run(sample_files)
    assert stored == [report]
    assert report.ai_status == AIStatus.OK
    assert (report.pairs_total, report.ok_count) == (34, 33)
    titles = [i.title for i in report.conflicts + report.warnings]
    assert len(titles) == 5
    assert "Conflict: BRD-001/R-03 vs US-002/US-02" in titles
    assert events[-1] is None


def test_progress_is_streamed_before_step_4_completes(sample_files, ollama_fake):
    _, events, _ = run(sample_files)
    step4 = [e for e in events if e and e["step"] == 4]
    progress = [e for e in step4 if "current" in e["data"]]
    assert len(progress) == 34
    assert step4[-1]["status"] == "complete"
    assert [e["data"]["current"] for e in progress] == list(range(1, 35))


def test_ollama_down_is_visible(sample_files, ollama_down):
    report, events, _ = run(sample_files)
    assert report.ai_status == AIStatus.UNAVAILABLE
    assert report.ok_count == 0
    step4 = [e for e in events if e and e["step"] == 4]
    assert step4[-1]["status"] == "error"


def test_crash_sends_error_event_and_sentinel(monkeypatch, sample_files):
    def boom(_docs):
        raise RuntimeError("simulated crash")
    monkeypatch.setattr(orch, "build_graph", boom)
    report, events, stored = run(sample_files)
    assert report is None and stored == []
    assert events[-2]["step"] == 2 and events[-2]["status"] == "error"
    assert "simulated crash" in events[-2]["message"]
    assert events[-1] is None


# ── HTTP API ─────────────────────────────────────────────────────────────────

@pytest.fixture
def client():
    with TestClient(main.app) as c:
        yield c


def test_rejects_non_markdown(client):
    r = client.post("/analyze", files=[("files", ("notes.txt", b"hi", "text/plain"))])
    assert r.status_code == 400 and "not a .md file" in r.json()["detail"]


def test_rejects_non_utf8(client):
    r = client.post("/analyze", files=[("files", ("a.md", "café".encode("latin-1"), "text/markdown"))])
    assert r.status_code == 400 and "UTF-8" in r.json()["detail"]


def test_rejects_oversized_file(client):
    big = b"x" * (main.MAX_FILE_BYTES + 1)
    r = client.post("/analyze", files=[("files", ("a.md", big, "text/markdown"))])
    assert r.status_code == 400


def test_end_to_end_stream_and_report(client, sample_files, ollama_down):
    upload = [("files", (name, text.encode("utf-8"), "text/markdown")) for name, text in sample_files]
    job_id = client.post("/analyze", files=upload).json()["job_id"]

    events = []
    with client.stream("GET", f"/stream/{job_id}") as resp:
        for line in resp.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    assert events[-1] == {"type": "done", "report_ready": True}

    report = client.get(f"/report/{job_id}").json()
    assert report["ai_status"] == "unavailable"
    assert report["impact_map"]["BRD-001"]["total_impacted"] == 10
