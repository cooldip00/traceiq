"""
Measure how well the local model judges requirement pairs.

Not part of the pytest suite (needs Ollama + qwen2.5:7b running):
    python -m tests.eval_ai              # both suites
    python -m tests.eval_ai sample       # the 34 real pairs from sample_docs/ (~5 min)
    python -m tests.eval_ai synthetic    # 12 short made-up pairs (~2 min)

Two suites, because they answer different questions:
  synthetic  short, clear-cut pairs: can the model spot a contradiction at all?
  sample     the real traced pairs the app sends: how noisy is it on real documents?

Labels are hand-written. Rubric:
  CONFLICT    a system that meets the downstream requirement breaks the upstream one
  PARTIAL     downstream implements the same capability but clearly drops a stated part
  CONSISTENT  anything else (adding detail, covering only its own slice, loose traces)

Scores reported:
  conflict precision / recall  CONFLICT is the positive class
  false alarms                 CONSISTENT pairs the model flagged (CONFLICT or PARTIAL)
  exact                        all three statuses matched
"""
import pathlib
import sys
import time
from collections import Counter

from backend.analyzer import analyze_pair, check_ollama, AnalysisError, MODEL_NAME
from backend.graph_builder import build_graph
from backend.models import Requirement
from backend.parser import parse_documents
from backend.validator import get_semantic_pairs

STATUSES = ("CONFLICT", "PARTIAL", "CONSISTENT")
SAMPLE_DIR = pathlib.Path(__file__).resolve().parent.parent / "sample_docs"


def R(req_id, title, description):
    return Requirement(req_id=req_id, title=title, description=description)


# ── Synthetic suite: (label, upstream (doc, req), downstream (doc, req)) ─────
SYNTHETIC = [
    # Contradictions
    ("CONFLICT",
     ("BRD-001", R("R-03", "Welcome email", "A welcome email must be sent within 2 minutes of enrolment.")),
     ("US-002", R("US-02", "Welcome email", "The welcome email is sent in a batch job within 60 minutes of enrolment."))),
    ("CONFLICT",
     ("BRD-001", R("R-05", "Redemption threshold", "Customers can redeem points once they have at least 100 points.")),
     ("PRD-001", R("F-02", "Redemption", "Redemption is enabled only for balances of 500 points or more."))),
    ("CONFLICT",
     ("BRD-001", R("R-02", "Channels", "Points are earned on web, mobile and in-store purchases.")),
     ("PRD-001", R("F-05", "Channels", "Points are earned on web purchases only; mobile and in-store are excluded."))),
    ("CONFLICT",
     ("CDD-001", R("C-02", "Data retention", "Transaction history is retained for 12 months.")),
     ("SDD-001", R("S-05", "Data retention", "Transaction records are deleted after 30 days."))),
    ("CONFLICT",
     ("BRD-001", R("R-08", "Point value", "Each point is worth $0.01 at checkout.")),
     ("US-005", R("US-05", "Point value", "At checkout, 1 point gives a $0.10 discount."))),
    ("CONFLICT",
     ("PRD-001", R("F-06", "Authentication", "Login must require multi-factor authentication for all users.")),
     ("US-006", R("US-06", "Quick login", "Users log in with email and password only; no second factor."))),
    # Consistent
    ("CONSISTENT",
     ("BRD-001", R("R-03", "Welcome email", "A welcome email must be sent within 2 minutes of enrolment.")),
     ("SDD-001", R("S-03", "Notification service", "The notification service sends the welcome email within 60 seconds."))),
    ("CONSISTENT",
     ("BRD-001", R("R-04", "Accrual", "Customers earn 1 point per $1 spent, credited within 24 hours.")),
     ("PRD-001", R("F-02", "Accrual", "Earn 1 point per dollar; points appear in the account the same day."))),
    ("CONSISTENT",
     ("BRD-001", R("R-06", "Dashboard", "Customers can see their tier, balance and 12 months of history.")),
     ("US-007", R("US-07", "Dashboard", "As a member I can view my tier, points balance and last 12 months of transactions."))),
    ("CONSISTENT",
     ("CDD-001", R("C-01", "Core service", "Loyalty logic runs as an independent microservice.")),
     ("ADD-001", R("COMP-01", "Deployment", "The loyalty service is deployed as its own Kubernetes deployment."))),
    ("CONSISTENT",
     ("BRD-001", R("R-05", "Redemption threshold", "Customers can redeem points once they have at least 100 points.")),
     ("US-008", R("US-08", "Redeem", "The redeem button is enabled when the balance is 100 points or more."))),
    ("CONSISTENT",
     ("PRD-001", R("F-06", "Authentication", "Login must require multi-factor authentication for all users.")),
     ("SDD-001", R("S-06", "Auth design", "Login uses password plus a TOTP code from an authenticator app."))),
]

