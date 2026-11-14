import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="rbfm_test_")
os.environ["DATA_DIR"] = _tmp
os.environ["AUTH_DISABLED"] = "1"
os.environ.pop("DATABASE_URL", None)
os.environ.pop("OPENAI_API_KEY", None)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture()
def client():
    from app import db, main
    db.Base.metadata.drop_all(db.engine)
    with TestClient(main.app) as c:
        yield c


def chr_lines(invoice="6200000001", mode="A", rows=None, subtotal="1,300.00", ref="009043,9033"):
    """Lines as pdfplumber yields them for a C.H. Robinson invoice (made-up numbers)."""
    rows = rows or ["Air Freight Per Chg Wt 200 USD 5.00 USD 1,000.00",
                    "Destination Terminal Handling Flat Rate 1 USD 55.00 USD 55.00", "Fee",
                    "Transfer Fee Fixed Charge 1 USD 95.00", "USD 95.00",
                    "Pickup @ Origin/Docs & Handling Flat Rate 1 INR 10,000.00 0.015 USD 150.00"]
    return [f"Invoice Number: {invoice} P.O. Box 1", "Invoice Date: March 3, 2026", "C.H. Robinson International, Inc.",
            "ATTN: ACCOUNTS PAYABLE Amount Due: USD " + subtotal, f"Mode: {mode}",
            "Shipment Number: 555000111 Branch Code: 0640 Houston", "MAWB#: 695-00000000 HAWB#: 555000111PVG",
            f"Customer Reference Number: {ref}", "CONTAINERS", "QTY UOM Weight UOM Chrg Wt UOM Destination Commodity",
            "10 CTN 400.00 LBS 441 LBS Dallas Clothing", "RATES AND ACCESSORIALS", "Description Type Units Per Unit Total",
            *rows, "Sub-Total: USD " + subtotal, "Payment Received: USD 0.00", "Amount Due: USD " + subtotal,
            "If you have questions please call Jane Doe at 555-123-4567 or email", "at jane.doe@example.com."]


def make_pdf(lines) -> bytes:
    import io
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(612, 792))
    y = 760
    for ln in lines:
        c.setFont("Helvetica", 8)
        c.drawString(30, y, ln)
        y -= 14
    c.save()
    return buf.getvalue()


REPORT_CSV = """Date,Period,Document Number,PO#,Total Value Excluding LC,Freight,Customs,IBS
03/01/2026,Mar 2026,IR1,PO009043,6000,700,900,2602-ABC-AIR-4
03/01/2026,Mar 2026,IR2,PO009033,4000,300,600,2602-ABC-AIR-4
04/01/2026,Apr 2026,IR3,PO009100,5000,0,0,2603-XYZ-AIR- 3
04/02/2026,Apr 2026,IR4,PO009101,2000,100,50,2603-QrSt-Boat-1
04/03/2026,Apr 2026,IR5,PO009102,900,10,5,IRRR
Overall Total,,,,17900,1110,1555,
"""
