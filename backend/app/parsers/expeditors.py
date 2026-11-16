"""Expeditors invoices (E23xxxxxxx). Verified on a real ocean (CFS) bill.

Layout: header block, then charge lines as "DESCRIPTION amount" down to
"SUB TOTAL:", then "INVOICE TOTAL: x USD". "DUTY BY CLIENT USD x" is
information only (duty is paid separately) and is never a charge."""
import re

from ..ids import normalise_pos
from .base import Charge, ParsedBill, money, parse_date

LINE_RE = re.compile(r"^(?P<desc>[A-Z][A-Z0-9 /&.,'()#%-]+?)\s+(?P<amt>-?[\d,]+\.\d{2})$")
DUTY_RE = re.compile(r"DUTY BY CLIENT\s+USD\s+([\d,]+\.\d{2})")


def matches(text: str) -> bool:
    return "expeditors" in text.lower()


def parse(pages: list[list[str]]) -> ParsedBill:
    bill = ParsedBill(vendor="Expeditors")
    done = False
    for pno, lines in enumerate(pages, start=1):
        if done:
            break                                   # later pages are attached emails and customs papers
        text = "\n".join(lines)
        if "INVOICE TOTAL" not in text and "SUB TOTAL" not in text:
            continue
        m = re.search(r"\b(E\d{9})\b", text)
        bill.invoice_no = m.group(1) if m else None
        m = re.search(r"INVOICE\s+(\d{2}/\d{2}/\d{2,4})", text)
        bill.invoice_date = parse_date(m.group(1)) if m else None
        m = re.search(r"HAWB/HBL:\s*([A-Z0-9-]+)", text)
        bill.hawb = m.group(1) if m else None
        m = re.search(r"AWB/BL:\s*([A-Z0-9-]+)", text)
        bill.mawb = m.group(1) if m else None
        if not bill.hawb:
            bill.hawb, bill.mawb = bill.mawb, None
        m = re.search(r"(?:CHG WGT|WEIGHT):\s*([\d,.]+)\s*(K|L)?", text)
        if m:
            bill.chargeable_weight, bill.weight_uom = money(m[1]), {"K": "KGS", "L": "LBS"}.get(m[2] or "K")
        bill.po_refs = normalise_pos(" ".join(re.findall(r"\bPO\s?\d{3,6}\b", text)))
        level = re.search(r"SERVICE LEVEL:\s*(.+)", text)
        hint = (level.group(1) if level else "") + " " + text
        if re.search(r"\b(CFS|FCL|LCL|OCEAN|VESSEL|\d{2}H[CQ]?)\b", hint):
            bill.mode = "OCEAN"
        elif re.search(r"\b(AIR|FLIGHT|AIRLINE)\b", hint):
            bill.mode = "AIR"
        for line in lines:
            d = DUTY_RE.search(line)
            if d:
                bill.stated_customs = round((bill.stated_customs or 0) + money(d[1]), 2)
                continue
            if line.upper().startswith("SUB TOTAL") or line.upper().startswith("INVOICE TOTAL"):
                done = True
                continue
            if not done:
                lm = LINE_RE.match(line)
                if lm:
                    bill.charges.append(Charge(description=lm["desc"].strip(), total_usd=money(lm["amt"]), currency="USD", page=pno))
        t = re.search(r"INVOICE TOTAL:\s*(-?[\d,]+\.\d{2})\s*USD", text)
        bill.subtotal = bill.amount_due = money(t[1]) if t else None
    return bill
