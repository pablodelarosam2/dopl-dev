# dopl Landing Page — Design Spec

**Date:** 2026-03-17
**Status:** Draft

## Overview

A single-page informative landing page for the dopl project (Deterministic Operation Persistence Layer) with an email subscription form for early access updates. The page communicates what dopl does, the problem it solves, and how its primitives work — targeting Python microservices engineers.

## Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Visual style | Hybrid/Technical | Dark base + clean copy + code snippets. GitHub/Supabase aesthetic. Credible with engineers, approachable for decision-makers. |
| Layout | Interactive Showcase | Tabbed primitives, side-by-side comparisons, hover effects. "Show, don't tell" for a dev tool. |
| Tech stack | Single HTML file | One `index.html` in `landing/` folder. Zero dependencies. Run with `python -m http.server`. |
| Form backend | localStorage | Captures email to localStorage, shows success message. Easy to swap for a real backend later. |

## Color Palette

- **Background primary:** `#0d1117` (deep dark)
- **Background secondary:** `#161b22` (card/section backgrounds)
- **Border/subtle:** `#30363d`
- **Text primary:** `#c9d1d9`
- **Text secondary:** `#8b949e`
- **Accent green:** `#3fb950` (CTAs, success states)
- **Accent blue:** `#58a6ff` (links, highlights)
- **Accent red:** `#f78166` (problem/warning states)
- **Accent purple:** `#d2a8ff` (code/decorative)

## Typography

- **Headings:** `-apple-system, BlinkMacSystemFont, 'Segoe UI', 'Noto Sans', Helvetica, Arial, sans-serif`, bold
- **Body:** Same system stack, regular weight
- **Code:** `'SF Mono', 'Fira Code', 'Cascadia Code', 'Courier New', monospace`

## Head / Meta

- **Title:** `dopl — Catch the bugs your tests miss`
- **Meta description:** `dopl records real service interactions and replays them deterministically, catching "200 OK but wrong" regressions before production.`
- **Favicon:** Inline SVG favicon — a green circle with a "d" lettermark
- **Open Graph:** `og:title`, `og:description`, `og:type=website`

## Interaction States

- **Links:** `#58a6ff` default → `#79b8ff` on hover
- **Buttons:** Green accent darkens slightly on hover (`#2ea043`), focus ring `#58a6ff` with 2px offset
- **Tabs:** Active tab has bottom border accent, inactive tabs lighten on hover
- **Cards:** Subtle border glow on hover (`#30363d` → `#58a6ff`)

## Sections

### 1. Hero

- **Background:** Dark gradient (`#0d1117` → `#161b22`) with subtle CSS grid pattern overlay
- **Headline:** "Catch the bugs your tests miss" — large, white, bold
- **Subheadline:** "dopl records real service interactions and replays them deterministically — catching '200 OK but wrong' regressions before they reach production."
- **CTA:** Email input + "Get Early Access" button with green accent (`#3fb950`)
- **Below CTA:** Terminal-style animation showing:
  ```
  $ dopl record
  ▓▓▓▓▓▓▓▓▓▓ 47 fixtures captured
  $ dopl replay --pr 142
  ▓▓▓▓▓▓▓▓▓▓ 0 regressions found ✓
  ```
  Animated with CSS keyframes + minimal vanilla JS for sequential line timing. Each line fades/types in after the previous completes using staggered `animation-delay`.

### 2. Problem Statement

- **Layout:** Side-by-side comparison panels
- **Left panel** ("Without dopl"):
  - Red-tinted border/accent (`#f78166`)
  - Checklist: ✓ Unit tests pass → ✓ Integration tests pass → ✓ Deploy → ✗ Customer reports wrong prices
  - Communicates: traditional testing misses data-level regressions
- **Right panel** ("With dopl"):
  - Green-tinted border/accent (`#3fb950`)
  - Checklist: ✓ Record baseline → ✓ Replay against PR → ✓ Diff caught wrong prices → ✓ Blocked before merge
  - Communicates: dopl catches what tests miss, before production

### 3. How It Works — The 4 Primitives

- **Layout:** 4 tab buttons across the top, content panel below
- **Tabs:** `@sim_trace` | `sim_capture()` | `sim_db()` | `sim_http()`
- **Note:** `sim_trace` is a **decorator** (`@sim_trace`). The other three are **context managers** (`with sim_db(...) as sdb:`).
- **Default tab:** `@sim_trace` (the outermost primitive)
- **Implementation:** Vanilla JS tab switching, no framework

#### Tab: @sim_trace
- **Description:** "Wrap any function to record its inputs and outputs. On replay, the function body is skipped entirely."
- **Code:**
  ```python
  @sim_trace
  def calculate_quote(user_id, items):
      # In record: executes normally, captures result
      # In replay: returns recorded result, body never runs
      return {"total": 99.50, "tax": 8.25}
  ```
- **Record mode:** Executes function, captures `{inputs → output}` as a fixture
- **Replay mode:** Computes fingerprint from args, returns stored output without executing

#### Tab: sim_capture()
- **Description:** "Capture any local side effect — tax lookups, pricing engines, internal computations."
- **Code:**
  ```python
  with sim_capture("tax_service") as cap:
      if not cap.replaying:
          tax_rate = tax_api.lookup(user_id)
          cap.set_result({"rate": tax_rate})
      tax = cap.result["rate"]
  ```
- **Record mode:** Block executes, `set_result()` stores the value
- **Replay mode:** Block skipped, `cap.result` returns recorded value

