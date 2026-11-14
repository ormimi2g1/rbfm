import React, { useEffect, useState } from "react";
import { api } from "./api.js";
import Dashboard from "./pages/Dashboard.jsx";
import Bills from "./pages/Bills.jsx";
import Shipments from "./pages/Shipments.jsx";
import IdIssues from "./pages/IdIssues.jsx";
import Setup from "./pages/Setup.jsx";

const PAGES = [["dashboard", "Dashboard"], ["bills", "Bills"], ["shipments", "Shipments"], ["ids", "Shipment IDs"], ["setup", "Data & settings"]];

function useHash() {
  const read = () => window.location.hash.replace(/^#\/?/, "") || "dashboard";
  const [h, setH] = useState(read());
  useEffect(() => {
    const on = () => setH(read());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return h;
}

function Login({ onDone }) {
  const [password, setPassword] = useState("");
  const [err, setErr] = useState("");
  const submit = async (e) => {
    e.preventDefault();
    try { await api.post("/login", { password }); onDone(); } catch (x) { setErr(x.message); }
  };
  return (
    <form className="login" onSubmit={submit}>
      <h1>Freight Landed-Cost Monitor</h1>
      <label>Password<input type="password" autoFocus value={password} onChange={(e) => setPassword(e.target.value)} /></label>
      {err && <p className="error">{err}</p>}
      <button className="primary">Sign in</button>
    </form>
  );
}

export default function App() {
  const [me, setMe] = useState(null);
  const hash = useHash();
  const load = () => api.get("/me").then(setMe).catch(() => setMe({ signed_in: false }));
  useEffect(() => {
    load();
    const out = () => setMe((m) => ({ ...(m || {}), signed_in: false }));
    window.addEventListener("signed-out", out);
    return () => window.removeEventListener("signed-out", out);
  }, []);
  if (!me) return null;
  if (!me.signed_in) return <Login onDone={load} />;
  const [page, arg] = hash.split("/");
  return (
    <div className="shell">
      <header>
        <strong>Freight Landed-Cost Monitor</strong>
        <nav>{PAGES.map(([k, label]) => <a key={k} href={`#/${k}`} className={page === k ? "on" : ""}>{label}</a>)}</nav>
        <span className="spacer" />
        <a className="btn" href="/api/export.xlsx">Export Excel</a>
        <button className="link" onClick={() => api.post("/logout").then(load)}>Sign out</button>
      </header>
      <main>
        {page === "dashboard" && <Dashboard />}
        {page === "bills" && <Bills me={me} />}
        {page === "shipments" && <Shipments selected={arg ? decodeURIComponent(arg) : null} />}
        {page === "ids" && <IdIssues />}
        {page === "setup" && <Setup me={me} />}
      </main>
    </div>
  );
}
