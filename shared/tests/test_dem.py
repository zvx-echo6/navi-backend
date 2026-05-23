"""Hermetic tests for shared.dem — no live PMTiles I/O (the real file is 657 GB).

Behavioural coverage of DEMReader.sample_point already lives in navi-geo's
test_reverse_bundle.py (via the geo_route._DEM mock); these only pin the shared
module's import + the env-override path contract.
"""
from shared.dem import DEMReader, dem_path, DEFAULT_DEM_PATH  # noqa: F401 (import smoke)


def test_dem_path_default_when_unset(monkeypatch):
    monkeypatch.delenv('NAVI_DEM_PMTILES', raising=False)
    assert dem_path() == DEFAULT_DEM_PATH


def test_dem_path_env_override(monkeypatch, tmp_path):
    custom = tmp_path / 'custom.pmtiles'
    monkeypatch.setenv('NAVI_DEM_PMTILES', str(custom))
    assert str(dem_path()) == str(custom)
