import logging
import re
import frontmatter
from backend.models import (
    ParsedDocument, Requirement, AcceptanceCriterion, DocType,
    Issue, IssueType, IssueSeverity, make_issue,
)

logger = logging.getLogger(__name__)

DOC_TYPE_MAP = {t.value: t for t in DocType}

# A real requirement header: "### R-01", "### F-04", "### COMP-02", ...
REQ_HEADER = re.compile(
    r"^###\s+((?:R|F|C|S|T|EP|US|COMP|NFR)-\d+)\b",
    re.MULTILINE,
)
# Anything that *looks* like an ID header ("### Req-01", "### r-01", "### X-1")
# but was not accepted by REQ_HEADER. Used to warn instead of silently dropping it.
ID_LIKE_HEADER = re.compile(r"^###\s+([A-Za-z]+-\d+)\b.*$", re.MULTILINE)

# A field value ends at the next "**Field:**" line or the next markdown heading.
_FIELD_END = r"(?=^\*\*[A-Za-z ]+:\*\*|^#{1,6}\s|\Z)"


def _field(block: str, name: str, multiline: bool = False) -> str | None:
    if multiline:
        m = re.search(rf"\*\*{name}:\*\*\s*([\s\S]*?){_FIELD_END}", block, re.MULTILINE)
    else:
        m = re.search(rf"\*\*{name}:\*\*[ \t]*(.+)", block)
    return m.group(1).strip() if m else None


def parse_acceptance_criteria(text: str) -> list[AcceptanceCriterion]:
    """Parse acceptance criteria lines like '- AC-01: description'."""
    criteria = []
    for line in text.strip().splitlines():
        line = line.strip().lstrip("-").strip()
        match = re.match(r"^(AC-\d+):\s*(.+)$", line)
        if match:
            criteria.append(AcceptanceCriterion(
                ac_id=match.group(1),
                description=match.group(2).strip()
            ))
    return criteria


def parse_traces_to(text: str) -> list[str]:
    """Parse comma-separated traces_to values."""
    return [t.strip() for t in text.split(",") if t.strip()]


def parse_requirements(content: str) -> list[Requirement]:
    """
    Parse requirement blocks from markdown body.
    Expects blocks starting with '### R-xx' or '### F-xx' etc.
    """
    requirements = []

    # splits = [before_first, id1, block1, id2, block2, ...]
    splits = REQ_HEADER.split(content)

    for i in range(1, len(splits) - 1, 2):
        req_id = splits[i].strip()
        block = splits[i + 1]

        ac_text = _field(block, "Acceptance Criteria", multiline=True)
        traces_text = _field(block, "Traces To")

        requirements.append(Requirement(
            req_id=req_id,
            title=_field(block, "Title") or req_id,
            priority=_field(block, "Priority"),
            status=_field(block, "Status"),
            description=_field(block, "Description", multiline=True) or "",
            acceptance_criteria=parse_acceptance_criteria(ac_text) if ac_text else [],
            traces_to=parse_traces_to(traces_text) if traces_text else [],
        ))

    return requirements


def find_malformed_headers(content: str) -> list[str]:
    """Return ID-like '###' headers that the requirement parser did not accept."""
    accepted = {m.start() for m in REQ_HEADER.finditer(content)}
    return [
        m.group(0).strip()
        for m in ID_LIKE_HEADER.finditer(content)
        if m.start() not in accepted
    ]


def _raw_version(content: str) -> str | None:
    """
    Read 'version' exactly as written in the frontmatter text.
    YAML turns an unquoted `version: 1.10` into the float 1.1, which is wrong.
    """
    head = re.match(r"^---\s*\n([\s\S]*?)\n---", content)
    if not head:
        return None
    m = re.search(r"^version:\s*[\"']?([^\"'\n#]+?)[\"']?\s*(?:#.*)?$", head.group(1), re.MULTILINE)
    return m.group(1).strip() if m else None


class DocumentError(ValueError):
    """A document that cannot be placed in the traceability chain."""


def parse_document(filename: str, content: str) -> ParsedDocument:
    """Parse a markdown document with YAML frontmatter."""
    post = frontmatter.loads(content)
    meta = post.metadata
    body = post.content

    doc_type_raw = str(meta.get("doc_type", "")).strip().upper()
    if doc_type_raw not in DOC_TYPE_MAP:
        allowed = ", ".join(DOC_TYPE_MAP)
        raise DocumentError(
            f"unknown or missing doc_type '{meta.get('doc_type', '')}' (allowed: {allowed})"
        )

    links_to_raw = meta.get("links_to") or []
    if isinstance(links_to_raw, str):
        links_to = [l.strip() for l in links_to_raw.split(",") if l.strip()]
    else:
        links_to = [str(l).strip() for l in links_to_raw]

    version = meta.get("version", "1.0")
    if not isinstance(version, str):
        version = _raw_version(content) or str(version)

    return ParsedDocument(
        doc_id=str(meta.get("doc_id", filename.rsplit(".", 1)[0])).strip(),
        doc_type=DOC_TYPE_MAP[doc_type_raw],
        title=str(meta.get("title", "Untitled")),
        version=version,
        author=str(meta.get("author", "")),
        date=str(meta.get("date", "")),
        links_to=links_to,
        requirements=parse_requirements(body),
        raw_content=body,
        filename=filename,
    )


def parse_documents(
    files: list[tuple[str, str]],
) -> tuple[list[ParsedDocument], list[Issue]]:
    """
    Parse a list of (filename, content) tuples.
    Returns (documents, parse_issues). Problems are reported as issues
    so the user sees them in the report instead of only in the server log.
    """
    docs: list[ParsedDocument] = []
    issues: list[Issue] = []
    seen_ids: dict[str, str] = {}

    for filename, content in files:
        try:
            doc = parse_document(filename, content)
        except Exception as e:
            logger.warning("Failed to parse %s: %s", filename, e)
            issues.append(make_issue(
                IssueType.PARSE_ERROR, IssueSeverity.HIGH, filename,
                f"Could not parse {filename}",
                f"'{filename}' was skipped and is not part of the analysis: {e}",
            ))
            continue

        if doc.doc_id in seen_ids:
            issues.append(make_issue(
                IssueType.PARSE_ERROR, IssueSeverity.HIGH, doc.doc_id,
                f"Duplicate doc_id {doc.doc_id}",
                f"'{filename}' uses doc_id '{doc.doc_id}', already used by "
                f"'{seen_ids[doc.doc_id]}'. The second file was skipped.",
            ))
            continue
        seen_ids[doc.doc_id] = filename

        for header in find_malformed_headers(doc.raw_content):
            issues.append(make_issue(
                IssueType.PARSE_ERROR, IssueSeverity.MEDIUM, doc.doc_id,
                f"Unrecognised requirement header in {doc.doc_id}: '{header}'",
                f"'{header}' looks like a requirement but does not match the "
                f"expected format (e.g. '### R-01', '### F-02'). It was not parsed.",
            ))

        docs.append(doc)

    return docs, issues
