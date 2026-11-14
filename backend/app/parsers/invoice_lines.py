"""SKU lines from the "Invoice Lines Report" that C.H. Robinson attaches to its
bill packets (one customs line per product code and size, with quantity, invoice
value, tariff and duty rate).

Those pages are images, usually rotated, so they are read with OCR. To keep the
numbers trustworthy each table cell is read on its own with a restricted
character set, and the result is checked against the report's printed
"Line Total". Verified on a real 49-page report (230 lines, total tied to the cent).
"""
import os
import pathlib
import re
import shutil
import subprocess
import tempfile
import statistics
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import pdfplumber
from rapidfuzz import process as fuzz_process
from rapidfuzz.distance import Levenshtein

try:
    import numpy as np
    import pytesseract
    from PIL import Image, ImageOps
    OCR_OK = True
except ImportError:  # pragma: no cover
    OCR_OK = False

TITLE_RE = re.compile(r"Invoice\s+Lines|Lines\s+Report", re.I)
CODE1_RE = re.compile(r"\d[A-Z]{2}\d{4}-\d{2}-[A-Z]")
CODE2_RE = re.compile(r"\d{4}-[0-9A-Z]{5}")
TOTAL_RE = re.compile(r"Line\s+Total\s+([\d,]+\.\d{2})")
HEADER_WORDS = [("Merged", 0, "merged"), ("Product", 0, "code"), ("Custom", 0, "attr"), ("Description", 0, "desc"),
                ("Invoice", 0, "qty"), ("UQ", 0, "uq"), ("Invoice", 1, "price"),
                ("Cur", 0, "cur"), ("Tariff", 0, "tariff"), ("Duty", 0, "duty")]
WHITELIST = {"code": "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ-", "qty": "0123456789.", "price": "0123456789.,",
             "tariff": "0123456789.", "rate": "0123456789.%FRE"}
DPI = 300


@dataclass
class ItemLine:
    line_no: int
    page: int
    sku: str
    description: str
    qty: float | None
    value_usd: float | None
    hts: str | None
    duty_rate: str | None
    component: bool = False          # extra customs line of the SKU above (e.g. the shorts of a set)
    note: str | None = None


@dataclass
class ItemLinesResult:
    lines: list[ItemLine] = field(default_factory=list)
    printed_total: float | None = None
    pages: list[int] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def lines_total(self) -> float:
        return round(sum(l.value_usd or 0 for l in self.lines), 2)

    def tied(self) -> bool:
        return bool(self.lines) and self.printed_total is not None and abs(self.lines_total - self.printed_total) <= 0.02


def _num(s: str) -> float | None:
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return None


def _render(page, dpi: int, rotation: int):
    im = page.to_image(resolution=dpi).original.convert("L")
    return im.rotate(rotation, expand=True) if rotation else im


def _render_pages(path, indexes: list[int], dpi: int, rotation: int, pdf) -> list:
    """Full-resolution page images. poppler's pdftoppm gives noticeably cleaner
    text for OCR than the built-in renderer, so it is used when installed."""
    if shutil.which("pdftoppm") and indexes:
        with tempfile.TemporaryDirectory() as tmp:
            first, last = indexes[0] + 1, indexes[-1] + 1
            subprocess.run(["pdftoppm", "-r", str(dpi), "-gray", "-png", "-f", str(first), "-l", str(last), str(path), f"{tmp}/p"],
                           check=True, capture_output=True, timeout=900)
            files = {int(f.stem.rsplit("-", 1)[1]): f for f in pathlib.Path(tmp).glob("p-*.png")}
            out = []
            for i in indexes:
                im = Image.open(files[i + 1]).convert("L")
                im.load()
                out.append(im.rotate(rotation, expand=True) if rotation else im)
            return out
    return [_render(pdf.pages[i], dpi, rotation) for i in indexes]


def find_report_pages(pdf, start: int = 0) -> tuple[list[int], int]:
    """Indexes of the report pages and the rotation that makes them upright."""
    candidates = [i for i in range(start, len(pdf.pages)) if len(pdf.pages[i].extract_words()) < 25]
    for rotation in (90, 270, 0):
        found = []
        for i in candidates:
            text = pytesseract.image_to_string(_render(pdf.pages[i], 60, rotation), config="--psm 6")
            if TITLE_RE.search(text):
                found.append(i)
            elif found and i - found[-1] > 3:
                break                        # the report is one contiguous block
        if found:
            return found, rotation
    return [], 0


