import React, { useEffect, useState } from "react";
import { api, money } from "../api.js";

export default function IdIssues() {
  const [d, setD] = useState(null);
  const load = () => api.get("/id-issues").then(setD);
  useEffect(() => { load(); }, []);
  if (!d) return <p className="muted">Loading…</p>;
  const use = async (bill, sid, remember) => {
    if (remember && bill.shipment_id_raw) await api.post("/aliases", { written: bill.shipment_id_raw, means: sid, note: `from invoice ${bill.invoice_no || ""}` });
    else await api.patch(`/bills/${bill.id}`, { shipment_id_raw: sid, source: "suggestion" });
    load();
  };
  return (
    <>
      <section>
        <h2>Bills not matched to a shipment</h2>
        {d.unmatched_bills.length === 0 ? <p className="muted">Every bill is matched.</p> : (
          <table>
            <thead><tr><th>Invoice</th><th>ID on the file</th><th>POs on the bill</th><th>Suggested shipment</th></tr></thead>
            <tbody>{d.unmatched_bills.map((b) => (
              <tr key={b.id}><td>{b.vendor} {b.invoice_no}</td><td>{b.shipment_id_raw || <span className="muted">none</span>}</td><td>{b.po_refs.join(", ") || "—"}</td>
                <td>{b.suggestions.length === 0 ? <span className="muted">No close match. Open the bill to type the ID.</span> : b.suggestions.map((s) => (
                  <div key={s.shipment_id} className="row"><b>{s.shipment_id}</b><span className="muted">{s.reason}</span>
                    <button onClick={() => use(b, s.shipment_id, false)}>Use for this bill</button>
                    {b.shipment_id_raw && <button onClick={() => use(b, s.shipment_id, true)}>Always treat “{b.shipment_id_raw}” as this</button>}</div>))}</td></tr>))}</tbody>
          </table>)}
        <p className="note">Suggestions are never applied on their own. The fix in NetSuite itself still has to be made there.</p>
      </section>
      <section>
        <h2>Shipment IDs written inconsistently in NetSuite</h2>
        {d.netsuite_ids.length === 0 ? <p className="muted">None found.</p> : (
          <table>
            <thead><tr><th>Read as</th><th>Written as</th><th>Problem</th><th className="num">Receipts</th><th className="num">Landed freight</th></tr></thead>
            <tbody>{d.netsuite_ids.map((i) => (
              <tr key={i.shipment_id}><td><a href={`#/shipments/${encodeURIComponent(i.shipment_id)}`}>{i.shipment_id}</a></td>
                <td>{i.written_as.map((w) => `“${w}”`).join(", ")}</td><td>{i.issues.join("; ")}</td><td className="num">{i.receipts}</td><td className="num">{money(i.ns_freight)}</td></tr>))}</tbody>
          </table>)}
      </section>
      {d.receipts_without_ibs && d.receipts_without_ibs.rows > 0 && (
        <section><h2>Item receipts with no shipment ID</h2>
          <p>{d.receipts_without_ibs.rows} receipts, {money(d.receipts_without_ibs.goods_value)} of goods, {money(d.receipts_without_ibs.landed_freight)} landed freight.
            No bill can be matched to these until NetSuite links them to a shipment.</p></section>)}
    </>
  );
}
