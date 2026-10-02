/**
 * PortfolioIQ — API Client
 * Thin wrapper around fetch() that talks to the Flask backend.
 * Netlify proxies /api to the Railway API. Local development connects directly.
 */

function getApiBase() {
  return window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1"
    ? "http://localhost:5000/api/v1"
    : `${window.location.origin}/api/v1`;
}

const API_BASE = getApiBase();

function getApiKey() {
  return (
    sessionStorage.getItem("portfolioiq_session_key") || ""
  );
}

function handleUnauthorized(errorData) {
  const msg = errorData?.error?.message || "Session unauthorized or expired (401).";
  if (window.PortfolioIQSession && typeof window.PortfolioIQSession.lock === "function") {
    window.PortfolioIQSession.lock(msg);
  }
  if (typeof window.showToast === "function") {
    window.showToast("Unauthorized (401). Please unlock session.", "error");
  }
}

// ── Toast Notification System ─────────────────────────────────

function getOrCreateToastContainer() {
  let container = document.getElementById("toast-container");
  if (!container) {
    container = document.createElement("div");
    container.id = "toast-container";
    container.className = "toast-container";
    container.setAttribute("role", "region");
    container.setAttribute("aria-live", "polite");
    container.setAttribute("aria-label", "Notifications");
    document.body.appendChild(container);
  }
  return container;
}

function showToast(message, type = "info", duration = 4000) {
  const container = getOrCreateToastContainer();
  const toast = document.createElement("div");
  toast.className = `toast toast-${type}`;
  toast.setAttribute("role", "status");

  const icons = {
    info: `<span class="material-symbols-outlined" style="font-size:18px;">info</span>`,
    success: `<span class="material-symbols-outlined" style="font-size:18px;color:#00ff88;">check_circle</span>`,
    warn: `<span class="material-symbols-outlined" style="font-size:18px;color:#ffd600;">warning</span>`,
    error: `<span class="material-symbols-outlined" style="font-size:18px;color:#ff3366;">error</span>`,
  };
  const icon = icons[type] || icons.info;

  toast.innerHTML = `
    <span class="toast-icon" aria-hidden="true">${icon}</span>
    <span class="toast-message">${message}</span>
    <button type="button" class="toast-close" aria-label="Dismiss notification"><span class="material-symbols-outlined" style="font-size:16px;">close</span></button>
  `;

  const closeBtn = toast.querySelector(".toast-close");
  const dismiss = () => {
    toast.classList.add("toast-leaving");
    setTimeout(() => {
      if (toast.parentNode === container) {
        container.removeChild(toast);
      }
    }, 250);
  };

  if (closeBtn) closeBtn.addEventListener("click", dismiss);

  container.appendChild(toast);

  if (duration > 0) {
    setTimeout(dismiss, duration);
  }
  return toast;
}
window.showToast = showToast;

function generateRequestId() {
  return "req_" + Math.random().toString(36).substring(2, 14);
}

function resolveUrl(path) {
  let cleanPath = path;
  if (cleanPath.startsWith("/api/v1/")) {
    cleanPath = cleanPath.slice(7);
  } else if (cleanPath.startsWith("/api/")) {
    cleanPath = cleanPath.slice(4);
  } else if (!cleanPath.startsWith("/")) {
    cleanPath = "/" + cleanPath;
  }
  return API_BASE + cleanPath;
}

async function apiGet(path, params = {}) {
  const url = new URL(resolveUrl(path));
  Object.entries(params).forEach(([k, v]) => url.searchParams.set(k, v));
  const res = await fetch(url.toString(), {
    headers: {
      "X-API-Key": getApiKey(),
      "X-Request-ID": generateRequestId(),
    },
  });
  const data = await res.json().catch(() => ({}));
  if (res.status === 401) {
    handleUnauthorized(data);
  }
  if (!res.ok) {
    const errorMsg = data.error?.message || (typeof data.error === "string" ? data.error : `HTTP ${res.status}`);
    return { ok: false, error: errorMsg, ...data };
  }
  return data;
}

