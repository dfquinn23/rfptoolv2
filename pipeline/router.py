"""
pipeline/router.py

Receives MatchResult objects from the matcher and sorts them into three
routing tiers based on confidence score:

    AUTO   (≥ 80)  — High confidence. Safe to insert verbatim.
    REVIEW (50-79) — Moderate confidence. Flag for human spot-check.
    HUMAN  (< 50)  — Low confidence. Escalate for full manual handling.

The router does two things:
  1. route()           — Pure sort: takes a list of MatchResults, returns a RouterOutput.
  2. route_rfp()       — Full pipeline entry point: questions in → RouterOutput out.

It also persists each session to a JSON staging file so the Streamlit UI
can load the results without re-running the matcher.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List

from pipeline.matcher import MatchResult, match_questions

# ---------------------------------------------------------------------------
# Thresholds — keep in sync with matcher.py
# ---------------------------------------------------------------------------
AUTO_THRESHOLD   = 80   # confidence ≥ 80 → AUTO
REVIEW_THRESHOLD = 50   # confidence 50–79 → REVIEW
                        # confidence < 50 → HUMAN

# Where to write the staging file that the UI reads
DEFAULT_SESSION_PATH = "pipeline/routing_session.json"


# ---------------------------------------------------------------------------
# RouterOutput
# ---------------------------------------------------------------------------

@dataclass
class RouterOutput:
    """Holds all MatchResults sorted into their three routing buckets."""

    auto:   List[MatchResult] = field(default_factory=list)
    review: List[MatchResult] = field(default_factory=list)
    human:  List[MatchResult] = field(default_factory=list)
    _all:   List[MatchResult] = field(default_factory=list)  # original document order

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    @property
    def total(self) -> int:
        return len(self.auto) + len(self.review) + len(self.human)

    def summary(self) -> dict:
        """Return a plain-dict summary suitable for printing or logging."""
        return {
            "total":  self.total,
            "auto":   len(self.auto),
            "review": len(self.review),
            "human":  len(self.human),
        }

    def print_summary(self) -> None:
        s = self.summary()
        print(f"\n--- Routing Summary ---")
        print(f"  Total questions : {s['total']}")
        print(f"  ✅ AUTO         : {s['auto']}")
        print(f"  🔶 REVIEW        : {s['review']}")
        print(f"  🔴 HUMAN         : {s['human']}")


# ---------------------------------------------------------------------------
# Core routing logic
# ---------------------------------------------------------------------------

def route(results: List[MatchResult]) -> RouterOutput:
    """
    Sort a list of MatchResults into routing buckets.

    The routing field on each MatchResult is set by the matcher, so this
    function is a pure sort — no threshold logic lives here.  That keeps
    thresholds in one place (matcher.py / _route()) and makes it easy to
    unit-test routing behaviour independently of the matcher.
    """
    output = RouterOutput()
    for result in results:
        output._all.append(result)  # preserve document order
        if result.routing == "AUTO":
            output.auto.append(result)
        elif result.routing == "REVIEW":
            output.review.append(result)
        else:
            output.human.append(result)
    return output


def route_rfp(
    questions: List[str],
    verbose: bool = False,
) -> RouterOutput:
    """
    Full pipeline entry point.

    Args:
        questions: List of question strings extracted from an incoming RFP.
        verbose:   Pass through to the matcher for per-candidate scoring output.

    Returns:
        RouterOutput with all questions sorted into routing tiers.
    """
    if not questions:
        print("[router] No questions provided — nothing to route.")
        return RouterOutput()

    print(f"[router] Matching {len(questions)} question(s)...")
    results = match_questions(questions, verbose=verbose)
    output  = route(results)
    output.print_summary()
    return output


# ---------------------------------------------------------------------------
# Session persistence (staging file for the UI)
# ---------------------------------------------------------------------------

def _result_to_dict(r: MatchResult) -> dict:
    """Serialize a MatchResult to a plain dict for JSON storage."""
    return {
        "question":         r.question,
        "answer":           r.answer,
        "matched_question": r.matched_question,
        "source":           r.source,
        "confidence":       r.confidence,
        "routing":          r.routing,
        "reason":           r.reason,
        "vector_score":     round(r.vector_score, 4),
        "date":             r.date,
        "candidates":       r.candidates,
    }


def save_session(
    output: RouterOutput,
    path: str = DEFAULT_SESSION_PATH,
) -> str:
    """
    Persist a RouterOutput to a JSON staging file.

    The Streamlit UI reads this file to display results without re-running
    the matcher.  The file is overwritten on each new RFP run.

    Returns:
        The path the file was written to.
    """
    # Build per-tier lists with doc_order injected from _all
    order_map = {id(r): i for i, r in enumerate(output._all)}

    def _with_order(r: MatchResult) -> dict:
        d = _result_to_dict(r)
        d["doc_order"] = order_map.get(id(r), 0)
        return d

    session = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "summary":   output.summary(),
        "auto":      [_with_order(r) for r in output.auto],
        "review":    [_with_order(r) for r in output.review],
        "human":     [_with_order(r) for r in output.human],
    }

    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)

    with open(dest, "w", encoding="utf-8") as f:
        json.dump(session, f, indent=2, ensure_ascii=False)

    print(f"[router] Session saved → {dest}")
    return str(dest)


def load_session(path: str = DEFAULT_SESSION_PATH) -> dict | None:
    """
    Load a previously saved routing session from disk.

    Returns the raw dict (keyed by 'auto', 'review', 'human', 'summary',
    'timestamp'), or None if the file doesn't exist yet.
    """
    dest = Path(path)
    if not dest.exists():
        return None

    with open(dest, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# CLI — quick smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    test_questions = [
        "Describe your firm's approach to cybersecurity.",
        "What is your disaster recovery plan?",
        "How does your team handle conflicts of interest?",
    ]

    verbose = "--verbose" in sys.argv or "-v" in sys.argv

    output = route_rfp(test_questions, verbose=verbose)

    print("\n--- AUTO ---")
    for r in output.auto:
        print(f"  [{r.confidence}] {r.question[:80]}")
        print(f"        → {r.answer[:100]}...")

    print("\n--- REVIEW ---")
    for r in output.review:
        print(f"  [{r.confidence}] {r.question[:80]}")
        print(f"        → {r.answer[:100]}...")

    print("\n--- HUMAN ---")
    for r in output.human:
        print(f"  [{r.confidence}] {r.question[:80]}")

    save_session(output)
