"""Presentation-layer regressions for app/ui.py.

These cover the shell behaviour that route handlers rely on but never assert:
glyph substitution staying out of scripts and attributes, label association,
and the mobile tab bar keeping the current page reachable.
"""

import re

from app import ui


# --- iconify --------------------------------------------------------------

def test_iconify_converts_glyphs_in_text():
    out = ui.iconify("<div>\U0001f512 Locked</div>")
    assert "i-lock" in out
    assert "\U0001f512" not in out


def test_iconify_leaves_script_bodies_alone():
    """A glyph inside inline JavaScript must not become an <svg> element.

    app/main.py ships `message('⚠️ ' + error.message, 'error')` in an
    inline script, so a blind document-wide substitution would splice markup
    into the middle of a JavaScript string literal and break the page.
    """
    html = "<script>var s = '\U0001f512'; alert('\U0001f512');</script>"
    assert ui.iconify(html) == html


def test_iconify_leaves_attribute_values_alone():
    html = "<div title=\"a \U0001f512 b\" data-x='\U0001f512'>plain</div>"
    assert ui.iconify(html) == html


def test_iconify_survives_a_gt_inside_an_attribute():
    """`onsubmit="return a > b"` must not be mistaken for the end of the tag."""
    html = "<form onsubmit=\"return 2 > 1\"><b>\U0001f512</b></form>"
    out = ui.iconify(html)
    assert 'onsubmit="return 2 > 1"' in out
    assert "i-lock" in out


def test_every_mapped_glyph_resolves_to_a_real_sprite_icon():
    for glyph, name in ui.GLYPH_ICONS.items():
        assert name in ui.ICON_PATHS, f"{glyph!r} maps to missing icon {name!r}"


def test_sprite_defines_every_icon():
    markup = str(ui.sprite())
    for name in ui.ICON_PATHS:
        assert f'id="i-{name}"' in markup


# --- label association ----------------------------------------------------

def test_sibling_label_is_wired_to_its_control():
    out = ui.associate_labels("<div><label>Shift</label><select name='shift'></select></div>")
    control_id = re.search(r'<label for="([^"]+)"', out).group(1)
    assert f'<select id="{control_id}"' in out


def test_existing_control_id_is_reused_not_replaced():
    out = ui.associate_labels("<label>A</label><input id='keepme' name='a'>")
    assert 'for="keepme"' in out
    assert out.count("keepme") == 2


def test_label_that_already_wraps_its_control_is_untouched():
    html = "<label class='checkbox'><input type='checkbox' name='x'> Night duty</label>"
    assert ui.associate_labels(html) == html


def test_label_with_an_explicit_for_is_untouched():
    html = "<label for='mine'>A</label><input id='mine' name='a'>"
    assert ui.associate_labels(html) == html


def test_generated_ids_do_not_collide_with_existing_ones():
    html = "<div id='fld-1'></div><label>A</label><input name='a'>"
    out = ui.associate_labels(html)
    assert 'for="fld-1"' not in out
    assert len(set(re.findall(r'id="(fld-\d+)"', out))) == 1


def test_placeholder_becomes_an_accessible_name_when_there_is_no_label():
    out = ui.associate_labels("<input name='q' placeholder='Name or Staff ID'>")
    assert 'aria-label="Name or Staff ID"' in out


def test_a_visible_label_outranks_the_placeholder():
    """aria-label would win over the <label>, so the field would be announced
    by its hint instead of its name. The label has to stay authoritative."""
    out = ui.associate_labels(
        "<label>Global search</label><input name='q' placeholder='Name, Staff ID, phone'>")
    assert "aria-label" not in out
    assert 'for="fld-1"' in out


def test_existing_aria_label_is_not_overwritten():
    html = "<input aria-label='Tracker link' placeholder='Hint' readonly value='x'>"
    assert ui.associate_labels(html) == html


def test_blank_placeholder_is_not_promoted():
    html = '<input name="a" placeholder="   ">'
    assert ui.associate_labels(html) == html


# --- navigation -----------------------------------------------------------

def _groups():
    perm = lambda flag: True  # noqa: E731 - every entry visible
    return ui.build_nav(perm)


def test_tabbar_holds_four_entries():
    assert len(ui.build_tabbar(_groups(), "dashboard")) == 4


def test_tabbar_always_contains_the_active_page():
    """`flat[:4]` left 13 of 23 pages with no active tab, so the mobile bar
    gave no indication of where you were."""
    groups = _groups()
    for group in groups:
        for item in group["entries"]:
            keys = [tab["key"] for tab in ui.build_tabbar(groups, item["key"])]
            assert item["key"] in keys, f"{item['key']} unreachable from the tab bar"


def test_tabbar_keeps_its_first_three_entries_stable():
    groups = _groups()
    head = [tab["key"] for tab in ui.build_tabbar(groups, "dashboard")][:3]
    for key in ("payroll", "settings", "reports"):
        assert [tab["key"] for tab in ui.build_tabbar(groups, key)][:3] == head


def test_unknown_active_key_does_not_disturb_the_tabbar():
    groups = _groups()
    assert ([t["key"] for t in ui.build_tabbar(groups, "nonexistent")]
            == [t["key"] for t in ui.build_tabbar(groups, "")])


# --- renderer -------------------------------------------------------------

def test_render_page_applies_both_passes():
    html = ui.render_page(
        title="Reports",
        body="<div><label>Month</label><input name='month'></div><p>\U0001f512 Secret</p>",
        chrome=True,
        nav_groups=_groups(),
        active="reports",
        user_name="Audit Admin",
        role_label="Super admin",
        today_line="Saturday",
    )
    assert "i-lock" in html                      # glyph became an icon
    assert re.search(r'<label for="fld-\d+">Month</label>', html)
    assert 'id="content"' in html                # skip-link target
    assert 'aria-expanded="false"' in html       # drawer state is declared
    assert "AA" in html                          # initials rendered


def test_every_active_key_handlers_pass_is_known_to_the_nav_model():
    """A key that matches no nav entry and no alias leaves the sidebar and the
    mobile tab bar with nothing highlighted, so the page shows no "you are
    here". Hub pages opt out explicitly by mapping to "".
    """
    import re
    from pathlib import Path

    nav = {key for _, entries in ui.NAV_BLUEPRINT for key, *_ in entries}
    known = nav | set(ui.ACTIVE_ALIASES)
    sources = ["app/main.py", "app/employee_portal.py", "app/leave_flow.py",
               "app/special_duty_ui.py", "app/web_checkin.py", "app/salary_sheet.py",
               "app/ai_insights.py", "app/browsing.py"]
    root = Path(__file__).resolve().parents[1]
    used = set()
    for name in sources:
        text = (root / name).read_text()
        used |= set(re.findall(r'layout\([^)]*?request,\s*["\']([a-z0-9_-]+)["\']\s*\)', text))
    unknown = {key for key in used if key and key not in known}
    assert not unknown, f"active keys unknown to the nav model: {sorted(unknown)}"
