import json

import httpx

from app import config, pii
from app.parsers import ai, chr as chr_, crane, expeditors, parse_pages, pdftext

from .conftest import chr_lines, make_pdf


def test_chr_wrapped_description_wrapped_total_and_fx():
    b = chr_.parse([chr_lines()])
    assert b.invoice_no == "6200000001" and b.mode == "AIR" and str(b.invoice_date) == "2026-03-03"
    assert [c.description for c in b.charges][1] == "Destination Terminal Handling Fee"
    assert b.charges[2].total_usd == 95.00 and b.charges[2].rate_type == "Fixed Charge"   # total on its own line
    assert b.charges[3].fx == 0.015 and b.charges[3].currency == "INR" and b.charges[3].total_usd == 150.00
    assert b.subtotal == 1300.00 and b.tied() and b.po_refs == ["PO009043", "PO009033"]
    assert b.chargeable_weight == 441 and b.hawb == "555000111PVG"


def test_chr_bill_that_does_not_tie_is_not_accepted():
    b = chr_.parse([chr_lines(subtotal="1,360.00")])
    assert not b.tied() and round(b.lines_total, 2) == 1300.00


def test_chr_through_a_real_pdf_file(tmp_path):
    p = tmp_path / "x.pdf"
    p.write_bytes(make_pdf(chr_lines(mode="S")))
    pages, ocr = pdftext.extract_pages(p)
    b = parse_pages(pages, ocr)
    assert b.vendor == "C.H. Robinson" and b.mode == "OCEAN" and b.tied() and b.method == "rules"


def test_crane_from_spec_description():
    lines = ["Crane Worldwide Logistics", "Invoice No. : IAIDFW250900001", "BL / AWB No. : HMAA10000001",
             "C.Wgt/M3/KT : 1,250.000", "AIR FREIGHT 4.250 1,250.000 KGS 5,312.50", "CWW-2025-000123",
             "FUEL SURCHARGE 0.800 1,250.000 KGS 1,000.00", "DELIVERY 175.000 1.000 SHP 175.00",
             "Total - US Dollar: SIX THOUSAND ... USD 6,487.50"]
    b = crane.parse([lines])
    assert b.invoice_no == "IAIDFW250900001" and b.mode == "AIR" and len(b.charges) == 3 and b.tied()
    brokerage = crane.parse([["Crane Worldwide", "Invoice No. : 5700001", "ENTRY FEE 137.500 1.000 SHP 137.50",
                              "Duties and Fees of $6,100.25 will be debited from your ACH account",
                              "Total - US Dollar: USD 137.50"]])
    assert brokerage.stated_customs == 6100.25 and brokerage.tied()


def test_expeditors_from_spec_description():
    lines = ["EXPEDITORS", "INVOICE NUMBER E930000001", "AWB/BL 1234567890", "ORIGINAL 03/01/26", "AIR FREIGHT 2,879.00",
             "HANDLING 421.11", "DUTY BY CLIENT USD 1,950.40", "SUB TOTAL: 3,300.11", "INVOICE TOTAL: 3,300.11 USD"]
    b = expeditors.parse([lines])
    assert b.invoice_no == "E930000001" and b.tied() and b.subtotal == 3300.11
    assert b.stated_customs == 1950.40 and all("DUTY" not in c.description for c in b.charges)


def test_redaction_keeps_invoice_numbers_but_drops_people():
    out = pii.redact_text("\n".join(pii.redact_lines(chr_lines())))
    assert "Jane" not in out and "example.com" not in out and "555-123-4567" not in out
    assert "6200000001" in out and "ATTN" not in out


def test_ai_fallback_for_unknown_layout(monkeypatch):
    sent = {}

    def handler(req: httpx.Request):
        sent["body"] = json.loads(req.content)
        answer = {"vendor": "New Forwarder", "invoice_no": "NF-1", "mode": "OCEAN", "po_numbers": ["PO 9195"],
                  "charges": [{"description": "Ocean Freight", "total_usd": 500}, {"description": "Docs", "total_usd": "50.00"}],
                  "subtotal": "550.00"}
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(answer)}}]})

    monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")
    client = httpx.Client(transport=httpx.MockTransport(handler))
    b = parse_pages([["New Forwarder Ltd", "Invoice NF-1", "Contact: John Smith john@nf.com", "Ocean Freight 500.00", "Docs 50.00", "Total 550.00"]], ai_client=client)
    assert b.method == "ai" and b.tied() and b.po_refs == ["PO009195"] and b.confidence < 1
    assert "John" not in sent["body"]["messages"][1]["content"]


def test_no_ai_key_means_unknown_layout_returns_nothing():
    assert not ai.available() and parse_pages([["Some unknown forwarder", "Total 5.00"]]) is None
