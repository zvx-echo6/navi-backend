"""Tests for navi-contacts /api/contacts/* — the first tests this code has ever had.

Each test gets a fresh on-disk SQLite DB under tmp_path (auto-created by
ContactsDB on first open). The fixture also closes the module-level
thread-local connection so a previous test's DB handle can't leak in.
"""
import pytest

import services.navi_contacts.contacts_db as cdb
from services.navi_contacts.app import create_app

AUTH = {'X-Authentik-Username': 'alice'}
AUTH_B = {'X-Authentik-Username': 'bob'}


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_file = tmp_path / 'contacts.db'
    monkeypatch.setenv('NAVI_CONTACTS_DB', str(db_file))
    # Reset the thread-local connection so we don't reuse a prior test's DB.
    conn = getattr(cdb._local, 'contacts_conn', None)
    if conn is not None:
        conn.close()
    cdb._local.contacts_conn = None
    app = create_app()
    c = app.test_client()
    c._db_file = db_file  # for the auto-create assertion
    return c


def _mk(label='Friend', **extra):
    body = {'label': label, 'name': 'Test', 'phone': '555'}
    body.update(extra)
    return body


# ── auth + auto-create ──

def test_auth_required(client):
    assert client.get('/api/contacts').status_code == 401


def test_autocreate_on_fresh_db(client):
    # DB file does not exist until the first request touches ContactsDB.
    assert not client._db_file.exists()
    resp = client.get('/api/contacts', headers=AUTH)
    assert resp.status_code == 200
    assert resp.get_json() == []
    assert client._db_file.exists()  # schema auto-created


# ── CRUD ──

def test_create_and_list(client):
    r = client.post('/api/contacts', json=_mk(), headers=AUTH)
    assert r.status_code == 201
    c = r.get_json()
    assert c['id'] and c['user_id'] == 'alice' and c['label'] == 'Friend'
    lst = client.get('/api/contacts', headers=AUTH).get_json()
    assert len(lst) == 1 and lst[0]['id'] == c['id']


def test_get_by_id_and_404(client):
    cid = client.post('/api/contacts', json=_mk(), headers=AUTH).get_json()['id']
    assert client.get(f'/api/contacts/{cid}', headers=AUTH).status_code == 200
    assert client.get('/api/contacts/99999', headers=AUTH).status_code == 404


def test_update_and_404(client):
    cid = client.post('/api/contacts', json=_mk(), headers=AUTH).get_json()['id']
    r = client.patch(f'/api/contacts/{cid}', json={'name': 'Renamed'}, headers=AUTH)
    assert r.status_code == 200 and r.get_json()['name'] == 'Renamed'
    assert client.patch('/api/contacts/99999', json={'name': 'x'}, headers=AUTH).status_code == 404


# ── soft delete / restore / purge ──

def test_soft_delete_hides_from_list(client):
    cid = client.post('/api/contacts', json=_mk(), headers=AUTH).get_json()['id']
    d = client.delete(f'/api/contacts/{cid}', headers=AUTH)
    assert d.status_code == 200 and d.get_json()['deleted_at']
    assert client.get('/api/contacts', headers=AUTH).get_json() == []
    deleted = client.get('/api/contacts/deleted', headers=AUTH).get_json()
    assert len(deleted) == 1 and deleted[0]['id'] == cid


def test_restore(client):
    cid = client.post('/api/contacts', json=_mk(), headers=AUTH).get_json()['id']
    client.delete(f'/api/contacts/{cid}', headers=AUTH)
    r = client.post(f'/api/contacts/{cid}/restore', headers=AUTH)
    assert r.status_code == 200 and r.get_json()['deleted_at'] is None
    assert len(client.get('/api/contacts', headers=AUTH).get_json()) == 1


