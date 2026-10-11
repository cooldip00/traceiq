"""
Measure how well the local model detects contradictions.

Not part of the pytest suite (needs Ollama + qwen2.5:7b running):
    python -m tests.eval_ai

Each case is a hand-labelled requirement pair. CONFLICT is the positive class:
  precision = of the pairs the model called CONFLICT, how many really are
  recall    = of the real conflicts, how many the model caught
PARTIAL predictions are counted as "not CONFLICT".
"""
import time

from backend.analyzer import analyze_pair, check_ollama, AnalysisError, MODEL_NAME
from backend.models import Requirement


def R(req_id, title, description):
    return Requirement(req_id=req_id, title=title, description=description)


# (label, upstream (doc, req), downstream (doc, req))
CASES = [
    # ── Contradictions ──────────────────────────────────────────────────────
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
    # ── Consistent ──────────────────────────────────────────────────────────
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


def main():
    ok, msg = check_ollama()
    if not ok:
        print(f"Cannot run eval: {msg}")
        return

    tp = fp = fn = tn = failed = 0
    start = time.time()
    for label, (doc_a, req_a), (doc_b, req_b) in CASES:
        try:
            status, issue = analyze_pair(doc_a, req_a, doc_b, req_b)
        except AnalysisError as e:
            failed += 1
            print(f"  FAILED    expected {label:10} {req_a.req_id} vs {req_b.req_id}: {e}")
            continue
        predicted_conflict, real_conflict = status == "CONFLICT", label == "CONFLICT"
        tp += predicted_conflict and real_conflict
        fp += predicted_conflict and not real_conflict
        fn += real_conflict and not predicted_conflict
        tn += not predicted_conflict and not real_conflict
        mark = "ok " if predicted_conflict == real_conflict else "XX "
        print(f"  {mark} expected {label:10} got {status:10} {doc_a}/{req_a.req_id} vs {doc_b}/{req_b.req_id}")

    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    print(f"\nModel: {MODEL_NAME} | cases: {len(CASES)} | failed calls: {failed} | {time.time() - start:.0f}s")
    print(f"TP={tp} FP={fp} FN={fn} TN={tn}")
    print(f"Precision={precision:.2f} Recall={recall:.2f}")


if __name__ == "__main__":
    main()
