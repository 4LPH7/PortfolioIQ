"""
Unit and DOM validation tests for Plan 06-06: System Status Dashboard.

Verifies:
- frontend/status.html existence and structural completeness
- Real-time telemetry cards (API Latency, Database, Broker Session, Market Hours)
- APScheduler tasks table and job status badges
- Auto-refresh toggle and live latency probe button
- Script dependencies loaded in strict sequence: session -> export -> alerts -> api
- Hyperlinked sidebar status pills linking to status.html across all views
"""

from pathlib import Path

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

CORE_PAGES_WITH_SIDEBAR = [
    "index.html",
    "analyzer.html",
    "rebalance.html",
    "tax.html",
    "audit.html",
    "settings.html",
    "timeline.html",
    "status.html",
]


class TestSystemStatusPage:
    def test_status_file_exists(self):
        status_path = FRONTEND_DIR / "status.html"
        assert status_path.exists(), "frontend/status.html does not exist"
        assert status_path.stat().st_size > 500, "frontend/status.html is unexpectedly small"

    def test_status_scripts_and_dependencies(self):
        content = (FRONTEND_DIR / "status.html").read_text(encoding="utf-8")

        idx_session = content.find('src="js/session.js"')
        idx_export = content.find('src="js/export.js"')
        idx_alerts = content.find('src="js/alerts.js"')
        idx_api = content.find('src="js/api.js"')

        assert idx_session != -1, "Missing session.js"
        assert idx_export != -1, "Missing export.js"
        assert idx_alerts != -1, "Missing alerts.js"
        assert idx_api != -1, "Missing api.js"

        assert idx_session < idx_export < idx_alerts < idx_api, (
            "Scripts must load in order: session.js -> export.js -> alerts.js -> api.js"
        )

    def test_status_telemetry_kpi_cards(self):
        content = (FRONTEND_DIR / "status.html").read_text(encoding="utf-8")
        assert 'id="kpi-latency"' in content
        assert 'id="kpi-db-state"' in content
        assert 'id="kpi-broker-state"' in content
        assert 'id="kpi-market-state"' in content

    def test_status_banner_and_controls(self):
        content = (FRONTEND_DIR / "status.html").read_text(encoding="utf-8")
        assert 'id="system-banner"' in content
        assert 'id="system-status-title"' in content
        assert 'id="system-badge"' in content
        assert 'id="auto-refresh-toggle"' in content
        assert 'id="btn-ping"' in content
        assert "fetchStatusTelemetry" in content

    def test_scheduler_jobs_table(self):
        content = (FRONTEND_DIR / "status.html").read_text(encoding="utf-8")
        assert 'id="scheduler-jobs-table"' in content
        assert 'id="scheduler-tbody"' in content
        assert 'id="scheduler-active-badge"' in content

    def test_status_api_endpoints_called(self):
        content = (FRONTEND_DIR / "status.html").read_text(encoding="utf-8")
        assert "/system/status" in content
        assert "/health" in content

    def test_status_topbar_bell_button(self):
        content = (FRONTEND_DIR / "status.html").read_text(encoding="utf-8")
        assert 'id="notification-bell-btn"' in content
        assert 'id="bell-badge"' in content


class TestSidebarStatusPillsLinking:
    def test_sidebar_status_pills_link_to_status_html(self):
        for page_name in CORE_PAGES_WITH_SIDEBAR:
            page_path = FRONTEND_DIR / page_name
            assert page_path.exists(), f"Missing {page_name}"
            html = page_path.read_text(encoding="utf-8")

            assert 'href="status.html"' in html, f"{page_name} missing status.html link in sidebar"
            assert 'id="market-status"' in html, f"{page_name} missing market-status pill"
            assert 'id="db-status"' in html, f"{page_name} missing db-status pill"
