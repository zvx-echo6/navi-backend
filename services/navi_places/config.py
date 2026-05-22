"""Deployment-profile feature flags for navi-places.

Reads the same vendored profile YAML navi-config serves, to gate the optional
enrichment paths (has_overture_enrichment / has_google_places_enrichment /
has_kiwix_wiki / has_wiki_rewriting), matching recon's in-process gating.

Env:
  NAVI_PROFILES_DIR  (default the repo's vendored config/profiles)
  RECON_PROFILE      (default "home")
"""
import os

import yaml

DEFAULT_PROFILES_DIR = '/home/zvx/projects/repos/navi-backend/config/profiles'

_config_cache = None


def _profiles_dir():
    return os.environ.get('NAVI_PROFILES_DIR', DEFAULT_PROFILES_DIR)


def _profile_name():
    return os.environ.get('RECON_PROFILE', 'home')


def get_config():
    """Return the parsed profile dict (cached), or {} if unavailable.

    Never raises — a missing/unreadable profile degrades to {} (all feature
    flags read as falsy), so enrichment simply no-ops rather than crashing.
    """
    global _config_cache
    if _config_cache is not None:
        return _config_cache
    path = os.path.join(_profiles_dir(), f'{_profile_name()}.yaml')
    try:
        with open(path) as f:
            _config_cache = yaml.safe_load(f) or {}
    except Exception:
        _config_cache = {}
    return _config_cache


def has_feature(flag):
    """Read a features.<flag> boolean from the active profile."""
    return bool(get_config().get('features', {}).get(flag, False))


def reset_config():
    """Drop the cached profile so the next access reloads (env may have changed)."""
    global _config_cache
    _config_cache = None
