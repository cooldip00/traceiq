import asyncio
import json
import uuid
from pathlib import Path

from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.responses import StreamingResponse, HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from backend.orchestrator import run_pipeline
from backend.models import AnalysisReport

app = FastAPI(title="TraceIQ — PDM Traceability Agent", version="1.0.0")

# ── In-memory job store ───────────────────────────────────────────────────────
# job_id → asyncio.Queue of StepEvent dicts (None = done)
job_queues: dict[str, asyncio.Queue] = {}
# job_id → final AnalysisReport
job_reports: dict[str, AnalysisReport] = {}

FRONTEND_DIR = Path(__file__).parent.parent / "frontend"


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

    # Read all file contents up front (can't read UploadFile in a thread)
    file_contents: list[tuple[str, str]] = []
    for f in files:
        content = await f.read()
        file_contents.append((f.filename, content.decode("utf-8")))

    job_id = str(uuid.uuid4())[:8]
    queue: asyncio.Queue = asyncio.Queue()
    job_queues[job_id] = queue

    # Run pipeline as background task
    async def _run():
        report = await run_pipeline(job_id, file_contents, queue)
        job_reports[job_id] = report

    asyncio.create_task(_run())

    return {"job_id": job_id}


# ── SSE stream endpoint ────────────────────────────────────────────────────────
@app.get("/stream/{job_id}")
async def stream_events(job_id: str):
    if job_id not in job_queues:
        raise HTTPException(status_code=404, detail="Job not found.")

    queue = job_queues[job_id]

    async def event_generator():
        try:
            while True:
                event = await asyncio.wait_for(queue.get(), timeout=180.0)
                if event is None:
                    # Pipeline done — send final sentinel event
                    yield f"data: {json.dumps({'type': 'done'})}\n\n"
                    break
                yield f"data: {json.dumps(event)}\n\n"
        except asyncio.TimeoutError:
            yield f"data: {json.dumps({'type': 'error', 'message': 'Analysis timed out'})}\n\n"

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
    if job_id not in job_reports:
        raise HTTPException(status_code=404, detail="Report not ready yet.")
    return job_reports[job_id].model_dump()


# ── Health check ──────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "ok", "service": "TraceIQ"}
