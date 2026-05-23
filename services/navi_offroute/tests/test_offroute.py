"""Hermetic tests for navi-offroute (extraction #8) — service-shape, not routing
correctness. OffrouteRouter is mocked; MVUM uses a tiny fixture SQLite; admin
probes (Valhalla/PG/osmium) are mocked. No live PostGIS/Valhalla/osmium/DEM.
"""
import sqlite3

import pytest
from shapely import wkb
from shapely.geometry import Point

import services.navi_offroute.offroute_route as route_mod
import services.navi_offroute.admin as admin_mod
from services.navi_offroute.app import create_app

AUTH = {'X-Authentik-Username': 'matt'}


@pytest.fixture
def client():
    return create_app().test_client()


# ── /api/offroute — mocked router ─────────────────────────────────────────

class FakeRouter:
    instances = []
    route_result = {'status': 'ok', 'route': {'type': 'FeatureCollection', 'features': []},
                    'summary': {'total_distance_km': 1.2, 'total_effort_minutes': 30,
                                'barrier_crossings': 0, 'mvum_closed_crossings': 0}}
    raise_on_init = False
    raise_on_route = False

    def __init__(self):
        if FakeRouter.raise_on_init:
            raise RuntimeError('router init boom')
        self.closed = False
        FakeRouter.instances.append(self)

    def route(self, **kwargs):
        if FakeRouter.raise_on_route:
            raise RuntimeError('route boom')
        return FakeRouter.route_result

    def close(self):
        self.closed = True


@pytest.fixture
def fake_router(monkeypatch):
    FakeRouter.instances = []
    FakeRouter.raise_on_init = False
    FakeRouter.raise_on_route = False
    FakeRouter.route_result = {'status': 'ok', 'route': {'type': 'FeatureCollection', 'features': []},
                               'summary': {'total_distance_km': 1.2, 'total_effort_minutes': 30,
                                           'barrier_crossings': 0, 'mvum_closed_crossings': 0}}
    monkeypatch.setattr(route_mod, 'OffrouteRouter', FakeRouter)
    return FakeRouter


def _post(client, body):
    return client.post('/api/offroute', json=body)


def test_offroute_empty_body_400(client, fake_router):
    # Body parses to a falsy value (JSON null) → the "No JSON body provided" 400
    # branch. (A *malformed* body makes Flask's get_json() raise BadRequest, which
    # the outer except turns into 500 — faithful to recon; not this branch.)
    r = client.post('/api/offroute', data='null', content_type='application/json')
    assert r.status_code == 400 and r.get_json()['message'] == 'No JSON body provided'


def test_offroute_missing_coords_400(client, fake_router):
    assert _post(client, {'start': [43.6, -116.2]}).status_code == 400


def test_offroute_bad_start_shape_400(client, fake_router):
    assert _post(client, {'start': [1, 2, 3], 'end': [4, 5]}).status_code == 400


def test_offroute_bad_mode_400(client, fake_router):
    r = _post(client, {'start': [43.6, -116.2], 'end': [43.7, -116.3], 'mode': 'spaceship'})
    assert r.status_code == 400 and 'mode must be' in r.get_json()['message']


def test_offroute_bad_boundary_mode_400(client, fake_router):
    r = _post(client, {'start': [43.6, -116.2], 'end': [43.7, -116.3], 'boundary_mode': 'yolo'})
    assert r.status_code == 400 and 'boundary_mode must be' in r.get_json()['message']


def test_offroute_happy_path_shape(client, fake_router):
    r = _post(client, {'start': [43.6, -116.2], 'end': [43.7, -116.3], 'mode': 'foot',
                       'boundary_mode': 'strict'})
    assert r.status_code == 200
    d = r.get_json()
    assert d['status'] == 'ok'
    assert d['route']['type'] == 'FeatureCollection'
    # the summary keys the UI reads (ManeuverList / DirectionsPanel)
    assert {'total_distance_km', 'total_effort_minutes', 'barrier_crossings',
            'mvum_closed_crossings'} <= set(d['summary'])
    assert fake_router.instances[0].closed is True   # always closed


