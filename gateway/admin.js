// Shared helpers for the Libre Thinker Agent admin pages.
const $ = (id) => document.getElementById(id);
let adminToken = "";
let tokenRejected = false;  // stop live polling after a rejected token so it cannot trip the login limiter
try { adminToken = localStorage.getItem("ltaAdminToken") || ""; } catch (e) { /* storage blocked */ }

async function api(path, options = {}) {
  const res = await fetch(path, Object.assign({}, options, {
    headers: Object.assign({ "X-Admin-Token": adminToken }, options.headers || {}),
  }));
  if (res.status === 401) { tokenRejected = true; throw new Error("Admin token rejected"); }
  if (res.status === 429) { tokenRejected = true; throw new Error("Too many failed logins; wait a few minutes"); }
  if (!res.ok) throw new Error("HTTP " + res.status);
  return res.json();
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// Spend is tiny per request, so show enough digits to be useful.
function money(v) {
  if (v === null || v === undefined) return "—";
  if (v === 0) return "$0.00";
  if (Math.abs(v) < 0.01) return "$" + v.toFixed(5);
  if (Math.abs(v) < 1) return "$" + v.toFixed(4);
  return "$" + v.toFixed(2);
}

function num(v) {
  if (v === null || v === undefined) return "—";
  if (v >= 1e6) return (v / 1e6).toFixed(2) + "M";
  if (v >= 1e4) return (v / 1e3).toFixed(1) + "k";
  return String(v);
}

function pct(part, whole) {
  return whole ? Math.round((part / whole) * 100) + "%" : "—";
}

function fmtTime(ts) {
  return ts ? new Date(ts * 1000).toLocaleString() : "never";
}

function ago(ts) {
  if (!ts) return "never";
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 60) return Math.floor(s) + "s ago";
  if (s < 3600) return Math.floor(s / 60) + "m ago";
  if (s < 86400) return Math.floor(s / 3600) + "h ago";
  return Math.floor(s / 86400) + "d ago";
}

function toast(message) {
  const el = $("toast");
  if (!el) return alert(message);
  el.textContent = message;
  el.style.display = "block";
  clearTimeout(toast.t);
  toast.t = setTimeout(() => { el.style.display = "none"; }, 3500);
}

// Header with navigation and the admin token box, shared by every page.
function shortModel(m) { return String(m).replace(/^[^/]+\//, ""); }

// One request as a table row (8 columns), plus its expandable detail row when open.
function requestRow(r, fresh, open) {
  const bad = r.status >= 400;
  const cached = r.cached_tokens ? ' <span class="pill cache" title="Served from the provider cache">' + pct(r.cached_tokens, r.prompt_tokens) + " cached</span>" : "";
  let html = '<tr class="req' + (fresh ? " new" : "") + '" data-id="' + r.id + '"><td title="' + escapeHtml(fmtTime(r.ts)) + '">' + ago(r.ts) +
    "</td><td>" + escapeHtml(r.device_name) + "</td><td>" + escapeHtml(shortModel(r.model)) +
    '</td><td><span class="pill ' + (bad ? "bad" : "on") + '">' + r.status + '</span></td><td class="num">' + (r.latency_ms / 1000).toFixed(1) +
    ' s</td><td class="num">' + num(r.prompt_tokens) + cached + '</td><td class="num">' + num(r.completion_tokens) +
    '</td><td class="num">' + money(r.cost) + "</td></tr>";
  if (open) html += requestDetail(r);
  return html;
}

function requestDetail(r) {
  const parts = [
    ["Time", fmtTime(r.ts)], ["Model", r.model], ["Mode", r.stream ? "streamed" : "single reply"],
    ["Prompt tokens", r.prompt_tokens ?? "\u2014"],
    ["Cached", r.cached_tokens ? r.cached_tokens + " (" + pct(r.cached_tokens, r.prompt_tokens) + ")" : "0"],
    ["Cache written", r.cache_write_tokens || 0],
    ["Output tokens", r.completion_tokens ?? "\u2014"], ["Reasoning", r.reasoning_tokens || 0],
    ["Cost", money(r.cost)], ["Saved by cache", r.saved ? money(r.saved) : "\u2014"],
  ];
  let html = '<tr class="detail"><td colspan="8"><div class="grid">' +
    parts.map((p) => "<div>" + p[0] + "<br><b>" + escapeHtml(p[1]) + "</b></div>").join("") + "</div>";
  if (r.error) html += '<div class="err" style="margin-top:8px">' + escapeHtml(r.error) + "</div>";
  if (r.transcript_ref) html += '<div style="margin-top:8px"><a class="btnlink" href="/conversation?id=' + r.id + '">View conversation &rarr;</a></div>';
  return html + "</td></tr>";
}

function renderHeader(active) {
  $("top").innerHTML =
    "<h1><img src='/assets/logo.svg' alt='' class='logo'>Libre Thinker Agent <span class='sub'>Gateway</span></h1>" +
    '<nav><a href="/" class="' + (active === "dash" ? "on" : "") + '">Dashboard</a>' +
    '<a href="/requests" class="' + (active === "requests" ? "on" : "") + '">All requests</a>' +
    '<a href="/logs" class="' + (active === "logs" ? "on" : "") + '">Logs</a>' +
    '<a href="/revoked" class="' + (active === "revoked" ? "on" : "") + '">Revoked devices</a></nav>' +
    '<span class="spacer"></span>' +
    '<label class="live" id="liveToggle" title="Refresh automatically"><span class="dot"></span><span id="liveText">Live</span></label>' +
    '<input id="adminToken" type="password" placeholder="Admin token" autocomplete="off" style="width:170px">' +
    '<button id="saveToken">Connect</button><span id="authState" class="muted"></span>';
  $("adminToken").value = adminToken;
  $("saveToken").onclick = () => {
    adminToken = $("adminToken").value.trim();
    tokenRejected = false;
    try { localStorage.setItem("ltaAdminToken", adminToken); } catch (e) { /* ignore */ }
    window.refreshAll();
  };
  $("adminToken").addEventListener("keydown", (e) => { if (e.key === "Enter") $("saveToken").click(); });
}

// Calls refresh() now and then on a timer while the tab is visible.
function startLive(refresh, intervalMs) {
  let on = true;
  try { on = localStorage.getItem("ltaLive") !== "off"; } catch (e) { /* ignore */ }
  const paint = () => { $("liveToggle").classList.toggle("on", on); $("liveText").textContent = on ? "Live" : "Paused"; };
  $("liveToggle").onclick = () => {
    on = !on;
    try { localStorage.setItem("ltaLive", on ? "on" : "off"); } catch (e) { /* ignore */ }
    paint();
    if (on) refresh();
  };
  paint();
  setInterval(() => { if (on && !document.hidden && adminToken && !tokenRejected) refresh(); }, intervalMs);
}

// Download an admin-only file (fetch with the token header, then save the blob).
async function download(path, filename) {
  const res = await fetch(path, { headers: { "X-Admin-Token": adminToken } });
  if (!res.ok) { toast("Download failed: HTTP " + res.status); return; }
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement("a");
  a.href = url; a.download = filename; document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
}

function bytes(n) {
  if (n >= 1 << 30) return (n / (1 << 30)).toFixed(2) + " GB";
  if (n >= 1 << 20) return (n / (1 << 20)).toFixed(1) + " MB";
  if (n >= 1 << 10) return (n / (1 << 10)).toFixed(0) + " KB";
  return n + " B";
}

function setAuth(ok, message) {
  const el = $("authState");
  el.textContent = message;
  el.className = ok ? "ok" : "err";
}
