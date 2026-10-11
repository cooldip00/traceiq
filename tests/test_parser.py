from backend.models import IssueType, DocType
from backend.parser import parse_documents, parse_document
from tests.conftest import doc, req


def test_sample_docs_parse_cleanly(sample_docs):
    assert len(sample_docs) == 13
    assert sum(len(d.requirements) for d in sample_docs) == 32


def test_description_keeps_bold_text():
    body = (
        "### R-01\n**Title:** T\n**Description:** Send the email **within 2 minutes** of signup.\n"
        "**Traces To:** PRD-001/F-01\n"
    )
    d = parse_document("x.md", doc("BRD-001", "BRD", body)[1])
    r = d.requirements[0]
    assert r.description == "Send the email **within 2 minutes** of signup."
    assert r.traces_to == ["PRD-001/F-01"]


def test_description_stops_at_next_heading():
    body = "### R-01\n**Title:** T\n**Description:** Line one.\n\n## NOTE\n## not part of it\n"
    d = parse_document("x.md", doc("BRD-001", "BRD", body)[1])
    assert d.requirements[0].description == "Line one."


def test_unquoted_version_is_not_turned_into_float():
    content = "---\ndoc_id: BRD-001\ndoc_type: BRD\ntitle: T\nversion: 1.10\n---\n"
    assert parse_document("x.md", content).version == "1.10"


def test_unknown_doc_type_is_reported_not_defaulted_to_brd():
    docs, issues = parse_documents([doc("X-001", "BDR")])
    assert docs == []
    assert [i.issue_type for i in issues] == [IssueType.PARSE_ERROR]
    assert "BDR" in issues[0].description


def test_doc_type_is_case_insensitive():
    docs, _ = parse_documents([doc("US-001", "user_story")])
    assert docs[0].doc_type == DocType.USER_STORY


def test_malformed_requirement_header_is_reported():
    body = req("R-01") + "### Req-02\n**Title:** lost\n"
    docs, issues = parse_documents([doc("BRD-001", "BRD", body)])
    assert [r.req_id for r in docs[0].requirements] == ["R-01"]
    assert len(issues) == 1 and "Req-02" in issues[0].title


def test_duplicate_doc_id_is_reported_and_skipped():
    docs, issues = parse_documents([doc("BRD-001", "BRD"), doc("BRD-001", "BRD")])
    assert len(docs) == 1
    assert issues[0].issue_type == IssueType.PARSE_ERROR
