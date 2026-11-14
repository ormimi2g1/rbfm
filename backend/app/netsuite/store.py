"""Shared snapshot handling for both NetSuite paths (report upload, API sync)."""
from sqlalchemy.orm import Session

from .. import config
from ..models import NsShipment, NsSnapshot


def latest(db: Session) -> NsSnapshot | None:
    return db.query(NsSnapshot).order_by(NsSnapshot.id.desc()).first()


def previous(db: Session) -> NsSnapshot | None:
    snaps = db.query(NsSnapshot).order_by(NsSnapshot.id.desc()).limit(2).all()
    return snaps[1] if len(snaps) > 1 else None


def prune(db: Session):
    old = db.query(NsSnapshot).order_by(NsSnapshot.id.desc()).offset(config.KEEP_SNAPSHOTS).all()
    for s in old:
        db.delete(s)   # ON DELETE CASCADE removes detail rows
    db.commit()


def shipments(db: Session, snapshot_id: int) -> dict[str, dict]:
    """Shipments of a snapshot keyed by normalised ID. Different spellings of
    the same ID ('2508-XYZ-AIR- 3' and '2508-XYZ-AIR-3') are merged, and the
    spellings are kept so they can be flagged."""
    out: dict[str, dict] = {}
    for s in db.query(NsShipment).filter(NsShipment.snapshot_id == snapshot_id):
        d = out.setdefault(s.ibs_norm, {"shipment_id": s.ibs_norm, "written_as": [], "goods_value": 0.0, "lc_freight": 0.0,
                                        "lc_customs": 0.0, "receipts": 0, "pos": set(), "freight_categories": set(),
                                        "first_date": None, "last_date": None, "ns_internal_ids": []})
        d["written_as"].append(s.ibs_raw)
        d["goods_value"] += s.goods_value or 0
        d["lc_freight"] += s.lc_freight or 0
        d["lc_customs"] += s.lc_customs or 0
        d["receipts"] += s.receipts or 0
        d["pos"] |= set(s.pos or [])
        d["freight_categories"] |= set(s.freight_categories or [])
        if s.ns_internal_id:
            d["ns_internal_ids"].append(s.ns_internal_id)
        if s.first_date and (d["first_date"] is None or s.first_date < d["first_date"]):
            d["first_date"] = s.first_date
        if s.last_date and (d["last_date"] is None or s.last_date > d["last_date"]):
            d["last_date"] = s.last_date
    return out