def _calibrate(images) -> dict | None:
    """Column positions from the table header. The layout is the same on every
    page, so each header word is located on several pages and the median used:
    one page with a misread header word does not matter."""
    xs: dict[str, list[int]] = {}
    hys, hs = [], []
    for im in images:
        d = pytesseract.image_to_data(im, config="--psm 6", output_type=pytesseract.Output.DICT)
        words = [dict(t=d["text"][i], x=d["left"][i], y=d["top"][i], h=d["height"][i]) for i in range(len(d["text"])) if d["text"][i].strip()]
        anchor = next((w for w in words if w["t"] == "Merged"), None)
        if not anchor:
            continue
        hys.append(anchor["y"])
        hs.append(anchor["h"])
        row = [w for w in words if abs(w["y"] - anchor["y"]) < anchor["h"]]
        for text, n, key in HEADER_WORDS:
            hits = sorted((w for w in row if w["t"].strip("_|.") == text), key=lambda w: w["x"])
            if len(hits) == (2 if text in ("Custom", "Invoice", "UQ") else 1):
                xs.setdefault(key, []).append(hits[n]["x"])
    if not hys or any(key not in xs for _, _, key in HEADER_WORDS):
        return None
    return {"x": {k: int(statistics.median(v)) for k, v in xs.items()}, "hy": int(statistics.median(hys)), "h": int(statistics.median(hs))}


def _cell(im, box, key: str) -> str:
    c = ImageOps.expand(im.crop(box), border=25, fill=255)
    c = c.resize((c.width * 2, c.height * 2), Image.LANCZOS)
    return pytesseract.image_to_string(c, config=f"--psm 7 -c tessedit_char_whitelist={WHITELIST[key]}").strip().replace(" ", "")


def _read_page(args):
    im, cal, page_no = args
    x, hy, h = cal["x"], cal["hy"], cal["h"]
    cols = {"code": (x["code"] - 25, x["attr"] - 15), "qty": (x["qty"] - 20, x["uq"] - 25),
            "price": (x["price"] - 20, x["cur"] - 20), "tariff": (x["tariff"] - 90, x["duty"] - 25),
            "rate": (x["duty"] - 25, x["duty"] + 190)}
    desc_box = (x["desc"] - 330, x["qty"] - 30)
    top = hy + int(h * 2.2)
    dark = np.array(im) < 140
    profile = dark[top:, cols["price"][0]:cols["price"][1]].sum(axis=1)
    rows, start = [], None
    for y, v in enumerate(profile):                 # every line item has a value in the price column
        if v > 0 and start is None:
            start = y
        elif v == 0 and start is not None:
            if y - start > h * 0.6:
                rows.append((top + start, top + y))
            start = None
    out, pad, step = [], 10, int(h * 2.05)
    for n, (ya, yb) in enumerate(rows):
        r = {"page": page_no}
        for key, (xa, xb) in cols.items():
            r[key] = _cell(im, (xa, ya - pad, xb, yb + pad), key)
        r["code2"] = _cell(im, (cols["code"][0], ya + step - pad, cols["code"][1], yb + step + pad), "code")
        y_end = rows[n + 1][0] - pad if n + 1 < len(rows) else min(im.height, yb + step * 5)
        text = pytesseract.image_to_string(im.crop((desc_box[0], ya - pad, desc_box[1], y_end)), config="--psm 6")
        r["desc"] = " ".join(text.split())
        out.append(r)
    # a line cut by the page break leaves the second half of its product code at the top of the next page
    gap_top = hy + int(h * 1.3)
    first_y = rows[0][0] if rows else im.height
    carry = _cell(im, (cols["code"][0], gap_top, cols["code"][1], first_y - int(h * 0.4)), "code") if first_y - gap_top > h * 1.5 else ""
    full = pytesseract.image_to_string(im.crop((0, top, im.width, im.height)), config="--psm 6") if not out or len(out) < 4 else ""
    m = TOTAL_RE.search(full)
    return out, carry, (_num(m.group(1)) if m else None)


def _fix_code1(raw: str, known: list[str]) -> tuple[str, str | None]:
    m = CODE1_RE.search(raw)
    if m:
        return m.group(0), None
    if known:
        best = fuzz_process.extractOne(raw, known, scorer=Levenshtein.distance)
        if best and best[1] <= 2:
            return best[0], f"product code read as '{raw}', corrected"
    return raw, f"product code '{raw}' could not be confirmed"


