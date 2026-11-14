# Architecture

## Flow

```
 PDFs / zips ──► intake queue ──► reader (rules → AI fallback → tie-out) ──► bill + charge lines
                                                                                   │
 item-receipt report ─┐                                                            ▼
                      ├─► NetSuite snapshot ──► one row per shipment ID ──► reconciliation ──► dashboard,
 SuiteQL sync ────────┘                                                     (computed on read)   shipments, ID
                                                                                                 issues, Excel
```

One process: FastAPI serves the JSON API and the built React app. Postgres on
Replit (`DATABASE_URL`), SQLite locally.

## Decisions

| Decision | Why |
|---|---|
| A bill counts only when it ties to its printed total | It is the one check that catches a misread line regardless of who read it (rules, OCR or AI). |
| Readers work on word positions (pdfplumber), not `pdftotext -layout` | `-layout` silently drops the right-hand Total column on some forwarder invoices. |
| PDFs are deleted after reading | Invoices print bank details and contact names. Each charge line keeps its PDF page number for traceability. |
| Both NetSuite paths write the same `ns_shipment` rows | Reconciliation does not know or care whether data came from a report or the API. |
| Snapshots, last 5 kept | Shows "changed in NetSuite since last load" and makes a true-up visible. |
| Flags and variances are computed on read, not stored | Re-runs are idempotent; a new snapshot, alias, bucket rule or threshold applies immediately. |
| Shipment-ID fixes are suggested, never auto-applied | The ID is typed by hand; a wrong automatic match would put cost on the wrong shipment. Confirmed fixes are stored as aliases. |
| "Main freight bill missing" suppresses the variance flag | A shipment with only a drayage or storage bill loaded is not final; comparing it would raise false over-landed alarms. |
| SKU lines are read cell by cell and checked against the printed Line Total | Whole-page OCR dropped decimal points and misread codes; one cell at a time with a restricted character set, rendered with poppler, tied to the cent on a real report. |
| Single shared password | v1 is for one small team. Roles come later. |

## Data model

| Table | Holds |
|---|---|
| `bill` | One uploaded PDF: forwarder, invoice, mode, PO numbers, printed total, tie result, how it was read, the shipment ID as written |
| `bill_charge` | Charge lines: description, type, units, rate, FX, USD total, page |
| `bill_item_line` | SKU lines read from the broker's Invoice Lines Report: SKU, quantity, invoice value, HTS, duty rate, page |
| `ibs_map` | Item receipt → shipment ID lookup, used when the receipt report has no shipment ID |
| `ns_snapshot` | One NetSuite load (report or API) with counts, including receipts that carry no shipment ID |
| `ns_shipment` | Per snapshot, per shipment ID *as written*: goods value, landed freight, landed customs, POs, receipt count |
| `item_receipt` | Report path detail rows |
| `ibs_item` | API path SKU lines for the per-SKU view |
| `alias` | Confirmed ID corrections (written → means) |
| `bucket_rule` | Which charge descriptions are customs / broker fee (everything else is freight) |
| `setting` | Variance thresholds |

## Calculations

- Billed freight = sum of freight-bucket lines of counted bills (duplicates and re-bills excluded).
- Billed customs = customs-bucket lines + duty a bill states as paid separately.
- Freight variance = NetSuite landed freight − billed freight; flagged when it exceeds both the dollar and the percent threshold.
- Per style-colour (API path): billed freight/unit = PO rate × billed freight ÷ goods value;
  NetSuite freight/unit = unit landed cost × landed freight ÷ total landed cost;
  "uneven" when a line's landed-cost % of cost is more than 0.5 pt from the shipment average.

## Code map

```
backend/app/parsers/     pdftext, chr, crane, expeditors, ai, base (tie-out)
backend/app/netsuite/    reports (upload path), suiteql (API path), store (snapshots)
backend/app/ids.py       normalise, validate, suggest shipment IDs; PO normalisation
backend/app/recon.py     variances, flags, dashboard, SKU rows, ID issues
backend/app/ingest.py    upload → queue → parse → bill rows
backend/app/export.py    Excel workbook
backend/app/main.py      API routes, sign-in, static frontend
frontend/src/            React pages
```
