import React, { useEffect, useRef, useState } from "react";
import { api } from "../api.js";

export default function Setup({ me }) {
  const [cfg, setCfg] = useState(null);
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [alias, setAlias] = useState({ written: "", means: "" });
  const [rule, setRule] = useState({ pattern: "", bucket: "customs" });
  const file = useRef();
  const load = () => api.get("/config").then(setCfg);
  useEffect(() => { load(); }, []);
  const run = async (fn) => { setErr(""); setMsg(""); setBusy(true); try { await fn(); await load(); } catch (e) { setErr(e.message); } setBusy(false); };
  if (!cfg) return <p className="muted">Loading…</p>;
  return (
    <>
      <section>
        <h2>NetSuite data</h2>
        <p>Upload the item-receipt report (columns: Date, Document Number, PO#, Total Value Excluding LC, Freight, Customs, IBS) as Excel or CSV. Each upload is kept as a snapshot so changes show on the dashboard.</p>
        <div className="row">
          <input ref={file} type="file" accept=".xlsx,.csv" disabled={busy} />
          <button className="primary" disabled={busy} onClick={() => run(async () => {
            if (!file.current.files.length) throw new Error("Choose a file first");
            const r = await api.upload("/netsuite/report", "file", [file.current.files[0]]);
            setMsg(`Loaded ${r.rows.toLocaleString()} receipts (${r.receipts_without_ibs} without a shipment ID).`);
          })}>Load report</button>
          <span className="spacer" />
          <button disabled={busy || !me.netsuite_api} title={me.netsuite_api ? "" : "NetSuite API credentials are not set"} onClick={() => run(async () => {
            const r = await api.post("/netsuite/sync"); setMsg(`Synced ${r.shipments} shipments from NetSuite.`);
          })}>Sync from NetSuite API</button>
        </div>
        {!me.netsuite_api && <p className="note">API sync is off until NS_ACCOUNT_ID, NS_CLIENT_ID, NS_CERT_ID, NS_PRIVATE_KEY and NS_SHIPMENT_ID_FIELD are set.</p>}
        {busy && <p className="muted">Working…</p>}{msg && <p className="okline">{msg}</p>}{err && <p className="error">{err}</p>}
      </section>

      <section>
        <h2>Variance threshold</h2>
        <p>A shipment is flagged when landed freight differs from the bills by more than both values.</p>
        <Thresholds settings={cfg.settings} onSave={(v) => run(() => api.put("/settings", v))} />
      </section>

      <section>
        <h2>Confirmed ID corrections</h2>
        <table>
          <thead><tr><th>Written as</th><th>Means</th><th>Note</th><th /></tr></thead>
          <tbody>{cfg.aliases.map((a) => (
            <tr key={a.id}><td>{a.written}</td><td>{a.means}</td><td>{a.note}</td><td><button className="link" onClick={() => run(() => api.del(`/aliases/${a.id}`))}>Remove</button></td></tr>))}
            <tr><td><input value={alias.written} onChange={(e) => setAlias({ ...alias, written: e.target.value })} placeholder="250-LMN-AIR-1" /></td>
              <td><input value={alias.means} onChange={(e) => setAlias({ ...alias, means: e.target.value })} placeholder="2509-LMN-AIR-1" /></td><td />
              <td><button onClick={() => run(async () => { await api.post("/aliases", alias); setAlias({ written: "", means: "" }); })}>Add</button></td></tr></tbody>
        </table>
      </section>

      <section>
        <h2>Charge buckets</h2>
        <p>A charge line whose description matches a pattern goes to that bucket; everything else is freight &amp; handling.</p>
        <table>
          <thead><tr><th>Description matches</th><th>Bucket</th><th /></tr></thead>
          <tbody>{cfg.bucket_rules.map((r) => (
            <tr key={r.id}><td><code>{r.pattern}</code></td><td>{r.bucket.replace("_", " ")}</td><td><button className="link" onClick={() => run(() => api.del(`/bucket-rules/${r.id}`))}>Remove</button></td></tr>))}
            <tr><td><input value={rule.pattern} onChange={(e) => setRule({ ...rule, pattern: e.target.value })} placeholder="anti-dumping duty" /></td>
              <td><select value={rule.bucket} onChange={(e) => setRule({ ...rule, bucket: e.target.value })}><option value="customs">customs</option><option value="broker_fee">broker fee</option><option value="freight">freight</option></select></td>
              <td><button onClick={() => run(async () => { await api.post("/bucket-rules", rule); setRule({ pattern: "", bucket: "customs" }); })}>Add</button></td></tr></tbody>
        </table>
      </section>
    </>
  );
}

function Thresholds({ settings, onSave }) {
  const [a, setA] = useState(settings.variance_abs);
  const [p, setP] = useState(settings.variance_pct);
  return (
    <div className="row">
      <label>Dollars <input type="number" min="0" step="0.01" value={a} onChange={(e) => setA(e.target.value)} /></label>
      <label>Percent <input type="number" min="0" step="0.1" value={p} onChange={(e) => setP(e.target.value)} /></label>
      <button onClick={() => onSave({ variance_abs: Number(a), variance_pct: Number(p) })}>Save</button>
    </div>
  );
}
