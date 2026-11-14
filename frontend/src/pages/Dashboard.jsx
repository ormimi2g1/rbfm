import React, { useEffect, useState } from "react";
import { api, money, signed, pct, FLAGS, flagClass } from "../api.js";

export default function Dashboard() {
  const [d, setD] = useState(null);
  const [err, setErr] = useState("");
  useEffect(() => { api.get("/dashboard").then(setD).catch((e) => setErr(e.message)); }, []);
  if (err) return <p className="error">{err}</p>;
  if (!d) return <p className="muted">Loading…</p>;
  if (!d.netsuite && !d.bills_total) {
    return (
      <div className="empty">
        <h2>Nothing loaded yet</h2>
        <p>Start by loading the NetSuite item-receipt report, then upload freight bills.</p>
        <a className="btn primary" href="#/setup">Load NetSuite data</a>
      </div>
    );
  }
  const c = d.compared, ns = d.netsuite;
  return (
    <>
      <div className="tiles">
        <div className="tile"><span>Under-landed freight</span><b className={c.under_landed > 0 ? "neg" : ""}>{money(c.under_landed)}</b>
          <small>billed above NetSuite landed cost, on {d.shipments.matched} compared shipment{d.shipments.matched === 1 ? "" : "s"}</small></div>
        <div className="tile"><span>Over-landed freight</span><b>{money(c.over_landed)}</b><small>NetSuite landed above the bills</small></div>
        <div className="tile"><span>Bills loaded</span><b>{d.bills_total}</b>
          <small>{d.unmatched_bills} not matched to a shipment · {d.bills.needs_review || 0} need review</small></div>
        <div className="tile"><span>Shipments awaiting a bill</span><b>{d.shipments.awaiting_bill}</b>
          <small>{d.shipments.partial} with only part of the bills</small></div>
      </div>

      <div className="grid2">
        <section>
          <h2>Largest freight variances</h2>
          {d.top_variances.length === 0 ? <p className="muted">No shipment has a main freight bill loaded yet.</p> : (
            <table>
              <thead><tr><th>Shipment</th><th className="num">Billed</th><th className="num">NetSuite</th><th className="num">Variance</th><th className="num">%</th></tr></thead>
              <tbody>{d.top_variances.map((s) => (
                <tr key={s.shipment_id} className="click" onClick={() => (window.location.hash = `#/shipments/${encodeURIComponent(s.shipment_id)}`)}>
                  <td>{s.shipment_id}</td><td className="num">{money(s.actual_freight)}</td><td className="num">{money(s.ns_freight)}</td>
                  <td className={"num " + (s.freight_var < 0 ? "neg" : "")}>{signed(s.freight_var)}</td><td className="num">{pct(s.freight_var_pct)}</td>
                </tr>))}</tbody>
            </table>)}
          <p className="note">Variance = NetSuite landed − billed. Negative means inventory cost was under-stated.
            Flag threshold: over ${money(d.thresholds.variance_abs)} and {d.thresholds.variance_pct}%.</p>
        </section>
        <section>
          <h2>Exceptions</h2>
          {d.flags.length === 0 ? <p className="muted">None.</p> : (
            <ul className="flags">{d.flags.map((f) => (
              <li key={f.code}><span className={flagClass(f.code)}>{FLAGS[f.code] || f.code}</span><span>{f.text}</span><b>{f.count}</b></li>))}</ul>)}
        </section>
      </div>

      {ns && (
        <section>
          <h2>NetSuite data</h2>
          <p>{ns.source === "api" ? "API sync" : "Report"} “{ns.label}”, loaded {new Date(ns.loaded_at + "Z").toLocaleString()}:
            {" "}{ns.rows.toLocaleString()} rows, {ns.shipments} shipments, {money(ns.landed_freight_total)} landed freight in total.</p>
          {ns.receipts_without_ibs.rows > 0 && (
            <p className="warnline">{ns.receipts_without_ibs.rows} item receipts have no shipment ID
              ({money(ns.receipts_without_ibs.goods_value)} of goods, {money(ns.receipts_without_ibs.landed_freight)} landed freight). Bills cannot be matched to them.</p>)}
          {d.changes.length > 0 && (
            <>
              <h3>Changed since the previous load</h3>
              <table>
                <thead><tr><th>Shipment</th><th>Change</th><th className="num">Freight before</th><th className="num">Freight now</th></tr></thead>
                <tbody>{d.changes.slice(0, 50).map((x) => (
                  <tr key={x.shipment_id}><td>{x.shipment_id}</td><td>{x.change}</td><td className="num">{money(x.freight_before)}</td><td className="num">{money(x.freight_now)}</td></tr>))}</tbody>
              </table>
              {d.changes.length > 50 && <p className="note">Showing 50 of {d.changes.length}.</p>}
            </>)}
        </section>)}
    </>
  );
}