def test_offroute_router_status_error_is_400(client, fake_router):
    fake_router.route_result = {'status': 'error', 'message': 'no route found'}
    r = _post(client, {'start': [43.6, -116.2], 'end': [43.7, -116.3]})
    assert r.status_code == 400 and r.get_json()['message'] == 'no route found'


def test_offroute_router_init_raises_is_500(client, fake_router):
    fake_router.raise_on_init = True
    r = _post(client, {'start': [43.6, -116.2], 'end': [43.7, -116.3]})
    assert r.status_code == 500 and r.get_json()['status'] == 'error'


def test_offroute_close_called_even_when_route_raises(client, fake_router):
    fake_router.raise_on_route = True
    r = _post(client, {'start': [43.6, -116.2], 'end': [43.7, -116.3]})
    assert r.status_code == 500                      # outer except -> 500
    assert fake_router.instances[0].closed is True   # finally still closed it


# ── /api/mvum — fixture SQLite ─────────────────────────────────────────────

_ROAD_COLS = ['ogc_fid', 'id', 'name', 'forestname', 'districtname', 'symbol',
              'operationalmaintlevel', 'surfacetype', 'seasonal', 'jurisdiction',
              'passengervehicle', 'passengervehicle_datesopen',
              'highclearancevehicle', 'highclearancevehicle_datesopen',
              'atv', 'atv_datesopen', 'motorcycle', 'motorcycle_datesopen',
              'fourwd_gt50inches', 'fourwd_gt50_datesopen',
              'twowd_gt50inches', 'twowd_gt50_datesopen',
              'e_bike_class1', 'e_bike_class1_dur', 'e_bike_class2', 'e_bike_class2_dur',
              'e_bike_class3', 'e_bike_class3_dur', 'shape']
_TRAIL_COLS = ['ogc_fid', 'id', 'name', 'forestname', 'districtname', 'symbol',
               'seasonal', 'jurisdiction', 'trailclass', 'trailsystem',
               'passengervehicle', 'passengervehicle_datesopen',
               'highclearancevehicle', 'highclearancevehicle_datesopen',
               'atv', 'atv_datesopen', 'motorcycle', 'motorcycle_datesopen',
               'fourwd_gt50inches', 'fourwd_gt50_datesopen',
               'twowd_gt50inches', 'twowd_gt50_datesopen',
               'e_bike_class1', 'e_bike_class1_dur', 'e_bike_class2', 'e_bike_class2_dur',
               'e_bike_class3', 'e_bike_class3_dur', 'shape']


def _make_table(conn, table, cols, row):
    conn.execute(f"CREATE TABLE {table} ({', '.join(cols)})")
    if row is not None:
        conn.execute(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join(['?'] * len(cols))})",
                     [row.get(c) for c in cols])
    conn.commit()


def _shape_at(lat, lon):
    return wkb.dumps(Point(lon, lat))


def _mvum_db(tmp_path, monkeypatch, roads=None, trails=None):
    db = tmp_path / 'navi.db'
    conn = sqlite3.connect(db)
    if roads is not None:
        _make_table(conn, 'mvum_roads', _ROAD_COLS, roads)
    if trails is not None:
        _make_table(conn, 'mvum_trails', _TRAIL_COLS, trails)
    conn.close()
    monkeypatch.setenv('NAVI_OFFROUTE_NAVI_DB', str(db))
    return db


def test_mvum_road_happy_path(client, tmp_path, monkeypatch):
    _mvum_db(tmp_path, monkeypatch, roads={
        'ogc_fid': 1, 'id': 'FR 123', 'name': 'Some Forest Road',
        'forestname': 'Sawtooth National Forest', 'districtname': 'Ketchum RD',
        'surfacetype': 'NAT', 'operationalmaintlevel': '2 - HIGH CLEARANCE VEHICLES',
        'seasonal': 'Seasonal', 'symbol': 2,
        'passengervehicle': 'Open', 'passengervehicle_datesopen': '06/15-10/15',
        'atv': 'Open', 'shape': _shape_at(43.6150, -116.2023)})
    r = client.get('/api/mvum?lat=43.6150&lon=-116.2023&radius=500')
    assert r.status_code == 200
    d = r.get_json()
    assert d['status'] == 'ok'
    f = d['feature']
    assert f['id'] == 'FR 123' and f['forest'] == 'Sawtooth National Forest'
    assert f['maintenance_level'] == 2                       # parsed from "2 - HIGH…"
    assert f['access']['passenger_vehicle'] == {'status': 'Open', 'dates': '06/15-10/15'}
    assert set(f['access']) == {'passenger_vehicle', 'high_clearance', 'atv', 'motorcycle',
                                '4wd_gt50', '2wd_gt50', 'e_bike_class1', 'e_bike_class2', 'e_bike_class3'}


