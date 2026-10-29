/* PortfolioIQ — pointer feel for the glass theme.
   One delegated listener writes the cursor position into each surface under the pointer, so the
   sheen in glass.css follows it. Only for fine pointers, and throttled to one write per frame. */
(function () {
  if (!window.matchMedia("(hover: hover) and (pointer: fine)").matches) return;
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;

  const SURFACE = ".card, .kpi-card, .metric-sm, .abstract-card";
  let frame = 0;
  let target = null;
  let x = 0;
  let y = 0;

  function paint() {
    frame = 0;
    if (!target) return;
    const r = target.getBoundingClientRect();
    target.style.setProperty("--glow-x", `${x - r.left}px`);
    target.style.setProperty("--glow-y", `${y - r.top}px`);
  }

  document.addEventListener("pointermove", (e) => {
    const el = e.target.closest && e.target.closest(SURFACE);
    if (!el) { target = null; return; }
    target = el;
    x = e.clientX;
    y = e.clientY;
    if (!frame) frame = requestAnimationFrame(paint);
  }, { passive: true });
})();
