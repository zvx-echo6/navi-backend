"""Tests for navi-places /api/place/* — first tests for this code (recon had none).

All upstreams are mocked: Nominatim/Overpass/Wikidata via a fake http_requests
on place_detail; Overture via monkeypatched overture functions; Google via the
gate; wiki_index via a real tmp SQLite DB; wiki-rewrite via monkeypatched
wiki_rewrite.rewrite_wiki_link. Feature flags via a stubbed config.has_feature.
The cache uses a real tmp SQLite (auto-created).
"""
import sqlite3

import pytest

import services.navi_places.place_detail as pd
import services.navi_places.place_cache as place_cache
import services.navi_places.wiki_index as wiki_index
from services.navi_places.app import create_app


class FakeResp:
    def __init__(self, status_code=200, json_data=None):
        self.status_code = status_code
        self._json = json_data if json_data is not None else {}

    def json(self):
        return self._json


class FakeHTTP:
    """Stand-in for place_detail.http_requests; .get/.post raise unless wired."""
    def __init__(self, get=None, post=None):
        self._get = get
        self._post = post

    def get(self, url, **kw):
        if self._get is None:
            raise AssertionError(f"unexpected GET {url}")
        return self._get(url, **kw)

    def post(self, url, **kw):
        if self._post is None:
            raise AssertionError(f"unexpected POST {url}")
        return self._post(url, **kw)


def _flags(monkeypatch, enabled=()):
    monkeypatch.setattr(pd.config, 'has_feature', lambda flag: flag in set(enabled))


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('NAVI_PLACE_CACHE_DB', str(tmp_path / 'place_cache.db'))
    _flags(monkeypatch, enabled=())          # all enrichment off by default
    app = create_app()
    return app.test_client()


NOMINATIM_CAFE = {
    'osm_id': 123, 'osm_type': 'W', 'category': 'amenity', 'type': 'cafe',
    'localname': 'Test Cafe', 'centroid': {'coordinates': [-114.6, 42.5]},
    'extratags': {}, 'address': [],
}


# ── validation ──

def test_bad_osm_type_400(client):
    assert client.get('/api/place/way/123').status_code == 400  # "way" not N/W/R


def test_zero_osm_id_400(client):
    assert client.get('/api/place/N/0').status_code == 400


# ── cache ──

def test_cache_hit_no_upstream(client, monkeypatch):
    place_cache.cache_put('N', 123, {'name': 'Cached', 'extratags': {}}, 'nominatim_local')
    monkeypatch.setattr(pd, 'http_requests', FakeHTTP())  # raises if called
    resp = client.get('/api/place/N/123')
    assert resp.status_code == 200
    d = resp.get_json()
    assert d['name'] == 'Cached' and d['source'] == 'cache'


def test_nominatim_hit(client, monkeypatch):
    monkeypatch.setattr(pd, 'http_requests', FakeHTTP(get=lambda url, **kw: FakeResp(200, NOMINATIM_CAFE)))
    resp = client.get('/api/place/W/123')
    assert resp.status_code == 200
    d = resp.get_json()
    assert d['name'] == 'Test Cafe' and d['source'] == 'nominatim_local'
    assert d['category'] == 'Coffee shop'


def test_nominatim_miss_then_overpass_fallback(client, monkeypatch):
    # Nominatim returns a non-matching osm_id -> no match; Overpass returns the element.
    overpass = {'elements': [{'tags': {'amenity': 'cafe', 'name': 'OP Cafe'},
                              'center': {'lat': 42.5, 'lon': -114.6}}]}
    monkeypatch.setattr(pd, 'http_requests', FakeHTTP(
        get=lambda url, **kw: FakeResp(200, {'osm_id': 999}),     # mismatch
        post=lambda url, **kw: FakeResp(200, overpass),
    ))
    resp = client.get('/api/place/W/123')
    assert resp.status_code == 200
    d = resp.get_json()
    assert d['source'] == 'overpass' and d['name'] == 'OP Cafe'


def test_both_sources_error_502(client, monkeypatch):
    def boom(url, **kw):
        raise RuntimeError('down')
    monkeypatch.setattr(pd, 'http_requests', FakeHTTP(get=boom, post=boom))
    assert client.get('/api/place/W/123').status_code == 502


def test_not_found_404(client, monkeypatch):
    # Nominatim mismatch (no error) + Overpass empty (no error) -> 404
    monkeypatch.setattr(pd, 'http_requests', FakeHTTP(
        get=lambda url, **kw: FakeResp(200, {'osm_id': 999}),
        post=lambda url, **kw: FakeResp(200, {'elements': []}),
    ))
    assert client.get('/api/place/W/123').status_code == 404


# ── wikidata route ──

