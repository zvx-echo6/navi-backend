"""Place detail orchestrator — port of recon's lib/place_detail.py.

Local Nominatim first, Overpass fallback, SQLite cache, then enrichment:
Overture (PostGIS) + Google Places + wiki. Both wiki steps are now in-process
local reads (no recon HTTP) — wiki_index summary from navi-places' own
wiki_index.db, and the Kiwix offline-wiki rewrite from the local Kiwix catalog
+ wiki_cache.db:
  - wiki_index summary/links  -> wiki_index.lookup  (local wiki_index.db, NAVI_WIKI_INDEX_DB)
  - Kiwix offline-wiki rewrite -> wiki_rewrite.rewrite_wiki_link  (local Kiwix catalog + wiki_cache.db, NAVI_WIKI_CACHE_DB)
Feature-flag gates (has_overture_enrichment / has_google_places_enrichment /
has_kiwix_wiki / has_wiki_rewriting) read from the vendored profile via config.py.
"""
import logging

import requests as http_requests
from flask import has_request_context, request

from shared.auth import get_user_id

from . import config
from . import overture
from . import google_places
from . import wiki_index
from . import wiki_rewrite
from .osm_categories import humanize_category
from .place_cache import cache_get, cache_put

logger = logging.getLogger('navi_places.place_detail')

NOMINATIM_URL = "http://localhost:8010/details.php"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
OVERPASS_UA = "Navi/1.0 (forge.echo6.co/matt/recon)"
WIKIDATA_API_URL = "https://www.wikidata.org/w/api.php"
VALID_OSM_TYPES = {"N", "W", "R"}


# ── Overture enrichment ─────────────────────────────────────────────────

def _enrich_with_overture(result, osm_type, osm_id):
    """Enrich a place result with Overture data (fills sparse extratags)."""
    if not config.has_feature('has_overture_enrichment'):
        return result

    enrichment = None
    match_method = None

    enrichment = overture.find_by_osm_id(osm_type, osm_id)
    if enrichment:
        match_method = 'osm_xref'

    if not enrichment and result.get('centroid') and result.get('name'):
        centroid = result['centroid']
        if centroid.get('lat') and centroid.get('lon'):
            enrichment = overture.find_by_coords_and_name(
                centroid['lat'], centroid['lon'], result['name']
            )
            if enrichment:
                match_method = 'coord_name_fuzzy'

    if not enrichment:
        return result

    extratags = result.get('extratags', {})
    fill_map = [
        ('phone', 'phone'),
        ('website', 'website'),
        ('brand', 'brand_name'),
        ('brand:wikidata', 'brand_wikidata'),
    ]
    for osm_key, overture_key in fill_map:
        if not extratags.get(osm_key) and enrichment.get(overture_key):
            extratags[osm_key] = enrichment[overture_key]
    result['extratags'] = extratags

    result['sources'] = {
        'primary': result.get('source', 'unknown'),
        'enrichment': 'overture',
        'overture_match_method': match_method,
        'overture_gers_id': enrichment.get('gers_id'),
        'overture_confidence': enrichment.get('confidence'),
        'overture_basic_category': enrichment.get('basic_category'),
    }
    logger.debug(f"Overture enrichment for {osm_type}/{osm_id}: {match_method}")
    return result


# ── Google Places enrichment (tertiary, gap-fill only) ──────────────

_BUSINESS_CLASSES = {'amenity', 'shop', 'tourism', 'leisure', 'office', 'craft'}
_GOOGLE_GAP_FIELDS = ('opening_hours', 'phone', 'website')


def _enrich_with_google(result, osm_type, osm_id):
    """Tertiary gap-fill via Google Places (New) for business POIs."""
    if not config.has_feature('has_google_places_enrichment'):
        return result

    poi_class = result.get('class', '')
    if poi_class not in _BUSINESS_CLASSES:
        return result

    extratags = result.get('extratags', {})
    gaps = [f for f in _GOOGLE_GAP_FIELDS if not extratags.get(f)]
    if not gaps:
        return result

    cached_pid, cached_data = google_places.cache_get_google(osm_type, osm_id)
    if cached_pid and cached_data:
        _apply_google_data(result, cached_data, gaps)
        result.setdefault('sources', {})['google_places'] = {
            'place_id': cached_pid, 'source': 'cache',
        }
        return result

    if cached_pid is not None:
        return result

    # Skip new (paid) Google API calls for guest users / outside a request.
    if not (has_request_context() and get_user_id(request)):
        return result

    if not google_places.check_daily_cap():
        return result

    name = result.get('name', '')
    centroid = result.get('centroid', {})
    lat = centroid.get('lat')
    lon = centroid.get('lon')
    if not name or not lat or not lon:
        return result

    place_id = google_places.search_place(name, lat, lon)
    if not place_id:
        google_places.cache_put_google(osm_type, osm_id, '__miss__', None)
        return result

    details = google_places.get_place_details(place_id)
    if not details:
        google_places.cache_put_google(osm_type, osm_id, place_id, None)
        return result

    google_places.cache_put_google(osm_type, osm_id, place_id, details)
    _apply_google_data(result, details, gaps)
    result.setdefault('sources', {})['google_places'] = {
        'place_id': place_id, 'source': 'api', 'daily_count': google_places.get_daily_count(),
    }
    return result


