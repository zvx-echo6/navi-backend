"""Hermetic unit tests for the navi-geo geocode engine.

Phase A §"Tests" flagged that recon's geocode_test.py is a *live* smoke test
(hits localhost:8420 + Photon + Netsyms). These are the CI-friendly equivalents:
they test the intent classifier, the reranker scoring, match-code building,
dedup, and the two short-circuits — with every upstream mocked. Assertions test
*meaning* (ordering, classification, signal presence), not magic score numbers.
"""
import pytest

import services.navi_geo.geocode as gc
from services.navi_geo.app import create_app


# ── Intent classification + parsing ──────────────────────────────────────

def test_classify_street_address():
    intent, parsed = gc._classify_and_parse("214 North St, Filer, ID")
    assert intent == 'ADDRESS'
    assert parsed['number'] == '214'
    assert parsed['state'] == 'ID'


def test_classify_coordinates():
    intent, _ = gc._classify_and_parse("43.6150, -116.2023")
    assert intent == 'COORD'


def test_classify_locality_from_state_suffix():
    intent, parsed = gc._classify_and_parse("Filer ID")
    assert intent == 'LOCALITY'
    assert parsed['state'] == 'ID'
    assert parsed['city'] == 'Filer'


def test_classify_full_state_name():
    intent, parsed = gc._classify_and_parse("Boise Idaho")
    assert intent == 'LOCALITY'
    assert parsed['state'] == 'ID'


def test_street_type_abbreviation_expands_in_query():
    # "st" must expand to "street" in the Photon-bound expanded query, while the
    # raw street (for Netsyms) keeps the original abbreviation.
    _, parsed = gc._classify_and_parse("100 Main St, Boise, ID")
    assert 'street' in parsed['expanded_query'].lower()
    assert 'st' in parsed['street_raw'].lower().split()


# ── Reranker scoring (relative, not magic numbers) ────────────────────────

def _addr_parsed(number='214', street='NORTH', city='FILER', state='ID'):
    return {'number': number, 'street': street, 'city': city, 'state': state,
            'raw_query': f'{number} {street} {city} {state}'}


def test_exact_housenumber_outranks_mismatch():
    parsed = _addr_parsed()
    exact = {'_number': '214', '_street': 'NORTH', '_city': 'FILER', '_state': 'ID',
             'name': '214 North', 'source': 'netsyms', 'type': 'street_address', 'raw': {}}
    wrong = {'_number': '999', '_street': 'NORTH', '_city': 'FILER', '_state': 'ID',
             'name': '999 North', 'source': 'netsyms', 'type': 'street_address', 'raw': {}}
    s_exact, sig_exact = gc._score_candidate(exact, parsed, 'ADDRESS')
    s_wrong, _ = gc._score_candidate(wrong, parsed, 'ADDRESS')
    assert s_exact > s_wrong
    assert 'housenumber_exact' in sig_exact


def test_netsyms_source_authority_only_for_address_intent():
    parsed = _addr_parsed()
    cand = {'_number': '214', '_street': 'NORTH', '_city': 'FILER', '_state': 'ID',
            'name': '214 North', 'source': 'netsyms', 'type': 'street_address', 'raw': {}}
    _, sig_addr = gc._score_candidate(cand, parsed, 'ADDRESS')
    _, sig_poi = gc._score_candidate(cand, parsed, 'POI')
    assert 'source_authority' in sig_addr
    assert 'source_authority' not in sig_poi


def test_poi_class_boost_and_highway_penalty_for_business_query():
    parsed = {'raw_query': 'joes coffee'}            # no road keywords
    shop = {'name': 'Joes Coffee', 'source': 'photon', 'type': 'poi',
            'raw': {'osm_key': 'amenity'}}
    road = {'name': 'Joes Coffee Rd', 'source': 'photon', 'type': 'poi',
            'raw': {'osm_key': 'highway'}}
    s_shop, sig_shop = gc._score_candidate(shop, parsed, 'POI')
    s_road, sig_road = gc._score_candidate(road, parsed, 'POI')
    assert 'poi_class_boost' in sig_shop
    assert 'highway_class_penalty' in sig_road
    assert s_shop > s_road


def test_match_code_housenumber_matched_vs_unmatched():
    parsed = _addr_parsed()
    matched = gc._build_match_code({'_number': '214', '_street': 'NORTH', '_city': 'FILER'},
                                   parsed, 'ADDRESS')
    unmatched = gc._build_match_code({'_number': '999', '_street': 'NORTH', '_city': 'FILER'},
                                     parsed, 'ADDRESS')
    assert matched['housenumber'] == 'matched'
    assert unmatched['housenumber'] == 'unmatched'


# ── geocode() short-circuits + retrieval (upstreams mocked) ───────────────

def test_geocode_empty_query_returns_empty():
    assert gc.geocode("") == {'query': '', 'results': [], 'count': 0}


def test_geocode_coordinate_short_circuit_no_upstream(monkeypatch):
    # A coordinate string must not touch Photon/Netsyms/address_book at all.
    monkeypatch.setattr(gc.requests, 'get', lambda *a, **k: pytest.fail("no upstream"))
    out = gc.geocode("43.6150, -116.2023")
    assert out['count'] == 1
    r = out['results'][0]
    assert r['source'] == 'coordinates' and r['type'] == 'coordinates'
    assert r['lat'] == 43.6150 and r['lon'] == -116.2023


