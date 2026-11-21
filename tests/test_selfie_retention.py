"""Retention must not destroy historical photos or any business records."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, text

from app import database
from app.selfie_retention import (
    POLICY_KEY, expire_selfie_images, initialize_policy, preview_sql,
)


@pytest.fixture
def retention_db(monkeypatch, tmp_path):
    # Fully isolated DB: never touch the configured production/shared test DB.
    engine = create_engine(f"sqlite:///{tmp_path / 'retention.db'}")
    monkeypatch.setattr(database, 'engine', engine)
    monkeypatch.setattr(database, '_active_url', 'sqlite:///retention.db')
    monkeypatch.setattr(database, '_startup_error', '')
    database.init_db()
    with engine.begin() as c:
        c.execute(text("INSERT INTO employees(id,staff_id,name) VALUES(1,'RETENTION','Employee')"))
        c.execute(text("INSERT INTO attendance(employee_id,work_date,check_in) VALUES(1,'2026-01-01','08:30')"))
        c.execute(text("INSERT INTO face_profiles(employee_id,reference_media_id) VALUES(1,'registration-photo')"))
    yield engine
    engine.dispose()


def insert_photo(engine, hours, status='approved'):
    created = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime('%Y-%m-%d %H:%M:%S')
    with engine.begin() as c:
        return c.execute(text("""INSERT INTO attendance_fingerprints(
            employee_id,action,phash,ahash,dhash,embedding,image_data,
            decision,review_status,latitude,longitude,created_at)
            VALUES(1,'check_in','p','a','d','[1,2]','photo','accept',:status,25,89,:created)
            RETURNING id"""), {'status': status, 'created': created}).scalar_one()


def snapshot(engine):
    from sqlalchemy import inspect
    with engine.begin() as c:
        return {name: [dict(row) for row in c.execute(text(f'SELECT * FROM "{name}"')).mappings()]
                for name in inspect(engine).get_table_names()}


def test_expiry_only_changes_new_old_thumbnail_payloads(retention_db):
    engine = retention_db
    historical = insert_photo(engine, 100)
    initialize_policy()
    expired = [insert_photo(engine, 49, status) for status in ('pending','approved','rejected')]
    boundary = insert_photo(engine, 48)
    recent = insert_photo(engine, 47.9)
    before = snapshot(engine)
    assert expire_selfie_images() == 4
    after = snapshot(engine)
    for row in before['attendance_fingerprints']:
        if row['id'] in [*expired, boundary]:
            row['image_data'] = None
    # Checks EVERY field and EVERY table, not just attendance counts.
    assert after == before
    assert expire_selfie_images() == 0
    with engine.begin() as c:
        photos = dict(c.execute(text('SELECT id,image_data FROM attendance_fingerprints')).all())
    assert photos[historical] == photos[recent] == 'photo'


def test_restart_does_not_move_boundary_and_expired_preview_is_hidden(retention_db):
    engine = retention_db
    historical = insert_photo(engine, 100)
    initialize_policy()
    expired = insert_photo(engine, 49, 'pending')
    recent = insert_photo(engine, 1, 'pending')
    initialize_policy()  # restarting must not exempt newly expired pictures
    with engine.begin() as c:
        assert c.execute(text('SELECT value FROM system_settings WHERE key=:key'),
                         {'key': POLICY_KEY}).scalar_one() == str(historical)
        previews = {r['id']: r['image_data'] for r in c.execute(text(
            f'SELECT f.id,{preview_sql()} FROM attendance_fingerprints f')).mappings()}
    assert previews == {historical: 'photo', expired: None, recent: 'photo'}
    assert expire_selfie_images() == 1


def test_missing_policy_cannot_expire_anything(retention_db):
    engine = retention_db
    insert_photo(engine, 100)
    before = snapshot(engine)
    assert expire_selfie_images() == 0
    assert snapshot(engine) == before
