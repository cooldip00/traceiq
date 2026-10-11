import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.responses import StreamingResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from backend.orchestrator import run_pipeline
from backend.models import AnalysisReport

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="TraceIQ — PDM Traceability Agent", version="1.1.0")

MAX_FILES = 100
MAX_FILE_BYTES = 1_000_000      # 1 MB per markdown file
JOB_TTL_SECONDS = 60 * 60       # forget jobs after 1 hour
KEEPALIVE_SECONDS = 15          # SSE comment ping while waiting for events


# ── In-memory job store ───────────────────────────────────────────────────────
# Fine for a single local process. A multi-worker deployment would need a
# shared store (e.g. Redis) because each worker has its own memory.
@dataclass
class Job:
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)  # StepEvent dicts, None = done
    report: AnalysisReport | None = None
    created_at: float = field(default_factory=time.monotonic)


jobs: dict[str, Job] = {}
# Strong references to running pipelines; the event loop only keeps weak
# references, so an unreferenced task could be garbage-collected mid-run.
background_tasks: set[asyncio.Task] = set()

FRONTEND_DIR = Path(__file__).parent.parent / "frontend"


def _purge_old_jobs() -> None:
    cutoff = time.monotonic() - JOB_TTL_SECONDS
    for job_id in [j for j, job in jobs.items() if job.created_at < cutoff]:
        del jobs[job_id]


# ── Static files ──────────────────────────────────────────────────────────────
app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = FRONTEND_DIR / "index.html"
    return HTMLResponse(content=html_path.read_text(encoding="utf-8"))


# ── Upload + start analysis ────────────────────────────────────────────────────
@app.post("/analyze")
async def analyze(files: list[UploadFile] = File(...)):
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded.")
    if len(files) > MAX_FILES:
        raise HTTPException(status_code=400, detail=f"Too many files (max {MAX_FILES}).")

    # Read all file contents up front (can't read UploadFile in a thread)
    file_contents: list[tuple[str, str]] = []
    for f in files:
        name = f.filename or "unnamed"
        if not name.lower().endswith(".md"):
            raise HTTPException(status_code=400, detail=f"'{name}' is not a .md file.")
        raw = await f.read(MAX_FILE_BYTES + 1)
        if len(raw) > MAX_FILE_BYTES:
            raise HTTPException(status_code=400, detail=f"'{name}' is larger than {MAX_FILE_BYTES // 1000} KB.")
        try:
            file_contents.append((name, raw.decode("utf-8-sig")))  # -sig strips a BOM
        except UnicodeDecodeError:
            raise HTTPException(status_code=400, detail=f"'{name}' is not valid UTF-8 text.")

    _purge_old_jobs()
    job_id = uuid.uuid4().hex[:8]
    job = jobs[job_id] = Job()

    def store_report(report: AnalysisReport) -> None:
        job.report = report

    task = asyncio.create_task(run_pipeline(job_id, file_contents, job.queue, on_report=store_report))
    background_tasks.add(task)
    task.add_done_callback(background_tasks.discard)

    return {"job_id": job_id}


# ── SSE stream endpoint ────────────────────────────────────────────────────────
@app.get("/stream/{job_id}")
async def stream_events(job_id: str):
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found.")
    job = jobs[job_id]

    async def event_generator():
        while True:
            try:
                event = await asyncio.wait_for(job.queue.get(), timeout=KEEPALIVE_SECONDS)
            except asyncio.TimeoutError:
                # Nothing new (e.g. a slow AI call). A comment line keeps the
                # connection alive; EventSource ignores it.
                yield ": keepalive\n\n"
                continue
            if event is None:
                # Pipeline finished (successfully or not)
                yield f"data: {json.dumps({'type': 'done', 'report_ready': job.report is not None})}\n\n"
                break
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ── Final report endpoint ──────────────────────────────────────────────────────
@app.get("/report/{job_id}")
async def get_report(job_id: str):
    job = jobs.get(job_id)
    if job is None or job.report is None:
        raise HTTPException(status_code=404, detail="Report not ready yet.")
    return job.report.model_dump()


# ── Health check ──────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "ok", "service": "TraceIQ"}
