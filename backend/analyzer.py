import json
import uuid
import requests
from backend.models import Issue, IssueType, IssueSeverity, Requirement

OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL_NAME = "qwen2.5:7b"

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
  "confidence": 0.0,
  "reason": "one sentence explanation",
  "severity": "HIGH" | "MEDIUM" | "LOW"
}"""


def _call_qwen(user_message: str) -> dict:
    """Call the local Qwen model via Ollama chat endpoint."""
    payload = {
        "model": MODEL_NAME,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        "stream": False,
        "options": {
            "temperature": 0.1,
            "num_predict": 200,
        },
    }
    response = requests.post(OLLAMA_URL, json=payload, timeout=120)
    response.raise_for_status()
    raw = response.json()["message"]["content"].strip()

    # Robustly extract JSON even if model adds surrounding text
    start = raw.find("{")
    end = raw.rfind("}") + 1
    if start != -1 and end > start:
        raw = raw[start:end]

    return json.loads(raw)


def analyze_pair(
    doc_id_a: str,
    req_a: Requirement,
    doc_id_b: str,
    req_b: Requirement,
) -> Issue | None:
    """
    Ask the AI to compare two requirements for semantic consistency.
    Returns an Issue if a conflict or partial match is found, else None.
    """
    user_message = f"""Compare these two requirements for semantic consistency:

Document 1 ({doc_id_a}, {req_a.req_id}):
Title: {req_a.title}
Description: {req_a.description}
{"Acceptance Criteria: " + "; ".join(f"{ac.ac_id}: {ac.description}" for ac in req_a.acceptance_criteria) if req_a.acceptance_criteria else ""}

Document 2 ({doc_id_b}, {req_b.req_id}):
Title: {req_b.title}
Description: {req_b.description}
{"Acceptance Criteria: " + "; ".join(f"{ac.ac_id}: {ac.description}" for ac in req_b.acceptance_criteria) if req_b.acceptance_criteria else ""}

Return JSON only."""

    try:
        result = _call_qwen(user_message)
        status = result.get("status", "CONSISTENT")
        confidence = float(result.get("confidence", 0.5))
        reason = result.get("reason", "")
        severity_raw = result.get("severity", "LOW")

        if status == "CONSISTENT":
            return None

        severity_map = {
            "HIGH": IssueSeverity.HIGH,
            "MEDIUM": IssueSeverity.MEDIUM,
            "LOW": IssueSeverity.LOW,
        }
        severity = severity_map.get(severity_raw, IssueSeverity.MEDIUM)

        issue_type = (
            IssueType.SEMANTIC_CONFLICT
            if status == "CONFLICT"
            else IssueType.COVERAGE_GAP
        )

        return Issue(
            issue_id=str(uuid.uuid4())[:8],
            issue_type=issue_type,
            severity=severity,
            source_doc=doc_id_a,
            source_req=req_a.req_id,
            target_doc=doc_id_b,
            target_req=req_b.req_id,
            title=f"{'Conflict' if status == 'CONFLICT' else 'Partial mismatch'}: "
                  f"{doc_id_a}/{req_a.req_id} vs {doc_id_b}/{req_b.req_id}",
            description=(
                f"Semantic analysis found a {status.lower()} between "
                f"'{req_a.title}' ({doc_id_a}) and '{req_b.title}' ({doc_id_b})."
            ),
            ai_reason=reason,
            confidence=confidence,
        )

    except Exception as e:
        print(f"Warning: AI analysis failed for pair "
              f"{doc_id_a}/{req_a.req_id} vs {doc_id_b}/{req_b.req_id}: {e}")
        return None


def analyze_all_pairs(
    pairs: list[tuple],
    progress_callback=None,
) -> list[Issue]:
    """
    Run semantic analysis on all requirement pairs.
    Optionally calls progress_callback(current, total, pair_label) per pair.
    """
    issues = []
    total = len(pairs)

    for i, (doc_id_a, req_a, doc_id_b, req_b) in enumerate(pairs):
        if progress_callback:
            progress_callback(
                i + 1,
                total,
                f"{doc_id_a}/{req_a.req_id} ↔ {doc_id_b}/{req_b.req_id}",
            )

        issue = analyze_pair(doc_id_a, req_a, doc_id_b, req_b)
        if issue:
            issues.append(issue)

    return issues
