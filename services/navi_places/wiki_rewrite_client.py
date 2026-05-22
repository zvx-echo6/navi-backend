"""HTTP client for recon's /api/wiki-rewrite (PR #9).

Replaces the in-process wiki_rewrite/Kiwix call (Kiwix + the wiki_cache table
stay in recon). Rewrites a single OSM wiki tag value to a local Kiwix URL.
"""
import logging
import os

import requests

logger = logging.getLogger('navi_places.wiki_rewrite_client')


def _base_url():
    return os.environ.get('RECON_BASE_URL', 'http://127.0.0.1:8420')


def rewrite_via_recon(tag: str, value: str, timeout: float = 3.0) -> dict:
    """HTTP-call recon's /api/wiki-rewrite. On any error/timeout, return
    {'url': value, 'status': 'original'} so the orchestrator can safely keep
    the original extratag unchanged (mirrors _enrich_wiki_links' ImportError
    self-degrade path)."""
    try:
        resp = requests.get(
            f"{_base_url()}/api/wiki-rewrite",
            params={'tag': tag, 'value': value},
            timeout=timeout,
        )
        if resp.status_code == 200:
            data = resp.json()
            # Defensive: ensure the expected shape
            if isinstance(data, dict) and 'url' in data and 'status' in data:
                return data
        return {'url': value, 'status': 'original'}
    except Exception as e:
        logger.debug(f"wiki-rewrite call failed for {tag}: {e}")
        return {'url': value, 'status': 'original'}
