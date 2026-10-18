"""Render a standalone HTML preview of the interface.

    python scripts/build_preview.py [output.html]

Produces a single self-contained file with the stylesheet and script inlined,
so the design can be reviewed in a browser without running the server or
touching the database. Uses sample data only.
"""

from __future__ import annotations

import random
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import ui  # noqa: E402

SAMPLE_PEOPLE = [
    ("Rakib Hasan", "BQ-0142", "Production", "present", "08:52", "17:40"),
    ("Nusrat Jahan", "BQ-0088", "Accounts", "late", "09:34", "17:35"),
    ("Md Shahin Alam", "BQ-0203", "Security", "present", "07:58", "—"),
    ("Farhana Akter", "BQ-0117", "Admin", "leave", "—", "—"),
    ("Jahidul Islam", "BQ-0064", "Logistics", "absent", "—", "—"),
]


def ribbon(rng: random.Random) -> str:
    marks = ""
    for _ in range(30):
        roll = rng.random()
        state = "present" if roll < .74 else "late" if roll < .86 else "absent" if roll < .95 else "leave"
        marks += f'<i data-s="{state}"></i>'
    return f"<span class='ribbon'>{marks}</span>"


def sample_body() -> str:
    rng = random.Random(7)

    rows = "".join(
        f"<tr><td><div class='kpi-row'><span class='avatar'>{name.split()[0][0]}{name.split()[1][0]}</span>"
        f"<span><b>{name}</b><div class='sub'>{staff_id} · {dept}</div></span></div></td>"
        f"<td><span class='status-badge status-{state}'>{state.title()}</span></td>"
        f"<td>{cin}</td><td>{cout}</td><td>{ribbon(rng)}</td></tr>"
        for name, staff_id, dept, state, cin, cout in SAMPLE_PEOPLE
    )

    week = [("Sun", 142), ("Mon", 151), ("Tue", 138), ("Wed", 147),
            ("Thu", 150), ("Fri", 12), ("Sat", 144)]
    peak = max(v for _, v in week)
    bars = ""
    for day, value in week:
        today = " is-today" if day == "Sat" else ""
        bars += (f"<div class='bar-wrap'><div class='bar{today}' "
                 f"style='height:{max(4.0, value / peak * 100):.1f}%' "
                 f"title='{day}: {value} present'>"
                 f"<span class='bar-value'>{value}</span></div>"
                 f"<div class='bar-label'>{day}</div></div>")

    # Tone names and icons match app/main.py's _kpi_card, so the preview and
    # the real dashboard cannot drift apart again.
    kpis = [
        ("present", "check", "Present today", "144", "92% of workforce", 92.0),
        ("late", "clock", "Late arrivals", "7", "Needs attention", 12.0),
        ("absent", "x", "Absent", "5", "3.2% of workforce", 8.0),
        ("leave", "calendar-minus", "On approved leave", "8", "5.1% of workforce", 14.0),
        ("overtime", "trending-up", "Checked out today", "96", "61% of those present", 61.0),
    ]
    kpi_html = ""
    for tone, icon_name, label, value, foot, pct in kpis:
        kpi_html += (
            f"<div class='card dashboard-kpi'><div class='kpi-row'>"
            f"<span class='kpi-symbol kpi-{tone}'>{ui.icon(icon_name)}</span>"
            f"<div class='kpi-text'><div class='metric-label'>{label}</div>"
            f"<div class='metric'>{value}</div></div></div>"
            f"<div class='metric-foot'>{foot}</div>"
            f"<div class='mini-line'><span class='mini-{tone}' style='width:{pct}%'></span></div>"
            f"</div>")

    # A three-segment breakdown of one total, like the app. The old preview
    # drew a single-value ring beside four legend rows that did not sum to it.
    staff = 157
    seg = [("present", "Present", 144), ("leave", "On leave", 8), ("absent", "Absent", 5)]
    stop_1 = seg[0][2] / staff * 100
    stop_2 = (seg[0][2] + seg[1][2]) / staff * 100
    legend = "".join(
        f"<div class='legend-row'><span class='legend-dot legend-{key}'></span>"
        f"<span>{label}</span><b>{count}</b>"
        f"<span class='legend-pct'>{count / staff * 100:.1f}%</span></div>"
        for key, label, count in seg
    )

    quick = "".join(
        f"<a href='#'><span class='qicon'>{glyph}</span><span>{label}</span></a>"
        for glyph, label in [("♙", "Add employee"), ("◎", "Mark attendance"), ("▣", "Assign duty"),
                             ("☂", "Add leave"), (str(ui.icon("banknote")), "Run payroll"), ("◔", "View reports")]
    )

    return f"""
<div class='dashboard-head'>
  <div><h1>Welcome back, Rakib</h1><div class='dashboard-date'>▣ Saturday · 01 August 2026</div></div>
  <div class='dashboard-tools'><input class='dashboard-search' type='search' placeholder='Search employees, IDs, departments'><button class='btn secondary'>⌕</button></div>
</div>
<div class='dashboard-kpis'>{kpi_html}</div>
<div class='section-gap'></div>
<div class='dashboard-main-grid'>
  <div class='card dashboard-panel'><div class='card-head'><h3>7-day attendance trend</h3><a class='btn secondary' href='#'>View report</a></div><div class='chart'>{bars}</div></div>
  <div class='card dashboard-panel'><div class='card-head'><h3>Workforce readiness</h3></div>
    <div class='readiness-wrap'><div class='donut donut-stack' style='--stop1:{stop_1:.1f};--stop2:{stop_2:.1f}'><div class='donut-value'><b>92%</b><span class='sub'>Today</span></div></div>
    <div class='legend-list'>{legend}</div></div></div>
</div>
<div class='section-gap'></div>
<div class='card'><div class='card-head'><h3>Live attendance</h3><span class='pill'>157 employees</span></div>
  <div class='table-scroll'><table class='dashboard-table'><thead><tr><th>Employee</th><th>Status</th><th>Check in</th><th>Check out</th><th>Last 30 days</th></tr></thead><tbody>{rows}</tbody></table></div></div>
<div class='section-gap'></div>
<h3 style='margin:2px 0 12px'>Quick actions</h3>
<div class='dashboard-quick'>{quick}</div>
"""


def build(destination: Path) -> None:
    html = ui.render_page(
        title="Dashboard",
        body=sample_body(),
        chrome=True,
        nav_groups=ui.build_nav(lambda flag: True),
        active="dashboard",
        user_name="Rakib Hasan",
        role_label="HR Manager",
        today_line="Sat 01 Aug, 07:47 PM",
    )
    css = (ROOT / "static/css/app.css").read_text(encoding="utf-8")
    js = (ROOT / "static/js/app.js").read_text(encoding="utf-8")
    html = re.sub(r'<link rel="stylesheet" href="/static/css/app\.css[^"]*">', f"<style>{css}</style>", html)
    html = re.sub(r'<script src="/static/js/app\.js[^"]*" defer></script>', f"<script>{js}</script>", html)
    destination.write_text(html, encoding="utf-8")
    print(f"wrote {destination} ({len(html):,} bytes)")


if __name__ == "__main__":
    build(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "ui-preview.html")
