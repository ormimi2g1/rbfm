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
    ocean = crane.parse([["Crane Worldwide Logistics", "LLC Date : 10/30/2026 Page : 1 / 1", "Invoice No. : ISIDFW260100001 /",
                          "Attn: Jane Doe Job No. : HSI0001", "BL / AWB No. : MLCW0001", "Port of Loading : X C.Wgt/M3/KT : 15.800 CBM",
                          "Destination : DFW Service Level: LCL Ocean Freight", "Description Rate Quantity Tax Amount", "ORIGINAL",
                          "Less Than Container Freight (LCL) 297.000 15.800 CBM 4,692.60",
                          "Customs Participating Government Agency 69.000 1.000 SHP 69.00", "(PGA) Fee",
                          "Destination Handling Fee 110.000 1.000 SHP 110.00",
                          "Total - US Dollar:FOUR THOUSAND ... ONLY USD 4,871.60", "Prepared By: Jane Doe"],
                         ["INVOICE", "PO009043 Some Style 300 3.800 1140.00"]])            # supplier invoice page is ignored
    assert ocean.mode == "OCEAN" and ocean.tied() and len(ocean.charges) == 3 and str(ocean.invoice_date) == "2026-10-30"
    assert ocean.charges[1].description.endswith("(PGA) Fee") and ocean.weight_uom == "CBM"
    brokerage = crane.parse([["Crane Worldwide", "Invoice No. : 5700001", "ENTRY FEE 137.500 1.000 SHP 137.50",
                              "Duties and Fees of $6,100.25 will be debited from your ACH account",
                              "Total - US Dollar: USD 137.50"]])
    assert brokerage.stated_customs == 6100.25 and brokerage.tied()


EXPEDITORS_LINES = [      # the real layout, made-up values; "ORIGINAL" is printed at the very end
    "REMIT TO", "EXPEDITORS INT'L OF WA INC", "506 EAST SOME ROAD, STE 400", "CLIENT NO: G0000001 INVOICE 03/01/26", "DATE",
    "Some Importer LLC", "1 Main Pkwy INVOICE E230000001", "NUMBER", "YOUR", "PO009043", "REFERENCE",
    "AWB/BL: ABCD1234567 HAWB/HBL: 62G0000001 ARRIVAL: 03/01/26", "CARRIER: SOME VESSEL CUSTOMS#: 231-0000000-0",
    "PCS: 18PCS WEIGHT: 161.0K", "DUTY BY CLIENT USD 1,950.40", "THROUGH ABI VIA ACH ON 04/01/26",
    "HBL FREIGHT MINIMUM 170.00", "MANIFEST FEE 35.00", "CUSTOMS ENTRY FEE 95.00", "LOCAL DELIVERY 351.00",
    "SUB TOTAL: 651.00", "INVOICE TOTAL: 651.00 USD", "SERVICE LEVEL: CFS to CFS", "CO ABCU1234567 18 40H",
    "Routing No.: 121000248 Routing No.: 121000248", "ORIGINAL"]


def test_expeditors_real_layout():
    emails = ["From: Jane Doe <jane@example.com>", "LTL: $351", "TOTAL DUE 999.00"]      # attached pages are ignored
    b = expeditors.parse([EXPEDITORS_LINES, emails])
    assert b.invoice_no == "E230000001" and str(b.invoice_date) == "2026-03-01" and b.mode == "OCEAN"
    assert [c.total_usd for c in b.charges] == [170.0, 35.0, 95.0, 351.0] and b.tied() and b.subtotal == 651.0
    assert b.stated_customs == 1950.40 and all("DUTY" not in c.description for c in b.charges)
    assert b.po_refs == ["PO009043"] and b.hawb == "62G0000001" and b.chargeable_weight == 161.0


def test_sideways_margin_text_is_ignored(tmp_path):
    import io
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(612, 792))
    c.setFont("Helvetica", 8)
    c.drawString(60, 700, "MANIFEST FEE 35.00")
    for n in range(8):
        c.drawString(60, 600 - 14 * n, f"FILLER LINE NUMBER {n} OF EIGHT")
    c.saveState()
    c.translate(40, 690)
    c.rotate(90)
    c.drawString(0, 0, "TERMS AND CONDITIONS")
    c.restoreState()
    c.save()
    p = tmp_path / "m.pdf"
    p.write_bytes(buf.getvalue())
    pages, _ = pdftext.extract_pages(p)
    assert pages[0][0] == "MANIFEST FEE 35.00" and len(pages[0]) == 9 and not any("TERMS" in ln for ln in pages[0])


def test_main_freight_is_recognised_under_forwarder_wordings():
    from app.buckets import MAIN_FREIGHT_RE
    for d in ("Air Freight", "Ocean Freight", "HBL FREIGHT MINIMUM", "Less Than Container Freight (LCL)", "FCL 40HC"):
        assert MAIN_FREIGHT_RE.search(d), d
    for d in ("Dray to Yard", "Storage @ Yard", "Customs Entry Fee", "Destination Terminal Handling Fee"):
        assert not MAIN_FREIGHT_RE.search(d), d


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
