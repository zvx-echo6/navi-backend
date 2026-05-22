"""Address Book — YAML-backed saved-location lookup.

Behavior-identical port of recon's ``lib/address_book.py``. Named locations
(home, work, etc.) with fuzzy matching over name + aliases + partial address.
Hot-reloads when the YAML's mtime changes.

Config path: env ``NAVI_ADDRESS_BOOK_YAML`` (default: the vendored
``config/address_book.yaml`` in this repo's deploy location).
"""
import logging
import os
import re
import threading

import yaml

logger = logging.getLogger('navi_contacts.address_book')

DEFAULT_CONFIG_PATH = '/home/zvx/projects/repos/navi-backend/config/address_book.yaml'

_lock = threading.Lock()
_entries: list[dict] = []
_mtime: float = 0.0
_loaded_path: str | None = None


def _config_path():
    return os.environ.get('NAVI_ADDRESS_BOOK_YAML', DEFAULT_CONFIG_PATH)


def reset_cache():
    """Drop cached entries so the next access reloads (env/path may have changed)."""
    global _entries, _mtime, _loaded_path
    with _lock:
        _entries = []
        _mtime = 0.0
        _loaded_path = None


def _reload_if_changed():
    """Reload the YAML file if its mtime (or path) has changed."""
    global _entries, _mtime, _loaded_path
    path = _config_path()
    try:
        st = os.stat(path)
    except FileNotFoundError:
        logger.warning("Address book not found: %s", path)
        _entries = []
        _mtime = 0.0
        _loaded_path = path
        return

    if st.st_mtime == _mtime and path == _loaded_path:
        return

    with _lock:
        # Double-check after acquiring lock
        try:
            st = os.stat(path)
        except FileNotFoundError:
            _entries = []
            _mtime = 0.0
            _loaded_path = path
            return
        if st.st_mtime == _mtime and path == _loaded_path:
            return

        with open(path, 'r') as f:
            data = yaml.safe_load(f) or {}

        raw = data.get('entries', [])
        loaded = []
        for entry in raw:
            # Normalise aliases to lowercase for matching
            aliases = [a.lower() for a in entry.get('aliases', [])]
            loaded.append({
                'id': entry.get('id', ''),
                'name': entry.get('name', ''),
                'aliases': aliases,
                'address': entry.get('address', ''),
                'lat': entry.get('lat'),
                'lon': entry.get('lon'),
                'tags': entry.get('tags', []),
            })
        _entries = loaded
        _mtime = st.st_mtime
        _loaded_path = path
        logger.info("Address book loaded: %d entries from %s", len(_entries), path)


def load():
    """Ensure the address book is loaded (and refreshed if the file changed)."""
    _reload_if_changed()
    return _entries


def _normalize(text: str) -> str:
    """Lowercase, strip, remove commas, collapse whitespace."""
    t = text.strip().lower()
    t = t.replace(',', ' ')
    return ' '.join(t.split())


def lookup(query: str):
    """
    Look up a query against name and aliases.

    Returns dict with the matching entry plus a 'confidence' field:
      - "exact": full name/alias match, OR query starts with alias + word boundary
      - "partial": alias starts with query + word boundary, or alias appears
        as a contiguous token sequence inside the query
      - None if no match

    Matching order (first exact wins, else first partial):
      1. normalized(query) == normalized(name or alias)         → exact
      2. normalized(query) starts with normalized(alias) + " "  → exact
      3. normalized(alias) starts with normalized(query) + " "  → partial
      4. normalized(alias) is a contiguous token sub-sequence    → partial
    """
    _reload_if_changed()
    q = _normalize(query)
    if not q:
        return None

    first_exact = None
    first_partial = None

    for entry in _entries:
        norm_name = _normalize(entry['name'])
        check_aliases = [_normalize(a) for a in entry.get('aliases', [])]
        all_forms = [norm_name] + check_aliases

        for form in all_forms:
            if not form:
                continue

            # Rule 1: exact match
            if q == form:
                return {**entry, 'confidence': 'exact'}

            # Rule 2: query starts with alias + word boundary
            if q.startswith(form + ' '):
                if first_exact is None:
                    first_exact = entry
                continue

            # Rule 3: alias starts with query (user still typing)
            if form.startswith(q) and len(q) < len(form):
                if first_partial is None:
                    first_partial = entry
                continue

            # Rule 4: alias is contiguous token sub-sequence in query
            # Build regex: token1\s+token2\s+...tokenN
            tokens = form.split()
            if len(tokens) >= 1:
                pattern = r'(?:^|\s)' + r'\s+'.join(re.escape(t) for t in tokens) + r'(?:\s|$)'
                if re.search(pattern, q):
                    if first_partial is None:
                        first_partial = entry

    if first_exact is not None:
        return {**first_exact, 'confidence': 'exact'}

    if first_partial is not None:
        return {**first_partial, 'confidence': 'partial'}

    return None


def list_all():
    """Return all address book entries."""
    _reload_if_changed()
    return list(_entries)