async function apiPost(path, body = {}) {
  const res = await fetch(resolveUrl(path), {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-API-Key": getApiKey(),
      "X-Request-ID": generateRequestId(),
    },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (res.status === 401) {
    handleUnauthorized(data);
  }
  if (!res.ok && data.error && typeof data.error === "object") {
    data.error_message = data.error.message;
  }
  return data;
}

async function apiPatch(path, body = {}) {
  const res = await fetch(resolveUrl(path), {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
      "X-API-Key": getApiKey(),
      "X-Request-ID": generateRequestId(),
    },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (res.status === 401) {
    handleUnauthorized(data);
  }
  if (!res.ok && data.error && typeof data.error === "object") {
    data.error_message = data.error.message;
  }
  return data;
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
  const norm = (signal || "").replace(/_/g, " ").toUpperCase().trim();
  const map = {
    "STRONG BUY":  "badge-strong-buy",
    "BUY":         "badge-buy",
    "HOLD":        "badge-hold",
    "SELL":        "badge-sell",
    "STRONG SELL": "badge-strong-sell",
  };
  const cls = map[norm] || "badge-hold";
  return `<span class="badge ${cls}">${norm || "—"}</span>`;
}

function sigColor(signal) {
  const norm = (signal || "").replace(/_/g, " ").toUpperCase().trim();
  const map = {
    "STRONG BUY":  "#00ff88",
    "BUY":         "#00d4ff",
    "HOLD":        "#ffd600",
    "SELL":        "#ff6b35",
    "STRONG SELL": "#ff3366",
  };
  return map[norm] || "#ffd600";
}

function sigIcon(signal) {
  const norm = (signal || "").replace(/_/g, " ").toUpperCase().trim();
  const map = {
    "STRONG BUY": `<span class="material-symbols-outlined" style="font-size:16px;color:#00ff88;vertical-align:middle;">rocket_launch</span>`,
    "BUY": `<span class="material-symbols-outlined" style="font-size:16px;color:#00d4ff;vertical-align:middle;">trending_up</span>`,
    "HOLD": `<span class="material-symbols-outlined" style="font-size:16px;color:#ffd600;vertical-align:middle;">pause_circle</span>`,
    "SELL": `<span class="material-symbols-outlined" style="font-size:16px;color:#ff6b35;vertical-align:middle;">trending_down</span>`,
    "STRONG SELL": `<span class="material-symbols-outlined" style="font-size:16px;color:#ff3366;vertical-align:middle;">arrow_downward</span>`,
  };
  return map[norm] || `<span class="material-symbols-outlined" style="font-size:16px;color:#ffd600;vertical-align:middle;">pause_circle</span>`;
}

function loading(msg = "Loading…") {
  return `<div class="loading-wrap"><div class="spinner"></div><span style="color:var(--text-muted)">${msg}</span></div>`;
}

function errBox(msg) {
  return `<div class="alert alert-err"><span class="alert-icon"><span class="material-symbols-outlined" style="color:#ff3366;">error</span></span><div class="alert-body"><div class="alert-title">Error</div><div class="alert-text">${msg}</div></div></div>`;
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
      pill.innerHTML = `<div class="status-dot"></div><span>${mkt.status_text}</span>`;
    }
  } catch { /* offline */ }

  try {
    const h = await apiGet("/api/health");
    const pill = document.getElementById("db-status");
    if (pill) {
      pill.className = `status-pill ${h.db ? "status-ok" : "status-err"}`;
      pill.innerHTML = `<div class="status-dot"></div><span>${h.db ? "Database OK" : "DB Offline"}</span>`;
    }
  } catch {
    const pill = document.getElementById("db-status");
    if (pill) { pill.className = "status-pill status-err"; pill.innerHTML = `<div class="status-dot"></div><span>Backend offline</span>`; }
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
      if (panel) {
        panel.classList.add("active");
        requestAnimationFrame(() => {
          panel.querySelectorAll(".js-plotly-plot").forEach(plot => {
            if (window.Plotly && typeof window.Plotly.Plots.resize === "function") {
              window.Plotly.Plots.resize(plot);
            }
          });
          window.dispatchEvent(new Event("resize"));
        });
      }
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

// ── Mobile Navigation & A11y ───────────────────────────────────

function initMobileNavigation() {
  const toggleBtn = document.getElementById("sidebar-toggle-btn");
  const retractBtn = document.getElementById("sidebar-retract-btn");
  const sidebar = document.querySelector(".sidebar");
  const sidebarHeader = document.querySelector(".sidebar-header");
  let backdrop = document.getElementById("sidebar-backdrop");

  function isMobile() {
    return window.innerWidth <= 768;
  }

  function updateRetractButtonUI(isRetracted) {
    if (!retractBtn) return;
    const icon = retractBtn.querySelector(".material-symbols-outlined");
    if (isMobile()) {
      if (icon) icon.textContent = "close";
      retractBtn.title = "Close Navigation Menu";
      retractBtn.setAttribute("aria-label", "Close navigation menu");
    } else {
      if (icon) icon.textContent = isRetracted ? "chevron_right" : "chevron_left";
      retractBtn.title = isRetracted ? "Expand Sidebar Shelf" : "Minimize Sidebar Shelf";
      retractBtn.setAttribute("aria-label", isRetracted ? "Expand sidebar shelf" : "Minimize sidebar shelf");
    }
  }

  // Restore desktop retractable shelf preference
  const savedRetracted = localStorage.getItem("portfolioiq_sidebar_retracted") === "true";
  if (!isMobile() && savedRetracted) {
    document.body.classList.add("sidebar-retracted");
  }
  updateRetractButtonUI(document.body.classList.contains("sidebar-retracted"));

  function toggleRetract() {
    const isRetracted = document.body.classList.toggle("sidebar-retracted");
    localStorage.setItem("portfolioiq_sidebar_retracted", isRetracted ? "true" : "false");
    updateRetractButtonUI(isRetracted);
    if (toggleBtn) {
      toggleBtn.setAttribute("aria-expanded", isRetracted ? "false" : "true");
    }
  }

  function openSidebar() {
    if (sidebar) sidebar.classList.add("open");
    if (backdrop) backdrop.classList.add("open");
    if (toggleBtn) toggleBtn.setAttribute("aria-expanded", "true");
    updateRetractButtonUI(false);
  }

  function closeSidebar() {
    if (sidebar) sidebar.classList.remove("open");
    if (backdrop) backdrop.classList.remove("open");
    if (toggleBtn) toggleBtn.setAttribute("aria-expanded", "false");
  }

  // Button on top of the shelf (#sidebar-retract-btn):
  // - In mobile view or when drawer is open: minimizes/dismisses the shelf
  // - In desktop view: toggles between compact 68px and retrieved 240px shelf
  if (retractBtn) {
    retractBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      if (isMobile() || (sidebar && sidebar.classList.contains("open"))) {
        closeSidebar();
      } else {
        toggleRetract();
      }
    });
  }

  // On desktop: clicking header/logo in minimized state also retrieves the shelf!
  if (sidebarHeader) {
    sidebarHeader.addEventListener("click", (e) => {
      if (!isMobile() && document.body.classList.contains("sidebar-retracted")) {
        if (!e.target.closest("#sidebar-retract-btn")) {
          toggleRetract();
        }
      }
    });
  }

  if (!backdrop) {
    backdrop = document.createElement("div");
    backdrop.id = "sidebar-backdrop";
    backdrop.className = "sidebar-backdrop";
    document.body.appendChild(backdrop);
  }

  if (toggleBtn) {
    toggleBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      if (isMobile()) {
        if (sidebar && sidebar.classList.contains("open")) {
          closeSidebar();
        } else {
          openSidebar();
        }
      } else {
        toggleRetract();
      }
    });
  }

  if (backdrop) {
    backdrop.addEventListener("click", closeSidebar);
  }

  window.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && sidebar && sidebar.classList.contains("open")) {
      closeSidebar();
    }
  });

  window.addEventListener("resize", () => {
    const isRetracted = document.body.classList.contains("sidebar-retracted");
    updateRetractButtonUI(isRetracted);
    if (!isMobile() && sidebar && sidebar.classList.contains("open")) {
      closeSidebar();
    }
  });

  // Highlight active bottom-nav item
  const path = window.location.pathname.split("/").pop() || "index.html";
  document.querySelectorAll(".bottom-nav-item").forEach(el => {
    if (el.getAttribute("href") === path) el.classList.add("active");
  });
}

