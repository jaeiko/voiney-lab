"""PDF fixture writers shared by the tests.

Moved verbatim from ``tests/test_protocol_claim_analysis.py`` when the
evidence-first claim-chunk path and most of that file's tests were deleted
(lane AN, 2026-10-13); the tests that still need a synthetic PDF import them
from here.
"""

from __future__ import annotations

from pathlib import Path

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject


_FIXTURE_PAGE_WIDTH = 4000  # wide enough that one unwrapped fixture line
# is never clipped: a bounded extractor would otherwise drop the tail and
# disagree with an unbounded one on synthetic input only.


_FIXTURE_LINE_LEADING = 12  # points between successive baselines


def write_lined_pages(
    path: Path,
    pages: tuple[tuple[str, ...], ...],
    *,
    title: str = "Protocol Evidence",
) -> None:
    """Write each page as real, separate text lines.

    ``write_pages`` collapses newlines to spaces, so every page it produces has
    zero line breaks.  Boundary rules that key on end-of-line punctuation are
    therefore never exercised by those fixtures.  This writer emits one
    positioned show operation per line instead, so the extracted text contains
    actual line breaks.  Existing ``write_pages`` callers are unaffected.
    """

    writer = PdfWriter()
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    font_ref = writer._add_object(font)
    for lines in pages:
        page = writer.add_blank_page(width=_FIXTURE_PAGE_WIDTH, height=792)
        operations = ["BT /F1 9 Tf"]
        baseline = 740
        for line in lines:
            escaped = (
                line.replace("\\", "\\\\")
                .replace("(", "\\(")
                .replace(")", "\\)")
                .replace("\n", " ")
            )
            operations.append(f"1 0 0 1 36 {baseline} Tm ({escaped}) Tj")
            baseline -= _FIXTURE_LINE_LEADING
        operations.append("ET")
        stream = DecodedStreamObject()
        stream.set_data(" ".join(operations).encode("ascii"))
        page[NameObject("/Resources")] = DictionaryObject(
            {
                NameObject("/Font"): DictionaryObject(
                    {NameObject("/F1"): font_ref}
                )
            }
        )
        page[NameObject("/Contents")] = writer._add_object(stream)
    writer.add_metadata({"/Title": title})
    with path.open("wb") as target:
        writer.write(target)


def write_pages(path: Path, page_texts: tuple[str, ...]) -> None:
    """Write one page per string, honouring any newlines it contains.

    This used to replace every newline with a space, so a fixture written as
    ``"Preparation\n1. Add buffer."`` silently became one line and its
    numbered step ended up mid-sentence. Those fixtures only passed because
    the trigger also matched a number in the middle of a line, which no real
    document needs. A page with no newline is written exactly as before.
    """

    if any("\n" in text for text in page_texts):
        write_lined_pages(
            path, tuple(tuple(text.split("\n")) for text in page_texts)
        )
        return
    writer = PdfWriter()
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    font_ref = writer._add_object(font)
    for raw_text in page_texts:
        page = writer.add_blank_page(width=_FIXTURE_PAGE_WIDTH, height=792)
        escaped = (
            raw_text.replace("\\", "\\\\")
            .replace("(", "\\(")
            .replace(")", "\\)")
            .replace("\n", " ")
        )
        stream = DecodedStreamObject()
        stream.set_data(
            f"BT /F1 9 Tf 36 740 Td ({escaped}) Tj ET".encode("ascii")
        )
        page[NameObject("/Resources")] = DictionaryObject(
            {
                NameObject("/Font"): DictionaryObject(
                    {NameObject("/F1"): font_ref}
                )
            }
        )
        page[NameObject("/Contents")] = writer._add_object(stream)
    writer.add_metadata({"/Title": "Protocol Evidence"})
    with path.open("wb") as target:
        writer.write(target)
