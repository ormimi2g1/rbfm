"""Reconciliation: bills vs NetSuite landed cost, per shipment. Everything is
computed from current data on each call, so re-runs are idempotent and a new
NetSuite snapshot, alias or bucket rule takes effect immediately.

Sign convention (as in the spec): variance = NetSuite landed - actual billed.
Negative = under-landed: more was billed than went into inventory cost."""
from collections import defaultdict

from sqlalchemy.orm import Session

from . import buckets, ids
from .models import Alias, Bill, BillItemLine, IbsItem, ItemReceipt
from .netsuite import store

COUNTED = ("parsed", "needs_review")

FLAG_TEXT = {
    "NO_LC": "Received in NetSuite with no landed cost at all",
    "NO_FREIGHT_LINE": "Freight was billed but NetSuite has no landed freight",
    "NO_CUSTOMS_LINE": "Customs was billed but NetSuite has no landed customs",
    "FREIGHT_VAR": "Landed freight differs from the bills beyond the threshold",
    "MAIN_FREIGHT_BILL_MISSING": "Only destination/extra charges loaded so far: variance is not final",
    "MODE_MISMATCH": "Bill mode (air/ocean) does not match the shipment ID",
    "CATEGORY_MISMATCH": "Landed-cost category does not match the shipment mode",
    "ID_FORMAT": "Shipment ID is written inconsistently in NetSuite",
    "ID_TYPO": "A bill reached this shipment only after correcting its ID",
    "PO_MISMATCH": "PO numbers on a bill are not on this shipment",
    "BILL_NOT_TIED": "A bill's lines do not add up to its printed total",
    "AI_READ": "A bill was read by AI: check it against the PDF",
    "NO_IBS": "Bill's shipment ID was not found in NetSuite",
    "NO_ID": "Bill has no shipment ID",
}


REVIEW_FLAGS = ("FREIGHT_VAR", "NO_LC", "NO_FREIGHT_LINE", "NO_CUSTOMS_LINE", "MODE_MISMATCH", "PO_MISMATCH", "BILL_NOT_TIED")


def _r(x) -> float:
    return round(float(x or 0), 2)


def bill_view(b: Bill, rules) -> dict:
    freight = customs = broker = 0.0
    has_main = False
    lines = []
    for c in b.charges:
        bucket = buckets.classify(c.description, rules)
        if bucket == "customs":
            customs += c.total_usd
        elif bucket == "broker_fee":
            broker += c.total_usd
        else:
            freight += c.total_usd
        has_main = has_main or bool(buckets.MAIN_FREIGHT_RE.search(c.description))
        lines.append({"line_no": c.line_no, "page": c.page, "description": c.description, "rate_type": c.rate_type,
                      "units": c.units, "currency": c.currency, "rate": c.rate, "fx": c.fx, "total_usd": _r(c.total_usd),
                      "bucket": bucket})
    return {
        "id": b.id, "file_name": b.file_name, "status": b.status, "vendor": b.vendor, "invoice_no": b.invoice_no,
        "invoice_date": b.invoice_date.isoformat() if b.invoice_date else None, "mode": b.mode, "hawb": b.hawb,
        "mawb": b.mawb, "shipment_number": b.shipment_number, "chargeable_weight": b.chargeable_weight,
        "weight_uom": b.weight_uom, "po_refs": b.po_refs or [], "subtotal": b.subtotal, "amount_due": b.amount_due,
        "payment_received": b.payment_received, "lines_total": b.lines_total, "tied": b.tied,
        "parse_method": b.parse_method, "confidence": b.confidence, "warnings": b.warnings or [],
        "shipment_id_raw": b.shipment_id_raw, "shipment_id_source": b.shipment_id_source, "excluded": b.excluded,
        "is_rebill": b.is_rebill, "freight": _r(freight), "customs": _r(customs + (b.stated_customs or 0)),
        "broker_fees": _r(broker), "stated_customs": b.stated_customs, "has_main_freight": has_main, "charges": lines,
        "created_at": b.created_at.isoformat() if b.created_at else None,
        "items_status": b.items_status, "items_printed_total": b.items_printed_total,
    }