def _apply_google_data(result, google_data, gaps):
    """Apply Google Places data to fill gap fields only."""
    extratags = result.get('extratags', {})
    if 'opening_hours' in gaps:
        osm_hours = google_data.get('opening_hours')
        if osm_hours:
            extratags['opening_hours'] = osm_hours
        elif google_data.get('opening_hours_raw'):
            extratags['opening_hours_raw'] = google_data['opening_hours_raw']
    if 'phone' in gaps and google_data.get('phone_number'):
        extratags['phone'] = google_data['phone_number']
    if 'website' in gaps and google_data.get('website'):
        extratags['website'] = google_data['website']
    result['extratags'] = extratags


# ── Wiki enrichment: wiki_index (local DB) + Kiwix link rewrite (local catalog + cache) ──

def _enrich_with_wiki_index(result):
    """Merge wiki_index fields (summary/population/urls) via a direct local read
    of wiki_index.db. Port of recon's in-process lookup. Gated on has_kiwix_wiki."""
    if not config.has_feature('has_kiwix_wiki'):
        return result

    extratags = result.get('extratags') or {}
    wikidata_id = result.get('wikidata_id') or extratags.get('wikidata')
    if isinstance(wikidata_id, str) and wikidata_id.startswith('http'):
        wikidata_id = wikidata_id.split('/')[-1]
    address = result.get('address') or {}
    name = result.get('name')
    country_code = address.get('country_code') or result.get('country_code')

    fields = wiki_index.lookup(
        wikidata_id=wikidata_id, name=name, country_code=country_code
    )
    if fields:
        for k in ('wiki_summary', 'wiki_population', 'wiki_url', 'wikivoyage_url'):
            if fields.get(k) is not None:
                result[k] = fields[k]
    return result


def _enrich_wiki_links(result):
    """Per-tag local Kiwix rewrite. Mirrors recon's in-process _enrich_wiki_links
    loop: for each of the 4 wiki tag keys present in extratags, rewrite to a
    local Kiwix URL when the article is mirrored; for any status != 'original',
    set extratags[tag] = url + record under sources.wiki_rewrites[tag] = status.
    Gated on has_wiki_rewriting."""
    if not config.has_feature('has_wiki_rewriting'):
        return result

    extratags = result.get('extratags') or {}
    sources_wr = result.setdefault('sources', {}).setdefault('wiki_rewrites', {})
    for tag in ('wikipedia', 'wikidata', 'wikivoyage', 'appropedia'):
        value = extratags.get(tag)
        if not value:
            continue
        url, status = wiki_rewrite.rewrite_wiki_link(tag, value)
        if status and status != 'original':
            extratags[tag] = url
            sources_wr[tag] = status
    return result


# ── Nominatim parsing ───────────────────────────────────────────────────

RANK_TO_FIELD = {
    4: 'country', 5: 'postcode', 6: 'state', 8: 'state', 12: 'county',
    16: 'city', 20: 'neighbourhood', 22: 'neighbourhood', 26: 'road', 28: 'house_number',
}


