"""
Unit and integration tests for Plan 06-05: Portfolio Timeline Page.

Verifies:
- frontend/timeline.html existence and structural completeness
- Plotly chart container, KPI cards, and timeframe range selectors
- Script dependencies loaded in strict sequence: session -> export -> alerts -> api
- Integration of timeline.html into sidebar navigation across all core pages
- API endpoints integration for snapshots and cash flows
- Universal export attribute binding for event history
"""

from pathlib import Path

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

ALL_CORE_PAGES = [
    "index.html",
    "analyzer.html",
    "rebalance.html",
    "tax.html",
    "audit.html",
    "settings.html",
    "timeline.html",
]


class TestTimelinePage:
    def test_timeline_file_exists(self):
        timeline_path = FRONTEND_DIR / "timeline.html"
        assert timeline_path.exists(), "frontend/timeline.html does not exist"
        assert timeline_path.stat().st_size > 500, "frontend/timeline.html is unexpectedly small"

    def test_timeline_scripts_and_dependencies(self):
        content = (FRONTEND_DIR / "timeline.html").read_text(encoding="utf-8")

        # Must include Plotly
        assert "plotly-2.35.2.min.js" in content or "plotly" in content.lower()

        # Strict dependency order
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

    def test_timeline_kpi_cards(self):
        content = (FRONTEND_DIR / "timeline.html").read_text(encoding="utf-8")
        assert 'id="kpi-unit-nav"' in content
        assert 'id="kpi-twr"' in content
        assert 'id="kpi-bench"' in content
        assert 'id="kpi-alpha"' in content
        assert 'id="kpi-drawdown"' in content

    def test_timeline_timeframe_controls(self):
        content = (FRONTEND_DIR / "timeline.html").read_text(encoding="utf-8")
        assert "btn-range-1m" in content
        assert "btn-range-3m" in content
        assert "btn-range-6m" in content
        assert "btn-range-1y" in content
        assert "btn-range-all" in content
        assert "setRange" in content

    def test_timeline_chart_and_export_table(self):
        content = (FRONTEND_DIR / "timeline.html").read_text(encoding="utf-8")
        assert 'id="equity-chart"' in content
        assert 'id="timeline-events-table"' in content
        assert 'data-export="timeline-events-table"' in content
        assert 'id="events-tbody"' in content
        assert 'id="event-count"' in content

    def test_timeline_api_endpoints(self):
        content = (FRONTEND_DIR / "timeline.html").read_text(encoding="utf-8")
        assert "/analytics/snapshots" in content
        assert "/portfolio/cash-flows" in content
        assert "/audit/orders" in content

    def test_timeline_topbar_bell_button(self):
        content = (FRONTEND_DIR / "timeline.html").read_text(encoding="utf-8")
        assert 'id="notification-bell-btn"' in content
        assert 'id="bell-badge"' in content


class TestSidebarNavigationAcrossPages:
    def test_all_pages_have_timeline_link(self):
        for page_name in ALL_CORE_PAGES:
            page_path = FRONTEND_DIR / page_name
            assert page_path.exists(), f"Missing {page_name}"
            html = page_path.read_text(encoding="utf-8")
            assert 'href="timeline.html"' in html, (
                f"{page_name} missing sidebar link to timeline.html"
            )

    def test_timeline_is_active_only_on_timeline_page(self):
        for page_name in ALL_CORE_PAGES:
            page_path = FRONTEND_DIR / page_name
            html = page_path.read_text(encoding="utf-8")

            if page_name == "timeline.html":
                assert 'class="nav-item active" href="timeline.html"' in html
            else:
                assert 'class="nav-item" href="timeline.html"' in html
