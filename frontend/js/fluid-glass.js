/* PortfolioIQ — FluidGlass: a liquid lens in WebGL2, no dependencies.
   Ported in spirit from React Bits FluidGlass: a dome of glass that refracts the light behind it,
   splits it at the edges, reflects a studio highlight, follows the pointer on a spring, can be picked
   up and thrown, and wobbles when it moves. Simplified on purpose: it bends a procedural light field
   rather than a page texture, so it needs one draw call per frame and no extra passes.

   Markup:  <div class="fluid-glass" data-fluid-glass data-size="168"></div>
   Without WebGL2, or with reduced motion, the element falls back to a static CSS glass disc. */
(function () {
  "use strict";

  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)");
  const PIXEL_BUDGET = 1.6e5; // canvas pixels per glass, capped on large or high-DPI screens

  const VERT = `#version 300 es
  const vec2 corners[3] = vec2[3](vec2(-1.0, -1.0), vec2(3.0, -1.0), vec2(-1.0, 3.0));
  void main() { gl_Position = vec4(corners[gl_VertexID], 0.0, 1.0); }`;

  const FRAG = `#version 300 es
  precision highp float;
  uniform vec2 uRes;     // canvas size, px
  uniform vec2 uCenter;  // glass centre, px from the top-left
  uniform float uRadius; // glass radius, px
  uniform vec2 uDir;     // unit direction of motion
  uniform float uStretch;// >= 1: how far the drop is pulled along its motion
  uniform float uWobble; // ripple amplitude on the outline
  uniform float uTime;
  uniform float uBend;   // refraction strength
  uniform float uSplit;  // colour dispersion at the edge
  out vec4 fragColor;

  // The light behind the glass: the page's near-black wallpaper with its two faint slate lights,
  // drawn tighter than the page's so the bending of their edges shows through the lens.
  vec3 backdrop(vec2 q) {
    vec3 c = vec3(0.02, 0.024, 0.031);
    vec2 a = q - vec2(-0.5, 0.42);  c += vec3(0.36, 0.42, 0.52) * exp(-dot(a, a) * 3.0) * 0.7;
    vec2 b = q - vec2( 0.55, -0.3); c += vec3(0.34, 0.34, 0.44) * exp(-dot(b, b) * 2.6) * 0.6;
    vec2 d = q - vec2( 0.1, 0.72);  c += vec3(0.75, 0.78, 0.85) * exp(-dot(d, d) * 6.0) * 0.35;
    return c;
  }

  void main() {
    // Work in the glass's own units: centre at 0, edge at 1, y pointing down.
    vec2 frag = vec2(gl_FragCoord.x, uRes.y - gl_FragCoord.y);
    vec2 p = (frag - uCenter) / uRadius;

    // Pull the drop along its motion: a soft squash that reads as liquid, not as a scale.
    vec2 perp = vec2(-uDir.y, uDir.x);
    vec2 local = vec2(dot(p, uDir) / uStretch, dot(p, perp) * sqrt(uStretch));
    p = uDir * local.x + perp * local.y;

    float r = length(p);
    float ripple = uWobble * 0.055 * sin(atan(p.y, p.x) * 3.0 + uTime * 2.2)
                 + uWobble * 0.03 * sin(atan(p.y, p.x) * 5.0 - uTime * 1.5);
    float d = r - (1.0 + ripple);               // signed distance, in radius units
    float aa = 1.6 / uRadius;
    float mask = 1.0 - smoothstep(-aa, aa, d);
    if (mask <= 0.0) { fragColor = vec4(0.0); return; }

    // A dome: flat in the middle, steep at the rim, so the rim bends light the most.
    float inside = clamp(1.0 - r * r, 0.0, 1.0);
    vec3 n = normalize(vec3(p * (0.35 + 0.65 * sqrt(inside)), sqrt(inside) + 0.12));
    float edge = pow(1.0 - sqrt(inside), 2.0);
    vec2 bend = -p * edge * uBend * 0.9;        // pull the light inward at the rim

    // Refract each colour by a slightly different amount: fringes at the edge, none in the middle.
    float split = uSplit * edge * 0.12;
    vec3 refracted = vec3(
      backdrop(p + bend * (1.0 + split)).r,
      backdrop(p + bend).g,
      backdrop(p + bend * (1.0 - split)).b
    );

    // Studio light: a soft specular highlight and a rim of reflected light.
    vec3 L = normalize(vec3(-0.45, 0.6, 0.75));
    vec3 H = normalize(L + vec3(0.0, 0.0, 1.0));
    float spec = pow(max(dot(n, H), 0.0), 70.0) * 0.9;
    float rim = pow(1.0 - n.z, 2.2) * 0.38;
    float outline = (1.0 - smoothstep(0.0, 0.035, abs(d))) * 0.45;

    vec3 col = refracted * 0.92 + vec3(spec + rim + outline);
    float alpha = clamp(mask * (0.9 + spec * 0.1), 0.0, 1.0);
    fragColor = vec4(col * alpha, alpha);      // premultiplied, to match the canvas
  }`;

  function compile(gl, type, src) {
    const s = gl.createShader(type);
    gl.shaderSource(s, src);
    gl.compileShader(s);
    if (gl.getShaderParameter(s, gl.COMPILE_STATUS)) return s;
    console.warn("FluidGlass shader:", gl.getShaderInfoLog(s));
    gl.deleteShader(s);
    return null;
  }

  function mount(host) {
    if (host.__fluid) return;
    const size = Math.max(64, parseFloat(host.dataset.size) || 168);
    host.style.width = size + "px";
    host.style.height = size + "px";

    let canvas = host.querySelector("canvas");
    if (!canvas) {
      canvas = document.createElement("canvas");
      canvas.setAttribute("aria-hidden", "true");
      host.appendChild(canvas);
    }
    const gl = canvas.getContext("webgl2", { alpha: true, premultipliedAlpha: true, antialias: false });
    const vs = gl && compile(gl, gl.VERTEX_SHADER, VERT);
    const fs = gl && compile(gl, gl.FRAGMENT_SHADER, FRAG);
    const program = vs && fs && gl.createProgram();
    if (program) {
      gl.attachShader(program, vs);
      gl.attachShader(program, fs);
      gl.linkProgram(program);
    }
    if (!program || !gl.getProgramParameter(program, gl.LINK_STATUS)) {
      canvas.remove();
      host.classList.add("fluid-glass--css");
      return;
    }
    host.classList.add("fluid-glass--gl");

    const U = {};
    ["uRes", "uCenter", "uRadius", "uDir", "uStretch", "uWobble", "uTime", "uBend", "uSplit"].forEach((name) => {
      U[name] = gl.getUniformLocation(program, name);
    });
    const vao = gl.createVertexArray();
    gl.bindVertexArray(vao);

    // Physical state, in host CSS pixels.
    const rest = { x: size / 2, y: size / 2 };
    const s = {
      x: rest.x, y: rest.y, vx: 0, vy: 0,
      tx: rest.x, ty: rest.y,        // where the spring pulls to
      dir: [1, 0], stretch: 1, vstretch: 0, kick: 0,
      grabbed: false, offX: 0, offY: 0, hover: false,
    };
    let dpr = 1;
    let visible = true;
    let raf = 0;
    let last = 0;
    let t = Math.random() * 10;

    function resize() {
      const rect = host.getBoundingClientRect();
      const css = Math.max(1, rect.width);
      dpr = Math.min(window.devicePixelRatio || 1, Math.sqrt(PIXEL_BUDGET) / css);
      dpr = Math.max(1, Math.min(2, dpr));
      const px = Math.round(css * dpr);
      if (canvas.width !== px) { canvas.width = px; canvas.height = px; }
      s.x = clamp(s.x, size * 0.36, css - size * 0.36);
      s.y = clamp(s.y, size * 0.36, css - size * 0.36);
    }

    const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

    function local(e) {
      const r = host.getBoundingClientRect();
      return { x: e.clientX - r.left, y: e.clientY - r.top, inside: e.clientX >= r.left && e.clientX <= r.right && e.clientY >= r.top && e.clientY <= r.bottom };
    }

    host.addEventListener("pointermove", (e) => {
      const p = local(e);
      s.hover = true;
      if (s.grabbed) { s.tx = p.x - s.offX; s.ty = p.y - s.offY; }
      else if (!reduced.matches && fine()) { s.tx = p.x; s.ty = p.y; }
      wake();
    });
    host.addEventListener("pointerleave", () => {
      s.hover = false;
      if (!s.grabbed) { s.tx = rest.x; s.ty = rest.y; }
      wake();
    });
    host.addEventListener("pointerdown", (e) => {
      const p = local(e);
      const near = Math.hypot(p.x - s.x, p.y - s.y) < size * 0.4;
      if (!near) return;
      s.grabbed = true;
      s.offX = p.x - s.x;
      s.offY = p.y - s.y;
      host.setPointerCapture(e.pointerId);
      host.classList.add("is-grabbing");
      s.kick = 1;
      wake();
    });
    const release = (e) => {
      if (!s.grabbed) return;
      s.grabbed = false;
      host.classList.remove("is-grabbing");
      try { host.releasePointerCapture(e.pointerId); } catch (_) { /* already released */ }
      // keep the throw velocity from the spring; it settles back to rest once it slows
      s.tx = s.hover ? s.x : rest.x;
      s.ty = s.hover ? s.y : rest.y;
      wake();
    };
    host.addEventListener("pointerup", release);
    host.addEventListener("pointercancel", release);

    function fine() { return window.matchMedia("(hover: hover) and (pointer: fine)").matches; }

    function frame(now) {
      raf = 0;
      const dt = last ? clamp((now - last) / 1000, 0, 0.05) : 1 / 60;
      last = now;
      t += dt;
      const css = host.getBoundingClientRect().width || size;
      const still = reduced.matches;

      // Idle drift when nobody is interacting: a slow figure-eight around the rest point.
      if (!still && !s.hover && !s.grabbed) {
        s.tx = rest.x + Math.sin(t * 0.45) * size * 0.14;
        s.ty = rest.y + Math.sin(t * 0.9 + 1.2) * size * 0.08;
      }

      // Spring to the target. A grabbed drop follows the pointer tightly; a free one floats.
      const k = s.grabbed ? 260 : 70;
      const c = s.grabbed ? 26 : 12;
      const steps = 4;
      for (let i = 0; i < steps; i++) {
        const h = dt / steps;
        s.vx += (k * (s.tx - s.x) - c * s.vx) * h;
        s.vy += (k * (s.ty - s.y) - c * s.vy) * h;
        s.x += s.vx * h;
        s.y += s.vy * h;
      }
      // Walls: a thrown drop bounces off the edge of its box, losing some energy.
      const lo = size * 0.36, hi = css - size * 0.36;
      if (s.x < lo || s.x > hi) { s.x = clamp(s.x, lo, hi); s.vx *= -0.5; s.kick = Math.max(s.kick, 0.4); }
      if (s.y < lo || s.y > hi) { s.y = clamp(s.y, lo, hi); s.vy *= -0.5; s.kick = Math.max(s.kick, 0.4); }

      // Direction and stretch follow the speed; the wobble decays back to a round drop.
      const speed = Math.hypot(s.vx, s.vy);
      if (speed > 6) {
        const d = [s.vx / speed, s.vy / speed];
        s.dir = [s.dir[0] + (d[0] - s.dir[0]) * 0.25, s.dir[1] + (d[1] - s.dir[1]) * 0.25];
        const n = Math.hypot(s.dir[0], s.dir[1]) || 1;
        s.dir = [s.dir[0] / n, s.dir[1] / n];
      }
      const stretchGoal = 1 + clamp(speed / 1400, 0, 0.45) + s.kick * 0.25;
      const ks = 160, cs = 13;
      s.vstretch += (ks * (stretchGoal - s.stretch) - cs * s.vstretch) * dt;
      s.stretch += s.vstretch * dt;
      s.kick = Math.max(0, s.kick - dt * 2.2);

      draw(css);

      const settled = !s.grabbed && !s.hover && speed < 4 && Math.abs(s.x - s.tx) < 0.2 && Math.abs(s.y - s.ty) < 0.2 && Math.abs(s.stretch - 1) < 0.002 && s.kick === 0;
      if (!still && visible && !document.hidden && !settled) raf = requestAnimationFrame(frame);
      else if (still) { /* a single static frame is drawn above */ }
    }

    function draw(css) {
      // The lens is drawn at 0.7 of the box, so the wobble and the stretch never leave the canvas.
      const r = size * 0.36;
      const ratio = canvas.width / css;
      gl.viewport(0, 0, canvas.width, canvas.height);
      gl.clearColor(0, 0, 0, 0);
      gl.clear(gl.COLOR_BUFFER_BIT);
      gl.useProgram(program);
      gl.bindVertexArray(vao);
      gl.uniform2f(U.uRes, canvas.width, canvas.height);
      gl.uniform2f(U.uCenter, s.x * ratio, s.y * ratio);
      gl.uniform1f(U.uRadius, r * ratio);
      gl.uniform2f(U.uDir, s.dir[0], s.dir[1]);
      gl.uniform1f(U.uStretch, Math.max(0.6, s.stretch));
      gl.uniform1f(U.uWobble, reduced.matches ? 0 : 0.6 + s.kick * 0.8);
      gl.uniform1f(U.uTime, reduced.matches ? 0 : t);
      gl.uniform1f(U.uBend, 0.85);
      gl.uniform1f(U.uSplit, 0.9);
      gl.drawArrays(gl.TRIANGLES, 0, 3);
    }

    function wake() {
      if (!raf && visible && !document.hidden) { last = 0; raf = requestAnimationFrame(frame); }
    }

    const ro = new ResizeObserver(() => { resize(); wake(); });
    ro.observe(host);
    new IntersectionObserver((entries) => {
      visible = entries.some((en) => en.isIntersecting);
      wake();
    }).observe(host);
    document.addEventListener("visibilitychange", wake);
    reduced.addEventListener?.("change", () => { resize(); wake(); });

    // Read-only view of the spring, for tests and debugging: where the lens is, and where it is heading.
    host.__fluid = true;
    host.fluidState = () => ({ x: s.x, y: s.y, targetX: s.tx, targetY: s.ty, stretch: s.stretch, grabbed: s.grabbed });
    resize();
    wake();
    if (reduced.matches) draw(host.getBoundingClientRect().width || size); // one still frame
  }

  function init() {
    document.querySelectorAll("[data-fluid-glass]").forEach(mount);
  }
  window.PortfolioIQFluid = { mount };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
