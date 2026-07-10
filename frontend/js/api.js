/**
 * PortfolioIQ — API Client
 * Thin wrapper around fetch() that talks to the Flask backend.
 * API_BASE auto-detects localhost vs deployed backend.
 */

const API_BASE = window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1"
  ? "http://localhost:5000"
  : "https://portfolioiq-z4r6.onrender.com";   // ← connected to live Render backend

async function apiGet(path, params = {}) {
  const url = new URL(API_BASE + path);
  Object.entries(params).forEach(([k, v]) => url.searchParams.set(k, v));
  const res = await fetch(url.toString());
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

async function apiPost(path, body = {}) {
  const res = await fetch(API_BASE + path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return res.json();
}

async function apiPatch(path, body = {}) {
  const res = await fetch(API_BASE + path, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return res.json();
}

// ── Helpers ───────────────────────────────────────────────────

function fmt(n, prefix = "₹", dp = 2) {
  if (n == null || isNaN(n)) return "—";
  const v = parseFloat(n);
  return prefix + v.toLocaleString("en-IN", { minimumFractionDigits: dp, maximumFractionDigits: dp });
}

function fmtPct(n, dp = 2) {
  if (n == null || isNaN(n)) return "—";
  const v = parseFloat(n);
  return (v >= 0 ? "+" : "") + v.toFixed(dp) + "%";
}

function colorClass(n) {
  const v = parseFloat(n);
  if (v > 0) return "td-pos";
  if (v < 0) return "td-neg";
  return "";
}

function signalBadge(signal) {
  const map = {
    "STRONG BUY":  "badge-strong-buy",
    "BUY":         "badge-buy",
    "HOLD":        "badge-hold",
    "SELL":        "badge-sell",
    "STRONG SELL": "badge-strong-sell",
  };
  const cls = map[signal] || "badge-hold";
  return `<span class="badge ${cls}">${signal || "—"}</span>`;
}

function sigColor(signal) {
  const map = {
    "STRONG BUY":  "#00ff88",
    "BUY":         "#00d4ff",
    "HOLD":        "#ffd600",
    "SELL":        "#ff6b35",
    "STRONG SELL": "#ff3366",
  };
  return map[signal] || "#ffd600";
}

function sigIcon(signal) {
  const map = {
    "STRONG BUY": "🚀", "BUY": "📈", "HOLD": "⏸️",
    "SELL": "📉", "STRONG SELL": "🔻",
  };
  return map[signal] || "⏸️";
}

function loading(msg = "Loading…") {
  return `<div class="loading-wrap"><div class="spinner"></div><span style="color:var(--text-muted)">${msg}</span></div>`;
}

function errBox(msg) {
  return `<div class="alert alert-err"><span class="alert-icon">⚠️</span><div class="alert-body"><div class="alert-title">Error</div><div class="alert-text">${msg}</div></div></div>`;
}

// ── Sidebar status ─────────────────────────────────────────────

async function loadSidebarStatus() {
  try {
    const r = await apiGet("/api/market/status");
    const mkt = r.data;
    const pill = document.getElementById("market-status");
    if (pill) {
      const isOpen = mkt.is_open;
      pill.className = `status-pill ${isOpen ? "status-ok" : "status-warn"}`;
      pill.innerHTML = `<span>${isOpen ? "🟢" : "🟡"}</span><span>${mkt.status_text}</span>`;
    }
  } catch { /* offline */ }

  try {
    const h = await apiGet("/api/health");
    const pill = document.getElementById("db-status");
    if (pill) {
      pill.className = `status-pill ${h.db ? "status-ok" : "status-err"}`;
      pill.innerHTML = `<span>${h.db ? "🗄️" : "🔴"}</span><span>${h.db ? "Database OK" : "DB Offline"}</span>`;
    }
  } catch {
    const pill = document.getElementById("db-status");
    if (pill) { pill.className = "status-pill status-err"; pill.innerHTML = "🔴 Backend offline"; }
  }
}

// ── Tab switcher ───────────────────────────────────────────────

function initTabs(containerId) {
  const container = document.getElementById(containerId);
  if (!container) return;
  const buttons = container.querySelectorAll(".tab-btn");
  const panels  = container.querySelectorAll(".tab-panel");

  buttons.forEach(btn => {
    btn.addEventListener("click", () => {
      buttons.forEach(b => b.classList.remove("active"));
      panels.forEach(p => p.classList.remove("active"));
      btn.classList.add("active");
      const target = btn.dataset.tab;
      const panel = container.querySelector(`.tab-panel[data-tab="${target}"]`);
      if (panel) panel.classList.add("active");
    });
  });

  // Activate first tab
  if (buttons[0]) buttons[0].click();
}

// ── Plotly dark theme defaults ─────────────────────────────────

const PLOTLY_LAYOUT = {
  paper_bgcolor: "rgba(0,0,0,0)",
  plot_bgcolor: "rgba(0,0,0,0)",
  font: { family: "Inter, sans-serif", color: "#ccd6f6", size: 12 },
  xaxis: { gridcolor: "rgba(255,255,255,0.04)", zeroline: false, color: "#8892b0" },
  yaxis: { gridcolor: "rgba(255,255,255,0.04)", zeroline: false, color: "#8892b0" },
  margin: { l: 40, r: 20, t: 30, b: 40 },
  legend: { bgcolor: "rgba(0,0,0,0)", font: { size: 11 } },
  hoverlabel: { bgcolor: "#0d1117", bordercolor: "#00d4ff", font: { color: "#e6f1ff", size: 12 } },
};

const PLOTLY_CONFIG = { responsive: true, displayModeBar: false };

// ── Run on DOM ready ───────────────────────────────────────────
document.addEventListener("DOMContentLoaded", () => {
  loadSidebarStatus();
  // Highlight active nav item
  const path = window.location.pathname.split("/").pop() || "index.html";
  document.querySelectorAll(".nav-item").forEach(el => {
    if (el.getAttribute("href") === path) el.classList.add("active");
  });
});