def test_mvum_falls_back_to_trails(client, tmp_path, monkeypatch):
    # No mvum_roads table → roads query returns None → trails consulted.
    _mvum_db(tmp_path, monkeypatch, trails={
        'ogc_fid': 1, 'id': 'TR 7', 'name': 'Goat Trail', 'forestname': 'Sawtooth NF',
        'trailclass': '2', 'trailsystem': 'Alpine', 'atv': 'Open',
        'shape': _shape_at(43.6150, -116.2023)})
    f = client.get('/api/mvum?lat=43.6150&lon=-116.2023&radius=500').get_json()['feature']
    assert f['id'] == 'TR 7' and f['trail_system'] == 'Alpine'


def test_mvum_no_match_returns_null_feature(client, tmp_path, monkeypatch):
    # Road exists but far outside the radius → null feature.
    _mvum_db(tmp_path, monkeypatch, roads={
        'ogc_fid': 1, 'id': 'FR 999', 'name': 'Far Road',
        'shape': _shape_at(0.0, 0.0)})
    d = client.get('/api/mvum?lat=43.6150&lon=-116.2023&radius=50').get_json()
    assert d == {'status': 'ok', 'feature': None}


def test_mvum_missing_coords_400(client):
    assert client.get('/api/mvum?lat=43.6').status_code == 400


def test_friction_reader_raises_file_not_found_when_missing(tmp_path):
    """The FileNotFoundError pre-check (review fix #2) fires before rasterio sees
    the path — consistent with the barriers/trails readers."""
    from services.navi_offroute.friction import FrictionReader
    reader = FrictionReader(tmp_path / 'does-not-exist.vrt')
    with pytest.raises(FileNotFoundError) as exc:
        reader._open()
    assert 'Friction VRT not found' in str(exc.value)


# ── admin-info — mocked probes ─────────────────────────────────────────────

def _mock_probes_ok(monkeypatch):
    class _Resp:
        status_code = 200
    monkeypatch.setattr(admin_mod.requests, 'get', lambda *a, **k: _Resp())

    class _Cur:
        def execute(self, *a): pass
        def fetchone(self): return (1,)
        def __enter__(self): return self
        def __exit__(self, *a): return False

    class _Conn:
        def cursor(self): return _Cur()
        def close(self): pass
    monkeypatch.setattr(admin_mod.psycopg2, 'connect', lambda *a, **k: _Conn())
    monkeypatch.setattr(admin_mod.subprocess, 'check_output', lambda *a, **k: 'osmium version 1.16.0\n')


def test_admin_info_auth_required(client):
    assert client.get('/api/admin/navi-offroute/info').status_code == 401


def test_admin_info_no_secrets_and_probes(client, monkeypatch):
    _mock_probes_ok(monkeypatch)
    d = client.get('/api/admin/navi-offroute/info', headers=AUTH).get_json()
    assert d['service'] == 'navi-offroute' and d['port'] == 8428
    # No masked secrets anywhere (Phase A §10 — none exist; DSN is peer-auth).
    assert all('...' not in str(e['value']) and e['value'] != '****' for e in d['env'])
    assert all('password' not in e['name'].lower() for e in d['env'])
    names = {dep['name'] for dep in d['dependencies']}
    assert names == {'valhalla', 'padus-postgis', 'osmium-tool'}
    # cheap file probes only (no row_count/size-of-db enrichment)
    fs_names = {f['name'] for f in d['filesystem']}
    assert {'dem', 'osm_pbf', 'navi_db', 'barriers_tif', 'wilderness_tif',
            'trails_tif', 'friction_vrt'} == fs_names
    assert all(set(f) == {'name', 'path', 'exists', 'readable'} for f in d['filesystem'])
