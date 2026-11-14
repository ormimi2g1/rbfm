import hmac
import secrets
import time
from contextlib import asynccontextmanager
from collections import defaultdict
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from . import buckets, config, export, ids, ingest, recon
from .db import get_db, init_db
from .models import Alias, Bill, BucketRule, Setting
from .netsuite import reports, store, suiteql
from .parsers import ai

@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    ingest.requeue_orphans()
    yield


app = FastAPI(title="Freight Landed-Cost Monitor", docs_url=None, redoc_url=None, lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=config.SECRET_KEY or secrets.token_hex(32),
                   same_site="lax", https_only=False, max_age=60 * 60 * 12)
_attempts: dict[str, list[float]] = defaultdict(list)


def auth(request: Request):
    if config.AUTH_DISABLED or request.session.get("ok"):
        return True
    raise HTTPException(401, "Please sign in")


class Login(BaseModel):
    password: str


@app.post("/api/login")
def login(body: Login, request: Request):
    if not config.APP_PASSWORD:
        raise HTTPException(503, "APP_PASSWORD is not set. Add it in Replit Secrets and restart.")
    ip = request.client.host if request.client else "?"
    recent = [t for t in _attempts[ip] if time.time() - t < 300]
    if len(recent) >= 8:
        raise HTTPException(429, "Too many attempts. Try again in a few minutes.")
    if not hmac.compare_digest(body.password.encode(), config.APP_PASSWORD.encode()):
        _attempts[ip] = recent + [time.time()]
        raise HTTPException(401, "Wrong password")
    request.session["ok"] = True
    return {"ok": True}


@app.post("/api/logout")
def logout(request: Request):
    request.session.clear()
    return {"ok": True}


@app.get("/api/me")
def me(request: Request):
    return {"signed_in": bool(config.AUTH_DISABLED or request.session.get("ok")),
            "ai_reader": ai.available(), "netsuite_api": suiteql.configured()}


# ---- bills ----------------------------------------------------------------
@app.post("/api/bills/upload", dependencies=[Depends(auth)])
async def upload_bills(background: BackgroundTasks, files: list[UploadFile] = File(...), db: Session = Depends(get_db)):
    queued, log = [], []
    for f in files:
        got, lg = ingest.accept_upload(db, f.filename or "upload.pdf", await f.read())
        queued += got
        log += lg
    if queued:
        background.add_task(ingest.process, queued)
    return {"queued": len(queued), "log": log}


@app.get("/api/bills", dependencies=[Depends(auth)])
def list_bills(db: Session = Depends(get_db)):
    state = recon.build(db)
    return [{k: v for k, v in b.items() if k != "charges"} for b in reversed(state["bills"])]


@app.get("/api/bills/{bill_id}", dependencies=[Depends(auth)])
def get_bill(bill_id: int, db: Session = Depends(get_db)):
    b = next((x for x in recon.build(db)["bills"] if x["id"] == bill_id), None)
    if not b:
        raise HTTPException(404, "Bill not found")
    return b


class BillPatch(BaseModel):
    shipment_id_raw: str | None = None
    excluded: bool | None = None
    notes: str | None = None
    source: str | None = None


@app.patch("/api/bills/{bill_id}", dependencies=[Depends(auth)])
def patch_bill(bill_id: int, body: BillPatch, db: Session = Depends(get_db)):
    b = db.get(Bill, bill_id)
    if not b:
        raise HTTPException(404, "Bill not found")
    if body.shipment_id_raw is not None:
        b.shipment_id_raw = body.shipment_id_raw.strip() or None
        b.shipment_id_source = body.source if body.source in ("manual", "suggestion") else "manual"
    if body.excluded is not None:
        b.excluded = body.excluded
    if body.notes is not None:
        b.notes = body.notes[:2000]
    db.commit()
    return {"ok": True}


@app.delete("/api/bills/{bill_id}", dependencies=[Depends(auth)])
def delete_bill(bill_id: int, db: Session = Depends(get_db)):
    b = db.get(Bill, bill_id)
    if b:
        db.delete(b)
        db.commit()
    return {"ok": True}


# ---- NetSuite data ---------------------------------------------------------
@app.post("/api/netsuite/report", dependencies=[Depends(auth)])
async def upload_report(file: UploadFile = File(...), db: Session = Depends(get_db)):
    try:
        snap = reports.import_item_receipts(db, await file.read(), file.filename or "report.xlsx")
    except reports.ReportError as e:
        raise HTTPException(400, str(e))
    return {"rows": snap.row_count, "receipts_without_ibs": snap.no_ibs_rows}


@app.post("/api/netsuite/sync", dependencies=[Depends(auth)])
def netsuite_sync(db: Session = Depends(get_db)):
    if not suiteql.configured():
        raise HTTPException(400, "NetSuite API is not configured (NS_ACCOUNT_ID, NS_CLIENT_ID, NS_CERT_ID, NS_PRIVATE_KEY, NS_SHIPMENT_ID_FIELD).")
    try:
        snap = suiteql.sync(db)
    except Exception as e:
        raise HTTPException(502, f"NetSuite sync failed: {type(e).__name__}: {str(e)[:300]}")
    return {"shipments": snap.row_count}


