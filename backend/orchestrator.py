import asyncio
import logging
from typing import Callable

import networkx as nx

from backend.parser import parse_documents
from backend.graph_builder import build_graph, get_impacted_documents
from backend.validator import validate_structural, get_semantic_pairs
from backend.analyzer import analyze_all_pairs, check_ollama, SemanticResult
from backend.reporter import generate_report
from backend.models import AnalysisReport, StepEvent

logger = logging.getLogger(__name__)

STEP_NAMES = {
    1: "Document Parser",
    2: "Graph Builder",
    3: "Structural Validation",
    4: "AI Semantic Analysis",
    5: "Report Generator",
}


async def run_pipeline(
    job_id: str,
    files: list[tuple[str, str]],
    queue: asyncio.Queue,
    on_report: Callable[[AnalysisReport], None] | None = None,
) -> AnalysisReport | None:
    """
    Run the full 5-step traceability analysis pipeline.
    Emits StepEvent dicts to `queue` as it goes. On success calls
    on_report(report) BEFORE signalling the end, so the report is already
    stored when the browser asks for it. Whatever happens, the last item put
    in the queue is None (sentinel), so the SSE stream always closes.
    """
    loop = asyncio.get_running_loop()
    current_step = 0

    async def emit(step: int, status: str, message: str, data: dict | None = None):
        nonlocal current_step
        current_step = step
        await queue.put(StepEvent(
            step=step, name=STEP_NAMES[step], status=status,
            message=message, data=data or {},
        ).model_dump())

    try:
        # ── STEP 1: Parse Documents ──────────────────────────────────────────
        await emit(1, "running", "Parsing uploaded markdown files...")
        documents, parse_issues = await asyncio.to_thread(parse_documents, files)
        total_reqs = sum(len(d.requirements) for d in documents)
        await emit(1, "complete",
                   f"Parsed {len(documents)} of {len(files)} file(s), extracted {total_reqs} requirements",
                   {
                       "documents_parsed": len(documents),
                       "requirements_extracted": total_reqs,
                       "parse_problems": len(parse_issues),
                       "doc_types": [d.doc_type.value for d in documents],
                   })

        # ── STEP 2: Build Traceability Graph + impact analysis ───────────────
        await emit(2, "running", "Building traceability dependency graph...")
        G: nx.DiGraph = await asyncio.to_thread(build_graph, documents)

        doc_nodes = [n for n, d in G.nodes(data=True) if d.get("node_type") == "document"]
        req_nodes = [n for n, d in G.nodes(data=True) if d.get("node_type") == "requirement"]
        link_edges = [(u, v) for u, v, d in G.edges(data=True) if d.get("edge_type") == "links_to"]
        trace_edges = [(u, v) for u, v, d in G.edges(data=True) if d.get("edge_type") == "traces_to"]

        impact_map = {}
        for doc_node in doc_nodes:
            impact = get_impacted_documents(G, doc_node)
            impact_map[doc_node] = {
                "upstream_docs": impact["upstream_docs"],
                "downstream_docs": impact["downstream_docs"],
                "affected_requirements": len(impact["affected_requirements"]),
                "total_impacted": impact["total_impacted"],
            }

        await emit(2, "complete",
                   f"Graph built: {len(doc_nodes)} document nodes, {len(req_nodes)} requirement nodes, "
                   f"{len(link_edges)} links, {len(trace_edges)} traces",
                   {
                       "document_nodes": len(doc_nodes),
                       "requirement_nodes": len(req_nodes),
                       "links": len(link_edges),
                       "traces": len(trace_edges),
                       "acyclic": nx.is_directed_acyclic_graph(G),
                   })

        # ── STEP 3: Structural Validation ────────────────────────────────────
        await emit(3, "running", "Checking for broken links, missing coverage, version mismatches...")
        structural_issues = parse_issues + await asyncio.to_thread(validate_structural, documents, G)
        await emit(3, "complete", f"Found {len(structural_issues)} structural issue(s)", {
            "issues_found": len(structural_issues),
            "issue_types": sorted({i.issue_type.value for i in structural_issues}),
        })

        # ── STEP 4: AI Semantic Analysis ─────────────────────────────────────
        semantic_pairs = get_semantic_pairs(documents, G)
        semantic = SemanticResult(total=len(semantic_pairs))
        ai_available, ai_message = True, ""

        if semantic_pairs:
            await emit(4, "running", "Checking that Ollama and the model are available...")
            ai_available, ai_message = await asyncio.to_thread(check_ollama)

        if not semantic_pairs:
            await emit(4, "complete", "No traced requirement pairs to analyze")
        elif not ai_available:
            await emit(4, "error", f"Skipped: {ai_message}. "
                                   f"{len(semantic_pairs)} pair(s) were not checked.")
        else:
            await emit(4, "running",
                       f"Analyzing {len(semantic_pairs)} requirement pair(s) with Qwen 2.5 7B...",
                       {"pairs_to_analyze": len(semantic_pairs)})

            def on_progress(current, total, label):
                # Runs in the worker thread. asyncio.Queue is not thread-safe,
                # so hand the put over to the event loop thread.
                event = StepEvent(
                    step=4, name=STEP_NAMES[4], status="running",
                    message=f"Checking pair {current}/{total}: {label}",
                    data={"current": current, "total": total, "pair": label},
                ).model_dump()
                loop.call_soon_threadsafe(queue.put_nowait, event)

            semantic = await asyncio.to_thread(analyze_all_pairs, semantic_pairs, on_progress)
            status = "error" if semantic.failed == semantic.total else "complete"
            await emit(4, status,
                       f"AI analysis complete. {len(semantic.issues)} issue(s), "
                       f"{semantic.consistent} consistent, {semantic.failed} failed",
                       {
                           "semantic_issues_found": len(semantic.issues),
                           "consistent": semantic.consistent,
                           "failed": semantic.failed,
                       })

        # ── STEP 5: Generate Report ──────────────────────────────────────────
        await emit(5, "running", "Compiling final conflict report...")
        report = generate_report(
            job_id=job_id,
            issues=structural_issues + semantic.issues,
            total_documents=len(documents),
            total_requirements=total_reqs,
            total_links=len(link_edges),
            pairs_total=semantic.total,
            pairs_consistent=semantic.consistent,
            pairs_failed=semantic.failed,
            ai_available=ai_available,
            ai_message=ai_message,
            impact_map=impact_map,
        )
        if on_report:
            on_report(report)
        await emit(5, "complete",
                   f"Report ready — {len(report.conflicts)} conflict(s), {len(report.warnings)} warning(s), "
                   f"{report.ok_count} consistent pair(s)",
                   {
                       "conflicts": len(report.conflicts),
                       "warnings": len(report.warnings),
                       "ok": report.ok_count,
                       "ai_status": report.ai_status.value,
                   })
        return report

    except Exception as e:
        logger.exception("Pipeline %s failed in step %s", job_id, current_step)
        await queue.put(StepEvent(
            step=max(current_step, 1), name=STEP_NAMES[max(current_step, 1)],
            status="error", message=f"Pipeline failed: {type(e).__name__}: {e}",
        ).model_dump())
        return None

    finally:
        # Sentinel: always close the SSE stream, even after a crash
        await queue.put(None)