def build(db: Session) -> dict:
    snap = store.latest(db)
    ns = store.shipments(db, snap.id) if snap else {}
    known = {sid: d["pos"] for sid, d in ns.items()}
    aliases = {ids.normalise(a.written): ids.normalise(a.means) for a in db.query(Alias)}
    rules = buckets.load_rules(db)
    st = buckets.settings(db)
    abs_thr, pct_thr = float(st.get("variance_abs", 1)), float(st.get("variance_pct", 2))

    by_ship, unmatched, all_bills = defaultdict(list), [], []
    for b in db.query(Bill).order_by(Bill.id):
        v = bill_view(b, rules)
        if b.status in COUNTED + ("duplicate",):
            res = ids.resolve(b.shipment_id_raw, known, aliases, b.po_refs or [])
            v["shipment_id"], v["match_method"], v["suggestions"] = res.shipment_id, res.method, res.suggestions
            if res.shipment_id:
                by_ship[res.shipment_id].append(v)
            elif b.status in COUNTED:
                v["flag"] = "NO_IBS" if b.shipment_id_raw else "NO_ID"
                unmatched.append(v)
        all_bills.append(v)

    shipments = []
    for sid in sorted(set(ns) | set(by_ship)):
        d = ns.get(sid)
        bills = by_ship.get(sid, [])
        counted = [x for x in bills if x["status"] in COUNTED and not x["excluded"]]
        parsed_id = ids.parse(sid) or {}
        s = {
            "shipment_id": sid, "written_as": sorted(set(d["written_as"])) if d else [], "mode": parsed_id.get("mode"),
            "vendor_code": parsed_id.get("vendor"), "goods_value": _r(d["goods_value"]) if d else 0.0,
            "ns_freight": _r(d["lc_freight"]) if d else 0.0, "ns_customs": _r(d["lc_customs"]) if d else 0.0,
            "receipts": d["receipts"] if d else 0, "pos": sorted(d["pos"]) if d else [],
            "last_receipt": d["last_date"].isoformat() if d and d["last_date"] else None,
            "freight_categories": sorted(d["freight_categories"]) if d else [],
            "bills": bills, "bill_count": len(counted),
            "actual_freight": _r(sum(x["freight"] for x in counted)),
            "actual_customs": _r(sum(x["customs"] for x in counted)),
            "broker_fees": _r(sum(x["broker_fees"] for x in counted)),
        }
        flags = []
        has_main = any(x["has_main_freight"] for x in counted)
        s["status"] = "awaiting_bill" if not counted else ("matched" if has_main else "partial")
        s["freight_var"] = s["freight_var_pct"] = s["customs_var"] = None
        if counted:
            s["freight_var"] = _r(s["ns_freight"] - s["actual_freight"])
            if s["actual_freight"]:
                s["freight_var_pct"] = round(100 * s["freight_var"] / s["actual_freight"], 2)
            if s["actual_customs"]:
                s["customs_var"] = _r(s["ns_customs"] - s["actual_customs"])
        if s["goods_value"]:
            s["freight_pct_ns"] = round(100 * s["ns_freight"] / s["goods_value"], 2)
            s["freight_pct_actual"] = round(100 * s["actual_freight"] / s["goods_value"], 2) if has_main else None
        if d and d["receipts"] and not s["ns_freight"] and not s["ns_customs"]:
            flags.append("NO_LC")
        elif counted and s["actual_freight"] > 0 and not s["ns_freight"]:
            flags.append("NO_FREIGHT_LINE")
        if counted and s["actual_customs"] > 0 and not s["ns_customs"] and "NO_LC" not in flags:
            flags.append("NO_CUSTOMS_LINE")
        if counted and not has_main:
            flags.append("MAIN_FREIGHT_BILL_MISSING")
        elif counted and s["ns_freight"] and abs(s["freight_var"]) > abs_thr and abs(s["freight_var_pct"] or 0) > pct_thr:
            flags.append("FREIGHT_VAR")
        id_mode = s["mode"] or ""
        for x in counted:
            if (x["mode"] == "OCEAN" and id_mode in ("AIR", "COUR")) or (x["mode"] == "AIR" and id_mode == "BOAT"):
                flags.append("MODE_MISMATCH")
            if x["match_method"] in ("normalised", "alias"):
                flags.append("ID_TYPO")
            if d and x["po_refs"] and not (set(x["po_refs"]) & d["pos"]) and d["pos"]:
                flags.append("PO_MISMATCH")
            if not x["tied"]:
                flags.append("BILL_NOT_TIED")
            if (x["parse_method"] or "").startswith("ai"):
                flags.append("AI_READ")
        cats = " ".join(s["freight_categories"]).lower()
        if (id_mode == "AIR" and "boat" in cats) or (id_mode == "BOAT" and "(air)" in cats):
            flags.append("CATEGORY_MISMATCH")
        if d and (len(set(d["written_as"])) > 1 or any(ids.format_issues(w) for w in d["written_as"])):
            flags.append("ID_FORMAT")
        s["flags"] = sorted(set(flags))
        shipments.append(s)

    # each bill also carries the verdict of its shipment, so the Bills page answers
    # "does this bill agree with NetSuite?" and not only "was the PDF read correctly?"
    by_id = {s["shipment_id"]: s for s in shipments}
    for v in all_bills:
        s = by_id.get(v.get("shipment_id") or "")
        if v["status"] not in COUNTED or v["excluded"]:
            v["ns_check"] = None
        elif not s:
            v["ns_check"] = {"state": "unmatched"}
        else:
            problems = [f for f in s["flags"] if f in REVIEW_FLAGS]
            state = "review" if problems else ("not_final" if s["status"] == "partial" else "ok")
            v["ns_check"] = {"state": state, "flags": problems, "freight_var": s["freight_var"], "freight_var_pct": s["freight_var_pct"],
                             "actual_freight": s["actual_freight"], "ns_freight": s["ns_freight"], "bills_on_shipment": s["bill_count"]}

    return {"snapshot": snap, "shipments": shipments, "unmatched_bills": unmatched, "bills": all_bills,
            "thresholds": {"variance_abs": abs_thr, "variance_pct": pct_thr}}


