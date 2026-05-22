"""TomTom traffic flow tile proxy.

Behavior-neutral port of recon ``lib/api.py:1212-1229`` (the PR #4 Orbis flow
proxy). Same upstream URL, same buffered passthrough, same headers, same error
codes. The only purpose is to keep ``TOMTOM_API_KEY`` server-side.
"""
import os

import requests as http_requests
from flask import Blueprint, make_response

bp = Blueprint('traffic', __name__)


@bp.route('/api/traffic/flow/<int:z>/<int:x>/<int:y>.png')
def api_traffic_flow(z, x, y):
    """Proxy TomTom traffic flow tiles to hide API key from frontend."""
    key = os.environ.get('TOMTOM_API_KEY')
    if not key:
        return 'Traffic service not configured', 503
    # Orbis Maps Traffic API (migrated from classic)
    url = f'https://api.tomtom.com/maps/orbis/traffic/tile/flow/{z}/{x}/{y}.png?key={key}&apiVersion=1&style=light'
    try:
        resp = http_requests.get(url, timeout=10)
        if resp.status_code != 200:
            return 'Upstream error', 502
        r = make_response(resp.content)
        r.headers['Content-Type'] = 'image/png'
        r.headers['Cache-Control'] = 'public, max-age=120'
        return r
    except Exception:
        return 'Upstream timeout', 504
