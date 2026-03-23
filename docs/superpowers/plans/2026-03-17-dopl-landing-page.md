# dopl Landing Page Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a single-file landing page for the dopl project with 6 sections and an email subscription form.

**Architecture:** Single `index.html` file in `landing/` folder with embedded CSS and vanilla JS. No build tools, no dependencies. Sections built incrementally: scaffold → hero → problem → primitives → code example → benefits → subscribe → polish.

**Tech Stack:** HTML5, CSS3 (custom properties, grid, flexbox, keyframes), vanilla JavaScript, `python -m http.server` for local dev.

**Spec:** `docs/superpowers/specs/2026-03-17-dopl-landing-page-design.md`

---

## Chunk 1: Scaffold and Hero

### Task 1: Create landing directory and HTML scaffold

**Files:**
- Create: `landing/index.html`

- [ ] **Step 1: Create `landing/` directory**

Run: `mkdir -p landing`

- [ ] **Step 2: Write the HTML scaffold with `<head>`, CSS custom properties, and empty `<body>` sections**

Create `landing/index.html` with:
- DOCTYPE, html lang="en"
- `<head>`: charset, viewport, title, meta description, OG tags, inline SVG favicon

Exact meta/OG tags:
```html
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>dopl — Catch the bugs your tests miss</title>
<meta name="description" content="dopl records real service interactions and replays them deterministically, catching '200 OK but wrong' regressions before production.">
<meta property="og:title" content="dopl — Catch the bugs your tests miss">
<meta property="og:description" content="dopl records real service interactions and replays them deterministically, catching '200 OK but wrong' regressions before production.">
<meta property="og:type" content="website">
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><circle cx='16' cy='16' r='15' fill='%233fb950'/><text x='16' y='22' text-anchor='middle' font-size='18' font-weight='bold' fill='white' font-family='sans-serif'>d</text></svg>">
```
- `<style>`: CSS reset, custom properties for the full color palette, typography stacks, base body styles, utility classes
- `<body>`: empty section placeholders with IDs for all 6 sections:
  - `<section id="hero">`, `<section id="problem">`, `<section id="primitives">`, `<section id="code-example">`, `<section id="benefits">`, `<section id="subscribe">`
- Responsive media queries skeleton (desktop > 1024, tablet 768-1024, mobile < 768)

CSS custom properties to define:
```css
:root {
  --bg-primary: #0d1117;
  --bg-secondary: #161b22;
  --border: #30363d;
  --text-primary: #c9d1d9;
  --text-secondary: #9da5ae; /* bumped from #8b949e for WCAG AA compliance */
  --accent-green: #3fb950;
  --accent-green-hover: #2ea043;
  --accent-blue: #58a6ff;
  --accent-blue-hover: #79b8ff;
  --accent-red: #f78166;
  --accent-purple: #d2a8ff;
  --font-body: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Noto Sans', Helvetica, Arial, sans-serif;
  --font-code: 'SF Mono', 'Fira Code', 'Cascadia Code', 'Courier New', monospace;
}
```

- [ ] **Step 3: Verify the page loads**

Run: `cd landing && python -m http.server 8080 &` then open http://localhost:8080
Expected: blank dark page, correct title in browser tab, favicon visible

- [ ] **Step 4: Commit**

```bash
git add landing/index.html
git commit -m "feat: scaffold landing page with CSS custom properties and meta tags"
```

---

### Task 2: Build the Hero section

**Files:**
- Modify: `landing/index.html`

- [ ] **Step 1: Add Hero HTML structure**

Inside `<body>`, add the hero `<section id="hero">` with:
- CSS class `hero`
- `<h1>`: "Catch the bugs your tests miss"
- `<p>` subtitle: "dopl records real service interactions and replays them deterministically — catching '200 OK but wrong' regressions before they reach production."
- Subscribe form: `<form>` with email `<input>` (type="email", placeholder="your@email.com") + `<button>` "Get Early Access"
  - `<label>` with `sr-only` class associated to the input
  - `<div>` for form feedback messages
- Terminal animation container: `<div class="terminal">` with 4 lines, each in a `<div class="terminal-line">`

Terminal lines:
```
<span class="terminal-prompt">$</span> dopl record
<span class="terminal-output">▓▓▓▓▓▓▓▓▓▓ 47 fixtures captured</span>
<span class="terminal-prompt">$</span> dopl replay --pr 142
<span class="terminal-output terminal-success">▓▓▓▓▓▓▓▓▓▓ 0 regressions found ✓</span>
```