def dashboard(db: Session) -> dict:
    state = build(db)
    snap, ships = state["snapshot"], state["shipments"]
    final = [s for s in ships if s["status"] == "matched"]
    flag_counts = defaultdict(int)
    for s in ships:
        if s["bill_count"] or "NO_LC" in s["flags"] or "ID_FORMAT" in s["flags"]:
            for f in s["flags"]:
                flag_counts[f] += 1
    statuses = defaultdict(int)
    for b in state["bills"]:
        statuses[b["status"]] += 1
    top = sorted(final, key=lambda s: -abs(s["freight_var"] or 0))[:10]
    return {
        "netsuite": None if not snap else {
            "source": snap.source, "label": snap.label, "loaded_at": snap.created_at.isoformat(), "rows": snap.row_count,
            "shipments": len([s for s in ships if s["receipts"] or s["written_as"]]),
            "receipts_without_ibs": {"rows": snap.no_ibs_rows, "goods_value": _r(snap.no_ibs_value), "landed_freight": _r(snap.no_ibs_freight)},
            "landed_freight_total": _r(sum(s["ns_freight"] for s in ships) + snap.no_ibs_freight),
        },
        "bills": dict(statuses), "bills_total": len(state["bills"]),
        "unmatched_bills": len(state["unmatched_bills"]),
        "shipments": {"matched": len(final), "partial": len([s for s in ships if s["status"] == "partial"]),
                      "awaiting_bill": len([s for s in ships if s["status"] == "awaiting_bill"])},
        "compared": {
            "actual_freight": _r(sum(s["actual_freight"] for s in final)),
            "ns_freight": _r(sum(s["ns_freight"] for s in final)),
            "freight_var": _r(sum(s["freight_var"] for s in final)),
            "under_landed": _r(sum(-s["freight_var"] for s in final if s["freight_var"] < 0)),
            "over_landed": _r(sum(s["freight_var"] for s in final if s["freight_var"] > 0)),
        },
        "flags": [{"code": k, "text": FLAG_TEXT.get(k, k), "count": v} for k, v in sorted(flag_counts.items(), key=lambda kv: -kv[1])],
        "top_variances": [{k: s[k] for k in ("shipment_id", "actual_freight", "ns_freight", "freight_var", "freight_var_pct", "flags")} for s in top],
        "thresholds": state["thresholds"],
        "changes": changes(db),
    }


