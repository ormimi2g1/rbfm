"""Shipment-ID handling. The ID (YYMM-VENDOR-MODE-SEQ) is typed by hand in
NetSuite and on bill file names, so nothing here trusts it blindly: IDs are
normalised, checked against the format, and near-misses are *suggested*, never
applied automatically."""
import re
from dataclasses import dataclass, field

from rapidfuzz import fuzz

ID_RE = re.compile(r"^(?P<yymm>\d{4})-(?P<vendor>[A-Z]+)-(?P<mode>BOAT-AIR|AIR|BOAT|COUR)-(?P<seq>\d+(?:\.\d+)?)(?:-(?P<suffix>[A-Z0-9]+))?$")
PO_RE = re.compile(r"(?:PO\s*)?0*(\d{3,6})(?!\d)", re.I)


def normalise(raw: str | None) -> str:
    return re.sub(r"\s+", "", raw or "").upper()


def parse(norm: str) -> dict | None:
    m = ID_RE.match(norm)
    return m.groupdict() if m else None


def format_issues(raw: str) -> list[str]:
    """Problems with how an ID is written (empty list = clean)."""
    out = []
    if raw != raw.strip() or re.search(r"\s", raw.strip()):
        out.append("contains spaces")
    if raw != raw.upper():
        out.append("mixed case")
    p = parse(normalise(raw))
    if not p:
        out.append("does not follow YYMM-VENDOR-MODE-SEQ")
    elif not 1 <= int(p["yymm"][2:]) <= 12:
        out.append("month is not 01-12")
    return out


def normalise_pos(text: str | None) -> list[str]:
    """'009043,9033' / 'PO 9195 /PO 9234' -> ['PO009043', 'PO009033'] ..."""
    seen = []
    for m in PO_RE.finditer(text or ""):
        po = "PO" + m.group(1).zfill(6)
        if po not in seen:
            seen.append(po)
    return seen


@dataclass
class Resolution:
    written: str | None
    shipment_id: str | None            # canonical ID found in NetSuite, or None
    method: str                        # exact | normalised | alias | none
    suggestions: list[dict] = field(default_factory=list)


def suggest(norm: str, known: dict[str, set], bill_pos: list[str], limit: int = 3) -> list[dict]:
    """Rank candidate IDs. `known` maps canonical ID -> set of PO numbers.
    Shared PO numbers are the strongest evidence; spelling similarity second."""
    scored = []
    bill_pos_set = set(bill_pos)
    for cand, pos in known.items():
        shared = sorted(bill_pos_set & pos)
        sim = fuzz.ratio(norm, cand) if norm else 0
        if not shared and sim < 88:
            continue
        reason = []
        if shared:
            reason.append("shares " + ", ".join(shared))
        if sim >= 88:
            reason.append(f"{sim:.0f}% similar spelling")
        scored.append({"shipment_id": cand, "score": round(sim + (100 if shared else 0), 1), "reason": "; ".join(reason)})
    scored.sort(key=lambda s: -s["score"])
    return scored[:limit]


def resolve(written: str | None, known: dict[str, set], aliases: dict[str, str], bill_pos: list[str] | None = None) -> Resolution:
    norm = normalise(written)
    bill_pos = bill_pos or []
    if norm and norm in aliases and aliases[norm] in known:
        return Resolution(written, aliases[norm], "alias")
    if norm and norm in known:
        return Resolution(written, norm, "exact" if written == norm else "normalised")
    return Resolution(written, None, "none", suggest(norm, known, bill_pos))


FILENAME_RE = re.compile(r"^(?P<sid>\d{3,4}\s*-[^_]+?)_+(?:INV)?(?P<inv>[A-Za-z0-9-]+)", re.I)


def from_filename(name: str) -> tuple[str | None, str | None]:
    """'<ShipmentID>_INV<invoice>.pdf' -> (shipment id as written, invoice)."""
    stem = re.sub(r"\.pdf$", "", name.rsplit("/", 1)[-1], flags=re.I)
    stem = re.sub(r"\s*\(\d+\)$", "", stem)
    m = FILENAME_RE.match(stem)
    return (m.group("sid").strip(), m.group("inv")) if m else (None, None)