def _parse_nominatim_address(address_array, country_code=None):
    """Parse Nominatim's ranked address array into a flat address dict."""
    addr = {
        'house_number': None, 'road': None, 'neighbourhood': None, 'city': None,
        'county': None, 'state': None, 'postcode': None, 'country': None,
        'country_code': country_code,
    }
    if not address_array:
        return addr

    for entry in address_array:
        if not entry.get('isaddress', False):
            continue
        name = entry.get('localname', '')
        rank = entry.get('rank_address', 0)
        etype = entry.get('type', '')
        eclass = entry.get('class', '')

        if etype == 'country' and eclass == 'place':
            addr['country'] = name
        elif etype == 'state' or (eclass == 'boundary' and etype == 'administrative' and rank == 8):
            if not addr['state']:
                addr['state'] = name
        elif etype == 'county' or (eclass == 'boundary' and etype == 'administrative' and rank in (10, 12)):
            if not addr['county']:
                addr['county'] = name
        elif etype in ('city', 'town', 'village', 'hamlet') and eclass == 'place':
            if not addr['city']:
                addr['city'] = name
        elif eclass == 'boundary' and etype == 'administrative' and rank == 16:
            if not addr['city']:
                addr['city'] = name
        elif etype == 'postcode':
            addr['postcode'] = name
        elif eclass == 'highway' or rank == 26:
            if not addr['road']:
                addr['road'] = name
        elif etype == 'house_number' or rank == 28:
            addr['house_number'] = name
        elif rank in (20, 22) and not addr['neighbourhood']:
            addr['neighbourhood'] = name

    addr.pop('county', None)
    return addr


def _parse_nominatim(data):
    """Parse a Nominatim /details response into our canonical shape."""
    osm_type = data.get('osm_type', '')
    osm_id = data.get('osm_id', 0)
    osm_class = data.get('category', '')
    osm_type_tag = data.get('type', '')

    centroid_geom = data.get('centroid', {})
    coords = centroid_geom.get('coordinates', [0, 0])
    centroid = {'lat': coords[1], 'lon': coords[0]} if len(coords) >= 2 else {'lat': 0, 'lon': 0}

    names = data.get('names', {})
    display_name = data.get('localname', '') or names.get('name', '')

    address = _parse_nominatim_address(data.get('address', []), country_code=data.get('country_code'))
    if not address.get('postcode') and data.get('calculated_postcode'):
        address['postcode'] = data['calculated_postcode']

    raw_extra = data.get('extratags', {})
    extratags = {
        'opening_hours': raw_extra.get('opening_hours'),
        'phone': raw_extra.get('phone') or raw_extra.get('contact:phone'),
        'website': raw_extra.get('website') or raw_extra.get('contact:website') or raw_extra.get('url'),
        'email': raw_extra.get('email') or raw_extra.get('contact:email'),
        'wikipedia': raw_extra.get('wikipedia'),
        'wikidata': raw_extra.get('wikidata'),
        'cuisine': raw_extra.get('cuisine'),
        'operator': raw_extra.get('operator'),
        'wheelchair': raw_extra.get('wheelchair'),
        'fee': raw_extra.get('fee'),
        'takeaway': raw_extra.get('takeaway'),
    }

    effective_class = osm_class
    effective_type = osm_type_tag
    if osm_class == 'boundary' and osm_type_tag == 'administrative':
        place_tag = raw_extra.get('place') or raw_extra.get('linked_place')
        if place_tag:
            effective_class = 'place'
            effective_type = place_tag

    category = humanize_category(effective_class, effective_type)
    extra_names = {k: v for k, v in names.items() if k != 'name'} if names else {}

    boundary = None
    geom = data.get('geometry')
    if geom and geom.get('type') in ('Polygon', 'MultiPolygon'):
        boundary = geom

    return {
        'osm_type': osm_type, 'osm_id': osm_id, 'name': display_name,
        'category': category, 'class': osm_class, 'type': osm_type_tag,
        'address': address, 'centroid': centroid, 'extratags': extratags,
        'names': extra_names if extra_names else None,
        'source': 'nominatim_local', 'boundary': boundary,
    }


# ── Overpass parsing ────────────────────────────────────────────────────

OVERPASS_TYPE_MAP = {'N': 'node', 'W': 'way', 'R': 'relation'}


def _build_overpass_query(osm_type, osm_id):
    elem = OVERPASS_TYPE_MAP.get(osm_type)
    if not elem:
        return None
    return f"[out:json][timeout:10];{elem}({osm_id});out tags center;"


