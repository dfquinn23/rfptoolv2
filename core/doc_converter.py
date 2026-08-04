"""
core/doc_converter.py — Legacy .doc → .docx conversion

Uses Word COM automation (pywin32) to convert legacy .doc files to .docx
so the rest of the pipeline (python-docx based) can handle them uniformly.

Windows-only. Requires Microsoft Word installed locally.
"""

import os
import tempfile
from pathlib import Path

import pythoncom
import win32com.client


class DocConversionError(Exception):
    """Raised when a .doc file cannot be converted to .docx."""
    pass


def convert_to_docx(file_path: str) -> str:
    """
    Convert a .doc file to .docx using Word COM automation.

    If the file is already .docx, its path is returned unchanged —
    callers can call this unconditionally without checking extension first.

    Args:
        file_path: Path to the source file (.doc or .docx).

    Returns:
        Path to a .docx file (either the original, or a newly created
        converted copy in the same temp directory as the source).

    Raises:
        DocConversionError: If conversion fails for any reason (Word not
            installed, corrupted file, COM failure, etc).
    """
    src = Path(file_path)

    if src.suffix.lower() == ".docx":
        return str(src)

    if src.suffix.lower() != ".doc":
        raise DocConversionError(
            f"Unsupported file type '{src.suffix}' — expected .doc or .docx"
        )

    out_path = src.with_suffix(".docx")

    # COM requires this in any thread that isn't the one Word's apartment
    # was originally created in — Streamlit's script-run threads qualify.
    pythoncom.CoInitialize()
    word = None
    doc = None
    try:
        word = win32com.client.DispatchEx("Word.Application")
        word.Visible = False
        word.DisplayAlerts = False

        doc = word.Documents.Open(str(src.resolve()))
        # 16 = wdFormatDocumentDefault (.docx)
        doc.SaveAs(str(out_path.resolve()), FileFormat=16)

    except Exception as e:
        raise DocConversionError(f"Failed to convert '{src.name}': {e}") from e

    finally:
        if doc is not None:
            try:
                doc.Close(False)
            except Exception:
                pass
        if word is not None:
            try:
                word.Quit()
            except Exception:
                pass
        pythoncom.CoUninitialize()

    if not out_path.exists():
        raise DocConversionError(
            f"Conversion reported success but output file not found: {out_path}"
        )

    return str(out_path)