# ── Sample suite: every traced pair in sample_docs/, keyed "UP -> DOWN" ──────
# Any pair not listed here is CONSISTENT. tests/test_eval_labels.py fails if a
# label names a pair that no longer exists, so the labels cannot silently rot.
SAMPLE_LABELS = {
    # The seeded conflict: 60 minutes breaks the 2-minute limit.
    "BRD-001/R-03 -> US-002/US-02": "CONFLICT",
    # Enrolment story keeps account settings but drops the checkout entry point.
    "PRD-001/F-01 -> US-001/US-01": "PARTIAL",
    # Dashboard covers cached offline view but not "accrual/redemption disabled offline".
    "BRD-001/R-07 -> PRD-001/F-03": "PARTIAL",
}
# Look-alikes deliberately labelled CONSISTENT (different events, not a clash):
#   PRD-001/F-02 -> US-003/US-03, EP-002/EP-02  tier update in 1 h vs points posted in 24 h
#   CDD-001/C-03 -> SDD-001/S-04                30 s confirmation vs 30 s payment timeout
#   BRD-001/R-03 -> CDD-001/C-05                event published in 5 s, inside the 2 min email limit


def pair_key(doc_a, req_a, doc_b, req_b):
    return f"{doc_a}/{req_a.req_id} -> {doc_b}/{req_b.req_id}"


def sample_pairs():
    files = [(p.name, p.read_text(encoding="utf-8")) for p in sorted(SAMPLE_DIR.rglob("*.md"))]
    docs, _ = parse_documents(files)
    return get_semantic_pairs(docs, build_graph(docs))


def sample_cases():
    return [(SAMPLE_LABELS.get(pair_key(*p), "CONSISTENT"), p[:2], p[2:]) for p in sample_pairs()]


def run_suite(name, cases):
    print(f"\n── {name}: {len(cases)} pairs ──")
    confusion, failed, start = Counter(), 0, time.time()
    for label, (doc_a, req_a), (doc_b, req_b) in cases:
        key = pair_key(doc_a, req_a, doc_b, req_b)
        try:
            status, _ = analyze_pair(doc_a, req_a, doc_b, req_b)
        except AnalysisError as e:
            failed += 1
            print(f"  FAILED expected {label:10} {key}: {e}")
            continue
        confusion[label, status] += 1
        if status != label:
            print(f"  XX  expected {label:10} got {status:10} {key}")

    def count(label=None, status=None):
        return sum(n for (l, s), n in confusion.items()
                   if (label is None or l == label) and (status is None or s == status))

    tp = count("CONFLICT", "CONFLICT")
    precision = tp / count(status="CONFLICT") if count(status="CONFLICT") else 0.0
    recall = tp / count(label="CONFLICT") if count(label="CONFLICT") else 0.0
    alarms = count("CONSISTENT") - count("CONSISTENT", "CONSISTENT")
    exact = sum(confusion[s, s] for s in STATUSES)

    print("  label/got   " + "".join(f"{s:>12}" for s in STATUSES))
    for label in STATUSES:
        print(f"  {label:12}" + "".join(f"{confusion[label, s]:>12}" for s in STATUSES))
    print(f"  Conflict precision={precision:.2f} recall={recall:.2f} | "
          f"false alarms={alarms}/{count('CONSISTENT')} | exact={exact}/{len(cases) - failed} | "
          f"failed={failed} | {time.time() - start:.0f}s")


def main():
    ok, msg = check_ollama()
    if not ok:
        print(f"Cannot run eval: {msg}")
        return
    suite = sys.argv[1] if len(sys.argv) > 1 else "all"
    print(f"Model: {MODEL_NAME}")
    if suite in ("all", "synthetic"):
        run_suite("synthetic", SYNTHETIC)
    if suite in ("all", "sample"):
        run_suite("sample docs", sample_cases())


if __name__ == "__main__":
    main()
