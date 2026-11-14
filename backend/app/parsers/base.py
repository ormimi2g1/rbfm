from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation


def money(s) -> float | None:
    """'1,234.56' / '(50.00)' / '-50.00' -> float; None if not a number."""
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return round(float(s), 2)
    t = str(s).strip().replace(",", "").replace("$", "").replace("USD", "").strip()
    neg = t.startswith("(") and t.endswith(")")
    try:
        v = Decimal(t.strip("()"))
    except InvalidOperation:
        return None
    return float(-v if neg else v)


def num(s) -> float | None:
    v = money(s)
    return v


def parse_date(s: str | None) -> date | None:
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d", "%d-%b-%Y", "%d %b %Y"):
        try:
            return datetime.strptime((s or "").strip(), fmt).date()
        except ValueError:
            pass
    return None


@dataclass
class Charge:
    description: str
    total_usd: float | None
    rate_type: str | None = None
    units: float | None = None
    currency: str | None = None
    rate: float | None = None
    fx: float | None = None
    page: int | None = None


@dataclass
class ParsedBill:
    vendor: str
    invoice_no: str | None = None
    invoice_date: date | None = None
    shipment_number: str | None = None
    hawb: str | None = None
    mawb: str | None = None
    mode: str | None = None                # AIR | OCEAN
    chargeable_weight: float | None = None
    weight_uom: str | None = None
    po_refs: list[str] = field(default_factory=list)
    charges: list[Charge] = field(default_factory=list)
    subtotal: float | None = None          # the printed total the lines must add up to
    payment_received: float | None = None
    amount_due: float | None = None
    stated_customs: float | None = None    # duty stated on the bill but not charged on it
    method: str = "rules"
    confidence: float = 1.0
    warnings: list[str] = field(default_factory=list)

    @property
    def lines_total(self) -> float:
        return float(sum(Decimal(str(c.total_usd or 0)) for c in self.charges))

    def tied(self, tol: float = 0.02) -> bool:
        """Every bill must tie: sum of charge lines = printed total."""
        if self.subtotal is None or not self.charges:
            return False
        if any(c.total_usd is None for c in self.charges):
            return False
        return abs(self.lines_total - self.subtotal) <= tol + 1e-9
