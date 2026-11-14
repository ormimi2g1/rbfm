from types import SimpleNamespace

from app import recon
from app.parsers import invoice_lines as il

from .conftest import REPORT_CSV


def _cell(code, code2, qty, price, page=5, tariff="6111.30.5010", rate="16%"):
    return {"page": page, "code": code, "code2": code2, "qty": qty, "price": price, "tariff": tariff, "rate": rate, "desc": "STYLE - SIZE"}


def _pages():
    sizes = ["0306M", "0612M", "1218M", "1824M", "2T000", "3T000"]
    p1 = [_cell("1AB1234-00-P", f"1200-{s}", "10.00", "50.00") for s in sizes for _ in range(1)] * 5   # makes every size common
    p2 = [
        _cell("Z1AB1234-00-P", "1200-2T000", "20.00", "100.00", page=6),     # stray leading character
        _cell("1A8B1234-00-P", "1200-3TO000", "10.00", "50.00", page=6),     # inserted characters in both halves
        _cell("1AB1234-00-P", "1200-21000", "10.00", "50.00", page=6),       # T read as 1
        _cell("2CD5678-00-P", "0627-2T3TO", "36.00", "107.50", page=6),      # letter O for zero, rare size
        _cell("", "", "36.00", "107.50", page=6),                            # second customs line of the set above
        _cell("2CD5678-00-P", "OS", "5.00", "42.56", page=6),                # code cut by the page break ...
    ]
    return [(p1, "", None), (p2, "", None), ([], "0627-1218M", 1957.56)]      # ... and continued on the next page


def test_assemble_cleans_codes_and_ties_to_the_printed_total():
    res = il.assemble(_pages())
    assert len(res.lines) == 36 and res.printed_total == 1957.56 and res.tied() and not res.warnings
    tail = res.lines[30:]
    assert [l.sku for l in tail] == ["1AB1234-00-P1200-2T000", "1AB1234-00-P1200-3T000", "1AB1234-00-P1200-2T000",
                                     "2CD5678-00-P0627-2T3T0", "2CD5678-00-P0627-2T3T0", "2CD5678-00-P0627-1218M"]
    assert tail[4].component and not tail[3].component
    assert "corrected" in tail[1].note and "corrected" in tail[2].note and tail[0].note is None


def test_assemble_reports_a_total_that_does_not_tie():
    pages = _pages()
    pages[1][0][0]["price"] = "1000.0"            # a lost decimal point
    res = il.assemble(pages)
    assert not res.tied() and "add to" in res.warnings[0]
    assert any("unit price" in (l.note or "") for l in res.lines)


def test_allocation_adds_up_exactly_to_the_shipment_variance():
    lines = [SimpleNamespace(sku=f"S{i}", description="d", qty=3.0, value_usd=v, hts="6111", duty_rate="16%", line_no=i, page=5,
                             component=False, note=None) for i, v in enumerate([33.33, 33.33, 33.34, 219.45, 0.07], start=1)]
    lines.append(SimpleNamespace(sku="S4", description="d", qty=3.0, value_usd=100.0, hts="6104", duty_rate="14.9%", line_no=6, page=5,
                                 component=True, note=None))
    s = {"actual_freight": 11810.48, "ns_freight": 9395.95, "goods_value": 400.0, "status": "matched"}
    out = recon.sku_allocation(lines, s)
    assert out["sku_count"] == 5 and out["line_count"] == 6 and out["invoice_value"] == 419.52
    assert round(sum(r["billed_freight"] for r in out["rows"]), 2) == 11810.48
    assert round(sum(r["ns_freight"] for r in out["rows"]), 2) == 9395.95
    assert round(sum(r["variance"] for r in out["rows"]), 2) == out["variance"] == -2414.53
    s4 = next(r for r in out["rows"] if r["sku"] == "S4")
    assert s4["qty"] == 3.0 and s4["value"] == 319.45 and s4["hts"] == "6111, 6104" and s4["lines"] == [4, 6]


def test_lookup_report_supplies_shipment_ids_when_the_receipt_report_has_none(client):
    no_ibs = "\n".join(",".join(ln.split(",")[:7]) for ln in REPORT_CSV.strip().splitlines())
    r = client.post("/api/netsuite/report", files={"file": ("ir.csv", no_ibs.encode())})
    assert r.status_code == 400 and "ibs" in r.json()["detail"].lower()
    lookup = "RB Shipment ID,IR#\n2602-ABC-AIR-4,IR1\n2602-ABC-AIR-4,IR2\n2603-XYZ-AIR-3,IR3\n2610-ABC-BOAT-9,- None -\n"
    assert client.post("/api/netsuite/ibs-map", files={"file": ("map.csv", lookup.encode())}).json() == {"receipts": 3}
    r = client.post("/api/netsuite/report", files={"file": ("ir.csv", no_ibs.encode())})
    assert r.json() == {"rows": 5, "receipts_without_ibs": 2}
    assert client.get("/api/shipments/2602-ABC-AIR-4").json()["ns_freight"] == 1000.0