Visually-hidden text alternative for screen readers (aria-hidden on terminal, sr-only text describing what it shows).

- [ ] **Step 2: Add Hero CSS**

```css
.hero {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  padding: 2rem;
  background: linear-gradient(180deg, var(--bg-primary), var(--bg-secondary));
  position: relative;
}
```

- Subtle grid pattern overlay using a pseudo-element with `background-image: linear-gradient(...)` creating a dot/grid pattern at low opacity
- `h1`: font-size 3.5rem, white, font-weight 800
- Subtitle: max-width 640px, text-secondary color, font-size 1.25rem, margin-bottom 2rem
- Form: flex row, gap 0.5rem, max-width 440px
- Input: dark background (--bg-secondary), border (--border), text-primary, padding, border-radius 8px, focus ring (--accent-blue)
- Button: --accent-green background, white text, bold, border-radius 8px, hover → --accent-green-hover, focus ring
- `.sr-only`: position absolute, clip, 1px dimensions (standard screen-reader-only class)
- Terminal: background --bg-secondary, border --border, border-radius 8px, padding, max-width 500px, font-family var(--font-code), text-align left, margin-top 3rem
- Terminal prompt: --accent-green color
- Terminal output: --text-secondary
- Terminal success: --accent-green

- [ ] **Step 3: Add terminal animation JS + CSS keyframes**

CSS:
```css
.terminal-line {
  opacity: 0;
  animation: fadeIn 0.5s ease forwards;
}
.terminal-line:nth-child(1) { animation-delay: 0.5s; }
.terminal-line:nth-child(2) { animation-delay: 1.5s; }
.terminal-line:nth-child(3) { animation-delay: 2.5s; }
.terminal-line:nth-child(4) { animation-delay: 3.5s; }

@keyframes fadeIn {
  from { opacity: 0; transform: translateY(4px); }
  to { opacity: 1; transform: translateY(0); }
}
```

Minimal JS at end of `<body>`: Intersection Observer on `.terminal` to only trigger animation when scrolled into view (reset animation by toggling a class).

- [ ] **Step 4: Verify hero renders correctly**

Run: refresh http://localhost:8080
Expected: full-viewport hero with headline, subtitle, email form, and animated terminal. Form submits do nothing yet (will wire up in Task 7).

- [ ] **Step 5: Commit**

```bash
git add landing/index.html
git commit -m "feat: add hero section with terminal animation and subscribe form"
```

---

## Chunk 2: Problem Statement and Primitives Tabs

### Task 3: Build the Problem Statement section

**Files:**
- Modify: `landing/index.html`

- [ ] **Step 1: Add Problem Statement HTML**

New `<section id="problem" class="problem">` after hero with:
- Section heading: `<h2>` "The problem with '200 OK'"
- `<div class="comparison">` containing two panels:
  - Left: `<div class="panel panel-without">` with heading "Without dopl" and a checklist `<ul>`:
    - ✓ Unit tests pass
    - ✓ Integration tests pass
    - ✓ Deploy to production
    - ✗ Customer reports wrong prices (red, different icon)
  - Right: `<div class="panel panel-with">` with heading "With dopl" and checklist `<ul>`:
    - ✓ Record baseline fixtures
    - ✓ Replay against PR
    - ✓ Diff caught wrong prices
    - ✓ Blocked before merge

- [ ] **Step 2: Add Problem Statement CSS**

```css
.problem { padding: 6rem 2rem; max-width: 900px; margin: 0 auto; }
.comparison { display: grid; grid-template-columns: 1fr 1fr; gap: 2rem; margin-top: 2rem; }
.panel {
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 2rem;
}
.panel-without { border-color: var(--accent-red); }
.panel-with { border-color: var(--accent-green); }
```

- Checklist items: flex row with icon + text
- Pass icon (✓): --accent-green
- Fail icon (✗): --accent-red, text also red-tinted
- Panel headings: uppercase, small, letter-spacing

- [ ] **Step 3: Add responsive rule**

In tablet media query: `.comparison { grid-template-columns: 1fr; }` (stack vertically)

- [ ] **Step 4: Verify**

Refresh page. Expected: two side-by-side panels below hero, stacking on narrow viewport.

- [ ] **Step 5: Commit**

```bash
git add landing/index.html
git commit -m "feat: add problem statement section with comparison panels"
```

---

### Task 4: Build the Primitives tabs section

**Files:**
- Modify: `landing/index.html`

- [ ] **Step 1: Add Primitives HTML structure**

