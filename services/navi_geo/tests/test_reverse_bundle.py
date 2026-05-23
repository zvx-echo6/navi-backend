"""Tests for the /api/reverse/<lat>/<lon> enrichment bundle (navi_geo.geo_route).

Ported from recon's reverse_bundle_test.py (9 tests). Photon/DEM/timezone are
mocked the same way; the in-process landclass mock becomes a mock of the
HTTP-delegated _reverse_landclass (Phase A §5). Two added tests exercise the
real navi-landclass HTTP client mapping (summary -> bundle['landclass']) and the
landclass-HTTP-failure -> null path. One timezone test exercises the real
SpatiaLite DB when present.
"""
import os

import pytest

import services.navi_geo.geo_route as geo_route
import services.navi_geo.landclass_client as landclass_client
from services.navi_geo.app import create_app

EXPECTED_KEYS = set(geo_route._BUNDLE_KEYS)


@pytest.fixture
def client():
    return create_app().test_client()


def _patch_all(monkeypatch, *, photon, timezone, landclass, elevation):
    monkeypatch.setattr(geo_route, '_reverse_photon', photon)
    monkeypatch.setattr(geo_route, '_reverse_timezone', timezone)
    monkeypatch.setattr(geo_route, '_reverse_landclass', landclass)
    monkeypatch.setattr(geo_route, '_reverse_elevation', elevation)


def test_happy_path(client, monkeypatch):
    _patch_all(
        monkeypatch,
        photon=lambda lat, lon: {
            'name': 'Where you are', 'city': 'Boise', 'county': 'Ada',
            'state': 'Idaho', 'country': 'United States', 'postal_code': '83701'},
        timezone=lambda lat, lon: 'America/Boise',
        landclass=lambda lat, lon: 'Boise National Forest',
        elevation=lambda lat, lon: 824,
    )
    resp = client.get('/api/reverse/43.6150/-116.2023')
    assert resp.status_code == 200
    data = resp.get_json()
    assert set(data.keys()) == EXPECTED_KEYS
    assert data['city'] == 'Boise' and data['timezone'] == 'America/Boise'
    assert data['landclass'] == 'Boise National Forest' and data['elevation_m'] == 824


def test_negative_and_integer_coords_parse(client, monkeypatch):
    # Regression: Flask's <float:> converter would 404 these; manual parse must not.
    _patch_all(monkeypatch, photon=lambda lat, lon: {}, timezone=lambda lat, lon: None,
               landclass=lambda lat, lon: None, elevation=lambda lat, lon: None)
    for path in ('/api/reverse/43.6/-116.2', '/api/reverse/43/-116'):
        resp = client.get(path)
        assert resp.status_code == 200, f"{path} -> {resp.status_code}"
        assert set(resp.get_json().keys()) == EXPECTED_KEYS


def test_partial_failure_returns_200_with_nulls(client, monkeypatch):
    def boom(lat, lon):
        raise RuntimeError('down')
    _patch_all(monkeypatch, photon=boom, timezone=lambda lat, lon: 'America/Boise',
               landclass=boom, elevation=lambda lat, lon: 824)
    resp = client.get('/api/reverse/43.6150/-116.2023')
    assert resp.status_code == 200
    data = resp.get_json()
    assert set(data.keys()) == EXPECTED_KEYS
    assert data['name'] is None and data['city'] is None     # photon failed -> nulls
    assert data['landclass'] is None                          # landclass failed -> null
    assert data['timezone'] == 'America/Boise' and data['elevation_m'] == 824


def test_ocean_point_mostly_null(client, monkeypatch):
    _patch_all(monkeypatch, photon=lambda lat, lon: {}, timezone=lambda lat, lon: 'Etc/GMT+2',
               landclass=lambda lat, lon: None, elevation=lambda lat, lon: 0)
    resp = client.get('/api/reverse/0.0/-30.0')
    assert resp.status_code == 200
    data = resp.get_json()
    assert set(data.keys()) == EXPECTED_KEYS
    assert data['city'] is None and data['country'] is None and data['landclass'] is None


def test_invalid_input_400(client):
    for path in ('/api/reverse/9999/0', '/api/reverse/0/9999', '/api/reverse/abc/0'):
        resp = client.get(path)
        assert resp.status_code == 400, f"{path} -> {resp.status_code}"


