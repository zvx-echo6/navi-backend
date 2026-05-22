"""Tests for navi-config `/api/config`.

Writes fixture profile YAMLs under a tmp dir, points
``NAVI_CONFIG_PROFILES_DIR`` at it, and exercises the response, headers, the
``RECON_PROFILE`` override, and the missing-profile path.

Note: the test client does NOT set ``app.testing = True`` — that would make
Flask re-raise unhandled exceptions instead of returning a response. We want
the missing-profile case to surface as an HTTP 500 (which is the production
behavior: a bad/missing profile fails the request loudly), so we let Flask's
default error handling produce the 500.
"""
import pytest

from services.navi_config.app import create_app

HOME_YAML = """\
profile: home
region_name: "North America"
services:
  geocode: "/api/geocode"
  valhalla: "/valhalla"
auth:
  login_url: "/outpost.goauthentik.io/start?rd=%2F"
  logout_url: "https://auth.echo6.co/if/flow/default-invalidation-flow/?next=https://navi.echo6.co/"
features:
  has_contacts: true
"""

MINIMAL_YAML = """\
profile: minimal_pi
region_name: "Idaho"
features:
  has_contacts: false
"""


@pytest.fixture
def profiles_dir(tmp_path, monkeypatch):
    (tmp_path / 'home.yaml').write_text(HOME_YAML)
    (tmp_path / 'minimal_pi.yaml').write_text(MINIMAL_YAML)
    monkeypatch.setenv('NAVI_CONFIG_PROFILES_DIR', str(tmp_path))
    return tmp_path


def _client():
    # No app.testing = True — see module docstring (we want a 500, not a raise).
    return create_app().test_client()


def test_config_returns_parsed_dict(profiles_dir, monkeypatch):
    monkeypatch.setenv('RECON_PROFILE', 'home')
    resp = _client().get('/api/config')
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['profile'] == 'home'
    assert data['region_name'] == 'North America'
    assert data['services']['geocode'] == '/api/geocode'
    # the auth block added in PR-A flows through unchanged
    assert data['auth']['login_url'] == '/outpost.goauthentik.io/start?rd=%2F'


def test_cache_control_header(profiles_dir, monkeypatch):
    monkeypatch.setenv('RECON_PROFILE', 'home')
    resp = _client().get('/api/config')
    assert resp.headers['Cache-Control'] == 'public, max-age=300'


def test_recon_profile_env_override(profiles_dir, monkeypatch):
    monkeypatch.setenv('RECON_PROFILE', 'minimal_pi')
    resp = _client().get('/api/config')
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['profile'] == 'minimal_pi'
    assert data['region_name'] == 'Idaho'


def test_default_profile_is_home(profiles_dir, monkeypatch):
    # No RECON_PROFILE set -> defaults to "home"
    monkeypatch.delenv('RECON_PROFILE', raising=False)
    resp = _client().get('/api/config')
    assert resp.status_code == 200
    assert resp.get_json()['profile'] == 'home'


def test_missing_profile_returns_500(profiles_dir, monkeypatch):
    monkeypatch.setenv('RECON_PROFILE', 'does_not_exist')
    resp = _client().get('/api/config')
    assert resp.status_code == 500
