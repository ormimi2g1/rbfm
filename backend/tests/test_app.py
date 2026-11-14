import io
import time
import zipfile

from openpyxl import load_workbook

from .conftest import REPORT_CSV, chr_lines, make_pdf


def _upload(client, name, data):
    r = client.post("/api/bills/upload", files=[("files", (name, data, "application/octet-stream"))])
    assert r.status_code == 200
    return r.json()


def _ship(client, sid):
    return client.get(f"/api/shipments/{sid}").json()


def test_end_to_end(client):
    r = client.post("/api/netsuite/report", files={"file": ("ir.csv", REPORT_CSV.encode())})
    assert r.json() == {"rows": 5, "receipts_without_ibs": 1}

    # zip with a good bill, a junk file and a path-traversal name
    z = io.BytesIO()
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("V2/CH Robinson/2602-ABC-AIR-4_INV6200000001.pdf", make_pdf(chr_lines()))
        zf.writestr("../../evil.txt", "x")
    out = _upload(client, "bills.zip", z.getvalue())
    assert out["queued"] == 1 and any("ignored" in l["result"] for l in out["log"])

    s = _ship(client, "2602-ABC-AIR-4")
    assert s["status"] == "matched" and s["actual_freight"] == 1300.00 and s["ns_freight"] == 1000.00
    assert s["freight_var"] == -300.00 and "FREIGHT_VAR" in s["flags"] and len(s["receipts_detail"]) == 2
    assert s["bills"][0]["charges"][0]["bucket"] == "freight" and s["bills"][0]["match_method"] == "exact"

    # same file again is skipped; same invoice in a different file is not counted twice
    _upload(client, "copy of 6200000001.pdf", make_pdf(chr_lines()))
    assert [b["status"] for b in client.get("/api/bills").json()] == ["duplicate", "parsed"]
    assert _ship(client, "2602-ABC-AIR-4")["actual_freight"] == 1300.00

    # ocean bill filed against an AIR id, written with a typo -> not matched, suggestion offered
    ocean = chr_lines(invoice="6200000002", mode="S", ref="PO 9100", subtotal="270.00",
                      rows=["Dray to Yard Flat Rate 1 USD 150.00 USD 150.00", "Storage @ Yard Flat Rate 1 USD 120.00 USD 120.00"])
    _upload(client, "263-XYZ-AIR-3_INV6200000002.pdf", make_pdf(ocean))
    issues = client.get("/api/id-issues").json()
    um = issues["unmatched_bills"][0]
    assert um["flag"] == "NO_IBS" and um["suggestions"][0]["shipment_id"] == "2603-XYZ-AIR-3"
    assert {i["shipment_id"] for i in issues["netsuite_ids"]} == {"2603-XYZ-AIR-3", "2603-QRST-BOAT-1"}
    assert issues["receipts_without_ibs"]["rows"] == 1

    # confirm the correction as an alias -> bill lands on the shipment with the right flags
    client.post("/api/aliases", json={"written": "263-XYZ-AIR-3", "means": "2603-XYZ-AIR-3"})
    s = _ship(client, "2603-XYZ-AIR-3")
    assert s["status"] == "partial" and s["bill_count"] == 1
    for f in ("NO_LC", "MAIN_FREIGHT_BILL_MISSING", "MODE_MISMATCH", "ID_TYPO", "ID_FORMAT"):
        assert f in s["flags"], f
    assert "FREIGHT_VAR" not in s["flags"]

    d = client.get("/api/dashboard").json()
    assert d["shipments"] == {"matched": 1, "partial": 1, "awaiting_bill": 1}
    assert d["compared"]["under_landed"] == 300.00 and d["unmatched_bills"] == 0

    # NetSuite corrected after the true-up: new snapshot shows the change and clears the variance
    client.post("/api/netsuite/report", files={"file": ("ir2.csv", REPORT_CSV.replace("6000,700,900", "6000,1000,900").encode())})
    d = client.get("/api/dashboard").json()
    assert d["changes"] == [{"shipment_id": "2602-ABC-AIR-4", "change": "landed cost changed", "freight_now": 1300.0,
                             "freight_before": 1000.0, "customs_now": 1500.0, "customs_before": 1500.0}]
    assert "FREIGHT_VAR" not in _ship(client, "2602-ABC-AIR-4")["flags"]

    wb = load_workbook(io.BytesIO(client.get("/api/export.xlsx").content))
    assert wb.sheetnames == ["IBS Summary", "Awaiting Bills", "Bill Register", "Bill Charges", "ID Issues"]
    assert wb["IBS Summary"].max_row == 3 and wb["Bill Charges"].max_row == 7


def test_customs_bucket_and_threshold(client):
    client.post("/api/netsuite/report", files={"file": ("ir.csv", REPORT_CSV.encode())})
    rows = ["Air Freight Per Chg Wt 200 USD 5.01 USD 1,002.00", "Duty Subject to Liquidation Flat Rate 1 USD 1,400.00 USD 1,400.00",
            "Merchandise Processing Fee Flat Rate 1 USD 100.00 USD 100.00", "Customs Disbursement Fee Flat Rate 1 USD 25.00 USD 25.00"]
    _upload(client, "2602-ABC-AIR-4_INV6200000009.pdf", make_pdf(chr_lines(invoice="6200000009", rows=rows, subtotal="2,527.00")))
    s = _ship(client, "2602-ABC-AIR-4")
    assert (s["actual_freight"], s["actual_customs"], s["broker_fees"]) == (1002.00, 1500.00, 25.00)
    assert s["customs_var"] == 0.0 and s["freight_var"] == -2.00 and "FREIGHT_VAR" not in s["flags"]   # 0.2% < 2%
    client.put("/api/settings", json={"variance_abs": 1, "variance_pct": 0.1})
    assert "FREIGHT_VAR" in _ship(client, "2602-ABC-AIR-4")["flags"]


def test_report_with_missing_columns_is_rejected(client):
    r = client.post("/api/netsuite/report", files={"file": ("x.csv", b"Date,Thing\n1,2\n")})
    assert r.status_code == 400 and "Missing column" in r.json()["detail"]


def test_login_required(client, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "AUTH_DISABLED", False)
    monkeypatch.setattr(config, "APP_PASSWORD", "s3cret")
    assert client.get("/api/dashboard").status_code == 401
    assert client.post("/api/login", json={"password": "nope"}).status_code == 401
    assert client.post("/api/login", json={"password": "s3cret"}).status_code == 200
    assert client.get("/api/dashboard").status_code == 200