# ---- views -----------------------------------------------------------------
@app.get("/api/dashboard", dependencies=[Depends(auth)])
def get_dashboard(db: Session = Depends(get_db)):
    return recon.dashboard(db)


@app.get("/api/shipments", dependencies=[Depends(auth)])
def list_shipments(scope: str = "billed", q: str = "", db: Session = Depends(get_db)):
    ships = recon.build(db)["shipments"]
    if scope == "billed":
        ships = [s for s in ships if s["bill_count"]]
    elif scope == "awaiting":
        ships = [s for s in ships if s["status"] == "awaiting_bill"]
    elif scope == "flagged":
        ships = [s for s in ships if s["flags"]]
    if q:
        needle = ids.normalise(q)
        ships = [s for s in ships if needle in s["shipment_id"] or any(needle in p for p in s["pos"])]
    return [{k: v for k, v in s.items() if k != "bills"} for s in ships]


@app.get("/api/shipments/{sid}", dependencies=[Depends(auth)])
def get_shipment(sid: str, db: Session = Depends(get_db)):
    s = recon.shipment_detail(db, ids.normalise(sid))
    if not s:
        raise HTTPException(404, "Shipment not found")
    return s


@app.get("/api/id-issues", dependencies=[Depends(auth)])
def get_id_issues(db: Session = Depends(get_db)):
    return recon.id_issues(db)


@app.get("/api/export.xlsx", dependencies=[Depends(auth)])
def export_xlsx(db: Session = Depends(get_db)):
    return Response(export.build(db), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": 'attachment; filename="freight_landed_cost.xlsx"'})


# ---- configuration ---------------------------------------------------------
class AliasIn(BaseModel):
    written: str
    means: str
    note: str | None = None


@app.get("/api/config", dependencies=[Depends(auth)])
def get_config(db: Session = Depends(get_db)):
    return {"settings": buckets.settings(db),
            "aliases": [{"id": a.id, "written": a.written, "means": a.means, "note": a.note} for a in db.query(Alias).order_by(Alias.written)],
            "bucket_rules": [{"id": r.id, "pattern": r.pattern, "bucket": r.bucket} for r in db.query(BucketRule).order_by(BucketRule.position, BucketRule.id)]}


@app.post("/api/aliases", dependencies=[Depends(auth)])
def add_alias(body: AliasIn, db: Session = Depends(get_db)):
    w, m = ids.normalise(body.written), ids.normalise(body.means)
    if not w or not m or w == m:
        raise HTTPException(400, "Give the ID as written and the different ID it means.")
    a = db.query(Alias).filter(Alias.written == w).first() or Alias(written=w)
    a.means, a.note = m, (body.note or "")[:200]
    db.add(a)
    db.commit()
    return {"ok": True}


@app.delete("/api/aliases/{alias_id}", dependencies=[Depends(auth)])
def del_alias(alias_id: int, db: Session = Depends(get_db)):
    a = db.get(Alias, alias_id)
    if a:
        db.delete(a)
        db.commit()
    return {"ok": True}


class SettingsIn(BaseModel):
    variance_abs: float
    variance_pct: float


@app.put("/api/settings", dependencies=[Depends(auth)])
def put_settings(body: SettingsIn, db: Session = Depends(get_db)):
    for k, v in body.model_dump().items():
        s = db.get(Setting, k) or Setting(key=k)
        s.value = str(max(0.0, v))
        db.add(s)
    db.commit()
    return {"ok": True}


class RuleIn(BaseModel):
    pattern: str
    bucket: str


@app.post("/api/bucket-rules", dependencies=[Depends(auth)])
def add_rule(body: RuleIn, db: Session = Depends(get_db)):
    import re
    if body.bucket not in ("customs", "broker_fee", "freight"):
        raise HTTPException(400, "Bucket must be customs, broker_fee or freight.")
    try:
        re.compile(body.pattern)
    except re.error:
        raise HTTPException(400, "That pattern is not a valid expression.")
    if not db.query(BucketRule).filter(BucketRule.pattern == body.pattern).first():
        db.add(BucketRule(pattern=body.pattern[:200], bucket=body.bucket, position=50))
        db.commit()
    return {"ok": True}


@app.delete("/api/bucket-rules/{rule_id}", dependencies=[Depends(auth)])
def del_rule(rule_id: int, db: Session = Depends(get_db)):
    r = db.get(BucketRule, rule_id)
    if r:
        db.delete(r)
        db.commit()
    return {"ok": True}


# ---- frontend (built React app) --------------------------------------------
DIST = Path(config.ROOT / "frontend" / "dist")
if (DIST / "assets").exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")


@app.get("/{path:path}", include_in_schema=False)
def spa(path: str):
    if path.startswith("api/"):
        raise HTTPException(404)
    index = DIST / "index.html"
    if not index.exists():
        return Response("Frontend is not built yet. Run: cd frontend && npm install && npm run build", media_type="text/plain")
    return FileResponse(index)