def test_geocode_nickname_short_circuit(monkeypatch):
    import services.navi_geo.address_book as ab
    monkeypatch.setattr(ab, 'lookup', lambda q: {
        'name': 'Home', 'address': '1 Main St', 'lat': 43.6, 'lon': -116.2,
        'confidence': 'exact'})
    monkeypatch.setattr(gc.requests, 'get', lambda *a, **k: pytest.fail("no upstream"))
    out = gc.geocode("home")           # single word + exact -> short-circuit
    assert out['count'] == 1
    assert out['results'][0]['source'] == 'address_book'
    assert out['results'][0]['type'] == 'nickname'


def test_geocode_address_ranks_exact_housenumber_first(monkeypatch):
    import services.navi_geo.netsyms as ns
    import services.navi_geo.address_book as ab
    monkeypatch.setattr(ab, 'lookup', lambda q: None)
    monkeypatch.setattr(ab, 'load', lambda: [])
    # Netsyms returns the exact match; Photon returns a wrong-number distractor.
    monkeypatch.setattr(ns, 'lookup_by_street', lambda *a, **k: [{
        'number': '214', 'street': 'NORTH', 'street2': None, 'city': 'FILER',
        'state': 'ID', 'zipcode': '83328', 'lat': 42.57, 'lon': -114.6,
        'source': 'netsyms'}])

    class FakeResp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {'features': [{
                'properties': {'housenumber': '999', 'street': 'North',
                               'city': 'Filer', 'state': 'ID', 'osm_key': 'place'},
                'geometry': {'coordinates': [-114.61, 42.58]}}]}
    monkeypatch.setattr(gc.requests, 'get', lambda *a, **k: FakeResp())

    out = gc.geocode("214 North St, Filer, ID", limit=10)
    assert out['count'] >= 1
    top = out['results'][0]
    assert top['source'] == 'netsyms'              # exact-housenumber netsyms wins
    assert top['confidence'] in ('exact', 'high')


def test_geocode_dedup_collapses_near_duplicates(monkeypatch):
    import services.navi_geo.address_book as ab
    monkeypatch.setattr(ab, 'lookup', lambda q: None)
    monkeypatch.setattr(ab, 'load', lambda: [])

    class FakeResp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            # Two features ~0 m apart, same source -> dedup to one.
            f = {'properties': {'name': 'Park', 'osm_key': 'leisure'},
                 'geometry': {'coordinates': [-116.2, 43.6]}}
            return {'features': [f, dict(f)]}
    monkeypatch.setattr(gc.requests, 'get', lambda *a, **k: FakeResp())
    out = gc.geocode("park", limit=10)
    assert out['count'] == 1


# ── Trace logger is opt-in (Phase B locked decision #3) ───────────────────

def test_trace_logger_off_by_default(monkeypatch):
    monkeypatch.delenv('NAVI_GEO_RERANK_TRACE_LOG', raising=False)
    gc.setup_trace_logger()
    assert not any(isinstance(h, gc.logging.FileHandler) for h in gc._trace_logger.handlers)


def test_trace_logger_attaches_when_env_set(tmp_path, monkeypatch):
    path = tmp_path / 'trace.log'
    monkeypatch.setenv('NAVI_GEO_RERANK_TRACE_LOG', str(path))
    gc.setup_trace_logger()
    assert any(isinstance(h, gc.logging.FileHandler) for h in gc._trace_logger.handlers)
    monkeypatch.delenv('NAVI_GEO_RERANK_TRACE_LOG', raising=False)
    gc.setup_trace_logger()      # restore default-off for other tests


# ── admin-info: no secrets, expected probes ───────────────────────────────

def test_admin_info_has_no_secrets_and_two_probes(monkeypatch):
    # require_auth needs the Authentik header; the edge would supply it.
    client = create_app().test_client()
    resp = client.get('/api/admin/navi-geo/info',
                      headers={'X-Authentik-Username': 'matt'})
    assert resp.status_code == 200
    info = resp.get_json()
    assert info['service'] == 'navi-geo' and info['port'] == 8426
    # No masked secrets present (Phase A §10) — no value contains the mask marker.
    assert all('...' not in str(e['value']) and e['value'] != '****' for e in info['env'])
    names = {d['name'] for d in info['dependencies']}
    assert names == {'photon', 'navi-landclass'}


def test_admin_info_requires_auth():
    client = create_app().test_client()
    assert client.get('/api/admin/navi-geo/info').status_code == 401


def test_admin_info_netsyms_entry_enriched_with_health(tmp_path, monkeypatch):
    # netsyms.health() is wired into the netsyms filesystem entry (review fix #3):
    # the entry carries row_count / file_size_bytes / indexed_countries on top of
    # the standard path/exists/readable. Use a tiny real sqlite so health() runs.
    import sqlite3
    db = tmp_path / 'netsyms.sqlite'
    con = sqlite3.connect(db)
    con.execute('CREATE TABLE addresses (country TEXT)')
    con.executemany('INSERT INTO addresses (country) VALUES (?)',
                    [('US',), ('US',), ('CA',)])
    con.commit()
    con.close()
    monkeypatch.setenv('NAVI_NETSYMS_DB', str(db))

    client = create_app().test_client()      # reset_conn() picks up the new path
    resp = client.get('/api/admin/navi-geo/info',
                      headers={'X-Authentik-Username': 'matt'})
    assert resp.status_code == 200
    fs = resp.get_json()['filesystem']
    netsyms_entry = next(e for e in fs if e['path'] == str(db))
    assert netsyms_entry['ok'] is True
    assert netsyms_entry['row_count'] == 3
    assert netsyms_entry['file_size_bytes'] > 0
    assert set(netsyms_entry['indexed_countries']) == {'US', 'CA'}