def _parse_overpass(data, osm_type, osm_id):
    elements = data.get('elements', [])
    if not elements:
        return None

    elem = elements[0]
    tags = elem.get('tags', {})

    lat = elem.get('lat') or (elem.get('center', {}).get('lat'))
    lon = elem.get('lon') or (elem.get('center', {}).get('lon'))
    centroid = {'lat': lat, 'lon': lon} if lat and lon else {'lat': 0, 'lon': 0}

    osm_class = ''
    osm_type_tag = ''
    for cls in ('amenity', 'shop', 'leisure', 'tourism', 'natural', 'highway',
                'boundary', 'place', 'building', 'waterway', 'landuse', 'historic'):
        if cls in tags:
            osm_class = cls
            osm_type_tag = tags[cls]
            break

    category = humanize_category(osm_class, osm_type_tag)

    address = {
        'house_number': tags.get('addr:housenumber'),
        'road': tags.get('addr:street'),
        'neighbourhood': tags.get('addr:suburb') or tags.get('addr:neighbourhood'),
        'city': tags.get('addr:city'),
        'state': tags.get('addr:state'),
        'postcode': tags.get('addr:postcode'),
        'country': tags.get('addr:country'),
        'country_code': tags.get('addr:country_code', tags.get('addr:country', '')).lower()[:2] or None,
    }

    extratags = {
        'opening_hours': tags.get('opening_hours'),
        'phone': tags.get('phone') or tags.get('contact:phone'),
        'website': tags.get('website') or tags.get('contact:website') or tags.get('url'),
        'email': tags.get('email') or tags.get('contact:email'),
        'wikipedia': tags.get('wikipedia'),
        'wikidata': tags.get('wikidata'),
        'cuisine': tags.get('cuisine'),
        'operator': tags.get('operator'),
        'wheelchair': tags.get('wheelchair'),
        'fee': tags.get('fee'),
        'takeaway': tags.get('takeaway'),
    }

    name = tags.get('name', '')
    extra_names = {}
    for k, v in tags.items():
        if k.startswith('name:') or k in ('alt_name', 'old_name', 'short_name', 'official_name'):
            extra_names[k] = v

    return {
        'osm_type': osm_type, 'osm_id': osm_id, 'name': name,
        'category': category, 'class': osm_class, 'type': osm_type_tag,
        'address': address, 'centroid': centroid, 'extratags': extratags,
        'names': extra_names if extra_names else None, 'source': 'overpass',
    }


def _enrich_all(result, osm_type, osm_id):
    """Run the enrichment chain in recon's order (overture, google, wiki rewrite,
    wiki index). Overture (PostGIS) and Google are external upstreams; both wiki
    steps are in-process local reads (no recon HTTP)."""
    result = _enrich_with_overture(result, osm_type, osm_id)
    result = _enrich_with_google(result, osm_type, osm_id)
    result = _enrich_wiki_links(result)
    result = _enrich_with_wiki_index(result)
    return result


# ── Public API ──────────────────────────────────────────────────────────

