"""Deployment profile loader for navi-config.

Behavior-faithful port of recon's ``lib/deployment_config.py``: read the active
profile name from ``RECON_PROFILE`` (default ``home``), load the matching
``<dir>/<profile>.yaml``, ``yaml.safe_load`` it, and cache the parsed dict in a
module global. The profiles directory is configurable via
``NAVI_CONFIG_PROFILES_DIR`` (default ``/opt/recon/config/profiles`` — the same
files recon serves, so the two agree byte-for-byte during cutover).

Difference from recon: recon eagerly loads at import time (fail-fast at start).
Here the load is lazy (first ``get_deployment_config()`` call), so the module
imports cleanly even where the default dir is absent (e.g. CI / a dev box), and
a missing profile surfaces as an HTTP 500 at request time rather than a failed
import. ``reset_cache()`` lets ``create_app()`` (and tests) force a fresh load.
"""
import os

import yaml

DEFAULT_PROFILES_DIR = '/opt/recon/config/profiles'

_config_cache = None


def profiles_dir():
    """Directory holding the profile YAMLs (env-overridable)."""
    return os.environ.get('NAVI_CONFIG_PROFILES_DIR', DEFAULT_PROFILES_DIR)


def profile_name():
    """Active profile name (env-overridable), matching recon's RECON_PROFILE."""
    return os.environ.get('RECON_PROFILE', 'home')


def active_profile_path():
    """Full path to the active profile YAML."""
    return os.path.join(profiles_dir(), f'{profile_name()}.yaml')


def load_deployment_config():
    """Load and cache the active profile. Raises FileNotFoundError if absent."""
    global _config_cache
    path = active_profile_path()
    if not os.path.exists(path):
        directory = profiles_dir()
        try:
            available = ', '.join(
                f.replace('.yaml', '') for f in os.listdir(directory) if f.endswith('.yaml')
            )
        except OSError:
            available = '(profiles dir missing)'
        raise FileNotFoundError(
            f"Deployment profile '{profile_name()}' not found at {path}. "
            f"Available profiles: {available}"
        )
    with open(path, 'r') as f:
        _config_cache = yaml.safe_load(f)
    return _config_cache


def get_deployment_config():
    """Return the cached deployment config dict, loading it on first use."""
    if _config_cache is None:
        load_deployment_config()
    return _config_cache


def reset_cache():
    """Drop the cached config so the next access reloads (env may have changed)."""
    global _config_cache
    _config_cache = None
