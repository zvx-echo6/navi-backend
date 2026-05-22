"""Tests for the navi-traffic flow proxy.

Covers all four status paths (200 / 503 / 502 / 504) and the success-path
headers, plus the mask_key convention. The TomTom upstream is mocked — no
network calls.
"""
from unittest.mock import patch, MagicMock

import pytest

from services.navi_traffic.app import create_app
from shared.admin_info import mask_key

UPSTREAM = (
    'https://api.tomtom.com/maps/orbis/traffic/tile/flow/'
    '10/200/400.png?key=abcdef123456&apiVersion=1&style=light'
)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv('TOMTOM_API_KEY', 'abcdef123456')
    app = create_app()
    app.testing = True
    return app.test_client()


def test_flow_success_returns_png_with_cache_headers(client):
    fake = MagicMock(status_code=200, content=b'\x89PNG\r\n fake tile bytes')
    with patch('services.navi_traffic.traffic.http_requests.get',
               return_value=fake) as mock_get:
        resp = client.get('/api/traffic/flow/10/200/400.png')

    assert resp.status_code == 200
    assert resp.headers['Content-Type'] == 'image/png'
    assert resp.headers['Cache-Control'] == 'public, max-age=120'
    assert resp.data == b'\x89PNG\r\n fake tile bytes'
    # exact upstream URL + params + 10s timeout (behavior-neutral port)
    assert mock_get.call_args.args[0] == UPSTREAM
    assert mock_get.call_args.kwargs.get('timeout') == 10


def test_flow_missing_key_returns_503(monkeypatch):
    monkeypatch.delenv('TOMTOM_API_KEY', raising=False)
    app = create_app()
    app.testing = True
    resp = app.test_client().get('/api/traffic/flow/1/2/3.png')
    assert resp.status_code == 503
    assert b'not configured' in resp.data


def test_flow_upstream_non_200_returns_502(client):
    fake = MagicMock(status_code=403, content=b'')
    with patch('services.navi_traffic.traffic.http_requests.get',
               return_value=fake):
        resp = client.get('/api/traffic/flow/1/2/3.png')
    assert resp.status_code == 502
    assert b'Upstream error' in resp.data


def test_flow_exception_returns_504(client):
    with patch('services.navi_traffic.traffic.http_requests.get',
               side_effect=Exception('connection reset')):
        resp = client.get('/api/traffic/flow/1/2/3.png')
    assert resp.status_code == 504
    assert b'Upstream timeout' in resp.data


def test_mask_key_recon_pattern():
    # matches recon's api_keys_admin._mask_key: first4 + '...' + last4
    assert mask_key('') is None
    assert mask_key(None) is None
    assert mask_key('abcdefgh') == '****'              # 8 chars, fully masked
    assert mask_key('tk_1234567890ABCDEF') == 'tk_1...CDEF'
    assert mask_key('TOMTOM_KEY_abcdefXYZ') == 'TOMT...fXYZ'