def get_place_detail(osm_type, osm_id):
    """Fetch place details for an OSM element. Returns (dict, status_code)."""
    osm_type = osm_type.upper()
    if osm_type not in VALID_OSM_TYPES:
        return {'error': f'Invalid osm_type: {osm_type}. Must be N, W, or R.'}, 400
    if osm_id <= 0:
        return {'error': 'osm_id must be a positive integer'}, 400

    cached = cache_get(osm_type, osm_id)
    if cached:
        logger.debug(f"Cache hit: {osm_type}/{osm_id}")
        return cached, 200

    nominatim_result = None
    nominatim_error = None
    try:
        resp = http_requests.get(NOMINATIM_URL, params={
            'osmtype': osm_type, 'osmid': osm_id, 'format': 'json',
            'addressdetails': 1, 'hierarchy': 0, 'keywords': 0, 'polygon_geojson': 1,
        }, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            if data.get('osm_id') == osm_id:
                nominatim_result = _parse_nominatim(data)
    except Exception as e:
        nominatim_error = str(e)
        logger.warning(f"Nominatim error for {osm_type}/{osm_id}: {e}")

    if nominatim_result:
        nominatim_result = _enrich_all(nominatim_result, osm_type, osm_id)
        cache_put(osm_type, osm_id, nominatim_result, 'nominatim_local')
        return nominatim_result, 200

    overpass_result = None
    overpass_error = None
    try:
        query = _build_overpass_query(osm_type, osm_id)
        if query:
            resp = http_requests.post(
                OVERPASS_URL, data={'data': query},
                headers={'User-Agent': OVERPASS_UA}, timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                overpass_result = _parse_overpass(data, osm_type, osm_id)
            elif resp.status_code == 429:
                overpass_error = "Overpass rate limited"
            else:
                overpass_error = f"Overpass HTTP {resp.status_code}"
    except Exception as e:
        overpass_error = str(e)
        logger.warning(f"Overpass error for {osm_type}/{osm_id}: {e}")

    if overpass_result:
        overpass_result = _enrich_all(overpass_result, osm_type, osm_id)
        cache_put(osm_type, osm_id, overpass_result, 'overpass')
        return overpass_result, 200

    if nominatim_error and overpass_error:
        logger.error(f"Both sources failed for {osm_type}/{osm_id}: "
                     f"Nominatim={nominatim_error}, Overpass={overpass_error}")
        return {'error': 'Both data sources unavailable'}, 502

    return {'error': f'{osm_type}/{osm_id} not found'}, 404


def get_place_by_wikidata(wikidata_id):
    """Fetch place details from a Wikidata entity. Returns (dict, status_code)."""
    wikidata_id = wikidata_id.upper().strip()
    if not wikidata_id.startswith("Q") or not wikidata_id[1:].isdigit():
        return {"error": f"Invalid wikidata ID: {wikidata_id}. Must be Q followed by digits."}, 400

    try:
        resp = http_requests.get(WIKIDATA_API_URL, params={
            "action": "wbgetentities", "ids": wikidata_id, "format": "json",
            "languages": "en", "props": "labels|descriptions|claims|sitelinks",
        }, timeout=10, headers={"User-Agent": OVERPASS_UA})

        if resp.status_code != 200:
            logger.warning(f"Wikidata API error for {wikidata_id}: HTTP {resp.status_code}")
            return {"error": "Wikidata API error"}, 502

        data = resp.json()
        entity = data.get("entities", {}).get(wikidata_id)
        if not entity or entity.get("missing"):
            return {"error": f"Wikidata entity {wikidata_id} not found"}, 404

        labels = entity.get("labels", {})
        descriptions = entity.get("descriptions", {})
        claims = entity.get("claims", {})

        name = labels.get("en", {}).get("value", wikidata_id)
        description = descriptions.get("en", {}).get("value", "")

        lat, lon = None, None
        if "P625" in claims:
            coord_claim = claims["P625"]
            if coord_claim and coord_claim[0].get("mainsnak", {}).get("datavalue"):
                coord_val = coord_claim[0]["mainsnak"]["datavalue"]["value"]
                lat = coord_val.get("latitude")
                lon = coord_val.get("longitude")

        population = None
        if "P1082" in claims:
            for claim in claims["P1082"]:
                if claim.get("mainsnak", {}).get("datavalue"):
                    try:
                        population = int(claim["mainsnak"]["datavalue"]["value"]["amount"].lstrip("+"))
                        break
                    except (KeyError, ValueError):
                        pass

        instance_of = []
        if "P31" in claims:
            for claim in claims["P31"]:
                if claim.get("mainsnak", {}).get("datavalue"):
                    instance_of.append(claim["mainsnak"]["datavalue"]["value"]["id"])

        osm_relation_id = None
        if "P402" in claims:
            osm_claims = claims["P402"]
            if osm_claims and osm_claims[0].get("mainsnak", {}).get("datavalue"):
                osm_relation_id = osm_claims[0]["mainsnak"]["datavalue"]["value"]

        sitelinks = entity.get("sitelinks", {})
        wikipedia = None
        if "enwiki" in sitelinks:
            wiki_title = sitelinks["enwiki"].get("title", "")
            if wiki_title:
                wikipedia = f"en:{wiki_title}"

        result = {
            "wikidata_id": wikidata_id, "name": name, "description": description,
            "centroid": {"lat": lat, "lon": lon} if lat and lon else None,
            "population": population, "instance_of": instance_of,
            "osm_relation_id": osm_relation_id, "source": "wikidata",
            "extratags": {"wikidata": wikidata_id},
        }
        if wikipedia:
            result["extratags"]["wikipedia"] = wikipedia

        boundary = None
        if osm_relation_id:
            try:
                nom_resp = http_requests.get(NOMINATIM_URL, params={
                    'osmtype': 'R', 'osmid': osm_relation_id, 'format': 'json', 'polygon_geojson': 1,
                }, timeout=5)
                if nom_resp.status_code == 200:
                    geom = nom_resp.json().get('geometry')
                    if geom and geom.get('type') in ('Polygon', 'MultiPolygon'):
                        boundary = geom
            except Exception as e:
                logger.debug(f"Wikidata boundary fetch failed: {e}")
        result["boundary"] = boundary

        result = _enrich_with_wiki_index(result)
        logger.debug(f"Wikidata hit: {wikidata_id} -> {name}")
        return result, 200

    except Exception as e:
        logger.warning(f"Wikidata error for {wikidata_id}: {e}")
        return {"error": "Wikidata lookup failed"}, 502