#### Tab: sim_db()
- **Description:** "Wrap any database cursor to record queries and results. Writes are blocked in replay to prevent mutations."
- **Code:**
  ```python
  with sim_db(db, name="products") as sdb:
      rows = sdb.query("SELECT price FROM products WHERE sku = %s", [sku])
      # INSERT/UPDATE blocked in replay → SimWriteBlockedError
  ```
- **Record mode:** Queries execute normally, results captured
- **Replay mode:** Returns recorded rows; INSERT/UPDATE/DELETE raise `SimWriteBlockedError`

#### Tab: sim_http()
- **Description:** "Wrap any HTTP client to record requests and responses. No network calls in replay."
- **Code:**
  ```python
  with sim_http(http_client, name="shipping") as client:
      resp = client.get("https://api.shipping.co/v1/rate", params={"weight": 2.5})
      cost = resp.json()["cost"]
  ```
- **Record mode:** HTTP request executes, response captured
- **Replay mode:** Returns `FakeResponse` with recorded data, no network call

### 4. Code Example

- **Full-width code block** with custom syntax highlighting (dark theme matching page)
- **Content:** Simplified `calculate_quote` showing all 4 primitives in ~25 lines
- **Syntax colors:**
  - Keywords: `#ff7b72`
  - Strings: `#a5d6ff`
  - Functions: `#d2a8ff`
  - Comments: `#8b949e`
  - Decorators: `#ffa657`

**Exact code to display:**
```python
@sim_trace  # ← Record inputs/output, skip body on replay
def calculate_quote(user_id, items):
    # Database reads: recorded, writes: blocked in replay
    with sim_db(db, name="products") as sdb:
        products = sdb.query(
            "SELECT sku, price FROM products WHERE sku IN %s", [items]
        )

    # Capture any local side effect
    with sim_capture("tax_service") as cap:
        if not cap.replaying:
            cap.set_result({"rate": tax_api.lookup(user_id)})
        tax_rate = cap.result["rate"]

    # HTTP calls: real in record, fake response in replay
    with sim_http(shipping_client, name="shipping") as client:
        resp = client.get("/v1/rate", params={"weight": total_weight})
        shipping = resp.json()["cost"]

    subtotal = sum(p["price"] for p in products)
    return {
        "subtotal": subtotal,
        "tax": subtotal * tax_rate,
        "shipping": shipping,
        "total": subtotal * (1 + tax_rate) + shipping,
    }
```

### 5. Benefits

- **Layout:** 3-column grid (2 rows = 6 items), responsive to 2-col on tablet, 1-col on mobile
- **Each item:** Icon (CSS/SVG) + title + one-line description
- **Items:**
  1. **Deterministic** (icon: circular arrows) — Same inputs, same outputs, every time
  2. **Framework-Agnostic** (icon: puzzle piece) — Works with Flask, FastAPI, Django, or plain Python
  3. **Zero Overhead** (icon: lightning bolt) — Off mode has zero performance impact in production
  4. **No Mocks Required** (icon: record circle) — Record real interactions, no manual stub writing
  5. **CI/CD Ready** (icon: git branch) — Runs in your pipeline, blocks regressions before merge
  6. **Protocol-Agnostic** (icon: layers/stack) — HTTP, gRPC, database, any side effect

All icons are inline SVGs embedded directly in the HTML.

### 6. Subscribe Footer

- **Background:** Slightly lighter (`#161b22`) to create visual separation
- **Heading:** "Stay in the loop"
- **Subtext:** "We'll notify you when dopl is ready for early access. No spam."
- **Form:** Email input + "Subscribe" button (same style as hero CTA)
- **Behavior:** Same localStorage handler as hero form; shows inline success message

## Subscribe Form Behavior

1. User enters email, clicks submit
2. **Empty input:** Show "Please enter your email address" (red text below input)
3. **Invalid format:** Validate with basic regex; show "Please enter a valid email address"
4. Check localStorage for duplicate (prevent re-subscribing same email)
5. **Duplicate:** Show "You're already subscribed!"
6. Store `{ email, timestamp }` in localStorage array key `dopl_subscribers`
7. **Success:** Show "You're on the list! We'll be in touch." (green text)
8. **localStorage unavailable** (e.g., private browsing): Show success message anyway, log `console.warn("localStorage unavailable — email not persisted")`
9. Both hero and footer forms share the same handler and localStorage key

## Responsive Behavior

- **Desktop (>1024px):** Full layout as described
- **Tablet (768-1024px):** Benefits grid → 2 columns, side-by-side panels stack vertically
- **Mobile (<768px):** Single column, tabs become vertical accordion using `<details>`/`<summary>` elements (zero JS, native behavior, first item open by default), code block scrolls horizontally

## File Structure

```
landing/
  index.html    # Single file with embedded CSS and JS
```

## How to Run

```bash
cd landing && python -m http.server 8080
# Open http://localhost:8080
```

## Accessibility

- Primitives tabs use `role="tablist"`, `role="tab"`, `role="tabpanel"` with `aria-selected`
- Keyboard navigation: tabs are focusable and navigable with arrow keys
- Text secondary color bumped to `#9da5ae` for WCAG AA compliance on dark backgrounds
- Terminal animation has `aria-hidden="true"` with a visually-hidden text alternative
- All form inputs have associated `<label>` elements
- Focus rings visible on all interactive elements

## Out of Scope

- Backend email collection (swap localStorage for API call later)
- Analytics/tracking
- Multi-page navigation
- Blog/changelog
- Authentication
