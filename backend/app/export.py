"""Excel export of the current state."""
import io

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter
from sqlalchemy.orm import Session

from . import recon


def _sheet(wb, title, headers, rows, money_cols=()):
    ws = wb.create_sheet(title)
    ws.append(headers)
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1F3A5F")
    for r in rows:
        ws.append(r)
    ws.freeze_panes = "A2"
    for i, h in enumerate(headers, start=1):
        width = max([len(str(h))] + [len(str(r[i - 1])) for r in rows[:300] if r[i - 1] is not None]) + 2
        ws.column_dimensions[get_column_letter(i)].width = min(width, 60)
        if h in money_cols:
            for cell in ws[get_column_letter(i)][1:]:
                cell.number_format = "#,##0.00"
    return ws


def build(db: Session) -> bytes:
    state = recon.build(db)
    wb = Workbook()
    wb.remove(wb.active)
    ships = [s for s in state["shipments"] if s["bill_count"]]
    M = ("Goods Value", "Actual Freight", "NS LC Freight", "Freight Var", "Actual Customs", "NS LC Customs", "Customs Var",
         "Broker Fees", "Subtotal", "Lines Total", "Freight", "Customs", "Total USD", "Amount Due")
    _sheet(wb, "IBS Summary",
           ["Shipment ID", "Status", "Mode", "Bills", "Goods Value", "Actual Freight", "NS LC Freight", "Freight Var",
            "Freight Var %", "Actual Customs", "NS LC Customs", "Customs Var", "Broker Fees", "Receipts", "POs", "Flags"],
           [[s["shipment_id"], s["status"], s["mode"], s["bill_count"], s["goods_value"], s["actual_freight"], s["ns_freight"],
             s["freight_var"], s["freight_var_pct"], s["actual_customs"] or None, s["ns_customs"], s["customs_var"],
             s["broker_fees"] or None, s["receipts"], ", ".join(s["pos"]), ", ".join(s["flags"])] for s in ships], M)
    _sheet(wb, "Awaiting Bills",
           ["Shipment ID", "Mode", "Last Receipt", "Receipts", "Goods Value", "NS LC Freight", "NS LC Customs", "POs", "Flags"],
           [[s["shipment_id"], s["mode"], s["last_receipt"], s["receipts"], s["goods_value"], s["ns_freight"], s["ns_customs"],
             ", ".join(s["pos"]), ", ".join(s["flags"])] for s in state["shipments"] if s["status"] == "awaiting_bill"], M)
    _sheet(wb, "Bill Register",
           ["Invoice", "Vendor", "Invoice Date", "File", "Status", "Shipment ID (written)", "Shipment ID (matched)", "Match",
            "Mode", "POs", "Subtotal", "Lines Total", "Tied", "Freight", "Customs", "Broker Fees", "Amount Due", "Read By", "Notes"],
           [[b["invoice_no"], b["vendor"], b["invoice_date"], b["file_name"], b["status"], b["shipment_id_raw"],
             b.get("shipment_id"), b.get("match_method"), b["mode"], ", ".join(b["po_refs"]), b["subtotal"], b["lines_total"],
             "yes" if b["tied"] else "no", b["freight"], b["customs"], b["broker_fees"], b["amount_due"], b["parse_method"],
             "; ".join(b["warnings"])] for b in state["bills"]], M)
    _sheet(wb, "Bill Charges",
           ["Invoice", "Shipment ID", "Line", "PDF Page", "Description", "Type", "Units", "Currency", "Rate", "FX", "Total USD", "Bucket"],
           [[b["invoice_no"], b.get("shipment_id"), c["line_no"], c["page"], c["description"], c["rate_type"], c["units"],
             c["currency"], c["rate"], c["fx"], c["total_usd"], c["bucket"]] for b in state["bills"] if not b["excluded"] for c in b["charges"]], M)
    issues = recon.id_issues(db)
    _sheet(wb, "ID Issues", ["Shipment ID", "Written As", "Issues", "Receipts", "NS LC Freight"],
           [[i["shipment_id"], " | ".join(i["written_as"]), "; ".join(i["issues"]), i["receipts"], i["ns_freight"]] for i in issues["netsuite_ids"]], M)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
