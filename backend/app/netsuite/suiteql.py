"""API path: read-only SuiteQL over REST with OAuth 2.0 machine-to-machine
(client-credentials with a signed JWT). Needs an integration record, a role
with SuiteQL + Inbound Shipment + Vendor Bill view permissions, and a
certificate mapping — all created by a NetSuite administrator.

NOT yet run against a live account; query shapes follow the spec's notes
(no `transaction` table, no BUILTIN.DF on list fields, stray spaces in IDs)."""
import time

import httpx
import jwt
from sqlalchemy.orm import Session

from .. import config
from ..ids import normalise
from ..models import IbsItem, NsShipment, NsSnapshot
from . import store

def q_headers() -> str:
    f = config.NS_SHIPMENT_ID_FIELD      # the account's custom field holding the shipment ID
    return f"SELECT id, shipmentNumber, {f} AS sid, vesselNumber, shipmentStatus FROM InboundShipment WHERE {f} IS NOT NULL"


Q_LC = """SELECT lc.landedCostInboundShipment AS ibs, cc.name AS cat, lc.landedCostAllocationMethod AS m,
 SUM(lc.landedCostAmount) AS amt FROM InboundShipmentLandedCost lc
 JOIN costCategory cc ON cc.id = lc.landedCostCostCategory
 GROUP BY lc.landedCostInboundShipment, cc.name, lc.landedCostAllocationMethod"""
Q_ITEMS = """SELECT inboundShipment AS i, shipmentItemText AS sku, quantityExpected AS q, poRate AS r,
 shipmentItemAmount AS a, unitLandedCost AS u FROM InboundShipmentItem"""


def configured() -> bool:
    return all([config.NS_ACCOUNT_ID, config.NS_CLIENT_ID, config.NS_CERT_ID, config.NS_PRIVATE_KEY, config.NS_SHIPMENT_ID_FIELD])


def is_customs(category: str) -> bool:
    c = (category or "").lower()
    return "custom" in c or "dut" in c


class SuiteQL:
    def __init__(self, client: httpx.Client | None = None):
        acct = config.NS_ACCOUNT_ID.lower().replace("_", "-")
        self.base = f"https://{acct}.suitetalk.api.netsuite.com/services/rest"
        self.http = client or httpx.Client(timeout=120)
        self._token, self._exp = None, 0.0

    def _auth(self) -> str:
        if self._token and time.time() < self._exp - 60:
            return self._token
        url = f"{self.base}/auth/oauth2/v1/token"
        now = int(time.time())
        assertion = jwt.encode(
            {"iss": config.NS_CLIENT_ID, "scope": "rest_webservices", "aud": url, "iat": now, "exp": now + 3000},
            config.NS_PRIVATE_KEY.replace("\\n", "\n"), algorithm="PS256", headers={"kid": config.NS_CERT_ID})
        r = self.http.post(url, data={
            "grant_type": "client_credentials",
            "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
            "client_assertion": assertion})
        r.raise_for_status()
        j = r.json()
        self._token, self._exp = j["access_token"], time.time() + int(j.get("expires_in", 3600))
        return self._token

    def query(self, sql: str, page: int = 1000) -> list[dict]:
        out, offset = [], 0
        while True:
            r = self.http.post(f"{self.base}/query/v1/suiteql", params={"limit": page, "offset": offset},
                               json={"q": " ".join(sql.split())},
                               headers={"Authorization": f"Bearer {self._auth()}", "Prefer": "transient"})
            r.raise_for_status()
            j = r.json()
            out.extend(j.get("items", []))
            if not j.get("hasMore"):
                return out
            offset += page


def sync(db: Session, api: SuiteQL | None = None) -> NsSnapshot:
    api = api or SuiteQL()
    headers, lc, items = api.query(q_headers()), api.query(Q_LC), api.query(Q_ITEMS)
    snap = NsSnapshot(source="api", label="SuiteQL sync", row_count=len(headers))
    db.add(snap)
    db.flush()
    by_id = {}
    for h in headers:
        raw = str(h.get("sid") or "").strip()
        if not raw:
            continue
        s = NsShipment(snapshot_id=snap.id, ibs_raw=raw, ibs_norm=normalise(raw), ns_internal_id=str(h["id"]),
                       goods_value=0, lc_freight=0, lc_customs=0, freight_categories=[], pos=[], receipts=0)
        by_id[str(h["id"])] = s
        db.add(s)
    for row in lc:
        s = by_id.get(str(row.get("ibs")))
        if not s:
            continue
        amt, cat = float(row.get("amt") or 0), str(row.get("cat") or "")
        if is_customs(cat):
            s.lc_customs = round(s.lc_customs + amt, 2)
        else:
            s.lc_freight = round(s.lc_freight + amt, 2)
            s.freight_categories = sorted(set(s.freight_categories) | {cat})
    rows = []
    for it in items:
        s = by_id.get(str(it.get("i")))
        if not s:
            continue
        amount = float(it.get("a") or 0)
        s.goods_value = round(s.goods_value + amount, 2)
        rows.append(dict(snapshot_id=snap.id, ibs_norm=s.ibs_norm, sku=str(it.get("sku") or ""),
                         qty_expected=float(it.get("q") or 0), po_rate=float(it.get("r") or 0), amount=amount,
                         unit_landed_cost=float(it.get("u") or 0)))
    db.bulk_insert_mappings(IbsItem, rows)
    db.commit()
    store.prune(db)
    return snap