def test_purge_requires_deleted_then_removes(client):
    cid = client.post('/api/contacts', json=_mk(), headers=AUTH).get_json()['id']
    # live contact can't be purged
    assert client.delete(f'/api/contacts/{cid}/purge', headers=AUTH).status_code == 400
    client.delete(f'/api/contacts/{cid}', headers=AUTH)  # soft delete
    assert client.delete(f'/api/contacts/{cid}/purge', headers=AUTH).status_code == 200
    # gone for good
    assert client.get('/api/contacts/deleted', headers=AUTH).get_json() == []


# ── Home/Work uniqueness (409) ──

def test_home_work_conflict_on_create(client):
    assert client.post('/api/contacts', json=_mk(label='Home'), headers=AUTH).status_code == 201
    assert client.post('/api/contacts', json=_mk(label='Home'), headers=AUTH).status_code == 409


def test_restore_conflict_when_label_taken(client):
    h1 = client.post('/api/contacts', json=_mk(label='Home'), headers=AUTH).get_json()['id']
    client.delete(f'/api/contacts/{h1}', headers=AUTH)          # soft-delete the old Home
    client.post('/api/contacts', json=_mk(label='Home'), headers=AUTH)  # new Home takes the slot
    assert client.post(f'/api/contacts/{h1}/restore', headers=AUTH).status_code == 409


def test_restore_as_relabels(client):
    h1 = client.post('/api/contacts', json=_mk(label='Home'), headers=AUTH).get_json()['id']
    client.delete(f'/api/contacts/{h1}', headers=AUTH)
    r = client.post(f'/api/contacts/{h1}/restore-as', json={'label': 'Cabin'}, headers=AUTH)
    assert r.status_code == 200 and r.get_json()['label'] == 'Cabin'
    # updated_at must be a well-formed ISO-8601 with seconds (regression guard:
    # strftime %f is microseconds-only, so it must be preceded by %S.).
    from datetime import datetime
    ts = r.get_json()['updated_at']
    datetime.strptime(ts, '%Y-%m-%dT%H:%M:%S.%fZ')  # raises if seconds missing


def test_restore_as_requires_label(client):
    h1 = client.post('/api/contacts', json=_mk(label='Home'), headers=AUTH).get_json()['id']
    client.delete(f'/api/contacts/{h1}', headers=AUTH)
    assert client.post(f'/api/contacts/{h1}/restore-as', json={'label': '  '}, headers=AUTH).status_code == 400


# ── nearby ──

def test_nearby_returns_distance(client):
    client.post('/api/contacts', json=_mk(label='Spot', lat=42.5736, lon=-114.6066,
                                          show_proximity=True), headers=AUTH)
    r = client.get('/api/contacts/nearby?lat=42.5736&lon=-114.6066&radius_m=100', headers=AUTH)
    assert r.status_code == 200
    rows = r.get_json()
    assert len(rows) == 1 and 'distance_m' in rows[0]


def test_nearby_excludes_far_and_non_proximity(client):
    # show_proximity off → excluded
    client.post('/api/contacts', json=_mk(label='Hidden', lat=42.5736, lon=-114.6066,
                                          show_proximity=False), headers=AUTH)
    r = client.get('/api/contacts/nearby?lat=42.5736&lon=-114.6066&radius_m=100', headers=AUTH)
    assert r.get_json() == []


def test_nearby_requires_latlon(client):
    assert client.get('/api/contacts/nearby', headers=AUTH).status_code == 400


# ── search/category filter + user partitioning ──

def test_search_and_category_filter(client):
    client.post('/api/contacts', json=_mk(label='Alpha', category='friends'), headers=AUTH)
    client.post('/api/contacts', json=_mk(label='Beta', category='work'), headers=AUTH)
    assert len(client.get('/api/contacts?category=work', headers=AUTH).get_json()) == 1
    assert len(client.get('/api/contacts?search=Alph', headers=AUTH).get_json()) == 1


def test_user_partitioning(client):
    client.post('/api/contacts', json=_mk(), headers=AUTH)        # alice
    assert client.get('/api/contacts', headers=AUTH_B).get_json() == []  # bob sees nothing
    assert len(client.get('/api/contacts', headers=AUTH).get_json()) == 1
