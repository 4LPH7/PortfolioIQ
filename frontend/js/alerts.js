/**
 * PortfolioIQ — In-App Notification Center & Alert Poller (frontend/js/alerts.js)
 * 
 * Provides:
 * - Recurring background alert polling via GET /api/v1/alerts.
 * - Topbar bell notification button with pulsing unread count badge.
 * - Accessible off-canvas slide-out drawer with tabbed filters (All, Drift, Price, Tax, System).
 * - Persistent dismissal tracking in localStorage.
 * - Real-time toast alerts for newly detected critical events.
 */

(function () {
  "use strict";

  const STORAGE_DISMISSED_KEY = "portfolioiq_dismissed_alerts";
  const POLL_INTERVAL_MS = 60000; // 60s background polling
  let pollTimer = null;
  let activeFilter = "ALL";
  let alertsCache = [];
  const knownAlertIds = new Set();

  function getDismissedIds() {
    try {
      const raw = localStorage.getItem(STORAGE_DISMISSED_KEY);
      return raw ? new Set(JSON.parse(raw)) : new Set();
    } catch {
      return new Set();
    }
  }

  function saveDismissedIds(idSet) {
    try {
      localStorage.setItem(STORAGE_DISMISSED_KEY, JSON.stringify([...idSet]));
    } catch (e) {
      console.warn("Could not persist dismissed alerts:", e);
    }
  }

  function formatRelativeTime(isoString) {
    if (!isoString) return "Just now";
    const date = new Date(isoString);
    const now = new Date();
    const diffSec = Math.floor((now - date) / 1000);

    if (diffSec < 30) return "Just now";
    if (diffSec < 60) return `${diffSec}s ago`;
    const diffMin = Math.floor(diffSec / 60);
    if (diffMin < 60) return `${diffMin}m ago`;
    const diffHours = Math.floor(diffMin / 60);
    if (diffHours < 24) return `${diffHours}h ago`;
    return date.toLocaleDateString("en-IN", { month: "short", day: "numeric" });
  }

  function ensureDrawerMarkup() {
    // 1. Ensure Backdrop
    let backdrop = document.getElementById("drawer-backdrop");
    if (!backdrop) {
      backdrop = document.createElement("div");
      backdrop.id = "drawer-backdrop";
      backdrop.className = "drawer-backdrop";
      backdrop.onclick = closeDrawer;
      document.body.appendChild(backdrop);
    }

    // 2. Ensure Drawer
    let drawer = document.getElementById("notification-drawer");
    if (!drawer) {
      drawer = document.createElement("aside");
      drawer.id = "notification-drawer";
      drawer.className = "notification-drawer";
      drawer.setAttribute("role", "dialog");
      drawer.setAttribute("aria-label", "Notifications");
      drawer.setAttribute("aria-modal", "true");

      drawer.innerHTML = `
        <div class="drawer-header">
          <div class="flex items-center gap-sm">
            <span class="material-symbols-outlined" style="font-size:1.25rem;color:var(--accent)">notifications</span>
            <div class="font-bold text-md">Notifications</div>
            <span class="badge badge-hold" id="drawer-count-badge">0</span>
          </div>
          <div class="flex items-center gap-sm">
            <button type="button" class="btn btn-xs btn-ghost text-xs" id="drawer-clear-all" onclick="PortfolioIQAlerts.clearAll()">
              Clear All
            </button>
            <button type="button" class="drawer-close-btn" onclick="PortfolioIQAlerts.closeDrawer()" aria-label="Close notifications">
              <span class="material-symbols-outlined" style="font-size:1.15rem">close</span>
            </button>
          </div>
        </div>

        <div class="drawer-tabs">
          <button type="button" class="drawer-tab-btn active" data-filter="ALL" onclick="PortfolioIQAlerts.setFilter('ALL')">All</button>
          <button type="button" class="drawer-tab-btn" data-filter="DRIFT" onclick="PortfolioIQAlerts.setFilter('DRIFT')">Drift</button>
          <button type="button" class="drawer-tab-btn" data-filter="PRICE_STALE" onclick="PortfolioIQAlerts.setFilter('PRICE_STALE')">Price</button>
          <button type="button" class="drawer-tab-btn" data-filter="TAX" onclick="PortfolioIQAlerts.setFilter('TAX')">Tax</button>
          <button type="button" class="drawer-tab-btn" data-filter="SYSTEM" onclick="PortfolioIQAlerts.setFilter('SYSTEM')">System</button>
        </div>

        <div class="drawer-content" id="drawer-alerts-list">
          <div class="text-center text-muted text-sm py-lg">Loading notifications…</div>
        </div>
      `;

      document.body.appendChild(drawer);
    }

    // 3. Ensure Bell Button in Topbar
    ensureBellButton();
  }

  function ensureBellButton() {
    let bellBtn = document.getElementById("notification-bell-btn");
    if (bellBtn) return bellBtn;

    const topbarRight = document.querySelector(".topbar-right");
    if (!topbarRight) return null;

    bellBtn = document.createElement("button");
    bellBtn.type = "button";
    bellBtn.id = "notification-bell-btn";
    bellBtn.className = "topbar-bell-btn";
    bellBtn.title = "View Notifications";
    bellBtn.setAttribute("aria-label", "View notifications");
    bellBtn.onclick = toggleDrawer;

    bellBtn.innerHTML = `
      <span class="bell-icon material-symbols-outlined" aria-hidden="true" style="font-size:1.15rem">notifications</span>
      <span class="bell-badge" id="bell-badge" style="display:none;">0</span>
    `;

    // Insert as first item in topbar-right or before refresh
    topbarRight.insertBefore(bellBtn, topbarRight.firstChild);
    return bellBtn;
  }

  async function fetchAlerts() {
    if (window.PORTFOLIOIQ_STATIC_PREVIEW) return;

    try {
      const base = window.getApiBase();

      const apiKey = typeof window.getApiKey === "function"
        ? window.getApiKey()
        : (sessionStorage.getItem("portfolioiq_session_key") || "");

      const res = await fetch(`${base}/alerts`, {
        headers: {
          "X-API-Key": apiKey,
        },
      });

      if (!res.ok) return;
      const json = await res.json().catch(() => ({}));
      if (json.ok && json.data) {
        processNewAlerts(json.data.alerts || []);
      }
    } catch {
      // Offline or network error
    }
  }

  function processNewAlerts(rawAlerts) {
    alertsCache = rawAlerts;
    const dismissed = getDismissedIds();

    // Notify on new critical alerts that haven't been seen in this session
    rawAlerts.forEach((a) => {
      if (!knownAlertIds.has(a.id) && !dismissed.has(a.id)) {
        knownAlertIds.add(a.id);
        if (a.severity === "CRITICAL" && typeof window.showToast === "function") {
          window.showToast(`${a.title}: ${a.message}`, "error", 6000);
        }
      }
    });

    renderAlerts();
  }

  function renderAlerts() {
    ensureDrawerMarkup();
    const listEl = document.getElementById("drawer-alerts-list");
    const badgeEl = document.getElementById("bell-badge");
    const countBadge = document.getElementById("drawer-count-badge");
    if (!listEl) return;

    const dismissed = getDismissedIds();
    const activeAlerts = alertsCache.filter((a) => !dismissed.has(a.id));

    // Update Topbar Badge
    const unreadCount = activeAlerts.length;
    if (badgeEl) {
      if (unreadCount > 0) {
        badgeEl.textContent = unreadCount > 9 ? "9+" : String(unreadCount);
        badgeEl.style.display = "flex";
      } else {
        badgeEl.style.display = "none";
      }
    }
    if (countBadge) {
      countBadge.textContent = String(unreadCount);
      countBadge.className = `badge ${unreadCount > 0 ? "badge-sell" : "badge-hold"}`;
    }

    // Filter alerts for current tab
    const filtered = activeAlerts.filter((a) => {
      if (activeFilter === "ALL") return true;
      return a.type === activeFilter;
    });

    if (filtered.length === 0) {
      listEl.innerHTML = `
        <div class="empty-state py-lg">
          <div class="empty-icon"><span class="material-symbols-outlined" style="font-size:2.4rem;color:var(--accent)">verified</span></div>
          <div class="empty-title">All clear!</div>
          <div class="empty-desc text-xs text-muted">No active ${activeFilter === "ALL" ? "" : activeFilter.toLowerCase()} alerts.</div>
        </div>
      `;
      return;
    }

    const typeIcons = {
      DRIFT: '<span class="material-symbols-outlined">balance</span>',
      PRICE_STALE: '<span class="material-symbols-outlined">schedule</span>',
      TAX: '<span class="material-symbols-outlined">shield</span>',
      SYSTEM: '<span class="material-symbols-outlined">power</span>',
    };

    const severityBadges = {
      CRITICAL: '<span class="badge badge-strong-sell">CRITICAL</span>',
      WARNING: '<span class="badge badge-sell">WARNING</span>',
      INFO: '<span class="badge badge-buy">INFO</span>',
    };

    listEl.innerHTML = filtered.map((a) => {
      const icon = typeIcons[a.type] || '<span class="material-symbols-outlined">info</span>';
      const sevBadge = severityBadges[a.severity] || "";
      const timeStr = formatRelativeTime(a.timestamp);
      const actionHtml = a.action_label && a.action_url
        ? `<a href="${a.action_url}" class="alert-action-btn">${a.action_label} &rarr;</a>`
        : "";

      return `
        <div class="alert-card alert-card-${a.severity.toLowerCase()}" id="alert-card-${a.id}">
          <div class="alert-card-top">
            <div class="flex items-center gap-sm">
              <span class="alert-card-icon">${icon}</span>
              <span class="alert-card-title">${a.title}</span>
            </div>
            <div class="flex items-center gap-xs">
              ${sevBadge}
              <button type="button" class="alert-dismiss-btn" onclick="PortfolioIQAlerts.dismissAlert('${a.id}')" title="Dismiss" aria-label="Dismiss alert">&times;</button>
            </div>
          </div>
          <div class="alert-card-msg">${a.message}</div>
          <div class="alert-card-bottom">
            <span class="alert-card-time">${timeStr}</span>
            ${actionHtml}
          </div>
        </div>
      `;
    }).join("");
  }

  function dismissAlert(alertId) {
    const dismissed = getDismissedIds();
    dismissed.add(alertId);
    saveDismissedIds(dismissed);
    renderAlerts();
  }

  function clearAll() {
    const dismissed = getDismissedIds();
    alertsCache.forEach((a) => dismissed.add(a.id));
    saveDismissedIds(dismissed);
    renderAlerts();
    if (typeof window.showToast === "function") {
      window.showToast("All notifications dismissed.", "info");
    }
  }

  function setFilter(filter) {
    activeFilter = filter;
    document.querySelectorAll(".drawer-tab-btn").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.filter === filter);
    });
    renderAlerts();
  }

  function openDrawer() {
    ensureDrawerMarkup();
    const drawer = document.getElementById("notification-drawer");
    const backdrop = document.getElementById("drawer-backdrop");
    if (drawer) drawer.classList.add("open");
    if (backdrop) backdrop.classList.add("open");
  }

  function closeDrawer() {
    const drawer = document.getElementById("notification-drawer");
    const backdrop = document.getElementById("drawer-backdrop");
    if (drawer) drawer.classList.remove("open");
    if (backdrop) backdrop.classList.remove("open");
  }

  function toggleDrawer() {
    const drawer = document.getElementById("notification-drawer");
    if (drawer && drawer.classList.contains("open")) {
      closeDrawer();
    } else {
      openDrawer();
    }
  }

  function initAlerts() {
    ensureDrawerMarkup();
    fetchAlerts();

    if (pollTimer) clearInterval(pollTimer);
    pollTimer = setInterval(fetchAlerts, POLL_INTERVAL_MS);

    // Close on Escape key
    window.addEventListener("keydown", (e) => {
      if (e.key === "Escape") closeDrawer();
    });
  }

  // Public Alerts API
  window.PortfolioIQAlerts = {
    init: initAlerts,
    fetchAlerts,
    openDrawer,
    closeDrawer,
    toggleDrawer,
    dismissAlert,
    clearAll,
    setFilter,
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initAlerts);
  } else {
    initAlerts();
  }
})();
