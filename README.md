# Freight Landed-Cost Monitor

Internal web app that checks every freight bill against the landed cost NetSuite
put into inventory for the same inbound shipment, and flags the gaps: freight
billed above what was landed, shipments with no landed cost, shipment IDs typed
inconsistently, bills that do not belong to any shipment.

**Read-only.** The app never writes to NetSuite.

> **Keep company data out of this repository.** Bills, NetSuite exports and the
> database are git-ignored. If this repository is public, make it private before
> adding anything client-specific.

## What it does (v1)

| Area | Behaviour |
|---|---|
| Bill intake | Upload PDFs or zips of PDFs. Each bill is read, and only accepted as "read and tied" when its charge lines add up to the printed total (±$0.02). The PDF is discarded after reading; only structured fields are kept. |
| Readers | Layout rules for C.H. Robinson (verified on real bills), Crane Worldwide and Expeditors (written from a layout description, **not yet verified on real bills**). Anything else, or anything that does not tie, goes to an OpenAI reader if `OPENAI_API_KEY` is set, with personal details and bank lines stripped first. Scanned first pages go through OCR. |
| NetSuite data | Two interchangeable paths: upload the item-receipt report (Excel/CSV), or sync through SuiteQL (**written but not yet run against a live account**). Every load is a snapshot, so landed-cost changes between loads are shown. |
| Matching | Bill → shipment by the shipment ID in the file name (`ShipmentID_INVinvoice.pdf`), normalised for spaces and case. Near-misses are **suggested** (shared PO numbers first, similar spelling second) and only applied when a person confirms. |
| Variance | Per shipment: NetSuite landed freight − billed freight, customs likewise when a customs bill is loaded. Sign: negative = inventory cost under-stated. |
| Flags | No landed cost, no landed freight/customs, freight variance, main freight bill missing, mode mismatch, wrong cost category (API path), ID written inconsistently, ID corrected, PO mismatch, bill does not tie, read by AI. |
| Per-SKU view | Value allocation by style-colour. Needs shipment item lines, which only the API path provides. |
| Export | Excel workbook: IBS Summary, Awaiting Bills, Bill Register, Bill Charges, ID Issues. |

Not built yet: bill-list (AP export) import and its split AP/COGS handling, run log,
tie-out to the accrued-freight account, rate benchmarking, alerts, roles.

## Run on Replit

1. Import this repository into Replit.
2. Add a PostgreSQL database (sets `DATABASE_URL`). Without it the app uses a local SQLite file, which does not survive redeploys.
3. Add Secrets:

| Secret | Needed | Purpose |
|---|---|---|
| `APP_PASSWORD` | yes | The shared sign-in password |
| `SECRET_KEY` | yes | Long random string that signs the session cookie |
| `OPENAI_API_KEY` | optional | Turns on the AI reader for unknown layouts |
| `OPENAI_MODEL` | optional | Model for the AI reader (default `gpt-4.1-mini`; must support JSON output) |
| `NS_ACCOUNT_ID`, `NS_CLIENT_ID`, `NS_CERT_ID`, `NS_PRIVATE_KEY`, `NS_SHIPMENT_ID_FIELD` | optional | SuiteQL sync (OAuth 2.0 machine-to-machine). `NS_SHIPMENT_ID_FIELD` is the custom field on Inbound Shipment that holds the shipment ID. |

4. Press Run. The first start installs dependencies and builds the frontend.

The `.replit` file has not been tried on Replit yet; if the run button does not
start the app, run `bash build.sh && bash run.sh` in the shell.

## Run locally

```bash
pip install -r requirements-dev.txt
(cd frontend && npm ci && npm run build)
cd backend && APP_PASSWORD=change-me SECRET_KEY=dev python -m uvicorn app.main:app --port 8000
```

Tests: `cd backend && python -m pytest`. All committed tests use made-up data.
To also check real bills, keep them outside the repo and set `RBFM_PRIVATE_DIR`
(see `backend/tests/test_private_samples.py`).

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the design.