def test_cache_hit_serves_without_recompute(client, monkeypatch):
    calls = {'n': 0}

    def counting_photon(lat, lon):
        calls['n'] += 1
        return {'name': 'X'}
    _patch_all(monkeypatch, photon=counting_photon, timezone=lambda lat, lon: None,
               landclass=lambda lat, lon: None, elevation=lambda lat, lon: None)
    client.get('/api/reverse/12.3456/-65.4321')
    client.get('/api/reverse/12.3456/-65.4321')   # same key (rounded) -> cached
    assert calls['n'] == 1, f"expected 1 compute, got {calls['n']}"


def test_real_timezone_db(monkeypatch):
    path = geo_route.tz_db_path()
    if not os.path.exists(path):
        pytest.skip("real timezone test (timezones.sqlite not present)")
    assert geo_route._reverse_timezone(43.6150, -116.2023) == 'America/Boise'
    assert geo_route._reverse_timezone(40.7128, -74.0060) == 'America/New_York'


def test_elevation_from_dem_reader_mock(client, monkeypatch):
    # elevation_m comes from DEMReader.sample_point; other components stubbed null.
    class FakeDEM:
        def __init__(self):
            self.called = 0

        def sample_point(self, lat, lon):
            self.called += 1
            return 824
    fake = FakeDEM()
    monkeypatch.setattr(geo_route, '_DEM', fake)
    monkeypatch.setattr(geo_route, '_reverse_photon', lambda lat, lon: {})
    monkeypatch.setattr(geo_route, '_reverse_timezone', lambda lat, lon: None)
    monkeypatch.setattr(geo_route, '_reverse_landclass', lambda lat, lon: None)
    resp = client.get('/api/reverse/43.6150/-116.2023')
    assert resp.status_code == 200
    data = resp.get_json()
    assert set(data.keys()) == EXPECTED_KEYS
    assert data['elevation_m'] == 824
    assert fake.called == 1


def test_elevation_dem_unavailable(client, monkeypatch):
    # DEMReader failed to init at startup (_DEM is None) -> elevation_m null, 200.
    monkeypatch.setattr(geo_route, '_DEM', None)
    monkeypatch.setattr(geo_route, '_reverse_photon', lambda lat, lon: {})
    monkeypatch.setattr(geo_route, '_reverse_timezone', lambda lat, lon: None)
    monkeypatch.setattr(geo_route, '_reverse_landclass', lambda lat, lon: None)
    resp = client.get('/api/reverse/43.6150/-116.2023')
    assert resp.status_code == 200
    assert resp.get_json()['elevation_m'] is None


# ── Added: the navi-landclass HTTP coupling (Phase A §5 / Phase B locked) ──

class _FakeResp:
    def __init__(self, status_code=200, json_data=None):
        self.status_code = status_code
        self._json = json_data or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f'HTTP {self.status_code}')

    def json(self):
        return self._json


def test_landclass_http_summary_maps_into_bundle(client, monkeypatch):
    # navi-landclass returns the full dict; navi-geo reads .summary into landclass.
    captured = {}

    def fake_get(url, params=None, timeout=None):
        captured['url'] = url
        captured['params'] = params
        return _FakeResp(200, {
            'lat': params['lat'], 'lon': params['lon'],
            'classifications': [{'unit_name': 'Boise National Forest'}],
            'count': 1, 'is_public': True, 'is_private': False,
            'summary': 'Boise National Forest',
        })
    monkeypatch.setattr(landclass_client.requests, 'get', fake_get)
    monkeypatch.setattr(geo_route, '_reverse_photon', lambda lat, lon: {})
    monkeypatch.setattr(geo_route, '_reverse_timezone', lambda lat, lon: None)
    monkeypatch.setattr(geo_route, '_reverse_elevation', lambda lat, lon: None)
    resp = client.get('/api/reverse/43.6150/-116.2023')
    data = resp.get_json()
    assert data['landclass'] == 'Boise National Forest'   # the summary string only
    assert captured['url'].endswith('/api/landclass')
    assert captured['params'] == {'lat': 43.615, 'lon': -116.2023}


def test_landclass_http_failure_yields_null(client, monkeypatch):
    def boom_get(url, params=None, timeout=None):
        raise RuntimeError('connection refused')
    monkeypatch.setattr(landclass_client.requests, 'get', boom_get)
    monkeypatch.setattr(geo_route, '_reverse_photon', lambda lat, lon: {})
    monkeypatch.setattr(geo_route, '_reverse_timezone', lambda lat, lon: None)
    monkeypatch.setattr(geo_route, '_reverse_elevation', lambda lat, lon: None)
    resp = client.get('/api/reverse/43.6150/-116.2023')
    assert resp.status_code == 200            # never 5xx
    assert resp.get_json()['landclass'] is None
