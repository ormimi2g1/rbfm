import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app import config, recon
from app.db import SessionLocal, init_db
from app.netsuite import suiteql


def test_sync_builds_the_same_shipment_shape_as_the_report_path(monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    for k, v in dict(NS_ACCOUNT_ID="1234567_SB1", NS_CLIENT_ID="cid", NS_CERT_ID="kid", NS_PRIVATE_KEY=pem, NS_SHIPMENT_ID_FIELD="custrecord_shipment_id").items():
        monkeypatch.setattr(config, k, v)

    def handler(req: httpx.Request):
        assert req.url.host == "1234567-sb1.suitetalk.api.netsuite.com"
        if req.url.path.endswith("/token"):
            assert b"client_assertion=" in req.content
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        assert req.headers["authorization"] == "Bearer tok"
        q = req.content.decode()
        if "InboundShipmentLandedCost" in q:
            items = [{"ibs": "9", "cat": "LC Freight (Boat)", "m": "VALUE", "amt": "800"}, {"ibs": "9", "cat": "LC Customs", "m": "VALUE", "amt": "200"}]
        elif "InboundShipmentItem" in q:
            items = [{"i": "9", "sku": "STYLE000-COLOR-01-2T", "q": "100", "r": "10", "a": "1000", "u": "1.0"},
                     {"i": "9", "sku": "STYLE000-COLOR-02-3T", "q": "100", "r": "10", "a": "1000", "u": "0"}]
        else:
            items = [{"id": "9", "sid": "2701-ABC-AIR- 1"}]
        return httpx.Response(200, json={"items": items, "hasMore": False})

    init_db()
    with SessionLocal() as db:
        suiteql.sync(db, suiteql.SuiteQL(httpx.Client(transport=httpx.MockTransport(handler))))
        s = recon.shipment_detail(db, "2701-ABC-AIR-1")
        assert (s["ns_freight"], s["ns_customs"], s["goods_value"]) == (800.0, 200.0, 2000.0)
        assert "CATEGORY_MISMATCH" in s["flags"] and "ID_FORMAT" in s["flags"]      # air shipment landed as Boat
        rows = s["sku_rows"]
        assert len(rows) == 2 and all(r["uneven"] for r in rows)                    # same PO rate, different unit LC
        assert sorted(r["ns_freight_unit"] for r in rows) == [0, 0.8]                                    # 1.0 x 800 / 1000
