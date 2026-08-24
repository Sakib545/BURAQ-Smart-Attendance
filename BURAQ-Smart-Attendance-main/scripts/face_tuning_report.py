"""Turn logged face events into threshold guidance.

    python scripts/face_tuning_report.py [--days 30]

Reads `face_events` and reports where accepted and rejected attempts actually
sit, so FACE_MATCH_THRESHOLD, FACE_QUALITY_MIN, FACE_MARGIN_MIN and the
liveness cut-offs can be set from evidence instead of taste.

Run it against the production database (set DATABASE_URL) after a couple of
weeks of real traffic. Before that there is nothing meaningful to read.
"""

from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.database import get_db, init_db  # noqa: E402


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))
    return ordered[index]


def describe(name: str, values: list[float], unit: str = "") -> None:
    if not values:
        print(f"  {name:<22} no data")
        return
    print(f"  {name:<22} n={len(values):<5} min={min(values):.3f}{unit}  "
          f"p05={percentile(values, .05):.3f}  median={statistics.median(values):.3f}  "
          f"p95={percentile(values, .95):.3f}  max={max(values):.3f}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=30)
    args = parser.parse_args()

    init_db()
    with get_db() as c:
        rows = c.execute(
            "SELECT stage, decision, reason, match_score, impostor_score, margin, quality, "
            "liveness_score, liveness_verdict, elapsed_ms FROM face_events "
            "WHERE created_at >= CURRENT_TIMESTAMP - INTERVAL '%d days'" % args.days
            if settings.is_postgres else
            "SELECT stage, decision, reason, match_score, impostor_score, margin, quality, "
            "liveness_score, liveness_verdict, elapsed_ms FROM face_events "
            "WHERE created_at >= datetime('now', '-%d days')" % args.days
        ).fetchall()

    if not rows:
        print(f"No face events in the last {args.days} days. Nothing to tune yet.")
        return 0

    verify = [r for r in rows if r["stage"] == "verify"]
    accepted = [r for r in verify if r["decision"] == "accepted"]
    rejected = [r for r in verify if r["decision"] == "rejected"]

    print(f"\nFace AI report — last {args.days} days")
    print("=" * 68)
    print(f"attempts {len(rows)}   verify {len(verify)}   accepted {len(accepted)}   rejected {len(rejected)}")
    if verify:
        print(f"acceptance rate {len(accepted) / len(verify) * 100:.1f}%")

    print("\nRejection reasons")
    reasons: dict[str, int] = {}
    for row in rejected:
        reasons[str(row["reason"] or "unknown")] = reasons.get(str(row["reason"] or "unknown"), 0) + 1
    for reason, count in sorted(reasons.items(), key=lambda kv: -kv[1]):
        print(f"  {reason:<34} {count:>5}  ({count / max(len(rejected), 1) * 100:.1f}%)")

    print("\nAccepted attempts")
    describe("match score", [float(r["match_score"]) for r in accepted])
    describe("margin over next", [float(r["margin"]) for r in accepted])
    describe("quality", [float(r["quality"]) for r in accepted])
    describe("liveness score", [float(r["liveness_score"]) for r in accepted])
    describe("elapsed ms", [float(r["elapsed_ms"]) for r in accepted])

    below = [r for r in rejected if r["reason"] == "below_threshold"]
    print("\nRejected for low match")
    describe("match score", [float(r["match_score"]) for r in below])

    print("\nCurrent settings")
    print(f"  FACE_MATCH_THRESHOLD   {settings.face_match_threshold}")
    print(f"  FACE_MARGIN_MIN        {settings.face_margin_min}")
    print(f"  FACE_QUALITY_MIN       {settings.face_quality_min}")
    print(f"  FACE_ENROLL_QUALITY_MIN {settings.face_enroll_quality_min}")

    print("\nSuggestions")
    accepted_scores = [float(r["match_score"]) for r in accepted]
    if accepted_scores:
        floor = percentile(accepted_scores, .02)
        print(f"  98% of accepted matches score above {floor:.3f}.")
        if floor - settings.face_match_threshold > 0.12:
            print(f"  -> Threshold could rise towards {floor - 0.08:.2f} without turning away "
                  "anyone who is currently getting in.")
        elif floor < settings.face_match_threshold + 0.02:
            print("  -> Genuine matches are landing close to the threshold. Expect false "
                  "rejections; check enrolment quality before lowering it.")

    near_miss = [float(r["match_score"]) for r in below if float(r["match_score"]) > settings.face_match_threshold - 0.08]
    if near_miss:
        print(f"  {len(near_miss)} rejections were within 0.08 of the threshold — likely genuine "
              "employees. Re-enrol these people rather than lowering the threshold.")

    live_scores = [float(r["liveness_score"]) for r in accepted]
    if live_scores:
        print(f"  Liveness on accepted attempts: p95 = {percentile(live_scores, .95):.2f}. "
              "Set LIVENESS_REJECT_AT comfortably above that.")

    quality_rejects = reasons.get("quality", 0)
    if quality_rejects > len(rejected) * 0.3:
        print("  Most rejections are quality, not identity. That is a camera and lighting "
              "problem at the gate, not a threshold problem.")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
