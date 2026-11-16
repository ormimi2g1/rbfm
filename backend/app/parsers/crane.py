"""Crane Worldwide invoices (IAIDFW… air, ISIDFW… ocean, 7-digit brokerage).

Verified on a real ocean (LCL) bill. Air and brokerage layouts follow the
spec's description and are not yet checked against real PDFs."""
import re

from ..ids import normalise_pos
from .base import Charge, ParsedBill, money, parse_date

LINE_RE = re.compile(r"^(?P<desc>.+?)\s+(?P<rate>-?[\d,]+\.\d{2,4})\s+(?P<qty>-?[\d,]+\.\d{2,4})\s+(?P<uom>[A-Z]{2,4})\s+(?P<amt>-?\(?[\d,]+\.\d{2}\)?)$")
TOTAL_RE = re.compile(r"Total\s*[-–]\s*US Dollar.*?USD\s+(-?[\d,]+\.\d{2})", re.I)
DUTY_RE = re.compile(r"Duties and Fees of \$?\s*([\d,]+\.\d{2})", re.I)


def matches(text: str) -> bool:
    return "crane worldwide" in text.lower()


def _after(label_rx: str, text: str) -> str | None:
    m = re.search(label_rx + r"\s*:?\s*([A-Z0-9][\w/-]*)", text, re.I)
    return m.group(1) if m else None


def parse(pages: list[list[str]]) -> ParsedBill:
    bill = ParsedBill(vendor="Crane Worldwide")
    text = "\n".join(l for p in pages for l in p)
    bill.invoice_no = _after(r"Invoice No\.?", text)
    bill.hawb = _after(r"BL\s*/\s*AWB No\.?", text)
    bill.mawb = _after(r"MBL\s*/\s*MAWB No\.?", text)
    m = re.search(r"(?<![\w/])Date\s*:\s*(\d{1,2}/\d{1,2}/\d{4})", text)
    bill.invoice_date = parse_date(m.group(1)) if m else None
    m = re.search(r"C\.?Wgt/M3/KT\s*:?\s*([\d,]+(?:\.\d+)?)\s*([A-Z]{2,4})?", text, re.I)
    if m:
        bill.chargeable_weight, bill.weight_uom = money(m[1]), (m[2] or "KGS").upper()
    inv = (bill.invoice_no or "").upper()
    bill.mode = "AIR" if inv.startswith("IAI") else "OCEAN" if inv.startswith("ISI") else None
    wrapped = False
    for pno, lines in enumerate(pages, start=1):
        if bill.charges and pno > 1:
            break                                   # later pages are the supplier's invoice and packing list
        for line in lines:
            if re.match(r"^CWW-\d", line) or TOTAL_RE.search(line):
                wrapped = False
                continue
            lm = LINE_RE.match(line)
            if lm:
                bill.charges.append(Charge(description=lm["desc"].strip(), rate=money(lm["rate"]), units=money(lm["qty"]),
                                           rate_type=lm["uom"], currency="USD", total_usd=money(lm["amt"]), page=pno))
                wrapped = True
            elif wrapped and bill.charges and len(line) < 40 and not re.search(r"\d|:", line):
                bill.charges[-1].description += " " + line.strip()      # "(PGA) Fee" on its own line
                wrapped = False
            else:
                wrapped = False
    t = TOTAL_RE.search(text)
    bill.subtotal = bill.amount_due = money(t[1]) if t else None
    d = DUTY_RE.search(text)
    if d:
        bill.stated_customs = money(d[1])
    bill.po_refs = normalise_pos(" ".join(re.findall(r"\bPO\s*#?\s*0*\d{3,6}\b", text)))
    m = re.search(r"Service Level\s*:\s*(.+)", text)
    if not bill.mode and m:
        bill.mode = "OCEAN" if re.search(r"ocean|LCL|FCL", m[1], re.I) else "AIR" if re.search(r"air", m[1], re.I) else None
    return bill
