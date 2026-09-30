"""
Unit and DOM validation tests for Plan 06-07: Monthly Statement & Universal CSV Exports.

Verifies:
- frontend/report.html existence and structural completeness
- Executive performance metrics (TWR, XIRR, Sharpe, Drawdown, Benchmark)
- Risk & attribution matrix (Alpha, Beta, Sharpe, Sortino, Drawdown)
- Indian transaction cost breakdown and basis point drag attribution
- Capital gains tax ledger (STCG @ 20%, LTCG @ 12.5%, ₹1.25L exemption)
- Statutory SEBI & tax compliance disclaimer
- Universal CSV export buttons across all major tables
- Print styles in main.css optimized for A4 PDF export
- Sidebar navigation links to report.html across all views
"""

from pathlib import Path

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
CSS_DIR = FRONTEND_DIR / "css"

CORE_PAGES = [
    "index.html",
    "analyzer.html",
    "rebalance.html",
    "tax.html",
    "audit.html",
    "settings.html",
    "timeline.html",
    "status.html",
    "report.html",
]


class TestMonthlyStatementPage:
    def test_report_file_exists(self):
        report_path = FRONTEND_DIR / "report.html"
        assert report_path.exists(), "frontend/report.html does not exist"
        assert report_path.stat().st_size > 500, "frontend/report.html is unexpectedly small"

    def test_report_scripts_and_dependencies(self):
        content = (FRONTEND_DIR / "report.html").read_text(encoding="utf-8")

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

    def test_executive_kpis(self):
        content = (FRONTEND_DIR / "report.html").read_text(encoding="utf-8")
        assert 'id="stmt-aum"' in content
        assert 'id="stmt-twr"' in content
        assert 'id="stmt-xirr"' in content
        assert 'id="stmt-bench"' in content

    def test_risk_and_attribution_section(self):
        content = (FRONTEND_DIR / "report.html").read_text(encoding="utf-8")
        assert 'id="attr-alpha"' in content
        assert 'id="attr-beta"' in content
        assert 'id="attr-sharpe"' in content
        assert 'id="attr-sortino"' in content
        assert 'id="attr-drawdown"' in content
        assert 'id="drag-total"' in content

    def test_capital_gains_tax_ledger(self):
        content = (FRONTEND_DIR / "report.html").read_text(encoding="utf-8")
        assert "1,25,000" in content
        assert "STCG" in content
        assert "LTCG" in content
        assert 'id="tax-exempt-limit"' in content
        assert 'id="tax-stcg-realized"' in content
        assert 'id="tax-ltcg-realized"' in content

    def test_statutory_sebi_disclaimer(self):
        content = (FRONTEND_DIR / "report.html").read_text(encoding="utf-8")
        assert "SEBI" in content
        assert "Statutory" in content or "Disclaimer" in content

    def test_print_and_export_controls(self):
        content = (FRONTEND_DIR / "report.html").read_text(encoding="utf-8")
        assert "window.print()" in content
        assert 'id="statement-month"' in content
        assert 'data-export="statement-holdings-table"' in content
        assert 'id="statement-holdings-table"' in content


class TestUniversalCsvExports:
    def test_index_holdings_export(self):
        html = (FRONTEND_DIR / "index.html").read_text(encoding="utf-8")
        assert 'data-export="holdings-table"' in html
        assert 'id="holdings-table"' in html

    def test_rebalance_tables_export(self):
        html = (FRONTEND_DIR / "rebalance.html").read_text(encoding="utf-8")
        assert 'data-export="drift-table"' in html
        assert 'id="drift-table"' in html
        assert 'data-export="rebalance-orders-table"' in html
        assert 'id="rebalance-orders-table"' in html

    def test_tax_table_export(self):
        html = (FRONTEND_DIR / "tax.html").read_text(encoding="utf-8")
        assert 'data-export="tax-lots-table"' in html
        assert 'id="tax-lots-table"' in html

    def test_audit_tables_export(self):
        html = (FRONTEND_DIR / "audit.html").read_text(encoding="utf-8")
        assert 'data-export="audit-orders-table"' in html
        assert 'id="audit-orders-table"' in html
        assert 'data-export="audit-validations-table"' in html
        assert 'id="audit-validations-table"' in html

    def test_analyzer_signals_export(self):
        html = (FRONTEND_DIR / "analyzer.html").read_text(encoding="utf-8")
        assert 'data-export="analyzer-signals-table"' in html
        assert 'id="analyzer-signals-table"' in html

    def test_timeline_events_export(self):
        html = (FRONTEND_DIR / "timeline.html").read_text(encoding="utf-8")
        assert 'data-export="timeline-events-table"' in html
        assert 'id="timeline-events-table"' in html


class TestPrintStylesheet:
    def test_print_media_rules(self):
        css = (CSS_DIR / "main.css").read_text(encoding="utf-8")
        assert "@media print" in css
        assert "@page" in css
        assert "A4" in css
        assert "page-break-inside: avoid" in css or "break-inside: avoid" in css
        assert ".sidebar" in css
        assert ".topbar" in css


class TestSidebarReportLinks:
    def test_all_pages_have_report_link(self):
        for page_name in CORE_PAGES:
            page_path = FRONTEND_DIR / page_name
            assert page_path.exists(), f"Missing {page_name}"
            html = page_path.read_text(encoding="utf-8")
            assert 'href="report.html"' in html, f"{page_name} missing sidebar link to report.html"
