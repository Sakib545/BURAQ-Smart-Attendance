"""The dashboard headline and its supporting helpers.

The page answers "is today all right?" in a sentence before it shows any
figure, so the sentence has to stay grammatical and the comparison chips have
to point the right way at every count, including the empty ones.
"""

import base64
import json
import os
import re

import pytest
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner

from app.main import _bn, _delta, _pct, app


def _client():
    client = TestClient(app)
    client.cookies.set("session", TimestampSigner(os.environ["SESSION_SECRET"]).sign(
        base64.b64encode(json.dumps(
            {"role": "super_admin", "user_name": "Tester", "admin": True}).encode())).decode())
    return client


# --- numerals -------------------------------------------------------------

def test_bengali_numerals():
    assert _bn(0) == "০"
    assert _bn(1234567890) == "১২৩৪৫৬৭৮৯০"


def test_bengali_conversion_leaves_other_characters_alone():
    assert _bn("23 জন") == "২৩ জন"


def test_bengali_numerals_round_trip_through_the_parser_used_elsewhere():
    """leave_flow.py reads Bengali digits back; the two must agree."""
    from app.leave_flow import _DIGITS
    assert _bn(2026).translate(_DIGITS) == "2026"


# --- comparison chips -----------------------------------------------------

def test_delta_without_a_baseline_renders_nothing():
    assert _delta(5, None) == ""


def test_delta_up_and_down_carry_opposite_classes():
    assert "delta-up" in _delta(12, 9)
    assert "delta-down" in _delta(9, 12)


def test_delta_reports_the_absolute_size_of_the_change():
    assert "3 vs yesterday" in _delta(9, 12)
    assert "-" not in re.sub(r"<[^>]+>", "", _delta(9, 12))


def test_delta_flat_when_unchanged():
    assert "delta-flat" in _delta(7, 7)


def test_delta_from_zero_baseline_is_still_a_rise():
    assert "delta-up" in _delta(4, 0)


# --- percentages ----------------------------------------------------------

def test_pct_guards_a_zero_denominator():
    assert _pct(5, 0) == 0.0


def test_pct_is_clamped():
    assert _pct(30, 10) == 100.0
    assert _pct(-5, 10) == 0.0


# --- the rendered headline ------------------------------------------------

def _headline(html):
    match = re.search(r"<p class='today-line'>(.*?)</p>", html, re.S)
    assert match, "dashboard has no headline"
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", match.group(1))).strip()


def test_dashboard_renders_with_no_active_staff():
    """No staff must not divide by zero or leave a dangling clause.

    The deactivation happens after the client starts, because app startup
    seeds employees.csv into an empty table.
    """
    from app.database import get_db
    with _client() as client:
        with get_db() as db:
            db.execute("UPDATE employees SET is_active=0 WHERE is_active")
        try:
            response = client.get("/dashboard")
            assert response.status_code == 200
            assert _headline(response.text) == "Nobody is expected in today."
        finally:
            with get_db() as db:
                db.execute("UPDATE employees SET is_active=1")


def test_everyone_absent_still_reads_as_a_sentence():
    """Nobody checked in is the state this page is most likely to be opened
    in first thing in the morning."""
    with _client() as client:
        line = _headline(client.get("/dashboard").text)
    assert re.match(r"^\d+ of the \d+ (person|people) expected in today (is|are) here\.", line)


def test_headline_states_turnout_and_a_status_word():
    with _client() as client:
        html = client.get("/dashboard").text
    assert re.search(r"<div class='today-pct'>\d+<span>%</span></div>", html)
    assert re.search(r"today-band card state-(present|late|absent|leave)", html)


def test_headline_sentence_has_no_double_spaces_or_empty_clauses():
    with _client() as client:
        line = _headline(client.get("/dashboard").text)
    assert "  " not in line
    assert ".." not in line
    assert " ," not in line and " ." not in line
    assert line.endswith(".")


def test_bengali_line_is_marked_as_bengali_for_screen_readers():
    with _client() as client:
        html = client.get("/dashboard").text
    assert "<p class='today-bn' lang='bn'>" in html


@pytest.mark.parametrize("tone", ["present", "absent", "leave"])
def test_the_three_primary_tiles_are_present_and_toned(tone):
    """Present + Absent + On leave account for every active employee once."""
    with _client() as client:
        html = client.get("/dashboard").text
    assert f"kpi-symbol kpi-{tone}" in html
    assert f"mini-{tone}" in html


def test_late_is_a_qualifier_on_present_not_a_tile_of_its_own():
    """Late arrivals are a subset of Present. As an equal fourth tile they
    read as a separate group of people and the counts appeared not to add up."""
    with _client() as client:
        html = client.get("/dashboard").text
    assert "Late arrivals" not in html
    assert "kpi-note" in html
