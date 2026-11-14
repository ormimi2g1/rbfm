"""Expeditors invoices (E23xxxxxxx).

Written from the layout description in the spec. NOT yet checked against a real
Expeditors PDF: a bill that does not tie falls through to the AI reader / review."""
import re

from ..ids import normalise_pos
from .base import Charge, ParsedBill, money

LINE_RE = re.compile(r"^(?P<desc>[A-Z][A-Z0-9 /&.,'()#-]+?)\s+(?P<amt>-?[\d,]+\.\d{2})$")
START_RE = re.compile(r"^(ADDITIONAL BILLING|ORIGINAL)\b", re.I)


def matches(text: str) -> bool:
    return "expeditors" in text.lower()


def parse(pages: list[list[str]]) -> ParsedBill:
    bill = ParsedBill(vendor="Expeditors")
    text = "\n".join(l for p in pages for l in p)
    m = re.search(r"INVOICE NUMBER\s*:?\s*(E\d{6,})", text) or re.search(r"\b(E2\d{8})\b", text)
    bill.invoice_no = m.group(1) if m else None
    m = re.search(r"AWB/BL\s*:?\s*([A-Z0-9-]+)", text)
    bill.hawb = m.group(1) if m else None
    m = re.search(r"CHG WGT\s*:?\s*([\d,.]+)", text)
    if m:
        bill.chargeable_weight, bill.weight_uom = money(m[1]), "KGS"
    m = re.search(r"YOUR REFERENCE\s*:?\s*(.+)", text)
    bill.po_refs = normalise_pos(m.group(1)) if m else []
    in_lines = False
    for pno, lines in enumerate(pages, start=1):
        for line in lines:
            if START_RE.match(line):
                in_lines = True
                continue
            if line.upper().startswith("SUB TOTAL"):
                in_lines = False
                continue
            d = re.search(r"DUTY BY CLIENT\s+USD\s+([\d,]+\.\d{2})", line)
            if d:                                     # informational: duty paid separately
                bill.stated_customs = round((bill.stated_customs or 0) + money(d[1]), 2)
                continue
            if in_lines:
                lm = LINE_RE.match(line)
                if lm:
                    bill.charges.append(Charge(description=lm["desc"].strip(), total_usd=money(lm["amt"]), currency="USD", page=pno))
    t = re.search(r"INVOICE TOTAL\s*:?\s*(-?[\d,]+\.\d{2})\s*USD", text)
    bill.subtotal = bill.amount_due = money(t[1]) if t else None
    bill.warnings.append("Expeditors layout rules are unverified against real bills")
    return bill