New `<section id="primitives" class="primitives">` with:
- `<h2>` "How it works"
- `<p>` subtitle: "Four composable primitives that record and replay any side effect."
- Tab bar: `<div role="tablist">` with 4 `<button role="tab">` elements:
  - `@sim_trace` (aria-selected="true", default active)
  - `sim_capture()`
  - `sim_db()`
  - `sim_http()`
- 4 `<div role="tabpanel">` elements (one per primitive), each containing:
  - Description paragraph (from spec)
  - `<pre><code>` block with the code snippet (from spec)
  - Two-column mini grid: "Record mode" vs "Replay mode" descriptions
- Only the active tabpanel is visible (`display: block`), others hidden

Use exact descriptions and code from spec sections "Tab: @sim_trace", etc.

- [ ] **Step 2: Add Primitives CSS**

```css
.primitives { padding: 6rem 2rem; max-width: 900px; margin: 0 auto; }
[role="tablist"] {
  display: flex; gap: 0; border-bottom: 1px solid var(--border);
  margin-bottom: 2rem;
}
[role="tab"] {
  background: none; border: none; border-bottom: 2px solid transparent;
  color: var(--text-secondary); font-family: var(--font-code);
  padding: 0.75rem 1.25rem; cursor: pointer; font-size: 0.95rem;
}
[role="tab"][aria-selected="true"] {
  color: var(--text-primary); border-bottom-color: var(--accent-blue);
}
[role="tab"]:hover { color: var(--text-primary); }
[role="tab"]:focus-visible { outline: 2px solid var(--accent-blue); outline-offset: 2px; }
[role="tabpanel"] { display: none; }
[role="tabpanel"].active { display: block; }
```

- Code blocks inside tabs: background --bg-primary, border --border, border-radius 8px, padding 1.5rem, overflow-x auto
- Mode comparison: two-column grid with labels "Record" (green) / "Replay" (blue)

- [ ] **Step 3: Add tab switching JS**

```javascript
document.querySelectorAll('[role="tab"]').forEach(tab => {
  tab.addEventListener('click', () => {
    // Deactivate all tabs and panels
    document.querySelectorAll('[role="tab"]').forEach(t => t.setAttribute('aria-selected', 'false'));
    document.querySelectorAll('[role="tabpanel"]').forEach(p => p.classList.remove('active'));
    // Activate clicked tab and its panel
    tab.setAttribute('aria-selected', 'true');
    document.getElementById(tab.getAttribute('aria-controls')).classList.add('active');
  });
  // Keyboard navigation: arrow keys
  tab.addEventListener('keydown', (e) => {
    const tabs = [...document.querySelectorAll('[role="tab"]')];
    const idx = tabs.indexOf(tab);
    if (e.key === 'ArrowRight') { tabs[(idx + 1) % tabs.length].focus(); tabs[(idx + 1) % tabs.length].click(); }
    if (e.key === 'ArrowLeft') { tabs[(idx - 1 + tabs.length) % tabs.length].focus(); tabs[(idx - 1 + tabs.length) % tabs.length].click(); }
  });
});
```

- [ ] **Step 4: Add mobile responsive styles**

On mobile (`<768px`), restyle the tabs as a vertical stack where all panels are visible (no tab switching needed). This avoids duplicating content:

```css
@media (max-width: 767px) {
  [role="tablist"] { display: none; }
  [role="tabpanel"] {
    display: block !important;
    border-bottom: 1px solid var(--border);
    padding-bottom: 2rem;
    margin-bottom: 2rem;
  }
  [role="tabpanel"]::before {
    content: attr(aria-label);
    display: block;
    font-family: var(--font-code);
    font-weight: 600;
    color: var(--accent-blue);
    margin-bottom: 1rem;
    font-size: 1rem;
  }
}
```

Each `[role="tabpanel"]` should have an `aria-label` attribute matching its primitive name (e.g., `aria-label="@sim_trace"`). On mobile, the label becomes a visible heading via `::before`, and all panels display vertically.

- [ ] **Step 5: Verify**

Refresh page. Expected: tabs switch content on click, arrow keys navigate, accordion visible on narrow viewport.

- [ ] **Step 6: Commit**

```bash
git add landing/index.html
git commit -m "feat: add interactive primitives section with tabs and mobile accordion"
```

---

## Chunk 3: Code Example, Benefits, and Subscribe

### Task 5: Build the Code Example section

**Files:**
- Modify: `landing/index.html`

- [ ] **Step 1: Add Code Example HTML**

