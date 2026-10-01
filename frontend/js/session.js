/**
 * PortfolioIQ — Session Management & Inactivity Lock (frontend/js/session.js)
 * 
 * Provides:
 * - Scoped session token storage in sessionStorage (tab-scoped isolation).
 * - 30-minute inactivity auto-lock tracking mouse, keyboard, touch, and scroll.
 * - Native accessible <dialog id="session-modal"> with backdrop blur and focus trap.
 * - Verification against POST /api/v1/auth/verify.
 * - Graceful 401 interception and programmatic lock/unlock callbacks.
 */

(function () {
  "use strict";

  const SESSION_KEY_NAME = "portfolioiq_session_key";
  const INACTIVITY_TIMEOUT_MS = 30 * 60 * 1000; // 30 minutes
  const ACTIVITY_DEBOUNCE_MS = 3000; // Update activity at most once every 3s
  const CHECK_INTERVAL_MS = 15000; // Check inactivity every 15s

  let lastActivityTime = Date.now();
  let debounceTimeout = null;
  let checkInterval = null;
  const unlockCallbacks = [];

  function getStoredSessionKey() {
    return sessionStorage.getItem(SESSION_KEY_NAME) || "";
  }

  function setStoredSessionKey(key) {
    if (key) {
      sessionStorage.setItem(SESSION_KEY_NAME, key);
    } else {
      sessionStorage.removeItem(SESSION_KEY_NAME);
    }
  }

  function clearStoredSessionKey() {
    sessionStorage.removeItem(SESSION_KEY_NAME);
  }

  function recordActivity() {
    const now = Date.now();
    if (now - lastActivityTime < ACTIVITY_DEBOUNCE_MS) return;
    lastActivityTime = now;
  }

  function ensureModalInDOM() {
    let dialog = document.getElementById("session-modal");
    if (dialog) return dialog;

    dialog = document.createElement("dialog");
    dialog.id = "session-modal";
    dialog.className = "session-dialog";
    dialog.setAttribute("aria-labelledby", "session-modal-title");
    dialog.setAttribute("aria-describedby", "session-modal-desc");

    dialog.innerHTML = `
      <div class="session-dialog-card">
        <div class="session-dialog-header">
          <div class="session-icon">🔐</div>
          <h2 id="session-modal-title" class="session-title">Unlock PortfolioIQ</h2>
          <p id="session-modal-desc" class="session-desc">
            Enter your Master API Key or Session PIN to access live execution and portfolio analytics.
          </p>
        </div>

        <form id="session-form" class="session-form" method="dialog">
          <div id="session-error" class="session-error" role="alert" style="display:none;"></div>

          <div class="form-group">
            <label for="session-key-input" class="form-label">Master API Key / PIN</label>
            <div class="input-with-action">
              <input
                type="password"
                id="session-key-input"
                name="session_key"
                class="form-control"
                placeholder="Your PortfolioIQ access key"
                autocomplete="current-password"
                required
              />
              <button
                type="button"
                id="session-toggle-vis"
                class="btn-icon-addon"
                title="Toggle visibility"
                aria-label="Toggle password visibility"
              >
                👁️
              </button>
            </div>
          </div>

          <div class="session-actions">
            <button type="submit" id="session-unlock-btn" class="btn btn-primary btn-block">
              <span id="session-btn-text">Unlock Session</span>
              <span id="session-spinner" class="spinner-sm" style="display:none;"></span>
            </button>
          </div>
        </form>

        <div class="session-footer">
          <span class="session-footer-note">
            Auto-locks after 30 minutes of inactivity · Protected by SEBI Algorithmic Safety Guard
          </span>
        </div>
      </div>
    `;

    document.body.appendChild(dialog);

    // Prevent closing via ESC key while locked
    dialog.addEventListener("cancel", (e) => {
      e.preventDefault();
    });

    // Toggle password visibility
    const toggleBtn = dialog.querySelector("#session-toggle-vis");
    const input = dialog.querySelector("#session-key-input");
    if (toggleBtn && input) {
      toggleBtn.addEventListener("click", () => {
        const isPassword = input.type === "password";
        input.type = isPassword ? "text" : "password";
        toggleBtn.textContent = isPassword ? "🙈" : "👁️";
      });
    }

    // Handle Form Submit
    const form = dialog.querySelector("#session-form");
    if (form) {
      form.addEventListener("submit", async (e) => {
        e.preventDefault();
        const keyVal = input ? input.value.trim() : "";
        if (!keyVal) return;
        await verifyAndUnlock(keyVal);
      });
    }

    return dialog;
  }

  async function verifyAndUnlock(keyVal) {
    const dialog = ensureModalInDOM();
    const errorEl = dialog.querySelector("#session-error");
    const btnText = dialog.querySelector("#session-btn-text");
    const spinner = dialog.querySelector("#session-spinner");
    const submitBtn = dialog.querySelector("#session-unlock-btn");
    const input = dialog.querySelector("#session-key-input");

    if (errorEl) {
      errorEl.style.display = "none";
      errorEl.textContent = "";
    }
    if (btnText) btnText.textContent = "Verifying…";
    if (spinner) spinner.style.display = "inline-block";
    if (submitBtn) submitBtn.disabled = true;
    if (input) input.disabled = true;

    try {
      const base = window.getApiBase();

      const res = await fetch(`${base}/auth/verify`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-API-Key": keyVal,
        },
        body: JSON.stringify({ api_key: keyVal }),
      });

      const data = await res.json().catch(() => ({}));

      if (res.ok && data.ok) {
        // Successful verification
        setStoredSessionKey(keyVal);
        lastActivityTime = Date.now();
        if (input) {
          input.value = "";
          input.disabled = false;
        }
        if (dialog.open) {
          dialog.close();
        }

        if (typeof window.showToast === "function") {
          window.showToast("PortfolioIQ unlocked successfully.", "success");
        }

        // Fire any pending callbacks or reload data
        while (unlockCallbacks.length > 0) {
          const cb = unlockCallbacks.shift();
          try {
            cb();
          } catch (err) {
            console.error("Error in unlock callback:", err);
          }
        }
      } else {
        // Verification failed
        const msg = data.error?.message || "Invalid Master Key or PIN. Access denied.";
        if (errorEl) {
          errorEl.textContent = msg;
          errorEl.style.display = "block";
        }
        if (input) {
          input.disabled = false;
          input.focus();
          input.select();
        }
      }
    } catch (err) {
      if (errorEl) {
        errorEl.textContent = "Connection failed. Ensure the PortfolioIQ API server is online.";
        errorEl.style.display = "block";
      }
      if (input) input.disabled = false;
    } finally {
      if (btnText) btnText.textContent = "Unlock Session";
      if (spinner) spinner.style.display = "none";
      if (submitBtn) submitBtn.disabled = false;
    }
  }

  function showLockModal(reasonText) {
    const dialog = ensureModalInDOM();
    const desc = dialog.querySelector("#session-modal-desc");
    const errorEl = dialog.querySelector("#session-error");
    const input = dialog.querySelector("#session-key-input");

    if (reasonText && desc) {
      desc.textContent = reasonText;
    }
    if (errorEl) {
      errorEl.style.display = "none";
      errorEl.textContent = "";
    }

    if (!dialog.open) {
      try {
        dialog.showModal();
      } catch (e) {
        // Fallback if showModal fails
        dialog.setAttribute("open", "");
      }
    }

    if (input) {
      input.value = "";
      setTimeout(() => input.focus(), 50);
    }
  }

  function checkInactivity() {
    const sessionKey = getStoredSessionKey();
    if (!sessionKey) return; // Already locked

    const elapsed = Date.now() - lastActivityTime;
    if (elapsed >= INACTIVITY_TIMEOUT_MS) {
      console.warn("PortfolioIQ session locked due to inactivity (30 mins).");
      clearStoredSessionKey();
      showLockModal("Session locked due to 30 minutes of inactivity. Please re-enter your key.");
      if (typeof window.showToast === "function") {
        window.showToast("Session auto-locked due to inactivity.", "warn");
      }
    }
  }

  function setupActivityListeners() {
    const events = ["mousedown", "mousemove", "keydown", "scroll", "touchstart"];
    events.forEach((evt) => {
      window.addEventListener(evt, recordActivity, { passive: true });
    });

    if (checkInterval) clearInterval(checkInterval);
    checkInterval = setInterval(checkInactivity, CHECK_INTERVAL_MS);
  }

  function initSession() {
    setupActivityListeners();
    ensureModalInDOM();

    // Check if we have an active session key in sessionStorage
    const existingSession = getStoredSessionKey();
    if (!existingSession) {
      showLockModal("Welcome to PortfolioIQ. Enter your access key to continue.");
    }
  }

  // Public Session API
  window.PortfolioIQSession = {
    init: initSession,
    getKey: getStoredSessionKey,
    setKey: setStoredSessionKey,
    clearKey: clearStoredSessionKey,
    isLocked: () => !getStoredSessionKey(),
    lock: (reason) => {
      clearStoredSessionKey();
      showLockModal(reason);
    },
    unlock: (key) => verifyAndUnlock(key),
    onUnlock: (callback) => {
      if (typeof callback === "function") {
        unlockCallbacks.push(callback);
      }
    },
  };

  // Run on DOM ready
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initSession);
  } else {
    initSession();
  }
})();
