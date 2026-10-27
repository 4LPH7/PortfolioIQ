/**
 * PortfolioIQ — API Client
 * Thin wrapper around fetch() that talks to the local Flask backend.
 */

function getApiBase() {
  if (window.location.protocol === "file:") {
    return "http://localhost:5000/api/v1";
  }
  return `${window.location.origin}/api/v1`;
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
    success: `<span class="material-symbols-outlined" style="font-size:18px;">check_circle</span>`,
    warn: `<span class="material-symbols-outlined" style="font-size:18px;">warning</span>`,
    error: `<span class="material-symbols-outlined" style="font-size:18px;">error</span>`,
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
    return { ...data, ok: false, error: errorMsg }; // spread first, or data.error (an object) wins
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

async function apiDelete(path) {
  const res = await fetch(resolveUrl(path), {
    method: "DELETE",
    headers: { "X-API-Key": getApiKey(), "X-Request-ID": generateRequestId() },
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
    "STRONG BUY":  "#2e7d32",
    "BUY":         "#2e7d32",
    "HOLD":        "#5f6368",
    "SELL":        "#c4242a",
    "STRONG SELL": "#c4242a",
  };
  return map[norm] || "#5f6368";
}

function sigIcon(signal) {
  const norm = (signal || "").replace(/_/g, " ").toUpperCase().trim();
  const map = {
    "STRONG BUY": `<span class="material-symbols-outlined" style="font-size:16px;color:#2e7d32;vertical-align:middle;">rocket_launch</span>`,
    "BUY": `<span class="material-symbols-outlined" style="font-size:16px;color:#2e7d32;vertical-align:middle;">trending_up</span>`,
    "HOLD": `<span class="material-symbols-outlined" style="font-size:16px;color:#5f6368;vertical-align:middle;">pause_circle</span>`,
    "SELL": `<span class="material-symbols-outlined" style="font-size:16px;color:#c4242a;vertical-align:middle;">trending_down</span>`,
    "STRONG SELL": `<span class="material-symbols-outlined" style="font-size:16px;color:#c4242a;vertical-align:middle;">arrow_downward</span>`,
  };
  return map[norm] || `<span class="material-symbols-outlined" style="font-size:16px;color:#5f6368;vertical-align:middle;">pause_circle</span>`;
}

function loading(msg = "Loading…") {
  return `<div class="loading-wrap"><div class="spinner"></div><span style="color:var(--text-muted)">${msg}</span></div>`;
}

function errBox(msg) {
  return `<div class="alert alert-err"><span class="alert-icon"><span class="material-symbols-outlined">error</span></span><div class="alert-body"><div class="alert-title">Error</div><div class="alert-text">${msg}</div></div></div>`;
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

// ── Plotly theme: ink on white, faint horizontal grid, no y-axis line ──

const CHART = { ink: "#0a0a0a", muted: "#5f6368", grid: "#e5e5e5", hue: "#1d5fbf", pos: "#2e7d32", neg: "#c4242a", pale: "#c9ced6" };

const PLOTLY_LAYOUT = {
  paper_bgcolor: "rgba(0,0,0,0)",
  plot_bgcolor: "rgba(0,0,0,0)",
  font: { family: "Geist, Inter, system-ui, sans-serif", color: CHART.ink, size: 12 },
  xaxis: { gridcolor: CHART.grid, zeroline: false, showline: false, color: CHART.muted, tickfont: { size: 12, color: CHART.muted } },
  yaxis: { gridcolor: CHART.grid, zeroline: false, showline: false, color: CHART.muted, tickfont: { size: 12, color: CHART.muted } },
  margin: { l: 48, r: 16, t: 16, b: 36 },
  legend: { bgcolor: "rgba(0,0,0,0)", font: { size: 12, color: CHART.muted } },
  hoverlabel: { bgcolor: "#141414", bordercolor: "#141414", font: { color: "#ffffff", size: 12, family: "Geist, Inter, sans-serif" } },
};

const PLOTLY_CONFIG = { responsive: true, displayModeBar: false };

// ── Mobile Navigation & A11y ───────────────────────────────────

function initMobileNavigation() {
  const toggleBtn = document.getElementById("sidebar-toggle-btn");
  const retractBtn = document.getElementById("sidebar-retract-btn");
  const sidebar = document.querySelector(".sidebar");
  let backdrop = document.getElementById("sidebar-backdrop");

  function isMobile() {
    return window.innerWidth <= 768;
  }

  // Ensure desktop view is always clean, full-width, and never trapped in retracted shelf
  if (!isMobile()) {
    localStorage.removeItem("portfolioiq_sidebar_retracted");
    document.body.classList.remove("sidebar-retracted");
  }

  if (!backdrop && typeof document !== "undefined") {
    backdrop = document.createElement("div");
    backdrop.id = "sidebar-backdrop";
    backdrop.className = "sidebar-backdrop";
    document.body.appendChild(backdrop);
  }

  function openSidebar() {
    if (sidebar) sidebar.classList.add("open");
    if (backdrop) backdrop.classList.add("open");
    if (toggleBtn) toggleBtn.setAttribute("aria-expanded", "true");
    if (retractBtn) {
      const icon = retractBtn.querySelector(".material-symbols-outlined");
      if (icon) icon.textContent = "chevron_left";
      retractBtn.title = "Shrink Navigation Shelf";
      retractBtn.setAttribute("aria-label", "Shrink navigation shelf");
    }
  }

  function closeSidebar() {
    if (sidebar) sidebar.classList.remove("open");
    if (backdrop) backdrop.classList.remove("open");
    if (toggleBtn) toggleBtn.setAttribute("aria-expanded", "false");
  }

  // Hamburger button in topbar (mobile only)
  if (toggleBtn) {
    toggleBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      if (sidebar && sidebar.classList.contains("open")) {
        closeSidebar();
      } else {
        openSidebar();
      }
    });
  }

  // Shelf header shrink / retrieve button (visible on mobile only when expanded)
  if (retractBtn) {
    retractBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      closeSidebar();
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
    if (!isMobile()) {
      closeSidebar();
      document.body.classList.remove("sidebar-retracted");
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

// ── Floating tooltip controller ───────────
function initGlobalTooltipController() {
  if (typeof window === "undefined" || typeof document === "undefined") return;

  let tooltipEl = document.getElementById("app-floating-tooltip");
  if (!tooltipEl) {
    tooltipEl = document.createElement("div");
    tooltipEl.id = "app-floating-tooltip";
    tooltipEl.className = "app-floating-tooltip";
    tooltipEl.setAttribute("role", "tooltip");
    tooltipEl.setAttribute("aria-hidden", "true");
    document.body.appendChild(tooltipEl);
  }

  let currentTarget = null;
  let showTimeout = null;

  function hideTooltip() {
    clearTimeout(showTimeout);
    currentTarget = null;
    tooltipEl.classList.remove("visible");
  }

  function getTooltipText(target) {
    if (!target) return "";
    const isSidebarRetracted = document.body.classList.contains("sidebar-retracted");
    const isNavItem = target.closest(".nav-item");

    // Only show sidebar nav tooltips if sidebar is retracted or explicitly requested
    if (isNavItem && !isSidebarRetracted && !target.hasAttribute("data-tip")) {
      return "";
    }

    const tip = target.getAttribute("data-tip") ||
                target.getAttribute("data-tooltip") ||
                target.getAttribute("data-title") ||
                (target.hasAttribute("title") ? target.getAttribute("title") : "");

    if (tip && target.hasAttribute("title")) {
      target.setAttribute("data-orig-title", target.getAttribute("title"));
      target.removeAttribute("title");
    }

    return tip ? tip.trim() : "";
  }

  function positionTooltip(target, text) {
    tooltipEl.textContent = text;
    tooltipEl.className = "app-floating-tooltip";

    const rect = target.getBoundingClientRect();
    const isSidebar = !!target.closest(".sidebar");

    // Temporarily position off-screen to measure true layout dimensions
    tooltipEl.style.left = "-9999px";
    tooltipEl.style.top = "-9999px";
    tooltipEl.style.visibility = "hidden";
    tooltipEl.classList.add("visible");
    const tipWidth = tooltipEl.offsetWidth;
    const tipHeight = tooltipEl.offsetHeight;
    tooltipEl.style.visibility = "";

    if (isSidebar) {
      // Place to the right of the sidebar item
      tooltipEl.classList.add("side", "placement-right", "visible");
      let left = rect.right + 12;
      let top = rect.top + (rect.height / 2) - (tipHeight / 2);
      top = Math.max(8, Math.min(top, window.innerHeight - tipHeight - 8));
      tooltipEl.style.left = `${Math.round(left)}px`;
      tooltipEl.style.top = `${Math.round(top)}px`;
      tooltipEl.style.setProperty("--arrow-offset", `${Math.round(rect.top + (rect.height / 2) - top)}px`);
    } else {
      // Place above target, flip to bottom if off-screen top
      let top = rect.top - tipHeight - 10;
      let placement = "placement-top";
      if (top < 10) {
        top = rect.bottom + 10;
        placement = "placement-bottom";
      }

      const idealCenter = rect.left + (rect.width / 2);
      let left = idealCenter - (tipWidth / 2);
      left = Math.max(10, Math.min(left, window.innerWidth - tipWidth - 10));

      tooltipEl.classList.add(placement, "visible");
      tooltipEl.style.left = `${Math.round(left)}px`;
      tooltipEl.style.top = `${Math.round(top)}px`;

      const arrowOffset = Math.max(12, Math.min(idealCenter - left, tipWidth - 12));
      tooltipEl.style.setProperty("--arrow-offset", `${Math.round(arrowOffset)}px`);
    }
  }

  document.addEventListener("pointerover", (e) => {
    const target = e.target.closest("[data-tip], [data-tooltip], [title], [data-title], .sidebar .nav-item");
    if (!target) return;
    const text = getTooltipText(target);
    if (!text) return;

    if (currentTarget === target) return;
    currentTarget = target;

    const delay = tooltipEl.classList.contains("visible") ? 20 : 70;
    clearTimeout(showTimeout);
    showTimeout = setTimeout(() => {
      if (currentTarget === target) {
        positionTooltip(target, text);
      }
    }, delay);
  }, { passive: true });

  document.addEventListener("pointerout", (e) => {
    if (!currentTarget) return;
    if (e.relatedTarget && currentTarget.contains(e.relatedTarget)) return;
    hideTooltip();
  }, { passive: true });

  window.addEventListener("scroll", hideTooltip, { passive: true });
  window.addEventListener("resize", hideTooltip, { passive: true });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") hideTooltip();
  });
}

function initEffectsAndTooltips() {
  initGlobalTooltipController();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initEffectsAndTooltips);
} else {
  initEffectsAndTooltips();
}
