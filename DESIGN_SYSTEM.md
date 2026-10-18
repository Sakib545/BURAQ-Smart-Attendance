# BURAQ design system

The interface is rendered by a handful of files. Route handlers in
`app/main.py` still return body markup exactly as before — they were not
rewritten; `app/ui.py` post-processes what they emit.

| File | Role |
| --- | --- |
| `templates/base.html` | Page shell: head, icon sprite, sidebar, topbar, mobile tab bar |
| `static/css/app.css` | All styling. Tokens at the top, components below |
| `static/js/app.js` | Theme toggle, sidebar rail, mobile drawer, ribbon rendering |
| `tests/test_ui_shell.py` | Regressions for the two render passes and the nav model |
| `tests/test_dashboard_summary.py` | Regressions for the dashboard headline and its helpers |
| `app/ui.py` | Icon sprite, navigation model, Jinja2 renderer |

## Colour

Green no longer means "everything". It means **present**.

| Token | Light | Dark | Used for |
| --- | --- | --- | --- |
| `--brand` | `#0B3B2E` | `#0A2B22` | Sidebar and identity only |
| `--action` | `#12211C` | `#E8F0EC` | Primary buttons |
| `--present` | `#1C7C54` | `#34D399` | Present, positive counts |
| `--late` | `#B4690E` | `#FBBF24` | Late |
| `--absent` | `#B42318` | `#F87171` | Absent |
| `--leave` | `#3D6DB5` | `#60A5FA` | Approved leave |
| `--overtime` | `#6E5AAE` | `#A78BFA` | Overtime |
| `--state-ink` | `#FFFFFF` | `#0E1512` | Text on a solid state fill |

Every token flips under `[data-theme="dark"]` — including the five state
colours, which are lifted there (`--present` becomes `#34d399`, `--absent`
`#f87171`, and so on) so they stay legible on a dark ground. Never hardcode a
hex in a route handler — use the variable so dark mode keeps working. Text that
sits *on top of* a solid state fill uses `--state-ink`, which flips with them.

The theme is resolved by the inline script in `base.html`: a stored choice
first, otherwise the operating system's `prefers-color-scheme`. Once someone
uses the toggle their choice is stored and the OS no longer overrides it.

Older variable names (`--panel`, `--line`, `--ok`, `--warn`, `--bad`, `--bg`)
are kept as aliases, so inline styles written before this change still resolve.

## Type

- Display (`--f-display`): Bricolage Grotesque — headings and numbers only
- Body (`--f-sans`): Geist, falling back to Inter and the system stack
- Data (`--f-mono`): Geist Mono — IDs, times, amounts
- Bengali: Hind Siliguri, already in the sans stack, so Bangla names render properly

Weights are 400, 500 and 600. Nothing heavier, with one exception: the `.logo`
wordmark sets 700, which is why Bricolage is loaded at 500/600/700.

## Icons

`app/ui.py` holds 32 hand-drawn 24×24 stroke icons, emitted once as an SVG
sprite and referenced with `<use>`. In a route handler:

```python
body = f"<span class='qicon'>{ui.icon('banknote')}</span>"
```

Old decorative characters (`♙ ◷ ▣ 🔒 🧾` …) are swapped for real icons at
render time by `ui.iconify()`, so existing handlers did not need editing.
Characters that mean something as text — `৳`, `★`, `•`, `→` — are left alone,
as are the Bengali WhatsApp message strings in `services.py`, `whatsapp.py`,
`leave_flow.py` and `performance.py`, which are not HTML and never reach the
renderer.

`iconify()` rewrites **text content only**. It skips attribute values and the
bodies of `<script>` and `<style>`, because `app/main.py` ships
`message('⚠️ ' + error.message, 'error')` inside an inline script — a
document-wide substitution would splice an `<svg>` element into the middle of a
JavaScript string literal.

## Labels

Handlers write fields as `<label>Shift</label><select …>`: siblings, with no
`for=`. `ui.associate_labels()` wires each pair up at render time, generating
`fld-N` ids where a control has none, so clicking the label focuses the field
and screen readers announce it. A control left with only a `placeholder` gets
that promoted to `aria-label`; one that already has a visible label does not,
since `aria-label` would outrank it.

## Dashboard reading order

The page is built to be understood top to bottom, so nobody has to assemble
the answer from a grid of numbers:

1. **The headline band** states today in a sentence, in English and Bangla,
   with one big turnout figure and a status word (On track / Needs attention /
   Action needed). Turnout is measured against the people *expected in* —
   approved leave is excluded, because someone on sanctioned leave is not a
   shortfall. The band's left edge takes the colour of that state.
2. **Three primary tiles** — Present, Absent, On leave — which account for
   every active employee exactly once, so they reconcile with the donut.
   Late arrivals are a subset of Present and sit *inside* that tile as a
   qualifier; as a fourth tile of equal weight they read as a separate group
   and the counts appeared not to add up.
3. **Three supporting tiles**, visibly lighter: checked out, selfies to
   review, spoof attempts.
4. Then the trends, the live table, and what is waiting for a decision.

Counts carry a `_delta()` chip comparing them with the same figure a period
earlier, since a bare number says nothing about whether today is normal. Use
it only where an increase is good — the chip colours a rise green.

`_bn()` renders Bengali numerals for the Bangla line. Keep it to prose; any
figure somebody has to read back, copy or reconcile stays in ASCII digits.

## Attendance ribbon

The signature component. A 30-day strip that shows a person's pattern at a
glance, straight from a table row:

```html
<span class="ribbon" data-days="pppplppaappvpppp..."></span>
```

`p` present · `l` late · `a` absent · `v` leave · `.` no record.
`app.js` expands it on load.

## Adding a nav item

Edit `NAV_BLUEPRINT` in `app/ui.py`. Each entry is
`(key, label, url, icon, permission_flag)`. Permission-gated automatically.

The mobile tab bar takes the first three visible items plus whichever entry
matches the current page, so every page highlights its own tab instead of
leaving the bar blank. A route whose `active` key is not a nav key needs an
entry in `ACTIVE_ALIASES` — map it to its parent, or to `""` for a hub page
that deliberately highlights nothing. `tests/test_ui_shell.py` fails if a
handler passes a key the nav model does not know.

## Cache busting

`ui.ASSET_VERSION` is set at import time and appended to the CSS and JS URLs, so
a redeploy always serves fresh assets.