def test_wikidata_happy(client, monkeypatch):
    entity = {'entities': {'Q42': {
        'labels': {'en': {'value': 'Douglas Adams Place'}},
        'descriptions': {'en': {'value': 'a place'}},
        'claims': {'P625': [{'mainsnak': {'datavalue': {'value': {'latitude': 42.5, 'longitude': -114.6}}}}]},
        'sitelinks': {},
    }}}
    monkeypatch.setattr(pd, 'http_requests', FakeHTTP(get=lambda url, **kw: FakeResp(200, entity)))
    resp = client.get('/api/place/wikidata/Q42')
    assert resp.status_code == 200
    d = resp.get_json()
    assert d['wikidata_id'] == 'Q42' and d['name'] == 'Douglas Adams Place'


def test_wikidata_invalid_400(client):
    assert client.get('/api/place/wikidata/not-a-qid').status_code == 400


# ── enrichment gating ──

def test_overture_gated_off_no_pg_call(client, monkeypatch):
    # has_overture_enrichment is OFF (default) -> overture must not be called.
    def explode(*a, **k):
        raise AssertionError('overture should not be called when gated off')
    monkeypatch.setattr(pd.overture, 'find_by_osm_id', explode)
    monkeypatch.setattr(pd.overture, 'find_by_coords_and_name', explode)
    monkeypatch.setattr(pd, 'http_requests', FakeHTTP(get=lambda url, **kw: FakeResp(200, NOMINATIM_CAFE)))
    assert client.get('/api/place/W/123').status_code == 200


# ── wiki rewrite via local Kiwix ──

def test_wiki_rewrite_local_hit(tmp_path, monkeypatch):
    monkeypatch.setenv('NAVI_PLACE_CACHE_DB', str(tmp_path / 'pc.db'))
    _flags(monkeypatch, enabled=('has_wiki_rewriting',))
    nom = {**NOMINATIM_CAFE, 'extratags': {'wikipedia': 'en:Filer, Idaho'}}
    monkeypatch.setattr(pd, 'http_requests', FakeHTTP(get=lambda url, **kw: FakeResp(200, nom)))
    monkeypatch.setattr(pd.wiki_rewrite, 'rewrite_wiki_link',
                        lambda tag, value: ('https://wiki.echo6.co/content/z/Filer,_Idaho', 'local'))
    client = create_app().test_client()
    d = client.get('/api/place/W/123').get_json()
    assert d['extratags']['wikipedia'] == 'https://wiki.echo6.co/content/z/Filer,_Idaho'
    assert d['sources']['wiki_rewrites']['wikipedia'] == 'local'


def test_wiki_rewrite_original_passes_through(tmp_path, monkeypatch):
    monkeypatch.setenv('NAVI_PLACE_CACHE_DB', str(tmp_path / 'pc.db'))
    _flags(monkeypatch, enabled=('has_wiki_rewriting',))
    nom = {**NOMINATIM_CAFE, 'extratags': {'wikipedia': 'en:Nowhere'}}
    monkeypatch.setattr(pd, 'http_requests', FakeHTTP(get=lambda url, **kw: FakeResp(200, nom)))
    monkeypatch.setattr(pd.wiki_rewrite, 'rewrite_wiki_link',
                        lambda tag, value: (value, 'original'))
    client = create_app().test_client()
    d = client.get('/api/place/W/123').get_json()
    assert d['extratags']['wikipedia'] == 'en:Nowhere'  # unchanged
    assert 'wikipedia' not in d.get('sources', {}).get('wiki_rewrites', {})


# ── wiki index summary via local wiki_index.db ──

def test_wiki_enrich_via_local_db_merges_fields(tmp_path, monkeypatch):
    monkeypatch.setenv('NAVI_PLACE_CACHE_DB', str(tmp_path / 'pc.db'))
    # Hermetic wiki_index.db: one wiki_places row keyed by wikidata_id.
    wi_path = tmp_path / 'wi.db'
    conn = sqlite3.connect(str(wi_path))
    conn.execute(
        "CREATE TABLE wiki_places (wikidata_id TEXT, place_name TEXT, "
        "country_code TEXT, summary TEXT, wiki_population INTEGER, "
        "wikipedia_title TEXT, wikivoyage_title TEXT)")
    conn.execute(
        "INSERT INTO wiki_places (wikidata_id, summary, wikipedia_title) VALUES (?,?,?)",
        ('Q830149', 'A city.', 'Filer'))
    conn.commit()
    conn.close()
    monkeypatch.setenv('NAVI_WIKI_INDEX_DB', str(wi_path))
    wiki_index.reset()
    _flags(monkeypatch, enabled=('has_kiwix_wiki',))
    nom = {**NOMINATIM_CAFE, 'extratags': {'wikidata': 'Q830149'}}
    monkeypatch.setattr(pd, 'http_requests', FakeHTTP(get=lambda url, **kw: FakeResp(200, nom)))
    client = create_app().test_client()
    d = client.get('/api/place/W/123').get_json()
    assert d['wiki_summary'] == 'A city.'
    assert d['wiki_url'] == 'https://en.wikipedia.org/wiki/Filer'
