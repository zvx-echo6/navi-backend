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

## The admin-info convention (§4.5)

Every service exposes `GET /api/admin/<service-name>/info`, gated by `require_auth`,
returning a uniform shape: `service`, `version` (git SHA), `port`, `config`, `env`
(names + masked values), `dependencies` (upstream health checks), `filesystem`,
`runtime` (uptime / request count / last error). No aggregator — a future admin
panel fans out to each service in parallel.
