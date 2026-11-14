import React, { useEffect, useState } from "react";
import { api, money, signed, pct, FLAGS, flagClass } from "../api.js";

const SCOPES = [["billed", "With bills"], ["flagged", "Flagged"], ["awaiting", "Awaiting a bill"], ["all", "All"]];
const STATUS = { matched: "Compared", partial: "Partly billed", awaiting_bill: "Awaiting bill" };

export default function Shipments({ selected }) {
  const [scope, setScope] = useState("billed");
  const [q, setQ] = useState("");
  const [rows, setRows] = useState(null);
  useEffect(() => {
    setRows(null);
    const t = setTimeout(() => api.get(`/shipments?scope=${scope}&q=${encodeURIComponent(q)}`).then(setRows), 200);
    return () => clearTimeout(t);
  }, [scope, q]);
  if (selected) return <Detail sid={selected} />;
  return (
    <section>
      <div className="row">
        <div className="tabs">{SCOPES.map(([k, l]) => <button key={k} className={scope === k ? "on" : ""} onClick={() => setScope(k)}>{l}</button>)}</div>
        <span className="spacer" />
        <input placeholder="Search shipment ID or PO" value={q} onChange={(e) => setQ(e.target.value)} />
      </div>
      {!rows ? <p className="muted">Loading…</p> : rows.length === 0 ? <p className="muted">No shipments here.</p> : (
        <table>
          <thead><tr><th>Shipment ID</th><th>Status</th><th className="num">Goods value</th><th className="num">Billed freight</th>
            <th className="num">NetSuite freight</th><th className="num">Variance</th><th className="num">%</th><th>Flags</th></tr></thead>
          <tbody>{rows.slice(0, 400).map((s) => (
            <tr key={s.shipment_id} className="click" onClick={() => (window.location.hash = `#/shipments/${encodeURIComponent(s.shipment_id)}`)}>
              <td>{s.shipment_id}</td><td>{STATUS[s.status]}</td><td className="num">{money(s.goods_value)}</td>
              <td className="num">{s.bill_count ? money(s.actual_freight) : "—"}</td><td className="num">{money(s.ns_freight)}</td>
              <td className={"num " + (s.status === "matched" && s.freight_var < 0 ? "neg" : "")}>{s.status === "matched" ? signed(s.freight_var) : "—"}</td>
              <td className="num">{s.status === "matched" ? pct(s.freight_var_pct) : "—"}</td>
              <td>{s.flags.map((f) => <span key={f} className={flagClass(f)}>{FLAGS[f] || f}</span>)}</td>
            </tr>))}</tbody>
        </table>)}
      {rows && rows.length > 400 && <p className="note">Showing 400 of {rows.length}. Search to narrow.</p>}
    </section>
  );
}

