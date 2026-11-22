"""PDF rendering and word-level text extraction with stable span IDs.

Every word on every page becomes a span:
    {"id": "p1_s042", "page": 1, "text": "6061-T6", "bbox": [x0, y0, x1, y1]}
bbox is in PDF points, origin top-left (PyMuPDF convention), so the frontend can
place overlays as a percentage of page width/height regardless of zoom.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pymupdf as fitz  # PyMuPDF

RENDER_DPI = 150
MAX_PAGES = 5
# A page with fewer extractable words than this is treated as scanned.
MIN_WORDS_FOR_VECTOR_TEXT = 8


class PdfError(Exception):
    """Raised with a user-readable message when a PDF can't be processed."""


@dataclass
class PageInfo:
    page: int
    png_path: Path
    width_pt: float
    height_pt: float
    text_source: str = "pdf"  # "pdf" | "ocr" | "none"

    def to_dict(self) -> dict:
        return {
            "page": self.page,
            "image": self.png_path.name,
            "width_pt": self.width_pt,
            "height_pt": self.height_pt,
            "text_source": self.text_source,
        }


def open_pdf(path: Path) -> fitz.Document:
    try:
        doc = fitz.open(path)
    except Exception as exc:  # fitz raises several types for corrupt files
        raise PdfError("This file could not be opened as a PDF. It may be corrupt or not a PDF.") from exc
    if not doc.is_pdf:
        raise PdfError("This file is not a PDF.")
    if doc.needs_pass:
        raise PdfError("This PDF is password-protected. Remove the password and try again.")
    if doc.page_count == 0:
        raise PdfError("This PDF has no pages.")
    return doc


def render_pages(doc: fitz.Document, out_dir: Path, dpi: int = RENDER_DPI) -> list[PageInfo]:
    out_dir.mkdir(parents=True, exist_ok=True)
    pages: list[PageInfo] = []
    for i, page in enumerate(doc):
        if i >= MAX_PAGES:
            break
        pix = page.get_pixmap(dpi=dpi, alpha=False)
        png = out_dir / f"page_{i + 1}.png"
        pix.save(png)
        pages.append(PageInfo(page=i + 1, png_path=png, width_pt=page.rect.width, height_pt=page.rect.height))
    return pages


def _make_id(page_no: int, idx: int) -> str:
    return f"p{page_no}_s{idx:03d}"


def extract_vector_spans(page: fitz.Page, page_no: int) -> list[dict]:
    words = page.get_text("words", sort=True)  # (x0, y0, x1, y1, word, block, line, word_no)
    spans = []
    for idx, w in enumerate(words, start=1):
        text = w[4].strip()
        if not text:
            continue
        spans.append({
            "id": _make_id(page_no, idx),
            "page": page_no,
            "text": text,
            "bbox": [round(w[0], 2), round(w[1], 2), round(w[2], 2), round(w[3], 2)],
        })
    return spans


def ocr_available() -> tuple[bool, str]:
    try:
        import pytesseract  # noqa: F401
    except ImportError:
        return False, "pytesseract is not installed."
    try:
        import pytesseract
        pytesseract.get_tesseract_version()
    except Exception:
        return False, "Tesseract OCR is not installed, so scanned pages can't be read. Install it from https://github.com/tesseract-ocr/tesseract."
    return True, "ok"


def extract_ocr_spans(png_path: Path, page_no: int, width_pt: float, height_pt: float) -> list[dict]:
    import pytesseract
    from PIL import Image

    img = Image.open(png_path)
    sx, sy = width_pt / img.width, height_pt / img.height
    data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
    spans = []
    idx = 0
    for i, text in enumerate(data["text"]):
        text = (text or "").strip()
        try:
            conf = float(data["conf"][i])
        except (TypeError, ValueError):
            conf = -1
        if not text or conf < 30:
            continue
        idx += 1
        x, y, w, h = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
        spans.append({
            "id": _make_id(page_no, idx),
            "page": page_no,
            "text": text,
            "bbox": [round(x * sx, 2), round(y * sy, 2), round((x + w) * sx, 2), round((y + h) * sy, 2)],
            "ocr_conf": conf,
        })
    return spans


def extract_spans(doc: fitz.Document, pages: list[PageInfo]) -> tuple[list[dict], list[str]]:
    """Return (spans, warnings). Falls back to OCR on pages without vector text."""
    spans: list[dict] = []
    warnings: list[str] = []
    for info in pages:
        page_spans = extract_vector_spans(doc[info.page - 1], info.page)
        if len(page_spans) < MIN_WORDS_FOR_VECTOR_TEXT:
            ok, msg = ocr_available()
            if ok:
                page_spans = extract_ocr_spans(info.png_path, info.page, info.width_pt, info.height_pt)
                info.text_source = "ocr"
                warnings.append(f"Page {info.page} looks scanned; text was read with OCR, so source links are approximate.")
            else:
                info.text_source = "none" if not page_spans else "pdf"
                warnings.append(f"Page {info.page} has almost no selectable text. {msg} Values from this page cannot be source-verified.")
        spans.extend(page_spans)
    return spans, warnings
