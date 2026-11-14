"""AI fallback reader (OpenAI) for unknown layouts and bills the rules cannot
tie. Text is redacted first; the result is still subject to the tie-out check
and is always marked as AI-read."""
import json

import httpx

from .. import config, pii
from ..ids import normalise_pos
from .base import Charge, ParsedBill, money, parse_date

SYSTEM = """You extract data from freight forwarder / customs broker invoices.
Return ONLY a JSON object with these keys:
vendor (company that issued the invoice), invoice_no, invoice_date (YYYY-MM-DD or null),
shipment_number, hawb (house bill / BL no.), mawb, mode ("AIR", "OCEAN" or null),
chargeable_weight (number or null), weight_uom, po_numbers (array of strings),
charges (array of {description, rate_type, units, currency, rate, fx, total_usd}),
subtotal (the printed total of the charge lines, before payments), payment_received, amount_due,
stated_customs (duty the invoice says is paid separately and is NOT a charge line, else null).
Rules: copy numbers exactly as printed; never invent or compute a line; total_usd is the USD
amount of each line; do not include personal names, emails, phone numbers or bank details."""


def available() -> bool:
    return bool(config.OPENAI_API_KEY)


def parse(pages: list[list[str]], client: httpx.Client | None = None) -> ParsedBill | None:
    if not available():
        return None
    text = pii.redact_text("\n".join("\n".join(pii.redact_lines(p)) for p in pages))[:14000]
    payload = {
        "model": config.OPENAI_MODEL,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": text}],
    }
    own = client is None
    client = client or httpx.Client(timeout=90)
    try:
        r = client.post(f"{config.OPENAI_BASE_URL}/chat/completions", json=payload,
                        headers={"Authorization": f"Bearer {config.OPENAI_API_KEY}"})
        r.raise_for_status()
        data = json.loads(r.json()["choices"][0]["message"]["content"])
    except Exception as e:  # network, quota, bad JSON: the bill simply goes to review
        b = ParsedBill(vendor="Unknown", method="ai", confidence=0)
        b.warnings.append(f"AI reader failed: {type(e).__name__}")
        return b
    finally:
        if own:
            client.close()
    return from_json(data)


def from_json(d: dict) -> ParsedBill:
    b = ParsedBill(vendor=str(d.get("vendor") or "Unknown")[:40], method="ai", confidence=0.6)
    b.invoice_no = (str(d["invoice_no"]).strip() if d.get("invoice_no") else None)
    b.invoice_date = parse_date(d.get("invoice_date"))
    b.shipment_number, b.hawb, b.mawb = d.get("shipment_number"), d.get("hawb"), d.get("mawb")
    b.mode = d.get("mode") if d.get("mode") in ("AIR", "OCEAN") else None
    b.chargeable_weight, b.weight_uom = money(d.get("chargeable_weight")), d.get("weight_uom")
    b.po_refs = normalise_pos(" , ".join(str(x) for x in d.get("po_numbers") or []))
    for c in d.get("charges") or []:
        b.charges.append(Charge(description=str(c.get("description") or "")[:200], rate_type=c.get("rate_type"),
                                units=money(c.get("units")), currency=c.get("currency"), rate=money(c.get("rate")),
                                fx=money(c.get("fx")), total_usd=money(c.get("total_usd"))))
    b.subtotal, b.payment_received = money(d.get("subtotal")), money(d.get("payment_received"))
    b.amount_due, b.stated_customs = money(d.get("amount_due")), money(d.get("stated_customs"))
    b.warnings.append("Read by AI: check against the PDF")
    return b
