"""HTTP client for recon's /api/wiki-enrich (PR #8).

Replaces the in-process wiki_index.db read (the 2.1 GB DB stays in recon).
Returns the wiki enrichment fields dict, or None on no-match/error/timeout.
"""
import logging
import os

import requests

logger = logging.getLogger('navi_places.wiki_client')


def _base_url():
    return os.environ.get('RECON_BASE_URL', 'http://127.0.0.1:8420')


def enrich_via_recon(wikidata_id=None, name=None, country_code=None, timeout=3.0):
    """GET ${RECON_BASE_URL}/api/wiki-enrich. Returns the fields dict on 200,
    or None on 404 / 400 / any error / timeout (graceful — wiki enrichment is
    optional)."""
    params = {}
    if wikidata_id:
        params['wikidata'] = wikidata_id
    if name and country_code:
        params['name'] = name
        params['country'] = country_code
    if not params:
        return None
    try:
        resp = requests.get(f"{_base_url()}/api/wiki-enrich", params=params, timeout=timeout)
        if resp.status_code == 200:
            return resp.json()
        return None
    except Exception as e:
        logger.debug(f"wiki-enrich call failed: {e}")
        return None
