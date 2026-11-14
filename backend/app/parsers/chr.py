"""C.H. Robinson invoices (numbers 62xxxxxxxx). Verified on real bills."""
import re

from ..ids import normalise_pos
from .base import Charge, ParsedBill, money, parse_date

LABELS = ["Customer Reference Number", "Destination Airport", "Payment Received", "Destination Port",
          "Shipment Number", "Invoice Number", "Origin Airport", "Departure Date", "Invoice Date", "Arrival Date",
          "Entry Number", "Payment Due", "Origin Port", "Branch Code", "PO Numbers", "Amount Due", "Entry Date",
          "Sail Date", "Consignee", "Sub-Total", "Reference", "Flight #", "Remit To", "Shipper", "Airline",
          "Vessel", "Voyage", "MAWB#", "HAWB#", "Mode", "BL#"]
_LBL = "|".join(re.escape(l) for l in LABELS)
FIELD_RE = re.compile(rf"(?<![\w#])({_LBL})\s*:\s*(.*?)(?=\s+(?:{_LBL})\s*:|$)")

TYPES = r"Flat Rate|Fixed Charge|Per Chg Wt|Per CBM|Per KG|Per LB|Per Container|Per Shipment|Per [A-Z][A-Za-z]{1,10}(?: [A-Z][a-z]{1,6})?"
N = r"-?\(?[\d,]+(?:\.\d+)?\)?"
ROW_RE = re.compile(
    rf"^(?P<desc>.*?)\s*\b(?P<type>{TYPES})\s+(?P<units>{N})\s+(?P<cur>[A-Z]{{3}})\s+(?P<rate>{N})"
    rf"(?:\s+(?P<fx>\d+(?:\.\d+)?))?(?:\s+USD\s+(?P<total>{N}))?$")
USD_ONLY_RE = re.compile(rf"^USD\s+({N})$")


def matches(text: str) -> bool:
    return "C.H. Robinson" in text and "RATES AND ACCESSORIALS" in text


def parse(pages: list[list[str]]) -> ParsedBill:
    bill = ParsedBill(vendor="C.H. Robinson")
    fields: dict[str, str] = {}
    in_table, done = False, False
    refs = []
    for pno, lines in enumerate(pages, start=1):
        for i, line in enumerate(lines):
            for label, value in FIELD_RE.findall(line):
                value = value.strip()
                if label in ("Customer Reference Number", "Reference", "PO Numbers"):
                    refs.append(value)
                if value and label not in fields:
                    fields[label] = value
            if done:
                continue
            if line.startswith("Description") and "Per Unit" in line:
                in_table = True
                continue
            if line.startswith("Sub-Total"):
                in_table, done = False, True
                continue
            if in_table:
                m = ROW_RE.match(line)
                if m:
                    bill.charges.append(Charge(
                        description=m["desc"].strip(), rate_type=m["type"], units=money(m["units"]),
                        currency=m["cur"], rate=money(m["rate"]), fx=money(m["fx"]) if m["fx"] else None,
                        total_usd=money(m["total"]) if m["total"] else None, page=pno))
                    continue
                u = USD_ONLY_RE.match(line)
                if u and bill.charges and bill.charges[-1].total_usd is None:
                    bill.charges[-1].total_usd = money(u[1])       # total wrapped to its own line
                elif bill.charges and not re.search(r"\d", line):
                    bill.charges[-1].description += " " + line.strip()   # wrapped description ("Fee")
                else:
                    bill.warnings.append(f"p{pno}: unread charge line: {line[:80]}")
            if line.startswith("CONTAINERS") and i + 2 < len(lines):
                _containers(bill, lines[i + 1], lines[i + 2])

    for c in bill.charges:
        if c.total_usd is None:
            if c.currency == "USD" and c.units is not None and c.rate is not None:
                c.total_usd = round(c.units * c.rate, 2)
                bill.warnings.append(f"total for '{c.description}' not printed; computed units x rate")
            else:
                bill.warnings.append(f"no total found for '{c.description}'")

    bill.invoice_no = (fields.get("Invoice Number") or "").split(" ")[0] or None
    bill.invoice_date = parse_date(fields.get("Invoice Date"))
    bill.shipment_number = fields.get("Shipment Number")
    bill.hawb = fields.get("HAWB#") or fields.get("BL#")
    bill.mawb = fields.get("MAWB#")
    mode = (fields.get("Mode") or "").strip()[:1].upper()
    if mode == "A" or (not mode and ("MAWB#" in fields or "Airline" in fields)):
        bill.mode = "AIR"
    elif mode == "S" or (not mode and ("Vessel" in fields or "BL#" in fields)):
        bill.mode = "OCEAN"
    bill.po_refs = normalise_pos(" , ".join(refs))
    bill.subtotal = money(fields.get("Sub-Total"))          # landed cost uses Sub-Total, not Amount Due
    bill.payment_received = money(fields.get("Payment Received"))
    bill.amount_due = money(fields.get("Amount Due"))
    return bill


def _containers(bill: ParsedBill, header: str, row: str):
    if "Chrg Wt" in header:
        m = re.search(r"([\d,.]+)\s+(LBS|KGS|KG|LB)\s+([\d,.]+)\s+(LBS|KGS|KG|LB)", row)
        if m:
            bill.chargeable_weight, bill.weight_uom = money(m[3]), m[4]
    else:
        m = re.search(r"([\d,.]+)\s+(LBS|KGS|KG|LB)\b", row)
        if m:
            bill.chargeable_weight, bill.weight_uom = money(m[1]), m[2]
