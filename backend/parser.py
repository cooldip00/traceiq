import re
import frontmatter
from backend.models import (
    ParsedDocument, Requirement, AcceptanceCriterion, DocType
)


DOC_TYPE_MAP = {
    "LVC": DocType.LVC,
    "TIA": DocType.TIA,
    "BRD": DocType.BRD,
    "CDD": DocType.CDD,
    "PRD": DocType.PRD,
    "SDD": DocType.SDD,
    "ADD": DocType.ADD,
    "EPIC": DocType.EPIC,
    "USER_STORY": DocType.USER_STORY,
}


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

    # Split on requirement headers (### R-xx, ### F-xx, ### C-xx, ### S-xx, ### EP-xx, ### US-xx, ### AC-xx)
    req_pattern = re.compile(
        r"^###\s+((?:R|F|C|S|T|EP|US|COMP|NFR)-\d+)",
        re.MULTILINE
    )
    splits = req_pattern.split(content)

    # splits = [before_first, id1, block1, id2, block2, ...]
    if len(splits) < 3:
        return requirements

    # Process pairs of (req_id, block)
    for i in range(1, len(splits) - 1, 2):
        req_id = splits[i].strip()
        block = splits[i + 1]

        title = ""
        priority = None
        status = None
        description = ""
        acceptance_criteria = []
        traces_to = []

        title_match = re.search(r"\*\*Title:\*\*\s*(.+)", block)
        if title_match:
            title = title_match.group(1).strip()

        priority_match = re.search(r"\*\*Priority:\*\*\s*(.+)", block)
        if priority_match:
            priority = priority_match.group(1).strip()

        status_match = re.search(r"\*\*Status:\*\*\s*(.+)", block)
        if status_match:
            status = status_match.group(1).strip()

        desc_match = re.search(
            r"\*\*Description:\*\*\s*([\s\S]+?)(?=\*\*|$)", block
        )
        if desc_match:
            description = desc_match.group(1).strip()

        ac_match = re.search(
            r"\*\*Acceptance Criteria:\*\*\s*([\s\S]+?)(?=\*\*|$)", block
        )
        if ac_match:
            acceptance_criteria = parse_acceptance_criteria(ac_match.group(1))

        traces_match = re.search(r"\*\*Traces To:\*\*\s*(.+)", block)
        if traces_match:
            traces_to = parse_traces_to(traces_match.group(1))

        requirements.append(Requirement(
            req_id=req_id,
            title=title or req_id,
            priority=priority,
            status=status,
            description=description,
            acceptance_criteria=acceptance_criteria,
            traces_to=traces_to,
        ))

    return requirements


def parse_document(filename: str, content: str) -> ParsedDocument:
    """Parse a markdown document with YAML frontmatter."""
    post = frontmatter.loads(content)
    meta = post.metadata
    body = post.content

    doc_type_raw = str(meta.get("doc_type", "BRD")).upper()
    doc_type = DOC_TYPE_MAP.get(doc_type_raw, DocType.BRD)

    links_to_raw = meta.get("links_to", [])
    if isinstance(links_to_raw, str):
        links_to = [l.strip() for l in links_to_raw.split(",") if l.strip()]
    else:
        links_to = list(links_to_raw)

    requirements = parse_requirements(body)

    return ParsedDocument(
        doc_id=str(meta.get("doc_id", filename.replace(".md", ""))),
        doc_type=doc_type,
        title=str(meta.get("title", "Untitled")),
        version=str(meta.get("version", "1.0")),
        author=str(meta.get("author", "")),
        date=str(meta.get("date", "")),
        links_to=links_to,
        requirements=requirements,
        raw_content=body,
        filename=filename,
    )


def parse_documents(files: list[tuple[str, str]]) -> list[ParsedDocument]:
    """Parse a list of (filename, content) tuples."""
    docs = []
    for filename, content in files:
        try:
            doc = parse_document(filename, content)
            docs.append(doc)
        except Exception as e:
            print(f"Warning: Failed to parse {filename}: {e}")
    return docs
