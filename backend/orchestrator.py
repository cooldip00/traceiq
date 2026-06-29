import asyncio
import networkx as nx

from backend.parser import parse_documents
from backend.graph_builder import build_graph
from backend.validator import validate_structural, get_semantic_pairs
from backend.analyzer import analyze_all_pairs
from backend.reporter import generate_report
from backend.models import AnalysisReport, StepEvent


async def run_pipeline(
    job_id: str,
    files: list[tuple[str, str]],
    queue: asyncio.Queue,
) -> AnalysisReport:
    """
    Run the full 5-step traceability analysis pipeline.
    Emits StepEvent objects to `queue` after each step.
    Puts None in queue when done (sentinel).
    """

    # ── STEP 1: Parse Documents ──────────────────────────────────────────────
    await queue.put(StepEvent(
        step=1, name="Document Parser",
        status="running", message="Parsing uploaded markdown files...",
    ).model_dump())

    await asyncio.sleep(0.2)  # yield to event loop
    documents = await asyncio.to_thread(parse_documents, files)

    total_reqs = sum(len(d.requirements) for d in documents)
    await queue.put(StepEvent(
        step=1, name="Document Parser",
        status="complete",
        message=f"Parsed {len(documents)} documents, extracted {total_reqs} requirements",
        data={
            "documents_parsed": len(documents),
            "requirements_extracted": total_reqs,
            "doc_types": [d.doc_type.value for d in documents],
        },
    ).model_dump())

    # ── STEP 2: Build Traceability Graph ─────────────────────────────────────
    await queue.put(StepEvent(
        step=2, name="Graph Builder",
        status="running", message="Building traceability dependency graph...",
    ).model_dump())

    await asyncio.sleep(0.2)
    G: nx.DiGraph = await asyncio.to_thread(build_graph, documents)

    doc_nodes = [n for n, d in G.nodes(data=True) if d.get("node_type") == "document"]
    req_nodes = [n for n, d in G.nodes(data=True) if d.get("node_type") == "requirement"]
    link_edges = [(u, v) for u, v, d in G.edges(data=True) if d.get("edge_type") == "links_to"]

    await queue.put(StepEvent(
        step=2, name="Graph Builder",
        status="complete",
        message=f"Graph built: {len(doc_nodes)} document nodes, {len(req_nodes)} requirement nodes, {len(link_edges)} links",
        data={
            "document_nodes": len(doc_nodes),
            "requirement_nodes": len(req_nodes),
            "links": len(link_edges),
        },
    ).model_dump())

    # ── STEP 3: Structural Validation ─────────────────────────────────────────
    await queue.put(StepEvent(
        step=3, name="Structural Validation",
        status="running", message="Checking for broken links, missing coverage, version mismatches...",
    ).model_dump())

    await asyncio.sleep(0.2)
    structural_issues = await asyncio.to_thread(validate_structural, documents, G)

    await queue.put(StepEvent(
        step=3, name="Structural Validation",
        status="complete",
        message=f"Found {len(structural_issues)} structural issue(s)",
        data={
            "issues_found": len(structural_issues),
            "issue_types": list({i.issue_type.value for i in structural_issues}),
        },
    ).model_dump())

    # ── STEP 4: AI Semantic Analysis ──────────────────────────────────────────
    semantic_pairs = get_semantic_pairs(documents)

    await queue.put(StepEvent(
        step=4, name="AI Semantic Analysis",
        status="running",
        message=f"Analyzing {len(semantic_pairs)} requirement pair(s) with Qwen 2.5 7B...",
        data={"pairs_to_analyze": len(semantic_pairs)},
    ).model_dump())

    ai_progress_events = []

    def on_progress(current, total, label):
        event = StepEvent(
            step=4, name="AI Semantic Analysis",
            status="running",
            message=f"Checking pair {current}/{total}: {label}",
            data={"current": current, "total": total, "pair": label},
        ).model_dump()
        ai_progress_events.append(event)

    semantic_issues = await asyncio.to_thread(
        analyze_all_pairs, semantic_pairs, on_progress
    )

    # Flush any progress events collected during thread execution
    for event in ai_progress_events:
        await queue.put(event)

    await queue.put(StepEvent(
        step=4, name="AI Semantic Analysis",
        status="complete",
        message=f"AI analysis complete. Found {len(semantic_issues)} semantic issue(s)",
        data={"semantic_issues_found": len(semantic_issues)},
    ).model_dump())

    # ── STEP 5: Generate Report ───────────────────────────────────────────────
    await queue.put(StepEvent(
        step=5, name="Report Generator",
        status="running", message="Compiling final conflict report...",
    ).model_dump())

    await asyncio.sleep(0.3)
    all_issues = structural_issues + semantic_issues
    report = generate_report(
        job_id=job_id,
        issues=all_issues,
        total_documents=len(documents),
        total_requirements=total_reqs,
        total_links=len(link_edges),
    )

    await queue.put(StepEvent(
        step=5, name="Report Generator",
        status="complete",
        message=f"Report ready — {len(report.conflicts)} conflict(s), {len(report.warnings)} warning(s), {report.ok_count} consistent",
        data={
            "conflicts": len(report.conflicts),
            "warnings": len(report.warnings),
            "ok": report.ok_count,
        },
    ).model_dump())

    # Sentinel: signal SSE stream to close
    await queue.put(None)

    return report