def changes(db: Session) -> list[dict]:
    """What moved in NetSuite between the last two snapshots."""
    cur, prev = store.latest(db), store.previous(db)
    if not cur or not prev:
        return []
    a, b = store.shipments(db, cur.id), store.shipments(db, prev.id)
    out = []
    for sid in sorted(set(a) | set(b)):
        x, y = a.get(sid), b.get(sid)
        if x and not y:
            out.append({"shipment_id": sid, "change": "new in NetSuite", "freight_now": _r(x["lc_freight"]), "freight_before": None})
        elif y and not x:
            out.append({"shipment_id": sid, "change": "no longer in NetSuite", "freight_now": None, "freight_before": _r(y["lc_freight"])})
        elif abs(x["lc_freight"] - y["lc_freight"]) > 0.005 or abs(x["lc_customs"] - y["lc_customs"]) > 0.005:
            out.append({"shipment_id": sid, "change": "landed cost changed", "freight_now": _r(x["lc_freight"]),
                        "freight_before": _r(y["lc_freight"]), "customs_now": _r(x["lc_customs"]), "customs_before": _r(y["lc_customs"])})
    return out


def shipment_detail(db: Session, sid: str) -> dict | None:
    state = build(db)
    s = next((x for x in state["shipments"] if x["shipment_id"] == sid), None)
    if not s:
        return None
    snap = state["snapshot"]
    s = dict(s, flag_text={f: FLAG_TEXT.get(f, f) for f in s["flags"]}, receipts_detail=[], sku_rows=[], sku_lines=None)
    if snap:
        for r in db.query(ItemReceipt).filter(ItemReceipt.snapshot_id == snap.id, ItemReceipt.ibs_norm == sid).order_by(ItemReceipt.receipt_date, ItemReceipt.doc_number):
            s["receipts_detail"].append({"doc_number": r.doc_number, "date": r.receipt_date.isoformat() if r.receipt_date else None, "po": r.po,
                                         "goods_value": _r(r.goods_value), "freight": _r(r.freight), "customs": _r(r.customs),
                                         "freight_pct": round(100 * r.freight / r.goods_value, 2) if r.goods_value else None})
        items = db.query(IbsItem).filter(IbsItem.snapshot_id == snap.id, IbsItem.ibs_norm == sid).all()
        s["sku_rows"] = sku_rows(items, s)
    bill_ids = [b["id"] for b in s["bills"] if b["status"] in COUNTED and not b["excluded"]]
    lines = db.query(BillItemLine).filter(BillItemLine.bill_id.in_(bill_ids)).order_by(BillItemLine.bill_id, BillItemLine.line_no).all() if bill_ids else []
    s["sku_lines"] = sku_allocation(lines, s)
    return s


