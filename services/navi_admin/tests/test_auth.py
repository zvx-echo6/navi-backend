"""Hermetic tests for navi-admin's /api/auth/whoami auth-state endpoint.

Mirrors the handler recon served pre-decoupling: reads X-Authentik-Username,
returns {authenticated, username}, never 401 (ungated by design).
"""
from services.navi_admin.app import create_app


def _client():
    return create_app().test_client()


def test_whoami_header_present():
    resp = _client().get('/api/auth/whoami', headers={'X-Authentik-Username': 'matt'})
    assert resp.status_code == 200
    assert resp.get_json() == {'authenticated': True, 'username': 'matt'}


def test_whoami_header_absent():
    resp = _client().get('/api/auth/whoami')
    assert resp.status_code == 200
    assert resp.get_json() == {'authenticated': False, 'username': None}
