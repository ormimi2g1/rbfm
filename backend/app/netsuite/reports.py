"""Report path: the item-receipt export (one row per item receipt with its
landed freight and customs and the shipment ID). Works without API access."""
import csv
import io
from datetime import date, datetime

from openpyxl import load_workbook
from sqlalchemy.orm import Session

from ..ids import normalise
from ..models import IbsMap, ItemReceipt, NsShipment, NsSnapshot
from . import store

COLUMNS = {  # our field -> accepted header spellings (lower-case)
    "date": ["date"],
    "doc": ["document number", "document no", "doc number"],
    "po": ["po#", "po", "po number", "created from"],
    "value": ["total value excluding lc", "amount", "total value"],
    "freight": ["freight", "lc freight", "landed freight"],
    "customs": ["customs", "lc customs", "landed customs"],
    "ibs": ["ibs", "shipment id", "inbound shipment"],
}
NO_IBS_MARKERS = {"", "IRRR", "#N/A", "NONE", "-NONE-"}


class ReportError(ValueError):
    pass


def _rows(data: bytes, filename: str):
    if filename.lower().endswith(".csv"):
        yield from csv.reader(io.StringIO(data.decode("utf-8-sig", errors="replace")))
        return
    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    yield from wb.worksheets[0].iter_rows(values_only=True)


def _f(v) -> float:
    if v is None or v == "":
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).replace(",", "").replace("$", ""))
    except ValueError:
        return 0.0


def _d(v) -> date | None:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y"):
        try:
            return datetime.strptime(str(v).strip(), fmt).date()
        except (ValueError, TypeError):
            pass
    return None


def import_ibs_map(db: Session, data: bytes, filename: str) -> int:
    """The lookup report: one row per item receipt with its shipment ID."""
    rows = _rows(data, filename)
    header = [str(h or "").strip().lower() for h in (next(rows, None) or [])]
    ibs_col = next((i for i, h in enumerate(header) if "shipment" in h or h == "ibs"), None)
    ir_col = next((i for i, h in enumerate(header) if h.startswith("ir") or "document" in h or "receipt" in h), None)
    if ibs_col is None or ir_col is None:
        raise ReportError("Expected two columns: the shipment ID and the item receipt number (IR#).")
    pairs = {}
    for r in rows:
        ibs, ir = str(r[ibs_col] or "").strip(), str(r[ir_col] or "").strip()
        if ibs and ir.upper().startswith("IR"):
            pairs[ir] = ibs
    if not pairs:
        raise ReportError("No item receipt numbers found in that file.")
    db.query(IbsMap).delete()
    db.bulk_insert_mappings(IbsMap, [{"doc_number": k, "ibs_raw": v} for k, v in pairs.items()])
    db.commit()
    return len(pairs)


def import_item_receipts(db: Session, data: bytes, filename: str) -> NsSnapshot:
    rows = _rows(data, filename)
    header = next(rows, None)
    if not header:
        raise ReportError("The file is empty.")
    names = [str(h or "").strip().lower() for h in header]
    idx = {}
    for field, options in COLUMNS.items():
        for o in options:
            if o in names:
                idx[field] = names.index(o)
                break
    ibs_map = {m.doc_number: m.ibs_raw for m in db.query(IbsMap)}
    missing = [COLUMNS[f][0] for f in ("doc", "freight", "customs") + (() if ibs_map else ("ibs",)) if f not in idx]
    if missing:
        raise ReportError("Missing column(s): " + ", ".join(missing) + ". Found: " + ", ".join(n for n in names if n))

    snap = NsSnapshot(source="report", label=filename[:300])
    db.add(snap)
    db.flush()
    agg: dict[str, dict] = {}
    receipts, n, blank_ibs = [], 0, 0
    for r in rows:
        get = lambda f: r[idx[f]] if f in idx and idx[f] < len(r) else None  # noqa: E731
        doc = str(get("doc") or "").strip()
        if not doc:
            continue                                   # 'Overall Total' and blank rows
        n += 1
        raw = str(get("ibs") or "").strip()
        if raw.upper() in NO_IBS_MARKERS and doc in ibs_map:
            raw = ibs_map[doc]                      # fall back to the receipt -> shipment lookup report
        if not raw:
            blank_ibs += 1
        value, freight, customs, when = _f(get("value")), _f(get("freight")), _f(get("customs")), _d(get("date"))
        po = str(get("po") or "").strip() or None
        has_ibs = raw.upper() not in NO_IBS_MARKERS
        norm = normalise(raw) if has_ibs else None
        receipts.append(dict(snapshot_id=snap.id, doc_number=doc, receipt_date=when, po=po, goods_value=value,
                             freight=freight, customs=customs, ibs_norm=norm))
        if not has_ibs:
            snap.no_ibs_rows += 1
            snap.no_ibs_value += value
            snap.no_ibs_freight += freight
            continue
        a = agg.setdefault(raw, dict(snapshot_id=snap.id, ibs_raw=raw, ibs_norm=norm, goods_value=0.0, lc_freight=0.0,
                                     lc_customs=0.0, receipts=0, pos=set(), first_date=None, last_date=None))
        a["goods_value"] += value
        a["lc_freight"] += freight
        a["lc_customs"] += customs
        a["receipts"] += 1
        if po:
            a["pos"].add(po)
        if when:
            a["first_date"] = min(a["first_date"] or when, when)
            a["last_date"] = max(a["last_date"] or when, when)
    if n and blank_ibs == n:
        db.rollback()
        raise ReportError("No shipment IDs found. The IBS column is a lookup formula with no saved values: open the file "
                          "in Excel, let it calculate and save again, or upload the receipt-to-shipment lookup report first.")
    snap.row_count = n
    db.bulk_insert_mappings(ItemReceipt, receipts)
    for a in agg.values():
        a["pos"] = sorted(a["pos"])
        for k in ("goods_value", "lc_freight", "lc_customs", ):
            a[k] = round(a[k], 2)
        db.add(NsShipment(**a))
    db.commit()
    store.prune(db)
    return snap
