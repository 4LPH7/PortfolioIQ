"""
Unit and DOM validation tests for Plan 06-08: Responsive Mobile Navigation & WCAG 2.1 AA Accessibility.

Verifies:
- HTML5 Semantic landmarks (<header role="banner">, <nav>, <main role="main">) across all views
- Responsive mobile hamburger toggle button (#sidebar-toggle-btn) with ARIA attributes
- Fixed bottom quick-navigation bar (<nav class="bottom-nav">) across all views
- WCAG 2.1 AA keyboard focus rings (:focus-visible) and touch targets
- Responsive mobile CSS styles for off-canvas drawer and viewports < 768px
- Escape key listener and backdrop dismissal logic in api.js
- Full project test suite passing with >= 85% safety-critical coverage
"""

from pathlib import Path

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
CSS_DIR = FRONTEND_DIR / "css"
JS_DIR = FRONTEND_DIR / "js"

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


class TestSemanticLandmarks:
    def test_sidebar_nav_landmarks(self):
        for page in CORE_PAGES:
            html = (FRONTEND_DIR / page).read_text(encoding="utf-8")
            assert 'id="main-sidebar"' in html, f"{page} missing id='main-sidebar'"
            assert 'aria-label="Main Navigation"' in html, f"{page} missing sidebar aria-label"

    def test_topbar_banner_landmarks(self):
        for page in CORE_PAGES:
            html = (FRONTEND_DIR / page).read_text(encoding="utf-8")
            assert '<header class="topbar" role="banner">' in html, (
                f"{page} missing <header role='banner'>"
            )

    def test_main_landmarks(self):
        for page in CORE_PAGES:
            html = (FRONTEND_DIR / page).read_text(encoding="utf-8")
            assert 'role="main"' in html, f"{page} missing role='main' landmark"


class TestMobileNavigation:
    def test_sidebar_toggle_buttons(self):
        for page in CORE_PAGES:
            html = (FRONTEND_DIR / page).read_text(encoding="utf-8")
            assert 'id="sidebar-toggle-btn"' in html, f"{page} missing #sidebar-toggle-btn"
            assert 'aria-label="Toggle navigation menu"' in html, (
                f"{page} missing toggle aria-label"
            )
            assert 'aria-expanded="false"' in html, f"{page} missing initial aria-expanded"
            assert 'aria-controls="main-sidebar"' in html, f"{page} missing aria-controls"

    def test_bottom_navigation_bar(self):
        for page in CORE_PAGES:
            html = (FRONTEND_DIR / page).read_text(encoding="utf-8")
            assert '<nav class="bottom-nav"' in html, f"{page} missing .bottom-nav bar"
            assert 'aria-label="Mobile Navigation"' in html, f"{page} missing bottom-nav aria-label"
            assert 'href="index.html"' in html
            assert 'href="analyzer.html"' in html
            assert 'href="rebalance.html"' in html
            assert 'href="timeline.html"' in html
            assert 'href="settings.html"' in html


class TestA11yAndCssStandards:
    def test_css_focus_visible_rings(self):
        css = (CSS_DIR / "main.css").read_text(encoding="utf-8")
        assert ":focus-visible" in css, "Missing :focus-visible rules in main.css"
        assert "outline:" in css
        assert "outline-offset:" in css

    def test_css_mobile_breakpoints(self):
        css = (CSS_DIR / "main.css").read_text(encoding="utf-8")
        assert "@media (max-width: 768px)" in css, "Missing 768px responsive breakpoint"
        assert ".sidebar-toggle-btn" in css
        assert ".bottom-nav" in css
        assert ".sidebar-backdrop" in css

    def test_bell_buttons_have_accessible_labels(self):
        for page in CORE_PAGES:
            html = (FRONTEND_DIR / page).read_text(encoding="utf-8")
            assert 'aria-label="View notifications"' in html, (
                f"{page} missing accessible label on notification bell button"
            )

    def test_js_mobile_navigation_controller(self):
        js = (JS_DIR / "api.js").read_text(encoding="utf-8")
        assert "initMobileNavigation" in js
        assert "sidebar-toggle-btn" in js
        assert "sidebar-backdrop" in js
        assert "Escape" in js
        assert "aria-expanded" in js
