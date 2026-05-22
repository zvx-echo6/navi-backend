"""Tests for navi-contacts address_book — ported from recon's lib/address_book_test.py.

Uses a tmp fixture YAML (mirroring the vendored home entry) pointed at via
NAVI_ADDRESS_BOOK_YAML, exercising lookup() confidence levels and list_all().
"""
import pytest

import services.navi_contacts.address_book as ab

FIXTURE_YAML = """\
entries:
  - id: home
    name: Home
    aliases:
      - home
      - matt's house
      - 214 north st
      - 214 north street
    address: "214 North St, Filer, ID 83328"
    lat: 42.5735833
    lon: -114.6066389
    tags:
      - residence
      - primary
"""


@pytest.fixture
def book(tmp_path, monkeypatch):
    f = tmp_path / 'address_book.yaml'
    f.write_text(FIXTURE_YAML)
    monkeypatch.setenv('NAVI_ADDRESS_BOOK_YAML', str(f))
    ab.reset_cache()
    return f


def test_lookup_exact_name(book):
    r = ab.lookup('home')
    assert r is not None and r['id'] == 'home' and r['confidence'] == 'exact'


def test_lookup_case_insensitive(book):
    r = ab.lookup('Home')
    assert r is not None and r['confidence'] == 'exact'


def test_lookup_alias_address_exact(book):
    assert ab.lookup('214 north st')['confidence'] == 'exact'
    assert ab.lookup('214 North Street')['confidence'] == 'exact'


def test_lookup_comma_normalization(book):
    # commas stripped; "214 north st" prefix + word boundary -> exact (rule 2)
    r = ab.lookup('214 north st, filer, id')
    assert r is not None and r['id'] == 'home' and r['confidence'] == 'exact'


def test_lookup_query_with_trailing_words_is_exact(book):
    # query starts with a full alias + word boundary -> exact (rule 2)
    assert ab.lookup('214 north st filer')['confidence'] == 'exact'
    assert ab.lookup('214 North St Filer ID')['confidence'] == 'exact'
    assert ab.lookup('home today')['confidence'] == 'exact'


def test_lookup_partial_prefix(book):
    # query is a prefix of an alias (user still typing) -> partial
    assert ab.lookup('214')['confidence'] == 'partial'
    assert ab.lookup('214 n')['confidence'] == 'partial'


def test_lookup_miss(book):
    assert ab.lookup('nonexistent place') is None


def test_lookup_empty(book):
    assert ab.lookup('') is None
    assert ab.lookup('   ') is None


def test_list_all(book):
    entries = ab.list_all()
    assert len(entries) == 1
    e = entries[0]
    assert e['id'] == 'home' and e['lat'] == 42.5735833
    # aliases normalized to lowercase at load
    assert all(a == a.lower() for a in e['aliases'])


def test_missing_file_is_empty(tmp_path, monkeypatch):
    monkeypatch.setenv('NAVI_ADDRESS_BOOK_YAML', str(tmp_path / 'does_not_exist.yaml'))
    ab.reset_cache()
    assert ab.list_all() == []
    assert ab.lookup('home') is None


def test_hot_reload_on_change(book):
    assert len(ab.list_all()) == 1
    # rewrite with an extra entry; mtime changes -> reload picks it up
    book.write_text(FIXTURE_YAML + """\
  - id: work
    name: Work
    aliases: [work, office]
    address: "100 Main St"
    lat: 42.6
    lon: -114.5
    tags: [work]
""")
    import os, time
    os.utime(book, (time.time() + 1, time.time() + 1))  # ensure mtime differs
    assert len(ab.list_all()) == 2
    assert ab.lookup('office')['id'] == 'work'
