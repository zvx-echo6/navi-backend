"""HTTP client for navi-landclass — the first navi→navi-landclass coupling.

Phase A §5 locked this edge: recon's reverse bundle called
``landclass.lookup_landclass`` + ``format_summary`` in-process and merged the
most-specific unit name (a bare string) into ``bundle['landclass']``. navi-geo
replaces that in-process call with an HTTP GET to navi-landclass
``/api/landclass``, whose ``summary`` field is exactly that same string.

navi-landclass returns 200 even with PostGIS down or no coverage
(``summary: null``), so this client only has to read ``.summary`` and let the
caller's try/except turn any transport error into ``None``.
"""
import os

import requests

DEFAULT_LANDCLASS_URL = 'http://127.0.0.1:8424'

# Matches recon's in-process landclass latency budget + small HTTP overhead.
LANDCLASS_TIMEOUT_S = 5


def landclass_url():
    """navi-landclass base URL, env-overridable via NAVI_LANDCLASS_URL."""
    return os.environ.get('NAVI_LANDCLASS_URL', DEFAULT_LANDCLASS_URL)


def reverse_landclass_summary(lat, lon):
    """Return the most-specific PAD-US unit name for a point, or None.

    Mirrors recon's ``_reverse_landclass`` return contract (a string or None).
    Raises on transport/HTTP error so the bundle's per-component try/except can
    log a warning and leave ``landclass`` null — never a 5xx.
    """
    resp = requests.get(
        f"{landclass_url()}/api/landclass",
        params={'lat': lat, 'lon': lon},
        timeout=LANDCLASS_TIMEOUT_S,
    )
    resp.raise_for_status()
    return resp.json().get('summary')