New `<section id="code-example" class="code-example">` with:
- `<h2>` "See it in action"
- `<p>` subtitle: "One function, four primitives. Record everything, replay deterministically."
- `<div class="code-block">` containing a `<pre><code>` with the exact code from the spec (lines 149-174)
- Manual syntax highlighting using `<span>` elements with classes:
  - `.kw` for keywords (def, with, as, if, not, for, in, return)
  - `.str` for strings
  - `.fn` for function names
  - `.cm` for comments
  - `.dec` for decorators
  - `.op` for operators

- [ ] **Step 2: Add Code Example CSS**

```css
.code-example { padding: 6rem 2rem; max-width: 900px; margin: 0 auto; }
.code-block {
  background: var(--bg-primary);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 2rem;
  overflow-x: auto;
  font-family: var(--font-code);
  font-size: 0.9rem;
  line-height: 1.6;
}
.code-block .kw { color: #ff7b72; }
.code-block .str { color: #a5d6ff; }
.code-block .fn { color: #d2a8ff; }
.code-block .cm { color: #8b949e; }
.code-block .dec { color: #ffa657; }
.code-block .op { color: var(--text-primary); }
```

- [ ] **Step 3: Verify**

Refresh page. Expected: syntax-highlighted code block with all 4 primitives visible, scrollable on mobile.

- [ ] **Step 4: Commit**

```bash
git add landing/index.html
git commit -m "feat: add syntax-highlighted code example section"
```

---

### Task 6: Build the Benefits section

**Files:**
- Modify: `landing/index.html`

- [ ] **Step 1: Add Benefits HTML**

New `<section id="benefits" class="benefits">` with:
- `<h2>` "Why dopl"
- `<div class="benefits-grid">` containing 6 `<div class="benefit">` items, each with:
  - Inline `<svg>` icon (24x24, stroke-based, matching descriptions from spec)
  - `<h3>` title
  - `<p>` description

Icons (inline SVGs, 24x24, stroke="currentColor", stroke-width="2", fill="none"):
1. **Deterministic** — circular arrows (two curved arrows forming a circle)
2. **Framework-Agnostic** — puzzle piece
3. **Zero Overhead** — lightning bolt
4. **No Mocks Required** — filled record circle
5. **CI/CD Ready** — git branch (line splitting into two)
6. **Protocol-Agnostic** — three stacked layers

- [ ] **Step 2: Add Benefits CSS**

```css
.benefits { padding: 6rem 2rem; max-width: 900px; margin: 0 auto; }
.benefits-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 2rem;
  margin-top: 2rem;
}
.benefit {
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 1.5rem;
  transition: border-color 0.2s;
}
.benefit:hover { border-color: var(--accent-blue); }
.benefit svg { color: var(--accent-blue); margin-bottom: 1rem; }
.benefit h3 { font-size: 1.1rem; margin-bottom: 0.5rem; }
.benefit p { color: var(--text-secondary); font-size: 0.9rem; }
```

Responsive:
- Tablet: `grid-template-columns: repeat(2, 1fr)`
- Mobile: `grid-template-columns: 1fr`

- [ ] **Step 3: Verify**

Refresh page. Expected: 3x2 grid of benefit cards with icons, hover glow effect.

- [ ] **Step 4: Commit**

```bash
git add landing/index.html
git commit -m "feat: add benefits grid section with inline SVG icons"
```

---

### Task 7: Build the Subscribe Footer and wire up form logic

**Files:**
- Modify: `landing/index.html`

- [ ] **Step 1: Add Subscribe Footer HTML**

New `<section id="subscribe" class="subscribe">` with:
- `<h2>` "Stay in the loop"
- `<p>`: "We'll notify you when dopl is ready for early access. No spam."
- `<form>` with email input + "Subscribe" button (same markup pattern as hero form)
- `<div class="form-message">` for feedback
- `<footer>`: small copyright text: "dopl — Deterministic Operation Persistence Layer" (plan addition, not in spec)

- [ ] **Step 2: Add Subscribe Footer CSS**

```css
.subscribe {
  padding: 6rem 2rem;
  background: var(--bg-secondary);
  text-align: center;
}
footer {
  padding: 2rem;
  text-align: center;
  color: var(--text-secondary);
  font-size: 0.85rem;
}
.form-message {
  margin-top: 0.75rem;
  font-size: 0.9rem;
  min-height: 1.5em;
}
.form-message.success { color: var(--accent-green); }
.form-message.error { color: var(--accent-red); }
.form-message.info { color: var(--accent-blue); }
```

- [ ] **Step 3: Write the subscribe form JS (shared handler for hero + footer forms)**

