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

    bars = ""
    for day, value in zip(["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"], [142, 151, 138, 147, 150, 12, 144]):
        height = max(6, int(value / 151 * 150))
        bars += (f"<div class='bar-wrap'><div class='bar' style='height:{height}px'>"
                 f"<span class='bar-value'>{value}</span></div><div class='bar-label'>{day}</div></div>")

    kpis = [
        ("kpi-green", "♙", "Present today", "144", "92% of workforce", 92, "var(--present)"),
        ("kpi-orange", "◷", "Late today", "7", "Needs attention", 12, "var(--late)"),
        ("kpi-red", "♙", "Absent", "5", "3.2% of workforce", 8, "var(--absent)"),
        ("kpi-blue", "☂", "On leave", "8", "5.1% of workforce", 14, "var(--leave)"),
        ("kpi-purple", "◷", "Overtime today", "320m", "96 check-outs", 0, "var(--overtime)"),
    ]
    kpi_html = ""
    for cls, glyph, label, value, foot, pct, colour in kpis:
        line = f"<div class='mini-line'><span style='width:{pct}%;background:{colour}'></span></div>" if pct else ""
        kpi_html += (f"<div class='card dashboard-kpi'><div class='kpi-row'><span class='kpi-symbol {cls}'>{glyph}</span>"
                     f"<div><div class='metric-label'>{label}</div><div class='metric'>{value}</div></div></div>"
                     f"<div class='metric-foot'>{foot}</div>{line}</div>")

    legend = "".join(
        f"<div class='legend-row'><span class='legend-dot' style='background:var(--{key})'></span>"
        f"<span>{label}</span><b>{count}</b></div>"
        for key, label, count in [("present", "Present", 144), ("late", "Late", 7),
                                  ("absent", "Absent", 5), ("leave", "On leave", 8)]
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
    <div class='readiness-wrap'><div class='donut' style='--pct:92'><div class='donut-value'><b>92%</b><span class='sub'>Today</span></div></div>
    <div class='legend-list'>{legend}</div></div></div>
</div>
<div class='section-gap'></div>
<div class='card'><div class='card-head'><h3>Live attendance</h3><span class='pill'>157 employees</span></div>
  <div style='overflow:auto'><table><thead><tr><th>Employee</th><th>Status</th><th>Check in</th><th>Check out</th><th>Last 30 days</th></tr></thead><tbody>{rows}</tbody></table></div></div>
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
