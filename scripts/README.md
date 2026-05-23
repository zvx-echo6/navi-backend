# navi-backend scripts

Operational scripts for navi-backend. Not part of any Flask service — run manually.

## `overture_import.py` — Overture Places ETL

Loads Overture Maps **Places** into the host `overture` PostgreSQL/PostGIS database
(table `places`), which **navi-places** consumes for place enrichment (phone,
website, brand, OSM cross-refs). Relocated from recon in the navi↔recon
decoupling (recon produced this data but consumed none of it).

- **Source:** public S3 Parquet — `s3://overturemaps-us-west-2/release/<OVERTURE_RELEASE>/theme=places/type=place/*`, read via DuckDB (`httpfs`, no credentials).
- **Release:** pinned in-code — `OVERTURE_RELEASE = '2026-04-15.0'`. Bump it (and re-run) when a newer Overture release is desired.
- **Filter:** North America bounding box.
- **Write semantics:** idempotent **UPSERT** (`INSERT … ON CONFLICT (id) DO UPDATE`), batched. Safe to re-run.
- **Config (env):** `OVERTURE_DB_HOST` / `OVERTURE_DB_PORT` / `OVERTURE_DB_NAME` / `OVERTURE_DB_USER` / `OVERTURE_DB_PASSWORD` (defaults `localhost` / `5432` / `overture` / `overture` / empty). On VM 1130 these come from the same host PG cluster navi-places reads.

### Trigger: manual-only

There is **no cron job or systemd timer** — run it on demand (e.g. when a new
Overture release is published and `OVERTURE_RELEASE` is bumped):

```sh
cd /home/zvx/projects/repos/navi-backend && .venv/bin/python scripts/overture_import.py
```

The full S3 query takes several minutes; progress is logged to stdout.
