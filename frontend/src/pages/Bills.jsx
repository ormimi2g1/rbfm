import React, { useEffect, useRef, useState } from "react";
import { api, money } from "../api.js";

const STATUS = { queued: "Reading…", parsed: "Read and tied", needs_review: "Needs review", unknown_format: "Unknown layout", failed: "Failed", duplicate: "Duplicate" };
const cls = { parsed: "tag ok", needs_review: "tag bad", unknown_format: "tag bad", failed: "tag bad", duplicate: "tag warn", queued: "tag" };

export default function Bills({ me }) {
  const [bills, setBills] = useState(null);
  const [log, setLog] = useState([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [open, setOpen] = useState(null);
  const input = useRef();
  const load = () => api.get("/bills").then(setBills).catch((e) => setErr(e.message));
  useEffect(() => { load(); }, []);
  useEffect(() => {
    if (!bills || !bills.some((b) => b.status === "queued")) return;
    const t = setTimeout(load, 2000);
    return () => clearTimeout(t);
  }, [bills]);

  const send = async (files) => {
    if (!files.length) return;
    setBusy(true); setErr("");
    try { const r = await api.upload("/bills/upload", "files", files); setLog(r.log); await load(); }
    catch (e) { setErr(e.message); }
    setBusy(false);
    if (input.current) input.current.value = "";
  };

  return (
    <>
      <section className="drop" onDragOver={(e) => e.preventDefault()} onDrop={(e) => { e.preventDefault(); send(e.dataTransfer.files); }}>
        <h2>Upload freight bills</h2>
        <p>Drop PDF bills or a zip of PDFs here. Name files <code>ShipmentID_INVinvoice.pdf</code> so they match automatically.</p>
        <input ref={input} type="file" multiple accept=".pdf,.zip" onChange={(e) => send(e.target.files)} disabled={busy} />
        {busy && <p className="muted">Uploading…</p>}
        {!me.ai_reader && <p className="note">AI reader is off (no OPENAI_API_KEY): only C.H. Robinson, Crane and Expeditors layouts are read.</p>}
        {err && <p className="error">{err}</p>}
        {log.filter((l) => l.result !== "queued").length > 0 && (
          <ul className="log">{log.filter((l) => l.result !== "queued").map((l, i) => <li key={i}><b>{l.file}</b>: {l.result}</li>)}</ul>)}
      </section>

      <section>
        <h2>Bills</h2>
        {!bills ? <p className="muted">Loading…</p> : bills.length === 0 ? <p className="muted">No bills uploaded yet.</p> : (
          <table>
            <thead><tr><th>Invoice</th><th>Forwarder</th><th>Date</th><th>Shipment ID</th><th className="num">Printed total</th><th>Status</th><th>Read by</th></tr></thead>
            <tbody>{bills.map((b) => (
              <tr key={b.id} className="click" onClick={() => setOpen(b.id)}>
                <td>{b.invoice_no || <span className="muted">{b.file_name}</span>}</td><td>{b.vendor || "—"}</td><td>{b.invoice_date || "—"}</td>
                <td>{b.shipment_id || (b.shipment_id_raw ? <span className="neg">{b.shipment_id_raw} (not in NetSuite)</span> : <span className="neg">none</span>)}
                  {b.match_method && !["exact", "none"].includes(b.match_method) && <span className="tag warn">{b.match_method}</span>}</td>
                <td className="num">{money(b.subtotal)}</td>
                <td><span className={cls[b.status]}>{STATUS[b.status] || b.status}</span></td><td>{b.parse_method || "—"}</td>
              </tr>))}</tbody>
          </table>)}
      </section>
      {open && <BillPanel id={open} onClose={() => { setOpen(null); load(); }} />}
    </>
  );
}

function BillPanel({ id, onClose }) {
  const [b, setB] = useState(null);
  const [sid, setSid] = useState("");
  const load = () => api.get(`/bills/${id}`).then((x) => { setB(x); setSid(x.shipment_id_raw || ""); });
  useEffect(() => { load(); }, [id]);
  if (!b) return null;
  const save = async (value, source) => { await api.patch(`/bills/${id}`, { shipment_id_raw: value, source }); load(); };
  return (
    <div className="overlay" onClick={onClose}>
      <aside onClick={(e) => e.stopPropagation()}>
        <button className="link right" onClick={onClose}>Close</button>
        <h2>{b.vendor || "Bill"} {b.invoice_no}</h2>
        <p className="muted">{b.file_name}</p>
        <dl>
          <dt>Printed total</dt><dd>{money(b.subtotal)}</dd>
          <dt>Lines add to</dt><dd className={b.tied ? "" : "neg"}>{money(b.lines_total)} {b.tied ? "(ties)" : "(does not tie)"}</dd>
          <dt>Mode</dt><dd>{b.mode || "—"}</dd>
          <dt>House / master bill</dt><dd>{b.hawb || "—"} / {b.mawb || "—"}</dd>
          <dt>Chargeable weight</dt><dd>{b.chargeable_weight ? `${b.chargeable_weight} ${b.weight_uom || ""}` : "—"}</dd>
          <dt>PO numbers on bill</dt><dd>{b.po_refs.join(", ") || "—"}</dd>
          <dt>Freight / customs / broker fees</dt><dd>{money(b.freight)} / {money(b.customs)} / {money(b.broker_fees)}</dd>
        </dl>
        {b.warnings.length > 0 && <ul className="log">{b.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>}
        <h3>Shipment ID</h3>
        <div className="row">
          <input value={sid} onChange={(e) => setSid(e.target.value)} placeholder="e.g. 2602-ABC-AIR-4" />
          <button onClick={() => save(sid, "manual")}>Save</button>
        </div>
        <p className="note">{b.shipment_id ? `Matched to ${b.shipment_id} (${b.match_method}).` : "Not matched to a NetSuite shipment."}</p>
        {(b.suggestions || []).map((s) => (
          <p key={s.shipment_id} className="row">Suggested: <b>{s.shipment_id}</b> <span className="muted">({s.reason})</span>
            <button onClick={() => save(s.shipment_id, "suggestion")}>Use this</button></p>))}
        <h3>Charge lines</h3>
        <table>
          <thead><tr><th>Description</th><th>Type</th><th className="num">Units</th><th className="num">Rate</th><th className="num">USD</th><th>Bucket</th><th>Page</th></tr></thead>
          <tbody>{b.charges.map((c) => (
            <tr key={c.line_no}><td>{c.description}</td><td>{c.rate_type || ""}</td><td className="num">{c.units ?? ""}</td>
              <td className="num">{c.rate != null ? `${c.currency || ""} ${c.rate}` : ""}</td><td className="num">{money(c.total_usd)}</td><td>{c.bucket.replace("_", " ")}</td><td>{c.page ?? ""}</td></tr>))}</tbody>
        </table>
        <div className="row end">
          <button onClick={async () => { await api.patch(`/bills/${id}`, { excluded: !b.excluded }); load(); }}>{b.excluded ? "Count this bill" : "Do not count this bill"}</button>
          <button className="danger" onClick={async () => { if (window.confirm("Remove this bill and its charge lines?")) { await api.del(`/bills/${id}`); onClose(); } }}>Remove</button>
        </div>
      </aside>
    </div>
  );
}
