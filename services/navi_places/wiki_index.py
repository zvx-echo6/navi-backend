"""Direct read of the local wiki_index.db (wiki_places table) for navi-places.

Ports recon's lib/place_detail.lookup_wiki_index (the /api/wiki-enrich read path)
to an in-process SQLite read, matching place_cache.py's path/conn pattern. The
2.1 GB wiki_index.db is now owned by navi-places (NAVI_WIKI_INDEX_DB). Pure
read; never raises (missing DB / errors → None so enrichment no-ops).
"""
import logging
import os
import sqlite3

logger = logging.getLogger('navi_places.wiki_index')

DEFAULT_DB_PATH = '/var/lib/navi-backend/wiki_index.db'

_db_conn = None


def db_path():
    return os.environ.get('NAVI_WIKI_INDEX_DB', DEFAULT_DB_PATH)


def _get_db():
    """Lazy module-level read-only connection. Returns None if the file is absent
    (enrichment then silently no-ops). row_factory=Row for name access."""
    global _db_conn
    if _db_conn is not None:
        return _db_conn
    path = db_path()
    if not os.path.exists(path):
        logger.debug(f"wiki_index.db not found at {path}")
        return None
    _db_conn = sqlite3.connect(path, check_same_thread=False)
    _db_conn.row_factory = sqlite3.Row
    logger.info(f"Wiki index DB ready at {path}")
    return _db_conn


def reset():
    """Close + drop the cached connection (per-worker / per-test refresh)."""
    global _db_conn
    if _db_conn is not None:
        try:
            _db_conn.close()
        except Exception:
            pass
    _db_conn = None


def lookup(wikidata_id=None, name=None, country_code=None):
    """wikidata_id first, then name+country_code fallback — exact port of recon's
    lookup_wiki_index. Returns {wiki_summary, wiki_population, wiki_url,
    wikivoyage_url} (only keys present), or None on no match / no DB."""
    db = _get_db()
    if not db:
        return None
    try:
        cur = db.cursor()
        row = None
        if wikidata_id:
            wid = wikidata_id
            if isinstance(wid, str) and wid.startswith("http"):
                wid = wid.split("/")[-1]
            cur.execute(
                "SELECT summary, wiki_population, wikipedia_title, wikivoyage_title "
                "FROM wiki_places WHERE wikidata_id = ?", (wid,))
            row = cur.fetchone()
        if not row and name and country_code:
            cur.execute(
                "SELECT summary, wiki_population, wikipedia_title, wikivoyage_title "
                "FROM wiki_places WHERE place_name = ? AND country_code = ? LIMIT 1",
                (name, country_code.lower()))
            row = cur.fetchone()
        if not row:
            return None
        out = {}
        if row["summary"]:
            out["wiki_summary"] = row["summary"]
        if row["wiki_population"]:
            try:
                out["wiki_population"] = int(row["wiki_population"])
            except (ValueError, TypeError):
                out["wiki_population"] = row["wiki_population"]
        if row["wikipedia_title"]:
            out["wiki_url"] = f"https://en.wikipedia.org/wiki/{row['wikipedia_title'].replace(' ', '_')}"
        if row["wikivoyage_title"]:
            out["wikivoyage_url"] = f"https://en.wikivoyage.org/wiki/{row['wikivoyage_title'].replace(' ', '_')}"
        return out or None
    except Exception as e:
        logger.debug(f"wiki_index lookup error: {e}")
        return None
