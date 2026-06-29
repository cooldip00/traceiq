from backend.models import Issue, IssueType, IssueSeverity, AnalysisReport


def generate_report(
    job_id: str,
    issues: list[Issue],
    total_documents: int,
    total_requirements: int,
    total_links: int,
) -> AnalysisReport:
    """Classify issues and build the final analysis report."""

    conflicts = [
        i for i in issues
        if i.severity == IssueSeverity.HIGH
        or i.issue_type == IssueType.SEMANTIC_CONFLICT
    ]
    warnings = [
        i for i in issues
        if i not in conflicts
    ]

    # Approximate ok count: requirement pairs checked minus issues found
    total_pairs_checked = total_links + total_requirements
    ok_count = max(0, total_pairs_checked - len(issues))

    summary_parts = []
    if conflicts:
        summary_parts.append(f"{len(conflicts)} conflict(s) require immediate attention")
    if warnings:
        summary_parts.append(f"{len(warnings)} warning(s) should be reviewed")
    if not issues:
        summary_parts.append("All requirements are consistent across documents")

    return AnalysisReport(
        job_id=job_id,
        total_documents=total_documents,
        total_requirements=total_requirements,
        total_links=total_links,
        conflicts=conflicts,
        warnings=warnings,
        ok_count=ok_count,
        summary=". ".join(summary_parts) + ".",
    )
