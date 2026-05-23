# navi-backend

Monorepo of small, single-responsibility HTTP services extracted from the `recon`
codebase as part of the **recon ↔ Navi decoupling** project. Each service owns a
slice of the `/api/*` surface that `navi.echo6.co` depends on, runs behind the
existing Caddy/Authentik edge, and is fronted by the `navi.echo6.co` nginx vhost.

See `HANDOFF-recon-navi-decoupling-v3.md` for the full plan. This repo is
extraction **#1**: `navi-traffic`.

## Layout

```
navi-backend/
├── shared/                  # cross-service helpers, imported by every service
│   ├── auth.py              # get_user_id(req), require_auth decorator (Authentik header)
│   └── admin_info.py        # build_info_response(), mask_key(), time_dependency()
├── services/
│   └── navi_traffic/        # extraction #1 — TomTom traffic tile proxy (:8421)
│       ├── app.py           # Flask factory (create_app) + gunicorn entry
│       ├── traffic.py       # /api/traffic/flow/<z>/<x>/<y>.png  (ported from recon)
│       ├── admin.py         # /api/admin/navi-traffic/info  (§4.5 admin convention)
│       └── tests/
└── deploy/
    ├── systemd/navi-traffic.service
    └── nginx/navi-traffic.conf.snippet
```

Service directories use an underscore (`navi_traffic`) so they're importable
Python packages; the **service name** stays `navi-traffic` (hyphen) in systemd,
nginx, and the admin-info `service` field.

## Setup

Single workspace, single virtualenv:

```bash
python -m venv .venv
.venv/bin/pip install -e .
```

## Test

```bash
.venv/bin/pytest services/navi_traffic/tests/ -v
```

## Run (local)

```bash
TOMTOM_API_KEY=... .venv/bin/gunicorn 'services.navi_traffic.app:create_app()' \
    --bind 127.0.0.1:8421 --workers 2
```

## Run (local) — navi-geo (extraction #6)

```bash
.venv/bin/pytest services/navi_geo/tests/ -v

# All paths/URLs are env-overridable (see deploy/env/navi-geo.env.example).
# No secrets — landclass is HTTP-delegated to navi-landclass (:8424).
.venv/bin/gunicorn 'services.navi_geo.app:create_app()' \
    --bind 127.0.0.1:8426 --workers 2
```

`navi-geo` serves `/api/geocode`, `/api/reverse?lat=&lon=`, and the reverse
enrichment bundle `/api/reverse/<lat>/<lon>` (Central's 9-key contract). All
public. The reverse bundle fans out to Photon, the SpatiaLite timezone DB,
navi-landclass (HTTP), and the planet-DEM PMTiles — each degrading to `null`
independently, never 5xx.

## Run (local) — navi-admin (extraction #7)

```bash
.venv/bin/pytest services/navi_admin/tests/ -v

# No secrets — read-only HTTP fan-out over localhost (see
# deploy/env/navi-admin.env.example). Owns no DB.
.venv/bin/gunicorn 'services.navi_admin.app:create_app()' \
    --bind 127.0.0.1:8427 --workers 2
```

`navi-admin` is the fleet admin front door: `/api/admin/fleet` fans out to every
navi-* service's localhost `/api/admin/<svc>/info` + recon's `/api/health`
(merged, never 5xx — failures land in `errors[]`); `/api/admin/recon/info` wraps
recon's health into the uniform shape; `/api/admin/navi-admin/info` self-describes.
All `@require_auth`. The per-service admin endpoints stay localhost-only; this is
the single edge-exposed admin surface (needs a Caddy `@authed_api` edit — see
`deploy/caddy/navi-admin.caddy.notes.md`).

## Run (local) — navi-offroute (extraction #8)

```bash
.venv/bin/pytest services/navi_offroute/tests/ -v

# All paths/URLs env-overridable (deploy/env/navi-offroute.env.example).
# No secrets — PADUS via libpq peer-auth (dbname=padus). DEM via shared/dem.py.
# Needs osmium-tool on the host + scikit-image/rasterio in the venv.
.venv/bin/gunicorn 'services.navi_offroute.app:create_app()' \
    --bind 127.0.0.1:8428 --workers 2 --timeout 130
```

`navi-offroute` serves `POST /api/offroute` (off-network effort-based routing —
in-Python least-cost path over a DEM/friction/barriers/trails/MVUM cost grid,
stitched to the road network via Valhalla) and `GET /api/mvum` (Motor Vehicle
Use Map road/trail access lookup). Both public. The `^~ /api/offroute` nginx
block needs a long `proxy_read_timeout` (130s); routes can take ~2 min.

## The admin-info convention (§4.5)

Every service exposes `GET /api/admin/<service-name>/info`, gated by `require_auth`,
returning a uniform shape: `service`, `version` (git SHA), `port`, `config`, `env`
(names + masked values), `dependencies` (upstream health checks), `filesystem`,
`runtime` (uptime / request count / last error). No aggregator — a future admin
panel fans out to each service in parallel.
