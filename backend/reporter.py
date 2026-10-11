from backend.models import Issue, IssueSeverity, AnalysisReport, AIStatus


def generate_report(
    job_id: str,
    issues: list[Issue],
    total_documents: int,
    total_requirements: int,
    total_links: int,
    pairs_total: int = 0,
    pairs_consistent: int = 0,
    pairs_failed: int = 0,
    ai_available: bool = True,
    ai_message: str = "",
    impact_map: dict = None,
) -> AnalysisReport:
    """
    Classify issues and build the final analysis report.

    Conflicts = severity HIGH (must fix). Warnings = MEDIUM / LOW (review).
    The AI assigns HIGH to every direct contradiction, so all AI conflicts
    land in the conflicts list.
    """
    conflicts = [i for i in issues if i.severity == IssueSeverity.HIGH]
    warnings = [i for i in issues if i.severity != IssueSeverity.HIGH]

    if pairs_total == 0:
        ai_status = AIStatus.NO_PAIRS
    elif not ai_available:
        ai_status = AIStatus.UNAVAILABLE
    elif pairs_failed:
        ai_status = AIStatus.PARTIAL
    else:
        ai_status = AIStatus.OK

    summary_parts = []
    if conflicts:
        summary_parts.append(f"{len(conflicts)} conflict(s) require immediate attention")
    if warnings:
        summary_parts.append(f"{len(warnings)} warning(s) should be reviewed")

    if ai_status == AIStatus.UNAVAILABLE:
        summary_parts.append(
            f"AI semantic analysis did not run ({ai_message or 'Ollama unavailable'}); "
            f"{pairs_total} traced pair(s) were NOT checked for contradictions"
        )
    elif ai_status == AIStatus.PARTIAL:
        summary_parts.append(
            f"AI analysis failed for {pairs_failed} of {pairs_total} pair(s); "
            f"those pairs were not checked"
        )

    if not issues and ai_status in (AIStatus.OK, AIStatus.NO_PAIRS):
        summary_parts.append("No issues found: all checks passed")

    return AnalysisReport(
        job_id=job_id,
        total_documents=total_documents,
        total_requirements=total_requirements,
        total_links=total_links,
        conflicts=conflicts,
        warnings=warnings,
        ok_count=pairs_consistent,
        pairs_total=pairs_total,
        pairs_failed=pairs_failed if ai_available else pairs_total,
        ai_status=ai_status,
        summary=". ".join(summary_parts) + ".",
        impact_map=impact_map or {},
    )
