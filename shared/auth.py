"""Authentication helpers shared across every navi-backend service.

Auth is enforced at the Caddy/Authentik edge (forward_auth). By the time a
request reaches a service it carries an ``X-Authentik-Username`` header iff the
user is authenticated. These helpers read that header — they do not perform
auth themselves, they assert that the edge already did.
"""
from functools import wraps

from flask import request, jsonify


def get_user_id(req):
    """Return the Authentik-supplied username for a request, or None if absent."""
    return req.headers.get('X-Authentik-Username')


def require_auth(fn):
    """Reject requests with no ``X-Authentik-Username`` header (401 JSON).

    Use on endpoints that must never be reachable unauthenticated even if the
    edge is misconfigured or bypassed (e.g. the internal nginx :8440 path).
    """
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not get_user_id(request):
            return jsonify({'error': 'authentication required'}), 401
        return fn(*args, **kwargs)
    return wrapper