def _spread(total: float, weights: list[float]) -> list[float]:
    """Split `total` by weight into cents that add back to exactly `total`."""
    wsum = sum(weights)
    if not wsum:
        return [0.0] * len(weights)
    cents = round(total * 100)
    raw = [cents * w / wsum for w in weights]
    out = [int(x // 1) for x in raw]
    order = sorted(range(len(raw)), key=lambda i: raw[i] - out[i], reverse=True)
    for i in order[: cents - sum(out)]:
        out[i] += 1
    return [c / 100 for c in out]


def sku_allocation(lines, s: dict) -> dict | None:
    """Spread the shipment's billed and NetSuite-landed freight over the SKU lines
    of the broker's Invoice Lines Report, by invoice value (NetSuite's VALUE
    method). The SKU variances add up exactly to the shipment variance."""
    if not lines:
        return None
    skus: dict[str, dict] = {}
    for l in lines:
        d = skus.setdefault(l.sku, {"sku": l.sku, "description": l.description, "qty": 0.0, "value": 0.0, "hts": [], "duty_rate": [],
                                    "lines": [], "pages": [], "notes": []})
        if not l.component:
            d["qty"] += l.qty or 0          # a set split into two customs lines is still one quantity
        d["value"] += l.value_usd or 0
        d["lines"].append(l.line_no)
        for key, val in (("hts", l.hts), ("duty_rate", l.duty_rate), ("pages", l.page), ("notes", l.note)):
            if val and val not in d[key]:
                d[key].append(val)
    rows = list(skus.values())
    weights = [r["value"] for r in rows]
    billed = _spread(s["actual_freight"], weights)
    landed = _spread(s["ns_freight"], weights)
    for r, b, n in zip(rows, billed, landed):
        r["value"] = _r(r["value"])
        r["billed_freight"], r["ns_freight"], r["variance"] = b, n, _r(n - b)
        r["variance_per_unit"] = round((n - b) / r["qty"], 4) if r["qty"] else None
        r["hts"], r["duty_rate"] = ", ".join(r["hts"]), ", ".join(r["duty_rate"])
        r["note"] = "; ".join(r.pop("notes")) or None
    total_value = _r(sum(weights))
    return {"rows": rows, "sku_count": len(rows), "line_count": len(lines), "invoice_value": total_value,
            "billed_freight": _r(sum(billed)), "ns_freight": _r(sum(landed)), "variance": _r(sum(landed) - sum(billed)),
            "goods_value_netsuite": s["goods_value"], "value_gap": _r(total_value - s["goods_value"]),
            "final": s["status"] == "matched"}


def sku_rows(items, s: dict) -> list[dict]:
    """Per style-colour comparison using value allocation (NetSuite's VALUE method).
    Actual freight/unit = PO rate x actual freight / goods value;
    NS freight/unit = unit landed cost x NS freight / NS landed total."""
    goods = sum(i.amount for i in items)
    ns_total = s["ns_freight"] + s["ns_customs"]
    if not items or not goods:
        return []
    groups = defaultdict(lambda: {"qty": 0.0, "amount": 0.0})
    for i in items:
        g = groups[(i.sku[:16], round(i.po_rate, 4), round(i.unit_landed_cost, 5))]
        g["qty"] += i.qty_expected
        g["amount"] += i.amount
    landed = sum(k[2] * g["qty"] for k, g in groups.items())
    avg_pct = 100 * landed / goods
    rows = []
    for (style, rate, ulc), g in sorted(groups.items()):
        actual_unit = rate * s["actual_freight"] / goods
        ns_unit = ulc * s["ns_freight"] / ns_total if ns_total else 0.0
        pct = 100 * ulc / rate if rate else None
        rows.append({"style": style, "po_rate": rate, "qty": g["qty"], "unit_landed_cost": ulc,
                     "actual_freight_unit": round(actual_unit, 5), "ns_freight_unit": round(ns_unit, 5),
                     "var_unit": round(ns_unit - actual_unit, 5), "extended_var": _r((ns_unit - actual_unit) * g["qty"]),
                     "lc_pct": round(pct, 2) if pct is not None else None,
                     "uneven": pct is not None and abs(pct - avg_pct) > 0.5})
    return rows


def id_issues(db: Session) -> dict:
    state = build(db)
    written = []
    for s in state["shipments"]:
        problems = {w: ids.format_issues(w) for w in s["written_as"]}
        if len(s["written_as"]) > 1 or any(problems.values()):
            written.append({"shipment_id": s["shipment_id"], "written_as": s["written_as"],
                            "issues": sorted({p for v in problems.values() for p in v} | ({"written more than one way"} if len(s["written_as"]) > 1 else set())),
                            "receipts": s["receipts"], "ns_freight": s["ns_freight"]})
    snap = state["snapshot"]
    return {"netsuite_ids": written, "unmatched_bills": state["unmatched_bills"],
            "receipts_without_ibs": None if not snap else {"rows": snap.no_ibs_rows, "goods_value": _r(snap.no_ibs_value), "landed_freight": _r(snap.no_ibs_freight)}}
