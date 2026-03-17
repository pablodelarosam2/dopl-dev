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

- **Headings:** System font stack (`-apple-system, BlinkMacSystemFont, 'Segoe UI', ...`), bold
- **Body:** Same system stack, regular weight
- **Code:** `'SF Mono', 'Fira Code', 'Cascadia Code', monospace`

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
  Animated with CSS keyframes (typewriter effect), no JS dependency.

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
- **Tabs:** `@sim_trace` | `sim_capture` | `sim_db` | `sim_http`
- **Each tab content:**
  - One-line description of the primitive
  - Small code snippet (3-6 lines) showing usage
  - Two-column "Record mode" vs "Replay mode" comparison
- **Default tab:** `@sim_trace` (the outermost primitive)
- **Implementation:** Pure CSS/JS tabs, no framework

### 4. Code Example

- **Full-width code block** with custom syntax highlighting (dark theme matching page)
- **Content:** The Flask demo `calculate_quote` function using all 4 primitives
- **Annotations:** Inline comments color-coded to show which parts record/replay
- **Syntax colors:**
  - Keywords: `#ff7b72`
  - Strings: `#a5d6ff`
  - Functions: `#d2a8ff`
  - Comments: `#8b949e`
  - Decorators: `#ffa657`

### 5. Benefits

- **Layout:** 3-column grid (2 rows = 6 items), responsive to 2-col on tablet, 1-col on mobile
- **Each item:** Icon (CSS/SVG) + title + one-line description
- **Items:**
  1. **Deterministic** — Same inputs, same outputs, every time
  2. **Framework-Agnostic** — Works with Flask, FastAPI, Django, or plain Python
  3. **Zero Overhead** — Off mode has zero performance impact in production
  4. **No Mocks Required** — Record real interactions, no manual stub writing
  5. **CI/CD Ready** — Runs in your pipeline, blocks regressions before merge
  6. **Protocol-Agnostic** — HTTP, gRPC, database, any side effect

### 6. Subscribe Footer

- **Background:** Slightly lighter (`#161b22`) to create visual separation
- **Heading:** "Stay in the loop"
- **Subtext:** "We'll notify you when dopl is ready for early access. No spam."
- **Form:** Email input + "Subscribe" button (same style as hero CTA)
- **Behavior:** Same localStorage handler as hero form; shows inline success message

## Subscribe Form Behavior

1. User enters email, clicks submit
2. Validate email format (basic regex)
3. Check localStorage for duplicate (prevent re-subscribing same email)
4. Store `{ email, timestamp }` in localStorage array key `dopl_subscribers`
5. Show inline success: "You're on the list! We'll be in touch."
6. If duplicate: "You're already subscribed!"
7. Both hero and footer forms share the same handler and localStorage key

## Responsive Behavior

- **Desktop (>1024px):** Full layout as described
- **Tablet (768-1024px):** Benefits grid → 2 columns, side-by-side panels stack vertically
- **Mobile (<768px):** Single column, tabs become vertical accordion, code block scrolls horizontally

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

## Out of Scope

- Backend email collection (swap localStorage for API call later)
- Analytics/tracking
- Multi-page navigation
- Blog/changelog
- Authentication
