# BURAQ design system

The interface is rendered by three files. Route handlers in `app/main.py` still
return body markup exactly as before — they were not rewritten.

| File | Role |
| --- | --- |
| `templates/base.html` | Page shell: head, icon sprite, sidebar, topbar, mobile tab bar |
| `static/css/app.css` | All styling. Tokens at the top, components below |
| `static/js/app.js` | Theme toggle, sidebar rail, mobile drawer, ribbon rendering |
| `app/ui.py` | Icon sprite, navigation model, Jinja2 renderer |

## Colour

Green no longer means "everything". It means **present**.

| Token | Light | Used for |
| --- | --- | --- |
| `--brand` | `#0B3B2E` | Sidebar and identity only |
| `--action` | `#12211C` | Primary buttons |
| `--present` | `#1C7C54` | Present, positive counts |
| `--late` | `#B4690E` | Late |
| `--absent` | `#B42318` | Absent |
| `--leave` | `#3D6DB5` | Approved leave |
| `--overtime` | `#6E5AAE` | Overtime |

Every token flips under `[data-theme="dark"]`. Never hardcode a hex in a route
handler — use the variable so dark mode keeps working.

Older variable names (`--panel`, `--line`, `--ok`, `--warn`, `--bad`, `--bg`)
are kept as aliases, so inline styles written before this change still resolve.

## Type

- Display (`--f-display`): Bricolage Grotesque — headings and numbers only
- Body (`--f-sans`): Geist, falling back to Inter and the system stack
- Data (`--f-mono`): Geist Mono — IDs, times, amounts
- Bengali: Hind Siliguri, already in the sans stack, so Bangla names render properly

Weights are 400, 500 and 600. Nothing heavier.

## Icons

`app/ui.py` holds 28 hand-drawn 24×24 stroke icons, emitted once as an SVG
sprite and referenced with `<use>`. In a route handler:

```python
body = f"<span class='qicon'>{ui.icon('banknote')}</span>"
```

Old decorative characters (`♙ ◷ ▣ 🔒 🧾` …) are swapped for real icons at
render time by `ui.iconify()`, so existing handlers did not need editing.
Characters that mean something as text — `৳`, `★`, `•`, `→` — are left alone.

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
`(key, label, url, icon, permission_flag)`. Permission-gated automatically, and
the first four visible items become the mobile tab bar.

## Cache busting

`ui.ASSET_VERSION` is set at import time and appended to the CSS and JS URLs, so
a redeploy always serves fresh assets.