def _fix_code2(raw: str, common: list[str]) -> tuple[str, str | None]:
    raw = raw.strip()
    prefix, _, suffix = raw.partition("-")
    if CODE2_RE.fullmatch(raw) and (suffix in common or not common):
        return raw, None
    if re.fullmatch(r"\d{4}", prefix) and suffix:
        o_fixed = suffix.replace("O", "0")
        if len(o_fixed) == 5 and o_fixed in common:
            return f"{prefix}-{o_fixed}", None
        best = fuzz_process.extractOne(suffix, common, scorer=Levenshtein.distance) if common else None
        if best and best[1] <= 1:
            return f"{prefix}-{best[0]}", f"size code read as '{suffix}', corrected"
        if len(o_fixed) == 5:
            return f"{prefix}-{o_fixed}", None if o_fixed == suffix else f"size code read as '{suffix}'"
    return raw, f"size code '{raw}' could not be confirmed"


def assemble(pages: list[tuple[list[dict], str, float | None]]) -> ItemLinesResult:
    """Turn raw cell readings (per page) into checked SKU lines."""
    res = ItemLinesResult()
    raw = []
    for rows, carry, total in pages:
        if carry and raw and not CODE2_RE.search(raw[-1]["code2"]):
            raw[-1]["code2"] = carry
        raw += rows
        if total is not None:
            res.printed_total = total
    known1 = sorted({m.group(0) for r in raw if (m := CODE1_RE.fullmatch(r["code"]))})
    suffixes = Counter(r["code2"].partition("-")[2] for r in raw if CODE2_RE.fullmatch(r["code2"]))
    common = [s for s, n in suffixes.items() if n >= 5]
    last_sku = None
    for n, r in enumerate(raw, start=1):
        notes = []
        if not r["code"]:                                # blank product code = another customs line of the SKU above
            sku, component = last_sku or "?", True
        else:
            c1, w1 = _fix_code1(r["code"], known1)
            c2, w2 = _fix_code2(r["code2"], common)
            notes += [w for w in (w1, w2) if w]
            sku, component = c1 + c2, False
            last_sku = sku
        qty, value = _num(r["qty"]), _num(r["price"])
        if qty is None or value is None:
            notes.append("quantity or value could not be read")
        res.lines.append(ItemLine(line_no=n, page=r["page"], sku=sku, description=r.get("desc", "")[:200], qty=qty, value_usd=value,
                                  hts=r["tariff"] or None, duty_rate=r["rate"] or None, component=component,
                                  note="; ".join(notes) or None))
    # same style and colour should have one unit price: a line that breaks the pattern is worth a look
    by_style: dict[str, list[float]] = {}
    for l in res.lines:
        if not l.component and l.qty and l.value_usd:
            by_style.setdefault(l.sku[:17], []).append(l.value_usd / l.qty)
    for l in res.lines:
        prices = by_style.get(l.sku[:17], [])
        if not l.component and l.qty and l.value_usd and len(prices) >= 3:
            med = statistics.median(prices)
            if med and abs(l.value_usd / l.qty - med) / med > 0.25 and abs(l.value_usd / l.qty - med / 2) / med > 0.05:
                l.note = "; ".join(filter(None, [l.note, "unit price differs from the rest of this style"]))
    if res.printed_total is None:
        res.warnings.append("The report's printed Line Total could not be read, so the SKU lines are not verified")
    elif not res.tied():
        res.warnings.append(f"SKU lines add to {res.lines_total:,.2f} but the report prints {res.printed_total:,.2f}")
    return res


def extract(path, start_page: int = 0) -> ItemLinesResult | None:
    """Read the Invoice Lines Report from a bill packet. None if there is none."""
    if not OCR_OK:
        return None
    with pdfplumber.open(str(path)) as pdf:
        idx, rotation = find_report_pages(pdf, start_page)
        if not idx:
            return None
        rendered = _render_pages(path, idx, DPI, rotation, pdf)
        images = [(im, i + 1) for im, i in zip(rendered, idx)]
        cal = _calibrate([im for im, _ in images[:10]])
        if not cal:
            r = ItemLinesResult(pages=[i + 1 for i in idx])
            r.warnings.append("Found an Invoice Lines Report but could not read its table header")
            return r
        with ThreadPoolExecutor(max_workers=min(8, os.cpu_count() or 2)) as ex:
            pages = list(ex.map(_read_page, [(im, cal, pno) for im, pno in images]))
    res = assemble(pages)
    res.pages = [i + 1 for i in idx]
    return res
