import json
import logging
from dataclasses import dataclass, field

import requests
from backend.models import Issue, IssueType, IssueSeverity, Requirement, make_issue

logger = logging.getLogger(__name__)

OLLAMA_BASE = "http://localhost:11434"
OLLAMA_URL = f"{OLLAMA_BASE}/api/chat"
MODEL_NAME = "qwen2.5:7b"
MAX_ATTEMPTS = 2  # one retry per pair

SYSTEM_PROMPT = """You are a requirements traceability analyst. Your only job is to
determine whether two requirements from different product documents are semantically
consistent with each other.

Rules:
- Look for contradictions in timing, scope, constraints, or specifications.
- A "CONFLICT" means the two requirements directly contradict each other.
- A "PARTIAL" means they are related but one is more restrictive or broader than the other.
- "CONSISTENT" means they agree and do not contradict.

You MUST return ONLY valid JSON. No preamble, no explanation outside the JSON.

Return this exact format:
{
  "status": "CONFLICT" | "CONSISTENT" | "PARTIAL",
  "confidence": <number between 0 and 1>,
  "reason": "one sentence explanation",
  "severity": "HIGH" | "MEDIUM" | "LOW"
}"""

VALID_STATUSES = {"CONFLICT", "CONSISTENT", "PARTIAL"}
SEVERITY_MAP = {s.value: s for s in IssueSeverity}
WORD_CONFIDENCE = {"HIGH": 0.9, "MEDIUM": 0.6, "LOW": 0.3}


class AnalysisError(Exception):
    """The model could not produce a usable verdict for a pair."""


@dataclass
class SemanticResult:
    issues: list[Issue] = field(default_factory=list)
    total: int = 0
    consistent: int = 0
    failed: int = 0


def check_ollama() -> tuple[bool, str]:
    """Is Ollama running and is the model pulled? Returns (ok, message)."""
    try:
        resp = requests.get(f"{OLLAMA_BASE}/api/tags", timeout=5)
        resp.raise_for_status()
    except requests.RequestException as e:
        return False, f"Ollama is not reachable at {OLLAMA_BASE} ({type(e).__name__})"
    names = {m.get("name", "") for m in resp.json().get("models", [])}
    if MODEL_NAME not in names:
        return False, f"Model '{MODEL_NAME}' is not pulled. Run: ollama pull {MODEL_NAME}"
    return True, "ok"


def extract_json(raw: str) -> dict:
    """Return the first JSON object in the model's reply (ignores fences/chatter)."""
    start = raw.find("{")
    if start == -1:
        raise AnalysisError(f"no JSON object in reply: {raw[:80]!r}")
    try:
        obj, _ = json.JSONDecoder().raw_decode(raw[start:])
    except json.JSONDecodeError as e:
        raise AnalysisError(f"invalid JSON in reply: {e}") from e
    if not isinstance(obj, dict):
        raise AnalysisError("reply JSON is not an object")
    return obj


def parse_confidence(value) -> float | None:
    """Accept 0.9, "0.9", "90%", 90 or "high"; clamp to 0..1; None if unusable."""
    if isinstance(value, str):
        text = value.strip().upper()
        if text in WORD_CONFIDENCE:
            return WORD_CONFIDENCE[text]
        is_percent = text.endswith("%")
        try:
            value = float(text.rstrip("%"))
        except ValueError:
            return None
        if is_percent:
            value /= 100
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value > 1:
        value /= 100  # "90" means 90%
    return max(0.0, min(1.0, float(value)))


def _call_qwen(user_message: str) -> dict:
    """Call the local Qwen model via Ollama chat endpoint."""
    payload = {
        "model": MODEL_NAME,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        "stream": False,
        "format": "json",  # Ollama constrains the output to valid JSON
        "options": {
            "temperature": 0.1,
            "num_predict": 200,
        },
    }
    response = requests.post(OLLAMA_URL, json=payload, timeout=120)
    response.raise_for_status()
    return extract_json(response.json()["message"]["content"])


def _format_req(doc_id: str, req: Requirement) -> str:
    lines = [f"({doc_id}, {req.req_id})", f"Title: {req.title}", f"Description: {req.description}"]
    if req.acceptance_criteria:
        lines.append("Acceptance Criteria: " + "; ".join(
            f"{ac.ac_id}: {ac.description}" for ac in req.acceptance_criteria
        ))
    return "\n".join(lines)


def analyze_pair(
    doc_id_a: str,
    req_a: Requirement,
    doc_id_b: str,
    req_b: Requirement,
) -> tuple[str, Issue | None]:
    """
    Ask the AI to compare two requirements for semantic consistency.
    Returns (status, issue): issue is None when the pair is CONSISTENT.
    Raises AnalysisError if no usable verdict was obtained after retrying.
    """
    user_message = (
        "Compare these two requirements for semantic consistency:\n\n"
        f"Document 1 (upstream) {_format_req(doc_id_a, req_a)}\n\n"
        f"Document 2 (downstream) {_format_req(doc_id_b, req_b)}\n\n"
        "Return JSON only."
    )

    last_error = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            result = _call_qwen(user_message)
            status = str(result.get("status", "")).strip().upper()
            if status not in VALID_STATUSES:
                raise AnalysisError(f"unexpected status {result.get('status')!r}")
            break
        except (requests.RequestException, AnalysisError, KeyError, ValueError) as e:
            last_error = e
            logger.warning("AI call %d/%d failed for %s/%s vs %s/%s: %s", attempt,
                           MAX_ATTEMPTS, doc_id_a, req_a.req_id, doc_id_b, req_b.req_id, e)
    else:
        raise AnalysisError(str(last_error))

    if status == "CONSISTENT":
        return status, None

    if status == "CONFLICT":
        # A direct contradiction is always a must-fix item
        issue_type, severity, label = IssueType.SEMANTIC_CONFLICT, IssueSeverity.HIGH, "Conflict"
    else:
        issue_type = IssueType.PARTIAL_MATCH
        severity = SEVERITY_MAP.get(str(result.get("severity", "")).strip().upper(), IssueSeverity.MEDIUM)
        label = "Partial mismatch"

    return status, make_issue(
        issue_type,
        severity,
        doc_id_a,
        f"{label}: {doc_id_a}/{req_a.req_id} vs {doc_id_b}/{req_b.req_id}",
        f"Semantic analysis found a {status.lower()} between "
        f"'{req_a.title}' ({doc_id_a}) and '{req_b.title}' ({doc_id_b}).",
        source_req=req_a.req_id,
        target_doc=doc_id_b,
        target_req=req_b.req_id,
        ai_reason=str(result.get("reason", "")),
        confidence=parse_confidence(result.get("confidence")),
    )


def analyze_all_pairs(
    pairs: list[tuple],
    progress_callback=None,
) -> SemanticResult:
    """
    Run semantic analysis on all requirement pairs.
    Optionally calls progress_callback(current, total, pair_label) per pair.
    Failed pairs are counted, never silently treated as consistent.
    """
    result = SemanticResult(total=len(pairs))

    for i, (doc_id_a, req_a, doc_id_b, req_b) in enumerate(pairs):
        if progress_callback:
            progress_callback(
                i + 1,
                result.total,
                f"{doc_id_a}/{req_a.req_id} ↔ {doc_id_b}/{req_b.req_id}",
            )
        try:
            status, issue = analyze_pair(doc_id_a, req_a, doc_id_b, req_b)
        except AnalysisError:
            result.failed += 1
            continue
        if issue:
            result.issues.append(issue)
        elif status == "CONSISTENT":
            result.consistent += 1

    return result
