"""Turn PDF pages into text lines using word positions (pdfplumber).

`pdftotext -layout` drops the right-hand Total column on some C.H. Robinson
bills; grouping words by their vertical position does not."""
from pathlib import Path

import pdfplumber


def _lines_from_words(words, tol=3.0) -> list[str]:
    words = sorted(words, key=lambda w: (round(w["top"]), w["x0"]))
    rows, cur, top = [], [], None
    for w in words:
        if top is None or abs(w["top"] - top) <= tol:
            cur.append(w)
            top = w["top"] if top is None else top
        else:
            rows.append(cur)
            cur, top = [w], w["top"]
    if cur:
        rows.append(cur)
    return [" ".join(x["text"] for x in sorted(r, key=lambda w: w["x0"])) for r in rows]


def _ocr_page(page) -> list[str]:
    try:
        import pytesseract
    except ImportError:
        return []
    try:
        img = page.to_image(resolution=220).original
        text = pytesseract.image_to_string(img, config="--psm 6")
    except Exception:
        return []
    return [ln.strip() for ln in text.splitlines() if ln.strip()]


def extract_pages(path: str | Path, max_pages: int = 4) -> tuple[list[list[str]], bool]:
    """Returns (lines per page, used_ocr). Only the first pages are read: the
    charge table is there, later pages are attached scans."""
    pages, used_ocr = [], False
    with pdfplumber.open(str(path)) as pdf:
        for i, page in enumerate(pdf.pages[:max_pages]):
            words = page.extract_words(x_tolerance=2, y_tolerance=2)
            lines = _lines_from_words(words)
            if len(words) < 25 and i == 0:      # first page is a scan
                ocr = _ocr_page(page)
                if ocr:
                    lines, used_ocr = ocr, True
            pages.append(lines)
    return pages, used_ocr
