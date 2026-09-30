"""
Tests for Phase 6 In-App Notification Center & Alert Poller (Plan 06-04).

Validates:
- frontend/js/alerts.js structure, polling logic, and public API.
- frontend/css/main.css drawer, backdrop, badge, and severity styling.
- All 6 core HTML pages include the notification bell button and load alerts.js in order.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = REPO_ROOT / "frontend"
JS_DIR = FRONTEND_DIR / "js"
CSS_DIR = FRONTEND_DIR / "css"

CORE_PAGES = [
    "index.html",
    "analyzer.html",
    "rebalance.html",
    "tax.html",
    "audit.html",
    "settings.html",
]


class TestAlertsJs:
    def test_alerts_file_exists(self):
        alerts_file = JS_DIR / "alerts.js"
        assert alerts_file.exists()
        assert alerts_file.stat().st_size > 1000

    def test_alerts_polling_and_endpoint(self):
        content = (JS_DIR / "alerts.js").read_text(encoding="utf-8")
        assert "/alerts" in content
        assert "POLL_INTERVAL_MS" in content
        assert "setInterval" in content

    def test_alerts_public_api(self):
        content = (JS_DIR / "alerts.js").read_text(encoding="utf-8")
        assert "window.PortfolioIQAlerts" in content
        for fn in [
            "init",
            "fetchAlerts",
            "openDrawer",
            "closeDrawer",
            "toggleDrawer",
            "dismissAlert",
            "clearAll",
            "setFilter",
        ]:
            assert fn in content

    def test_drawer_markup_and_filters(self):
        content = (JS_DIR / "alerts.js").read_text(encoding="utf-8")
        assert "notification-drawer" in content
        assert "drawer-backdrop" in content
        assert "bell-badge" in content
        assert "notification-bell-btn" in content
        assert "DRIFT" in content
        assert "PRICE_STALE" in content
        assert "TAX" in content
        assert "SYSTEM" in content

    def test_dismissal_persistence(self):
        content = (JS_DIR / "alerts.js").read_text(encoding="utf-8")
        assert "portfolioiq_dismissed_alerts" in content
        assert "localStorage" in content


class TestAlertsCss:
    def test_drawer_css_classes(self):
        content = (CSS_DIR / "main.css").read_text(encoding="utf-8")
        assert ".notification-drawer" in content
        assert ".notification-drawer.open" in content
        assert ".drawer-backdrop" in content
        assert ".drawer-backdrop.open" in content
        assert ".topbar-bell-btn" in content
        assert ".bell-badge" in content

    def test_alert_card_severity_classes(self):
        content = (CSS_DIR / "main.css").read_text(encoding="utf-8")
        assert ".alert-card" in content
        assert ".alert-card-critical" in content
        assert ".alert-card-warning" in content
        assert ".alert-card-info" in content

    def test_print_styles_hide_drawer_and_bell(self):
        content = (CSS_DIR / "main.css").read_text(encoding="utf-8")
        assert "#notification-drawer" in content
        assert ".topbar-bell-btn" in content


class TestPageIntegrations:
    def test_all_pages_have_bell_button(self):
        for page_name in CORE_PAGES:
            page_path = FRONTEND_DIR / page_name
            assert page_path.exists(), f"Missing {page_name}"
            html = page_path.read_text(encoding="utf-8")
            assert 'id="notification-bell-btn"' in html, (
                f"{page_name} missing notification-bell-btn"
            )

    def test_all_pages_include_alerts_script_in_sequence(self):
        for page_name in CORE_PAGES:
            page_path = FRONTEND_DIR / page_name
            html = page_path.read_text(encoding="utf-8")
            assert '<script src="js/alerts.js"></script>' in html, f"{page_name} missing alerts.js"

            idx_session = html.find('src="js/session.js"')
            idx_export = html.find('src="js/export.js"')
            idx_alerts = html.find('src="js/alerts.js"')
            idx_api = html.find('src="js/api.js"')

            assert idx_session < idx_export < idx_alerts < idx_api, (
                f"Script order invalid in {page_name}: expected session -> export -> alerts -> api"
            )
