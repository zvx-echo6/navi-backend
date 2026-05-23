"""Hermetic tests for navi-admin's fleet aggregator (extraction #7).

No live calls: services.navi_admin.fleet.requests.get is mocked to a router
keyed on the port in the URL, so we exercise the real _get_json mapping
(timeout -> 'timeout', non-200 -> 'HTTP <code>') and the real fan-out/merge.
recon_git_sha is stubbed so tests don't depend on /opt/recon.
"""
import pytest
import requests

import services.navi_admin.fleet as fleet
from services.navi_admin.app import create_app

AUTH = {'X-Authentik-Username': 'matt'}


def _svc_info(name, port):
    """A canonical per-service admin-info (build_info_response shape)."""
    return {'service': name, 'version': 'abc1234', 'port': port, 'config': {},
            'env': [], 'dependencies': [], 'filesystem': [],
            'runtime': {'uptime_s': 1.0, 'request_count': 0, 'last_error_at': None}}


def _recon_health(status='healthy'):
    return {'status': status, 'uptime': '2026-05-23T00:00:00Z',
            'components': {'qdrant': {'status': 'up', 'vectors': 42},
                           'tei': {'status': 'up'},
                           'nfs': {'status': 'up'}},
            'pipeline': {'total': 100, 'done': 90}}


class _FakeResp:
    def __init__(self, status_code, json_data):
        self.status_code = status_code
        self._json = json_data

    def json(self):
        return self._json


def _router(behaviors, captured_headers):
    """behaviors: {port:int -> ('ok', json) | ('http', code) | 'timeout' | 'conn'}."""
    def fake_get(url, headers=None, timeout=None):
        captured_headers.append(headers or {})
        for port, behavior in behaviors.items():
            if f':{port}/' in url:
                if behavior == 'timeout':
                    raise requests.Timeout()
                if behavior == 'conn':
                    raise requests.ConnectionError()
                kind, payload = behavior
                if kind == 'http':
                    return _FakeResp(payload, {})
                return _FakeResp(200, payload)
        raise AssertionError(f'unexpected GET {url}')
    return fake_get


def _all_ok():
    """Every navi-* service 200 + recon health 200."""
    b = {port: ('ok', _svc_info(name, port)) for name, port in fleet.SERVICES}
    b[fleet.RECON_PORT] = ('ok', _recon_health())
    return b


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(fleet, 'recon_git_sha', lambda: 'recon99')
    return create_app().test_client()


@pytest.fixture
def captured():
    return []


def _wire(monkeypatch, behaviors, captured):
    monkeypatch.setattr(fleet.requests, 'get', _router(behaviors, captured))


# ── fleet fan-out ─────────────────────────────────────────────────────────

def test_fleet_happy_path(client, monkeypatch, captured):
    _wire(monkeypatch, _all_ok(), captured)
    data = client.get('/api/admin/fleet', headers=AUTH).get_json()
    expected = {name for name, _ in fleet.SERVICES} | {'recon'}
    assert set(data['services'].keys()) == expected
    assert data['errors'] == []
    assert data['fetched_at'].endswith('Z')
    assert data['services']['navi-geo']['port'] == 8426
    assert data['services']['recon']['runtime']['recon_status'] == 'healthy'


def test_fleet_service_timeout_lands_in_errors(client, monkeypatch, captured):
    b = _all_ok()
    b[8425] = 'timeout'                       # navi-places times out
    _wire(monkeypatch, b, captured)
    data = client.get('/api/admin/fleet', headers=AUTH).get_json()
    assert 'navi-places' not in data['services']
    assert {'service': 'navi-places', 'error': 'timeout'} in data['errors']


def test_fleet_service_http_500_lands_in_errors(client, monkeypatch, captured):
    b = _all_ok()
    b[8426] = ('http', 500)                   # navi-geo 500s
    _wire(monkeypatch, b, captured)
    data = client.get('/api/admin/fleet', headers=AUTH).get_json()
    assert 'navi-geo' not in data['services']
    assert {'service': 'navi-geo', 'error': 'HTTP 500'} in data['errors']


