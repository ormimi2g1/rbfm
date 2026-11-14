async function call(path, opts = {}) {
  const r = await fetch("/api" + path, { credentials: "same-origin", ...opts });
  if (r.status === 401 && path !== "/login") {
    window.dispatchEvent(new Event("signed-out"));
    throw new Error("Please sign in");
  }
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.detail || `Request failed (${r.status})`);
  return body;
}
const json = (method, data) => ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });

export const api = {
  get: (p) => call(p),
  post: (p, d) => call(p, json("POST", d || {})),
  put: (p, d) => call(p, json("PUT", d)),
  patch: (p, d) => call(p, json("PATCH", d)),
  del: (p) => call(p, { method: "DELETE" }),
  upload: (p, field, files) => {
    const fd = new FormData();
    [...files].forEach((f) => fd.append(field, f));
    return call(p, { method: "POST", body: fd });
  },
};

export const money = (v) => (v === null || v === undefined ? "—" : Number(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
export const signed = (v) => (v === null || v === undefined ? "—" : (v > 0 ? "+" : "") + money(v));
export const pct = (v) => (v === null || v === undefined ? "—" : `${v > 0 ? "+" : ""}${Number(v).toFixed(1)}%`);

export const FLAGS = {
  NO_LC: "No landed cost", NO_FREIGHT_LINE: "No landed freight", NO_CUSTOMS_LINE: "No landed customs",
  FREIGHT_VAR: "Freight variance", MAIN_FREIGHT_BILL_MISSING: "Main freight bill missing", MODE_MISMATCH: "Mode mismatch",
  CATEGORY_MISMATCH: "Wrong cost category", ID_FORMAT: "ID written inconsistently", ID_TYPO: "ID corrected",
  PO_MISMATCH: "PO mismatch", BILL_NOT_TIED: "Bill does not tie", AI_READ: "Read by AI", NO_IBS: "ID not in NetSuite", NO_ID: "No shipment ID",
};
const SERIOUS = new Set(["NO_LC", "NO_FREIGHT_LINE", "NO_CUSTOMS_LINE", "FREIGHT_VAR", "BILL_NOT_TIED", "NO_IBS", "NO_ID", "MODE_MISMATCH", "PO_MISMATCH"]);
export const flagClass = (f) => (SERIOUS.has(f) ? "tag bad" : "tag warn");
