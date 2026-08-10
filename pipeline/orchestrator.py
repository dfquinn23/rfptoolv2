"""
pipeline/orchestrator.py

Orchestrator — final stage of the merge/export pipeline. Wires together
everything built so far behind a single call:

    original document bytes + approved Q&A pairs
        → Locator (via Inserter, which runs it internally when no
          pre-computed location map is supplied)
        → Answer Inserter (writes verified answers into the original doc)
        → Addendum Builder (everything the Inserter couldn't verify)
        → merged .docx + addendum .docx, both as in-memory bytes ready
          for a Streamlit download_button

No new logic lives here — this module only sequences the three existing,
independently-tested agents and packages their output for the UI. Kept
deliberately thin on purpose: if something goes wrong, the bug is almost
certainly in the Locator, Inserter, or Addendum Builder, each of which can
still be tested standalone via their own CLI entry points.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from pipeline.inserter import insert_answers
from pipeline.addendum import build_addendum


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class OrchestratorResult:
    merged_docx_bytes:   bytes
    merged_filename:      str
    addendum_docx_bytes: bytes
    addendum_filename:    str
    inserted_count:        int
    skipped_count:         int
    addendum_counts:      dict = field(default_factory=dict)  # group -> count

    @property
    def total_questions(self) -> int:
        return self.inserted_count + self.skipped_count

    @property
    def has_addendum_items(self) -> bool:
        return self.skipped_count > 0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_merge(
    original_docx_bytes: bytes,
    source_filename: str,
    approved_answers: list[dict],
    location_map: list[dict] | None = None,
    verbose: bool = False,
) -> OrchestratorResult:
    """
    Run the full merge pipeline end to end.

    Args:
        original_docx_bytes: Raw bytes of the ORIGINAL incoming RFP,
                              already converted to .docx if it started as
                              .doc (Pipeline Stage 1 handles that
                              conversion — this expects the post-conversion
                              bytes).
        source_filename:      Original filename (for display and for
                              deriving output filenames). Extension is
                              normalized to .docx regardless of input.
        approved_answers:     List of dicts with "question" and "answer"
                              keys — the approved subset of
                              st.session_state["answers"].values() from the
                              Review page.
        location_map:          Pre-computed output of locate_answer_slots(),
                              e.g. already produced and shown to the user
                              during a "Verify RFP Structure" step. When
                              provided, the Orchestrator will NOT re-run the
                              Locator — the location map the user actually
                              saw and confirmed is the one that gets used.
                              If None, the Inserter computes it fresh (one
                              extra LLM pass).
        verbose:               Print per-stage progress (passed through to
                              the Locator and Inserter).

    Returns:
        OrchestratorResult — both output documents as in-memory bytes,
        ready to hand straight to st.download_button, plus summary counts
        for display.
    """
    stem = Path(source_filename).stem or "rfp"

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_original = Path(tmpdir) / f"{stem}_original.docx"
        tmp_original.write_bytes(original_docx_bytes)

        merged_path   = Path(tmpdir) / f"{stem}_merged.docx"
        addendum_path = Path(tmpdir) / f"{stem}_addendum.docx"

        print(f"[orchestrator] Merging {len(approved_answers)} approved answer(s) "
              f"into '{source_filename}'...")

        insert_result = insert_answers(
            str(tmp_original),
            approved_answers,
            output_path=str(merged_path),
            location_map=location_map,  # None => Inserter computes it fresh
            verbose=verbose,
        )

        addendum_result = build_addendum(
            insert_result.skipped,
            str(addendum_path),
            source_filename=source_filename,
        )

        result = OrchestratorResult(
            merged_docx_bytes=merged_path.read_bytes(),
            merged_filename=f"{stem}_merged.docx",
            addendum_docx_bytes=addendum_path.read_bytes(),
            addendum_filename=f"{stem}_addendum.docx",
            inserted_count=insert_result.inserted_count,
            skipped_count=insert_result.skipped_count,
            addendum_counts=addendum_result.counts,
        )

    print(f"[orchestrator] Done. {result.inserted_count} inserted into the "
          f"merged document, {result.skipped_count} in the addendum.")

    return result


# ---------------------------------------------------------------------------
# CLI — smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    import json

    if len(sys.argv) < 3:
        print("Usage: python -m pipeline.orchestrator <original_rfp.docx> <approved_answers.json>")
        print('  approved_answers.json: [{"question": "...", "answer": "..."}, ...]')
        print("       add --verbose for per-question progress")
        sys.exit(1)

    original_path = sys.argv[1]
    answers_path  = sys.argv[2]
    verbose = "--verbose" in sys.argv or "-v" in sys.argv

    with open(answers_path, "r", encoding="utf-8") as f:
        approved = json.load(f)

    with open(original_path, "rb") as f:
        original_bytes = f.read()

    result = run_merge(
        original_bytes,
        source_filename=Path(original_path).name,
        approved_answers=approved,
        verbose=verbose,
    )

    Path(result.merged_filename).write_bytes(result.merged_docx_bytes)
    Path(result.addendum_filename).write_bytes(result.addendum_docx_bytes)

    print(f"\n--- Merge Summary ---")
    print(f"  Total questions:   {result.total_questions}")
    print(f"  Inserted:          {result.inserted_count}")
    print(f"  Needs addendum:    {result.skipped_count}  {result.addendum_counts}")
    print(f"\nWrote: {result.merged_filename}")
    print(f"Wrote: {result.addendum_filename}")