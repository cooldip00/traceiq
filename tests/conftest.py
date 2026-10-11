from pathlib import Path

import pytest

from backend.parser import parse_documents
from backend.graph_builder import build_graph

SAMPLE_DIR = Path(__file__).parent.parent / "sample_docs"


def doc(doc_id, doc_type, body="", version="1.0", links=()):
    """Build a minimal markdown document for tests."""
    links_yaml = "".join(f"\n  - {l}" for l in links) if links else " []"
    return (
        f"{doc_id}.md",
        f"---\ndoc_id: {doc_id}\ndoc_type: {doc_type}\ntitle: {doc_id}\n"
        f'version: "{version}"\nlinks_to:{links_yaml}\n---\n\n{body}',
    )


def req(req_id, traces=(), ac=True, description="Some requirement."):
    lines = [f"### {req_id}", f"**Title:** {req_id} title", f"**Description:** {description}"]
    if ac:
        lines += ["**Acceptance Criteria:**", "- AC-01: It works"]
    if traces:
        lines.append("**Traces To:** " + ", ".join(traces))
    return "\n".join(lines) + "\n\n"


@pytest.fixture(scope="session")
def sample_files():
    return [(p.name, p.read_text(encoding="utf-8")) for p in sorted(SAMPLE_DIR.glob("*/*.md"))]


@pytest.fixture(scope="session")
def sample_docs(sample_files):
    docs, issues = parse_documents(sample_files)
    assert issues == []
    return docs


@pytest.fixture(scope="session")
def sample_graph(sample_docs):
    return build_graph(sample_docs)
