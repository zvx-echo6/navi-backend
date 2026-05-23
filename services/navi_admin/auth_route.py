"""navi-admin auth-state route.

  GET /api/auth/whoami  the caller's Authentik auth state, for the frontend

Ungated by design: this is the "am I logged in?" check, so it must answer even
when unauthenticated (no ``@require_auth``). Behind Caddy's forward_auth the
``X-Authentik-Username`` header is present iff the caller is authenticated;
this mirrors the handler recon served before the navi-recon decoupling.
"""
from flask import Blueprint, jsonify, request

bp = Blueprint('navi_admin_auth', __name__)


@bp.route('/api/auth/whoami')
def auth_whoami():
    """Return the caller's auth state. Behind forward_auth, so the header is
    present when authenticated; absent → the unauthenticated response (not 401)."""
    username = request.headers.get('X-Authentik-Username')
    if username:
        return jsonify({'authenticated': True, 'username': username})
    return jsonify({'authenticated': False, 'username': None})
