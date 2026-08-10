"""
pipeline/inserter.py

Answer Inserter — third stage of the merge/export pipeline (after the
Locator produces a location map, and the user approves answers on the
Review page).

Purely deterministic. No LLM involvement of any kind. This is the "code
writes" half of the LLM-locates / code-writes split established in
pipeline/locator.py — the Locator decided WHERE an answer goes; this module
just writes the already-approved text into that exact spot with
python-docx.

Only writes into locations the Locator's validation guard actually verified
("blank_line" or "inline_bracket"). Anything "table", "unclear", or with no
confident match to an approved answer is left completely untouched in the
document and reported back as skipped — those are picked up by the
Addendum Builder (next stage), never guessed at here.

CRITICAL: answer text is inserted EXACTLY as approved — no rewriting, no
paraphrasing, no reformatting beyond what's mechanically required to place
it in the target paragraph. Verbatim in, verbatim out.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from docx import Document

from pipeline.locator import locate_answer_slots, match_to_approved


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class InsertResult:
    output_path: str
    inserted: list[dict] = field(default_factory=list)  # {question_text, para_idx}
    skipped:  list[dict] = field(default_factory=list)  # {question_text, answer_text, insertion_type, reason}

    @property
    def inserted_count(self) -> int:
        return len(self.inserted)

    @property
    def skipped_count(self) -> int:
        return len(self.skipped)


# ---------------------------------------------------------------------------
# Write logic
# ---------------------------------------------------------------------------

def _write_answer(doc: Document, para_idx: int, insertion_type: str, answer_text: str) -> None:
    """
    Write answer_text verbatim into the target paragraph.

    blank_line:      The target paragraph was validated blank by the
                      Locator's guard — safe to set its text directly.
    inline_bracket:   The target paragraph IS the question's own paragraph
                      (validated answer_para_idx == question_para_idx) —
                      the answer is appended to the existing text, never
                      overwriting the question itself.
    """
    para = doc.paragraphs[para_idx]

    if insertion_type == "blank_line":
        # Paragraph was validated blank. If it somehow already has runs
        # (shouldn't, but be defensive), reuse the first one so we inherit
        # its formatting rather than losing paragraph styling.
        if para.runs:
            para.runs[0].text = answer_text
            for extra in para.runs[1:]:
                extra.text = ""
        else:
            para.add_run(answer_text)

    elif insertion_type == "inline_bracket":
        existing = para.text
        needs_space = bool(existing) and not existing.endswith((":", "[", " "))
        para.add_run(f"{' ' if needs_space else ''}{answer_text}")

    else:
        raise ValueError(f"[inserter] Cannot write insertion_type={insertion_type!r}")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def insert_answers(
    original_file_path: str,
    approved_answers: list[dict],
    output_path: str | None = None,
    location_map: list[dict] | None = None,
    verbose: bool = False,
) -> InsertResult:
    """
    Write approved answers into their verified locations in the original RFP.

    Args:
        original_file_path: Path to the ORIGINAL incoming RFP .docx — the
                              same file the location map's paragraph indices
                              refer to.
        approved_answers:    List of dicts, each with at least "question"
                              and "answer" keys. Typically the approved
                              subset of st.session_state["answers"].values()
                              from the Review page (filter to
                              item["approved"] is True before calling).
        output_path:          Where to save the merged document. Defaults to
                              "<original stem>_merged.docx" next to the
                              original.
        location_map:         Pre-computed output of locate_answer_slots(),
                              e.g. already produced during the Verify
                              Structure step. If None, it's computed here
                              (an extra LLM pass) — prefer passing it in
                              when you already have it, both to save the
                              extra pass and so the location map the user
                              actually verified is the one that gets used.
        verbose:               Print per-question progress.

    Returns:
        InsertResult — output path, plus which questions were inserted vs
        skipped (and why). Skipped items are exactly what the Addendum
        Builder needs to pick up next.
    """
    path = Path(original_file_path)
    if not path.exists():
        raise FileNotFoundError(f"[inserter] File not found: {original_file_path}")
    if path.suffix.lower() != ".docx":
        raise ValueError(f"[inserter] Expected .docx file, got: {path.suffix}")

    if output_path is None:
        output_path = str(path.with_name(f"{path.stem}_merged.docx"))

    if location_map is None:
        print("[inserter] No location map supplied — running locator now...")
        location_map = locate_answer_slots(str(path), verbose=verbose)

    approved_questions = [a["question"] for a in approved_answers]
    matched = match_to_approved(location_map, approved_questions)  # aligned 1:1 with approved_answers

    doc = Document(str(path))
    result = InsertResult(output_path=output_path)

    for answer_item, loc in zip(approved_answers, matched):
        qtext = answer_item.get("question", "")
        atext = (answer_item.get("answer") or "").strip()
        itype = loc.get("insertion_type")
        aidx  = loc.get("answer_para_idx")

        if not atext:
            result.skipped.append({
                "question_text": qtext, "answer_text": atext,
                "insertion_type": itype, "reason": "empty approved answer",
            })
            continue

        if itype not in ("blank_line", "inline_bracket") or aidx is None:
            result.skipped.append({
                "question_text": qtext, "answer_text": atext,
                "insertion_type": itype,
                "reason": f"no verified location (insertion_type={itype})",
            })
            continue

        if aidx >= len(doc.paragraphs):
            result.skipped.append({
                "question_text": qtext, "answer_text": atext,
                "insertion_type": itype,
                "reason": f"answer_para_idx {aidx} out of range for this document",
            })
            continue

        try:
            _write_answer(doc, aidx, itype, atext)
            result.inserted.append({"question_text": qtext, "para_idx": aidx})
            if verbose:
                print(f"[inserter] ✅ Inserted @ para {aidx}: {qtext[:60]}")
        except Exception as e:
            result.skipped.append({
                "question_text": qtext, "answer_text": atext,
                "insertion_type": itype, "reason": f"write failed: {e}",
            })

    doc.save(output_path)
    print(f"[inserter] Done. {result.inserted_count} inserted, "
          f"{result.skipped_count} skipped → {output_path}")

    return result


# ---------------------------------------------------------------------------
# CLI — smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    import json
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

    if len(sys.argv) < 3:
        print("Usage: python -m pipeline.inserter <original_rfp.docx> <approved_answers.json>")
        print('  approved_answers.json: [{"question": "...", "answer": "..."}, ...]')
        print("       add --verbose for per-question progress")
        sys.exit(1)

    original_path = sys.argv[1]
    answers_path  = sys.argv[2]
    verbose = "--verbose" in sys.argv or "-v" in sys.argv

    with open(answers_path, "r", encoding="utf-8") as f:
        approved = json.load(f)

    result = insert_answers(original_path, approved, verbose=verbose)

    print(f"\n--- Inserted ({result.inserted_count}) ---")
    for r in result.inserted:
        print(f"  @{r['para_idx']:<4} {r['question_text'][:70]}")

    print(f"\n--- Skipped ({result.skipped_count}) — these need the Addendum Builder ---")
    for r in result.skipped:
        print(f"  [{r['reason']}] {r['question_text'][:70]}")

    print(f"\nOutput: {result.output_path}")