# navi-admin — Caddy edit notes (deploy task, NOT done in the PR)

navi-admin's `/api/admin/*` is **auth-gated** (every admin-info is `@require_auth`).
Today the `navi.echo6.co` Caddy block on **CT 101** (`192.168.1.241 → pct 101`,
`/etc/caddy/Caddyfile`) routes `/api/admin/*` through `@public_api` (path `/api/*`)
with **no auth** → it would serve the fleet/admin endpoints unauthenticated.

So the deploy task must add `/api/admin/*` to the `@authed_api` matcher. **This is
the first Caddy edit since extraction #2** (Phase A §6/§8).

## The edit

Inside the `navi.echo6.co { ... }` block, the `@authed_api` matcher:

**Before**
```caddyfile
@authed_api {
    path /api/contacts /api/contacts/* /api/auth/whoami /api/traffic /api/traffic/*
}
```

**After** (append `/api/admin/*`)
```caddyfile
@authed_api {
    path /api/contacts /api/contacts/* /api/auth/whoami /api/traffic /api/traffic/* /api/admin/*
}
```

No other change. `@authed_api` already runs `forward_auth https://auth.echo6.co`
then `reverse_proxy 100.64.0.24:8440`; adding the path makes `/api/admin/*` take
that authed path instead of falling through to `@public_api`. Because Caddy
evaluates the `handle` blocks in source order and `@authed_api` is defined before
`@public_api`, the new path wins for `/api/admin/*` while `/api/*` still catches
everything else publicly.

## Apply (on CT 101)

```bash
ssh root@192.168.1.241 "pct exec 101 -- caddy validate --config /etc/caddy/Caddyfile"
ssh root@192.168.1.241 "pct exec 101 -- systemctl reload caddy"   # acme/admin off → reload is fine here
```

## Why this is the only Caddy entry needed

The per-service `/api/admin/<svc>/info` endpoints stay **localhost-only** — they
are never edge-routed (nginx has no per-service admin block; navi-admin reaches
them over `127.0.0.1`). Only navi-admin's single `/api/admin` front door is
edge-exposed, so only this one path needs adding to `@authed_api`.

## nginx side (VM 1130)

Pair this with the `^~ /api/admin → 127.0.0.1:8427` block in
`deploy/nginx/navi-admin.conf.snippet`, added before the `location /api/`
catch-all in `/etc/nginx/sites-available/navi.echo6.co`.