// ── Run on DOM ready ───────────────────────────────────────────
document.addEventListener("DOMContentLoaded", () => {
  loadSidebarStatus();
  initMobileNavigation();

  // Highlight active nav item
  const path = window.location.pathname.split("/").pop() || "index.html";
  document.querySelectorAll(".nav-item").forEach(el => {
    if (el.getAttribute("href") === path) el.classList.add("active");
  });
});

// ── React Bits Spotlight Controller (Global Cursor Tracking) ───
function initSpotlightController() {
  if (typeof window === "undefined") return;

  // Track cursor position for the whole-site atmospheric background spotlight
  window.addEventListener("pointermove", (e) => {
    document.body.style.setProperty("--mouse-x", `${e.clientX}px`);
    document.body.style.setProperty("--mouse-y", `${e.clientY}px`);
  }, { passive: true });

  // Track cursor relative to each card for the SpotlightCard effect
  document.addEventListener("pointermove", (e) => {
    const card = e.target.closest(".card-spotlight, .card, .kpi-card");
    if (!card) return;
    const rect = card.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;
    card.style.setProperty("--mouse-x", `${x}px`);
    card.style.setProperty("--mouse-y", `${y}px`);
  }, { passive: true });
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initSpotlightController);
} else {
  initSpotlightController();
}
