"""
Tests for Phase 6 Frontend Client Infrastructure:
- frontend/js/session.js (Session Management & Auto-Lock)
- frontend/js/export.js (Universal CSV Exporter)
- frontend/js/api.js (401 Interception, Session Key Extraction, Toast System)
- frontend/css/main.css (Session Modal, Toast Styles, Print Styles)
- HTML Script Tag Integrations across all 6 core frontend pages.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = REPO_ROOT / "frontend"
JS_DIR = FRONTEND_DIR / "js"
CSS_DIR = FRONTEND_DIR / "css"


class TestSessionJs:
    def test_session_file_exists(self):
        session_file = JS_DIR / "session.js"
        assert session_file.exists()
        assert session_file.stat().st_size > 500

    def test_session_key_and_storage(self):
        content = (JS_DIR / "session.js").read_text(encoding="utf-8")
        assert "portfolioiq_session_key" in content
        assert "sessionStorage.getItem" in content
        assert "sessionStorage.setItem" in content
        assert "sessionStorage.removeItem" in content

    def test_inactivity_timer_30_minutes(self):
        content = (JS_DIR / "session.js").read_text(encoding="utf-8")
        # Assert 30 minutes in milliseconds (30 * 60 * 1000 = 1,800,000)
        assert "30 * 60 * 1000" in content or "1800000" in content
        assert "INACTIVITY_TIMEOUT_MS" in content
        assert "recordActivity" in content

    def test_accessible_dialog_markup(self):
        content = (JS_DIR / "session.js").read_text(encoding="utf-8")
        assert 'id="session-modal"' in content or "session-modal" in content
        assert "aria-labelledby" in content and "session-modal-title" in content
        assert "aria-describedby" in content and "session-modal-desc" in content
        assert 'type="password"' in content
        assert 'autocomplete="current-password"' in content
        assert "showModal" in content

    def test_auth_verify_endpoint_called(self):
        content = (JS_DIR / "session.js").read_text(encoding="utf-8")
        assert "/auth/verify" in content
        assert "X-API-Key" in content

    def test_public_api_exposed(self):
        content = (JS_DIR / "session.js").read_text(encoding="utf-8")
        assert "window.PortfolioIQSession" in content
        for method in ["init", "getKey", "setKey", "clearKey", "isLocked", "lock", "unlock"]:
            assert f"{method}:" in content or f"{method} :" in content


class TestExportJs:
    def test_export_file_exists(self):
        export_file = JS_DIR / "export.js"
        assert export_file.exists()
        assert export_file.stat().st_size > 500

    def test_export_functions_defined(self):
        content = (JS_DIR / "export.js").read_text(encoding="utf-8")
        assert "function exportDataToCSV" in content
        assert "function exportTableToCSV" in content
        assert "window.PortfolioIQExport" in content
        assert "window.exportDataToCSV" in content
        assert "window.exportTableToCSV" in content

    def test_utf8_bom_present(self):
        content = (JS_DIR / "export.js").read_text(encoding="utf-8")
        # Ensure UTF-8 BOM (\uFEFF) is prepended for Excel compatibility
        assert "\\uFEFF" in content
        assert "text/csv;charset=utf-8;" in content

    def test_data_attribute_binding(self):
        content = (JS_DIR / "export.js").read_text(encoding="utf-8")
        assert "[data-export-table]" in content
        assert "data-export-bound" in content or "data-export-table" in content


class TestApiClientUpdates:
    def test_api_session_key_priority(self):
        content = (JS_DIR / "api.js").read_text(encoding="utf-8")
        assert 'sessionStorage.getItem("portfolioiq_session_key")' in content
        assert 'localStorage.getItem("portfolioiq_api_key")' not in content
        assert '|| "dev-secret-key"' not in content

    def test_api_401_interception(self):
        content = (JS_DIR / "api.js").read_text(encoding="utf-8")
        assert "res.status === 401" in content
        assert "handleUnauthorized" in content
        assert "PortfolioIQSession.lock" in content

    def test_toast_system_implementation(self):
        content = (JS_DIR / "api.js").read_text(encoding="utf-8")
        assert "showToast" in content
        assert "toast-container" in content
        assert "aria-live" in content
        assert "window.showToast = showToast" in content


class TestCssAdditions:
    def test_session_modal_css(self):
        content = (CSS_DIR / "main.css").read_text(encoding="utf-8")
        assert ".session-dialog" in content
        assert ".session-dialog::backdrop" in content
        assert "backdrop-filter: blur" in content
        assert ".session-dialog-card" in content
        assert ".session-error" in content

    def test_toast_css(self):
        content = (CSS_DIR / "main.css").read_text(encoding="utf-8")
        assert ".toast-container" in content
        assert ".toast" in content
        assert ".toast-success" in content
        assert ".toast-error" in content
        assert ".toast-warn" in content
        assert ".toast-info" in content
        assert "@keyframes toastSlideIn" in content

    def test_print_styles(self):
        content = (CSS_DIR / "main.css").read_text(encoding="utf-8")
        assert "@media print" in content
        assert ".sidebar" in content
        assert ".topbar" in content
        assert "display: none !important" in content


class TestHtmlIntegrations:
    CORE_PAGES = [
        "index.html",
        "analyzer.html",
        "rebalance.html",
        "tax.html",
        "audit.html",
        "settings.html",
    ]

    def test_all_pages_include_scripts_in_order(self):
        for page_name in self.CORE_PAGES:
            page_path = FRONTEND_DIR / page_name
            assert page_path.exists(), f"Missing page {page_name}"
            html = page_path.read_text(encoding="utf-8")

            # Check scripts exist
            assert '<script src="js/session.js"></script>' in html, (
                f"{page_name} missing session.js"
            )
            assert '<script src="js/export.js"></script>' in html, f"{page_name} missing export.js"
            assert '<script src="js/api.js"></script>' in html, f"{page_name} missing api.js"

            # Check script load order: session.js before export.js before api.js
            idx_session = html.find('src="js/session.js"')
            idx_export = html.find('src="js/export.js"')
            idx_api = html.find('src="js/api.js"')
            assert idx_session < idx_export < idx_api, f"Script order incorrect in {page_name}"
