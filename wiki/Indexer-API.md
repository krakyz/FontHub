# Indexer API v1

Base: `http://127.0.0.1:8766/api/v1`. JSON over HTTP; no CORS browser integration
is required because FontHub proxies requests. If FONT_INDEXER_TOKEN is set, all
requests require `X-Api-Key`. Configure the matching FONTHUB_INDEXER_TOKEN on
FontHub. Keys are server configuration and are not returned to the browser.

| Method/path | Meaning |
|---|---|
| GET /health | Service health and API major version |
| GET /sources | Registered adapters, capabilities, active snapshot and latest job |
| POST /sources/{source}/sync | Start sync; 202 with job_id |
| GET /jobs/{id} | Durable status, phase, timestamps, error and revision |
| GET /search | Search local active snapshot |
| GET /families/{id} | Complete normalized/raw record and documents |

Search parameters: q (all case-insensitive terms), source, type
(`variable`/`static`), format, sort (`name`/`name_desc`), offset, limit (1–2000).
Response: `{total, items}`. Dates, coverage verification and popularity sorting
are not supported external query capabilities. Omitted optional values remain
unknown; callers must not infer absence of support from a missing value.

Family detail accepts `revision=<commit>`; omission selects the active snapshot.
This is important when the index updates between search and download. Artifacts
return `{name, format, url}`. Documents are a filename-to-original-text map;
raw metadata is a field-to-list map preserving repeated fields.

Sync errors: 409 concurrent job, 429 cooldown. The durable job's `error` records
Git/parse/publication failures; they do not overwrite the active snapshot. Missing
families/jobs return 404, malformed pagination 400, wrong configured token 401.

Compatibility policy: `/api/v1` identifies the contract version. Clients should
ignore unfamiliar response fields. Breaking changes require a new major path.
This is our contract policy; it is not a promise about third-party upstream APIs.

Source metadata includes capability flags, source name, active snapshot and last job. Search without a source filter queries all active snapshots; `source=<id>` restricts it. IDs are globally unique and source-prefixed. Unknown type metadata is excluded from type-filtered results. Per-source snapshot identity is source+revision.


## Implementation availability

`GET /api/v1/sources` includes `active` (boolean) and `inactive_reason` for each
registered adapter, separately from snapshot availability and the FontHub search
preference. Syncing an inactive adapter returns HTTP 409 and creates no job.
A published snapshot is retained for diagnostics; FontHub excludes inactive
external offers and cannot enable that source. Active adapters without a published
index also cannot be enabled in FontHub search. Registration alone is not readiness.
