"""Tests for navi-landclass /api/landclass.

The PostGIS layer is mocked at the db module's `_query_all` / `_get_pool`
boundary (these are resolved at call time inside lookup_landclass, so patching
the module attribute works regardless of how the route imports the function).
Ports recon's 2 cases (Yosemite match / ocean miss) and adds input validation
(400) and graceful PG-down (200 empty) coverage.
"""
import pytest

import services.navi_landclass.db as db
from services.navi_landclass.app import create_app

# A canned pad_units row (column names as ogr2ogr lowercases them).
YOSEMITE_ROW = {
    'unit_nm': 'Yosemite National Park',
    'mang_name': 'NPS',
    'mang_type': 'FED',
    'own_name': 'NPS',
    'own_type': 'FED',
    'des_tp': 'NP',
    'gap_sts': '1',
    'pub_access': 'OA',
    'category': 'Fee',
    'gis_acres': 761747.5,
    'state_nm': 'CA',
}


@pytest.fixture
def client():
    return create_app().test_client()


# ── lookup behavior (Yosemite / ocean), via the route ──

def test_point_with_coverage_returns_classification(client, monkeypatch):
    monkeypatch.setattr(db, '_query_all', lambda sql, params: [YOSEMITE_ROW])
    resp = client.get('/api/landclass?lat=37.85&lon=-119.55')
    assert resp.status_code == 200
    d = resp.get_json()
    assert d['count'] == 1 and d['is_public'] is True and d['is_private'] is False
    c = d['classifications'][0]
    assert c['unit_name'] == 'Yosemite National Park'
    assert d['summary'] == 'Yosemite National Park'


def test_decode_maps_codes_to_labels(client, monkeypatch):
    monkeypatch.setattr(db, '_query_all', lambda sql, params: [YOSEMITE_ROW])
    c = client.get('/api/landclass?lat=37.85&lon=-119.55').get_json()['classifications'][0]
    assert c['manager_name'] == 'National Park Service'   # NPS
    assert c['manager_type'] == 'Federal'                 # FED
    assert c['designation_type'] == 'National Park'       # NP
    assert c['public_access'] == 'Open Access'            # OA
    assert c['state'] == 'California'                      # CA


def test_ocean_point_returns_empty(client, monkeypatch):
    monkeypatch.setattr(db, '_query_all', lambda sql, params: [])
    resp = client.get('/api/landclass?lat=0&lon=-150')
    assert resp.status_code == 200
    d = resp.get_json()
    assert d['count'] == 0 and d['is_public'] is False and d['is_private'] is True
    assert d['classifications'] == [] and d['summary'] is None


# ── input validation (no DB needed) ──

def test_bad_latlon_returns_400(client):
    assert client.get('/api/landclass?lat=abc&lon=-119').status_code == 400


def test_missing_params_returns_400(client):
    assert client.get('/api/landclass').status_code == 400


def test_out_of_range_returns_400(client):
    assert client.get('/api/landclass?lat=200&lon=0').status_code == 400
    assert client.get('/api/landclass?lat=0&lon=999').status_code == 400


# ── graceful degradation: PG unreachable -> 200 empty, never 500 ──

def test_pg_down_degrades_to_empty(client, monkeypatch):
    # Simulate an unreachable pool: _get_pool returns None -> _query_all -> [].
    monkeypatch.setattr(db, '_get_pool', lambda: None)
    resp = client.get('/api/landclass?lat=37.85&lon=-119.55')
    assert resp.status_code == 200
    d = resp.get_json()
    assert d['count'] == 0 and d['is_private'] is True


# ── format_summary unit ──

def test_format_summary_picks_first():
    rows = [{'unit_name': 'Small Unit'}, {'unit_name': 'Big Unit'}]
    assert db.format_summary(rows) == 'Small Unit'
    assert db.format_summary([]) is None
