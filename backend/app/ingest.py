"""Bill intake: PDFs and zip archives -> queue -> parsed Bill rows.
The PDF is deleted after parsing (invoices print bank details; only the
structured fields are kept, each charge with its page number)."""
import hashlib
import re
import zipfile
from pathlib import Path

from sqlalchemy.orm import Session

from . import config, ids, parsers
from .db import SessionLocal
from .models import Bill, BillCharge, BillItemLine
from .parsers import invoice_lines

REBILL_RE = re.compile(r"^(\w+?)\s*-\s*\d{1,2}$")


def _enqueue(db: Session, name: str, data: bytes, log: list):
    if not data.startswith(b"%PDF"):
        log.append({"file": name, "result": "skipped: not a PDF"})
        return None
    if len(data) > config.MAX_PDF_BYTES:
        log.append({"file": name, "result": "skipped: file too large"})
        return None
    sha = hashlib.sha256(data).hexdigest()
    prior = db.query(Bill).filter(Bill.file_sha256 == sha).first()
    if prior and prior.status in ("failed", "unknown_format"):
        db.delete(prior)          # let a file that could not be read be tried again
        db.flush()
    elif prior:
        log.append({"file": name, "result": "skipped: this exact file was already uploaded"})
        return None
    (config.QUEUE_DIR / f"{sha}.pdf").write_bytes(data)
    sid, _ = ids.from_filename(name)
    bill = Bill(file_name=Path(name).name[:300], file_sha256=sha, status="queued",
                shipment_id_raw=sid, shipment_id_source="filename" if sid else None)
    db.add(bill)
    db.flush()
    log.append({"file": name, "result": "queued"})
    return bill.id


def accept_upload(db: Session, name: str, data: bytes) -> tuple[list[int], list[dict]]:
    """Returns (bill ids to parse, per-file log)."""
    log, out = [], []
    if name.lower().endswith(".zip") or data[:2] == b"PK":
        try:
            with zipfile.ZipFile(__import__("io").BytesIO(data)) as z:
                members = [m for m in z.infolist() if not m.is_dir()]
                if len(members) > config.MAX_ZIP_MEMBERS:
                    return [], [{"file": name, "result": f"rejected: more than {config.MAX_ZIP_MEMBERS} files in the zip"}]
                for m in members:
                    base = Path(m.filename).name      # never trust paths inside an archive
                    if base.startswith(".") or "__MACOSX" in m.filename:
                        continue
                    if not base.lower().endswith(".pdf"):
                        log.append({"file": m.filename, "result": "ignored: not a PDF"})
                        continue
                    if m.file_size > config.MAX_PDF_BYTES:
                        log.append({"file": m.filename, "result": "skipped: file too large"})
                        continue
                    bid = _enqueue(db, base, z.read(m), log)
                    if bid:
                        out.append(bid)
        except zipfile.BadZipFile:
            log.append({"file": name, "result": "rejected: not a readable zip"})
    else:
        bid = _enqueue(db, name, data, log)
        if bid:
            out.append(bid)
    db.commit()
    return out, log


