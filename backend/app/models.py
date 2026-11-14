"""Data model.

Bills come from PDFs. NetSuite data arrives as snapshots (an uploaded report or
an API sync); every snapshot is reduced to one NsShipment row per shipment ID so
reconciliation does not care which path produced it.
"""
from datetime import date, datetime, timezone

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Bill(Base):
    __tablename__ = "bill"
    id: Mapped[int] = mapped_column(primary_key=True)
    file_name: Mapped[str] = mapped_column(String(300))
    file_sha256: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    # queued | parsed | needs_review | unknown_format | failed | duplicate
    vendor: Mapped[str | None] = mapped_column(String(40))
    invoice_no: Mapped[str | None] = mapped_column(String(60), index=True)
    invoice_date: Mapped[date | None] = mapped_column(Date)
    shipment_number: Mapped[str | None] = mapped_column(String(60))   # forwarder's own reference
    hawb: Mapped[str | None] = mapped_column(String(60))
    mawb: Mapped[str | None] = mapped_column(String(60))
    mode: Mapped[str | None] = mapped_column(String(10))               # AIR | OCEAN
    chargeable_weight: Mapped[float | None] = mapped_column(Float)
    weight_uom: Mapped[str | None] = mapped_column(String(10))
    po_refs: Mapped[list] = mapped_column(JSON, default=list)
    subtotal: Mapped[float | None] = mapped_column(Float)
    payment_received: Mapped[float | None] = mapped_column(Float)
    amount_due: Mapped[float | None] = mapped_column(Float)
    lines_total: Mapped[float | None] = mapped_column(Float)
    tied: Mapped[bool] = mapped_column(Boolean, default=False)
    stated_customs: Mapped[float | None] = mapped_column(Float)        # duty stated but not billed here
    parse_method: Mapped[str | None] = mapped_column(String(20))       # rules | ai | rules+ocr | ai+ocr
    confidence: Mapped[float | None] = mapped_column(Float)
    warnings: Mapped[list] = mapped_column(JSON, default=list)
    shipment_id_raw: Mapped[str | None] = mapped_column(String(80))    # as written on the file name / typed by a user
    shipment_id_source: Mapped[str | None] = mapped_column(String(20)) # filename | manual | suggestion
    is_rebill: Mapped[bool] = mapped_column(Boolean, default=False)
    excluded: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    charges: Mapped[list["BillCharge"]] = relationship(back_populates="bill", cascade="all, delete-orphan", order_by="BillCharge.line_no")


class BillCharge(Base):
    __tablename__ = "bill_charge"
    id: Mapped[int] = mapped_column(primary_key=True)
    bill_id: Mapped[int] = mapped_column(ForeignKey("bill.id", ondelete="CASCADE"), index=True)
    line_no: Mapped[int] = mapped_column(Integer)
    page: Mapped[int | None] = mapped_column(Integer)
    description: Mapped[str] = mapped_column(String(200))
    rate_type: Mapped[str | None] = mapped_column(String(40))
    units: Mapped[float | None] = mapped_column(Float)
    currency: Mapped[str | None] = mapped_column(String(3))
    rate: Mapped[float | None] = mapped_column(Float)
    fx: Mapped[float | None] = mapped_column(Float)
    total_usd: Mapped[float] = mapped_column(Float)
    bill: Mapped[Bill] = relationship(back_populates="charges")


class NsSnapshot(Base):
    __tablename__ = "ns_snapshot"
    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(20))        # report | api
    label: Mapped[str | None] = mapped_column(String(300))
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    no_ibs_rows: Mapped[int] = mapped_column(Integer, default=0)
    no_ibs_value: Mapped[float] = mapped_column(Float, default=0)
    no_ibs_freight: Mapped[float] = mapped_column(Float, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class NsShipment(Base):
    """One row per shipment ID *as written* in NetSuite, per snapshot."""
    __tablename__ = "ns_shipment"
    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("ns_snapshot.id", ondelete="CASCADE"), index=True)
    ibs_raw: Mapped[str] = mapped_column(String(80))
    ibs_norm: Mapped[str] = mapped_column(String(80), index=True)
    ns_internal_id: Mapped[str | None] = mapped_column(String(30))
    goods_value: Mapped[float] = mapped_column(Float, default=0)
    lc_freight: Mapped[float] = mapped_column(Float, default=0)
    lc_customs: Mapped[float] = mapped_column(Float, default=0)
    freight_categories: Mapped[list] = mapped_column(JSON, default=list)  # API path only
    receipts: Mapped[int] = mapped_column(Integer, default=0)
    pos: Mapped[list] = mapped_column(JSON, default=list)
    first_date: Mapped[date | None] = mapped_column(Date)
    last_date: Mapped[date | None] = mapped_column(Date)


class ItemReceipt(Base):
    """Detail rows from the item-receipt report (report path)."""
    __tablename__ = "item_receipt"
    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("ns_snapshot.id", ondelete="CASCADE"), index=True)
    doc_number: Mapped[str] = mapped_column(String(40))
    receipt_date: Mapped[date | None] = mapped_column(Date)
    po: Mapped[str | None] = mapped_column(String(40), index=True)
    goods_value: Mapped[float] = mapped_column(Float, default=0)
    freight: Mapped[float] = mapped_column(Float, default=0)
    customs: Mapped[float] = mapped_column(Float, default=0)
    ibs_norm: Mapped[str | None] = mapped_column(String(80), index=True)


class IbsItem(Base):
    """SKU lines of an inbound shipment (API path) for the per-SKU view."""
    __tablename__ = "ibs_item"
    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("ns_snapshot.id", ondelete="CASCADE"), index=True)
    ibs_norm: Mapped[str] = mapped_column(String(80), index=True)
    sku: Mapped[str] = mapped_column(String(80))
    qty_expected: Mapped[float] = mapped_column(Float, default=0)
    po_rate: Mapped[float] = mapped_column(Float, default=0)
    amount: Mapped[float] = mapped_column(Float, default=0)
    unit_landed_cost: Mapped[float] = mapped_column(Float, default=0)


class Alias(Base):
    """Confirmed shipment-ID corrections: what was written -> what it means."""
    __tablename__ = "alias"
    id: Mapped[int] = mapped_column(primary_key=True)
    written: Mapped[str] = mapped_column(String(80), unique=True)
    means: Mapped[str] = mapped_column(String(80))
    note: Mapped[str | None] = mapped_column(String(200))


class BucketRule(Base):
    __tablename__ = "bucket_rule"
    id: Mapped[int] = mapped_column(primary_key=True)
    pattern: Mapped[str] = mapped_column(String(200), unique=True)   # case-insensitive regex on the charge description
    bucket: Mapped[str] = mapped_column(String(20))                   # customs | broker_fee | freight
    position: Mapped[int] = mapped_column(Integer, default=100)


class Setting(Base):
    __tablename__ = "setting"
    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[str] = mapped_column(String(200))