function Detail({ sid }) {
  const [s, setS] = useState(null);
  const [err, setErr] = useState("");
  useEffect(() => { setS(null); api.get(`/shipments/${encodeURIComponent(sid)}`).then(setS).catch((e) => setErr(e.message)); }, [sid]);
  if (err) return <p className="error">{err} <a href="#/shipments">Back</a></p>;
  if (!s) return <p className="muted">Loading…</p>;
  const final = s.status === "matched";
  return (
    <>
      <p><a href="#/shipments">← Shipments</a></p>
      <h1>{s.shipment_id} <small className="muted">{STATUS[s.status]}</small></h1>
      {s.written_as.length > 0 && s.written_as.join() !== s.shipment_id && <p className="warnline">Written in NetSuite as: {s.written_as.map((w) => `“${w}”`).join(", ")}</p>}
      <div className="tiles">
        <div className="tile"><span>Billed freight</span><b>{s.bill_count ? money(s.actual_freight) : "—"}</b><small>{s.bill_count} bill{s.bill_count === 1 ? "" : "s"} counted</small></div>
        <div className="tile"><span>NetSuite landed freight</span><b>{money(s.ns_freight)}</b><small>{s.freight_pct_ns != null ? `${s.freight_pct_ns}% of goods value` : ""}</small></div>
        <div className="tile"><span>Freight variance</span><b className={final && s.freight_var < 0 ? "neg" : ""}>{final ? signed(s.freight_var) : "—"}</b>
          <small>{final ? pct(s.freight_var_pct) : s.bill_count ? "not final: main freight bill missing" : "no bill yet"}</small></div>
        <div className="tile"><span>Customs: billed / NetSuite</span><b>{s.actual_customs ? money(s.actual_customs) : "—"} / {money(s.ns_customs)}</b>
          <small>{s.actual_customs ? `variance ${signed(s.customs_var)}` : "no customs bill loaded: NetSuite value not verified"}</small></div>
      </div>
      {s.flags.length > 0 && (
        <section><h2>Exceptions</h2>
          <ul className="flags">{s.flags.map((f) => <li key={f}><span className={flagClass(f)}>{FLAGS[f] || f}</span><span>{s.flag_text[f]}</span></li>)}</ul></section>)}
      <section>
        <h2>Bills</h2>
        {s.bills.length === 0 ? <p className="muted">No bill uploaded for this shipment.</p> : s.bills.map((b) => (
          <div key={b.id} className="bill">
            <h3>{b.vendor} {b.invoice_no} <small className="muted">{b.invoice_date} · {money(b.subtotal)} · {b.excluded ? "not counted" : b.tied ? "ties" : "does not tie"} · POs {b.po_refs.join(", ") || "none"}</small></h3>
            <table>
              <thead><tr><th>Description</th><th>Type</th><th className="num">Units</th><th className="num">Rate</th><th className="num">USD</th><th>Bucket</th></tr></thead>
              <tbody>{b.charges.map((c) => (
                <tr key={c.line_no}><td>{c.description}</td><td>{c.rate_type || ""}</td><td className="num">{c.units ?? ""}</td>
                  <td className="num">{c.rate != null ? `${c.currency || ""} ${c.rate}` : ""}</td><td className="num">{money(c.total_usd)}</td><td>{c.bucket.replace("_", " ")}</td></tr>))}</tbody>
            </table>
          </div>))}
      </section>
      {s.receipts_detail.length > 0 && (
        <section>
          <h2>NetSuite item receipts</h2>
          <table>
            <thead><tr><th>Receipt</th><th>Date</th><th>PO</th><th className="num">Goods value</th><th className="num">Freight</th><th className="num">Freight %</th><th className="num">Customs</th></tr></thead>
            <tbody>{s.receipts_detail.map((r) => (
              <tr key={r.doc_number}><td>{r.doc_number}</td><td>{r.date}</td><td>{r.po}</td><td className="num">{money(r.goods_value)}</td>
                <td className="num">{money(r.freight)}</td><td className="num">{r.freight_pct != null ? `${r.freight_pct}%` : "—"}</td><td className="num">{money(r.customs)}</td></tr>))}</tbody>
          </table>
        </section>)}
      {s.sku_rows.length > 0 && (
        <section>
          <h2>By style-colour (value allocation)</h2>
          <table>
            <thead><tr><th>Style-colour</th><th className="num">PO rate</th><th className="num">Qty</th><th className="num">Billed freight / unit</th>
              <th className="num">NetSuite freight / unit</th><th className="num">Variance / unit</th><th className="num">Extended</th><th className="num">LC % of cost</th></tr></thead>
            <tbody>{s.sku_rows.map((r, i) => (
              <tr key={i}><td>{r.style}</td><td className="num">{r.po_rate}</td><td className="num">{r.qty}</td><td className="num">{r.actual_freight_unit}</td>
                <td className="num">{r.ns_freight_unit}</td><td className="num">{r.var_unit}</td><td className="num">{signed(r.extended_var)}</td>
                <td className="num">{r.lc_pct}%{r.uneven && <span className="tag warn">uneven</span>}</td></tr>))}</tbody>
          </table>
        </section>)}
    </>
  );
}