def test_fleet_forwards_auth_header(client, monkeypatch, captured):
    _wire(monkeypatch, _all_ok(), captured)
    client.get('/api/admin/fleet', headers=AUTH)
    assert captured and all(h.get('X-Authentik-Username') == 'matt' for h in captured)


def test_fleet_never_5xx_when_recon_down_and_a_service_errors(client, monkeypatch, captured):
    b = _all_ok()
    b[fleet.RECON_PORT] = 'timeout'           # recon health down
    b[8421] = ('http', 502)                   # navi-traffic 502
    _wire(monkeypatch, b, captured)
    resp = client.get('/api/admin/fleet', headers=AUTH)
    assert resp.status_code == 200            # never 5xx
    data = resp.get_json()
    # recon still present as a degraded entry, AND recorded in errors
    assert data['services']['recon']['runtime']['recon_status'] == 'unreachable'
    assert {'service': 'recon', 'error': 'timeout'} in data['errors']
    assert {'service': 'navi-traffic', 'error': 'HTTP 502'} in data['errors']
    assert 'navi-traffic' not in data['services']


# ── recon/info wrapper ──────────────────────────────────────────────────────

def test_recon_info_wraps_health(client, monkeypatch, captured):
    _wire(monkeypatch, {fleet.RECON_PORT: ('ok', _recon_health())}, captured)
    data = client.get('/api/admin/recon/info', headers=AUTH).get_json()
    assert data['service'] == 'recon' and data['port'] == 8420
    assert data['version'] == 'recon99'
    dep_names = {d['name'] for d in data['dependencies']}
    assert {'qdrant', 'tei', 'nfs'} <= dep_names
    assert data['runtime']['recon_status'] == 'healthy'
    assert data['runtime']['pipeline'] == {'total': 100, 'done': 90}


def test_recon_info_recon_down_is_degraded_not_5xx(client, monkeypatch, captured):
    _wire(monkeypatch, {fleet.RECON_PORT: 'timeout'}, captured)
    resp = client.get('/api/admin/recon/info', headers=AUTH)
    assert resp.status_code == 200            # degraded, not 5xx
    data = resp.get_json()
    assert data['service'] == 'recon'
    assert data['runtime']['recon_status'] == 'unreachable'
    assert data['dependencies'][0]['status'] == 'error'


# ── self-info ───────────────────────────────────────────────────────────────

def test_self_info_no_secrets_and_lists_deps(client, monkeypatch, captured):
    _wire(monkeypatch, _all_ok(), captured)
    data = client.get('/api/admin/navi-admin/info', headers=AUTH).get_json()
    assert data['service'] == 'navi-admin' and data['port'] == 8427
    assert data['filesystem'] == []           # owns no files/DB
    # No masked secrets anywhere in env (Phase A §9 — none exist).
    assert all('...' not in str(e['value']) and e['value'] != '****' for e in data['env'])
    dep_names = {d['name'] for d in data['dependencies']}
    assert 'recon-health' in dep_names
    assert {name for name, _ in fleet.SERVICES} <= dep_names


def test_self_info_config_lists_fanned_services(client, monkeypatch, captured):
    _wire(monkeypatch, _all_ok(), captured)
    data = client.get('/api/admin/navi-admin/info', headers=AUTH).get_json()
    fanned = {s['name']: s['port'] for s in data['config']['fanned_services']}
    assert fanned == {name: port for name, port in fleet.SERVICES}
    assert data['config']['recon']['git_sha'] == 'recon99'


# ── auth gating ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize('path', [
    '/api/admin/fleet', '/api/admin/recon/info', '/api/admin/navi-admin/info'])
def test_auth_required(client, path):
    assert client.get(path).status_code == 401