def apply_parsed(db: Session, bill: Bill, p: parsers.base.ParsedBill | None):
    if p is None or (not p.charges and p.subtotal is None):
        bill.status = "unknown_format"
        bill.warnings = (p.warnings if p else []) + (
            [] if parsers.ai.available() else ["No rules match this layout and no OPENAI_API_KEY is set for the AI reader"])
        return
    bill.vendor, bill.invoice_no, bill.invoice_date = p.vendor, p.invoice_no, p.invoice_date
    bill.shipment_number, bill.hawb, bill.mawb, bill.mode = p.shipment_number, p.hawb, p.mawb, p.mode
    bill.chargeable_weight, bill.weight_uom, bill.po_refs = p.chargeable_weight, p.weight_uom, p.po_refs
    bill.subtotal, bill.payment_received, bill.amount_due = p.subtotal, p.payment_received, p.amount_due
    bill.stated_customs, bill.parse_method, bill.confidence = p.stated_customs, p.method, p.confidence
    bill.lines_total, bill.tied, bill.warnings = round(p.lines_total, 2), p.tied(config.TIE_TOLERANCE), list(p.warnings)
    bill.charges = [BillCharge(line_no=i, page=c.page, description=c.description[:200], rate_type=c.rate_type, units=c.units,
                               currency=c.currency, rate=c.rate, fx=c.fx, total_usd=c.total_usd or 0)
                    for i, c in enumerate(p.charges, start=1)]
    bill.status = "parsed" if bill.tied else "needs_review"
    if not bill.tied:
        bill.warnings.append(f"Lines add to {bill.lines_total:,.2f} but the printed total is "
                             f"{'missing' if p.subtotal is None else format(p.subtotal, ',.2f')}")
    if bill.invoice_no:
        twin = db.query(Bill).filter(Bill.invoice_no == bill.invoice_no, Bill.id != bill.id,
                                     Bill.status.in_(["parsed", "needs_review"])).first()
        if twin:
            bill.status, bill.excluded = "duplicate", True
            bill.warnings.append(f"Same invoice number as '{twin.file_name}': not counted twice")
        m = REBILL_RE.match(bill.invoice_no)
        if m and db.query(Bill).filter(Bill.invoice_no == m.group(1)).first():
            bill.is_rebill, bill.excluded = True, True
            bill.warnings.append("Balance-due re-bill of an invoice already loaded: not additional cost")


def process(bill_ids: list[int]):
    """Background worker: parse queued bills one by one."""
    with SessionLocal() as db:
        for bid in bill_ids:
            bill = db.get(Bill, bid)
            if not bill or bill.status != "queued":
                continue
            path = config.QUEUE_DIR / f"{bill.file_sha256}.pdf"
            try:
                apply_parsed(db, bill, parsers.parse_pdf(path))
            except Exception as e:
                bill.status = "failed"
                bill.warnings = [f"Could not read this PDF ({type(e).__name__})"]
            db.commit()                      # the bill is usable now; SKU lines can take minutes
            try:
                read_item_lines(db, bill, path)
            except Exception as e:
                bill.items_status = "failed"
                bill.warnings = list(bill.warnings or []) + [f"SKU lines could not be read ({type(e).__name__})"]
            finally:
                path.unlink(missing_ok=True)
            db.commit()


def read_item_lines(db: Session, bill: Bill, path):
    """Read the attached Invoice Lines Report (if any) into SKU lines."""
    if bill.status not in ("parsed", "needs_review") or not invoice_lines.OCR_OK:
        return
    if bill.vendor != "C.H. Robinson":       # only their packets carry the Invoice Lines Report
        bill.items_status = "none"
        return
    bill.items_status = "reading"
    db.commit()
    res = invoice_lines.extract(path, start_page=config.PARSE_PAGES - 2)
    if res is None:
        bill.items_status = "none"
        return
    bill.items = [BillItemLine(line_no=l.line_no, page=l.page, sku=l.sku[:60], description=l.description, qty=l.qty,
                               value_usd=l.value_usd, hts=l.hts, duty_rate=l.duty_rate, component=l.component, note=l.note)
                  for l in res.lines]
    bill.items_printed_total = res.printed_total
    bill.items_status = "tied" if res.tied() else ("not_tied" if res.lines else "failed")
    bill.warnings = list(bill.warnings or []) + res.warnings


def requeue_orphans():
    """After a restart: finish bills whose file is still waiting, fail the rest."""
    with SessionLocal() as db:
        todo = []
        for b in db.query(Bill).filter(Bill.status == "queued"):
            if (config.QUEUE_DIR / f"{b.file_sha256}.pdf").exists():
                todo.append(b.id)
            else:
                b.status, b.warnings = "failed", ["Upload was interrupted: please upload this file again"]
        db.commit()
    if todo:
        process(todo)
