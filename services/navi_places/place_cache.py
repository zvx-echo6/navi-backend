"""SQLite place cache for navi-places (port of recon's place_detail cache layer).

Owns the shared connection to NAVI_PLACE_CACHE_DB (default
/var/lib/navi-backend/place_cache.db) and AUTO-CREATES the schema on first open:
  - `place_cache` (incl. the google_place_id/google_data/google_fetched_at
    columns recon added by migration — created here so a fresh DB works for both
    the base cache and the google_places cache)
  - `google_api_calls` (the Google daily-cap counter)
WAL, check_same_thread=False, lazy module-level conn. reset_cache() lets
create_app() refresh per worker / tests point at a tmp DB.
"""
import json
import os
import time

DEFAULT_DB_PATH = '/var/lib/navi-backend/place_cache.db'

_db_conn = None


def db_path():
    return os.environ.get('NAVI_PLACE_CACHE_DB', DEFAULT_DB_PATH)


def get_conn():
    """Return the module-level SQLite connection (lazy init + auto-create schema)."""
    global _db_conn
    if _db_conn is not None:
        return _db_conn

    import sqlite3
    path = db_path()
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    _db_conn = sqlite3.connect(path, check_same_thread=False)
    _db_conn.execute("PRAGMA journal_mode=WAL")
    _db_conn.execute("PRAGMA synchronous=NORMAL")
    # Full place_cache schema incl. the google_* columns (recon added these by
    # migration; we create them up front so a fresh auto-created DB serves both
    # cache_put and cache_put_google).
    _db_conn.execute("""
        CREATE TABLE IF NOT EXISTS place_cache (
            osm_type TEXT NOT NULL,
            osm_id INTEGER NOT NULL,
            data TEXT NOT NULL,
            source TEXT NOT NULL,
            cached_at INTEGER NOT NULL,
            google_place_id TEXT,
            google_data TEXT,
            google_fetched_at INTEGER,
            PRIMARY KEY (osm_type, osm_id)
        )
    """)
    _db_conn.execute("""
        CREATE TABLE IF NOT EXISTS google_api_calls (
            call_date TEXT PRIMARY KEY,
            call_count INTEGER NOT NULL DEFAULT 0
        )
    """)
    _db_conn.commit()
    return _db_conn


def reset_cache():
    """Close + drop the cached connection so the next access reopens fresh."""
    global _db_conn
    if _db_conn is not None:
        try:
            _db_conn.close()
        except Exception:
            pass
    _db_conn = None


def cache_get(osm_type, osm_id):
    """Return cached place dict or None."""
    db = get_conn()
    row = db.execute(
        "SELECT data FROM place_cache WHERE osm_type=? AND osm_id=?",
        (osm_type, osm_id)
    ).fetchone()
    if row and row[0]:
        try:
            result = json.loads(row[0])
            result['source'] = 'cache'
            return result
        except (json.JSONDecodeError, TypeError):
            pass
    return None


def cache_put(osm_type, osm_id, data, source):
    """Store a place detail result in the cache (preserves google columns)."""
    db = get_conn()
    now = int(time.time())
    db.execute("""
        INSERT INTO place_cache (osm_type, osm_id, data, source, cached_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(osm_type, osm_id) DO UPDATE SET
            data = excluded.data,
            source = excluded.source,
            cached_at = excluded.cached_at
    """, (osm_type, osm_id, json.dumps(data), source, now))
    db.commit()