```javascript
function handleSubscribe(form) {
  const input = form.querySelector('input[type="email"]');
  const msg = form.querySelector('.form-message');
  const email = input.value.trim();

  // Empty check
  if (!email) {
    msg.textContent = 'Please enter your email address';
    msg.className = 'form-message error';
    return;
  }
  // Format check
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
    msg.textContent = 'Please enter a valid email address';
    msg.className = 'form-message error';
    return;
  }

  try {
    const subs = JSON.parse(localStorage.getItem('dopl_subscribers') || '[]');
    if (subs.some(s => s.email === email)) {
      msg.textContent = "You're already subscribed!";
      msg.className = 'form-message info';
      return;
    }
    subs.push({ email, timestamp: new Date().toISOString() });
    localStorage.setItem('dopl_subscribers', JSON.stringify(subs));
  } catch (e) {
    console.warn('localStorage unavailable — email not persisted');
  }

  msg.textContent = "You're on the list! We'll be in touch.";
  msg.className = 'form-message success';
  input.value = '';
}

document.querySelectorAll('.subscribe-form').forEach(form => {
  form.addEventListener('submit', (e) => {
    e.preventDefault();
    handleSubscribe(form);
  });
});
```

Both the hero and footer forms use class `subscribe-form`.

- [ ] **Step 4: Verify all form states**

Test in browser:
1. Submit empty → "Please enter your email address" (red)
2. Submit "invalid" → "Please enter a valid email address" (red)
3. Submit "test@example.com" → "You're on the list!" (green)
4. Submit same email again → "You're already subscribed!" (blue)
5. Check localStorage in DevTools: `dopl_subscribers` array has the entry
6. Both hero and footer forms work independently

- [ ] **Step 5: Commit**

```bash
git add landing/index.html
git commit -m "feat: add subscribe footer and wire up form logic with localStorage"
```

---

## Chunk 4: Polish and Final Verification

### Task 8: Final polish — smooth scrolling, nav hint, and responsive QA

**Files:**
- Modify: `landing/index.html`

- [ ] **Step 1: Add smooth scroll CSS**

```css
html { scroll-behavior: smooth; }
```

- [ ] **Step 2: Add `prefers-reduced-motion` media query**

```css
@media (prefers-reduced-motion: reduce) {
  .terminal-line { animation: none; opacity: 1; }
  * { transition-duration: 0.01ms !important; }
  html { scroll-behavior: auto; }
}
```

- [ ] **Step 3: Add a minimal fixed nav bar**

> **Note:** This is a plan addition not in the original spec — improves UX by providing persistent navigation and a visible CTA.

A simple top bar with:
- "dopl" logo text (left, font-code, bold, accent-green)
- "Get Early Access" link (right, `href="#hero"`, scrolls to hero form)
- Background: --bg-primary with slight transparency + backdrop-blur
- Fixed position, z-index 100

```css
.nav {
  position: fixed; top: 0; left: 0; right: 0; z-index: 100;
  display: flex; justify-content: space-between; align-items: center;
  padding: 1rem 2rem;
  background: rgba(13, 17, 23, 0.8);
  backdrop-filter: blur(8px);
  border-bottom: 1px solid var(--border);
}
```

- [ ] **Step 4: Responsive QA pass**

Verify all breakpoints in browser DevTools:
- Desktop (1200px): 3-col benefits, side-by-side panels, tabs
- Tablet (800px): 2-col benefits, stacked panels
- Mobile (375px): 1-col everything, accordion for primitives, horizontal scroll on code

- [ ] **Step 5: Final commit**

```bash
git add landing/index.html
git commit -m "feat: add nav bar, smooth scrolling, reduced-motion, and responsive polish"
```

---

### Task 9: Verify and wrap up

- [ ] **Step 1: Start fresh server and full walkthrough**

```bash
cd landing && python -m http.server 8080
```

Open http://localhost:8080 and verify:
1. Page loads with correct title and favicon
2. Hero: headline, subtitle, form, terminal animation plays
3. Problem: two panels, correct colors and icons
4. Primitives: all 4 tabs switch, code displays correctly, keyboard nav works
5. Code example: syntax highlighted, scrollable on mobile
6. Benefits: 6 cards, hover effects, responsive grid
7. Subscribe: form works, all states (empty, invalid, success, duplicate)
8. Nav: fixed, scrolls to form on click
9. Responsive: test at 1200px, 800px, 375px

- [ ] **Step 2: Final commit if any touch-ups needed**

```bash
git add landing/index.html
git commit -m "fix: landing page touch-ups from final QA"
```
