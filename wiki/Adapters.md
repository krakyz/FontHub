# Adapter architecture and authoring

FontHub uses one indexer service with independent adapter modules. A source is
not a new HTTP service and does not add branches to FontHub's routes. The registry
is discovered from `indexer/adapters/` at startup; helper modules without `ID`
are ignored. FontHub discovers registered sources through `/api/v1/sources`.
Installing an adapter is installing trusted Python code, not uploading data.

## Implemented sources and honest capability boundaries

| Adapter | Acquisition | Snapshot / files |
|---|---|---|
| google_fonts.py | Sparse Git + METADATA.pb | Full Google catalogue; commit-pinned binaries and license documents |
| noto.py | Sparse Git state.json + fontrepos.json | Distribution families/builds; commit-pinned files; release notes/dates when supplied |
| adobe.py | Paginated public organization listing, sparse Git default branches, public release HTML when necessary | Automatically discovered Adobe font projects, including experimental fonts; licenses/readmes and pinned Git files or release assets |
| font_library.py (inactive) | Fixture-tested HTML catalogue pagination | Listing metadata and original source-page links; no automatic file manifest |
| font_squirrel.py (inactive) | Fixture-tested HTML catalogue pagination | Listing metadata and original source-page links; no automatic file manifest |

Live validation on 2026-10-04: Google Fonts 2031 families, Noto 206. Expanded Adobe
discovery read 40 public repositories across two pages and published 33 font
projects (previously 5). Seven projects have no supported committed binaries or
release downloads: Source Bengali Sans, Source Devanagari Sans, Source Gujarati
Sans, Source Gurmukhi Sans, Source Kannada Sans, Source Tamil Sans and
VFfirst Source Serif. Their source code is not treated as installable fonts.
Font Library's catalogue timed out from this host; Font Squirrel returned an empty
202 response. Their adapters are installed and tested against HTML fixtures, but
no real snapshot was published. These are explicit failed jobs, not empty working
catalogues. A successful HEAD link check does not establish working indexing.

The two HTML adapters currently provide discovery/manual acquisition only. They
must not advertise preview/import until family-detail parsing yields a trustworthy
file manifest. Indexing them does not automatically crawl every family detail
page, execute JavaScript, download binaries or bypass access challenges.

GitHub API quota exhaustion occurred during initial live probes. Noto/Adobe
acquisition was switched to Git and public release HTML; no token is required.
Git is metadata-only: listing a binary in the Git tree does not download its blob.
Noto type/style/coverage are not inferred from family names. Adobe no longer has
a five-project allowlist: all pages of the public `adobe-fonts` organization are
read from GitHub's embedded JSON, then every repository is inspected. A changed
listing payload fails the job; it does not publish a partial replacement.
Repositories without supported committed files or release artifacts are omitted.
Archived and experimental font projects are included. Repository metadata,
including descriptions, license labels and update timestamps, is retained.
License labels are source assertions, not a legal analysis; original license
documents remain available. Update timestamps describe the repository, not a
verified font release. One catalogue row currently represents one repository
project; multi-family packages are not split without reading font binaries.
The five-name mapping only supplies familiar display names, never filters scope.
This source covers public GitHub projects, not the Adobe subscription service.

## Small adapter contract

A module provides `ID`, optional `NAME`, `REPOSITORY`, `HOSTS`, `CAPABILITIES`,
optional `DOWNLOAD_PREFIXES`, and `collect(context) -> (revision, records)`.
Use the existing Google adapter as a textproto example, Noto as a JSON example,
and HTML catalogue adapters as pagination examples. Each requires no database,
HTTP server, thread, retry loop or FontHub handler.

```python
ID = 'my-fonts'
NAME = 'My Fonts'
REPOSITORY = 'https://example.org/catalogue'
HOSTS = {'example.org'}
CAPABILITIES = ['catalogue', 'search', 'download']
DOWNLOAD_PREFIXES = {'example.org': '/files/'}

def collect(context):
    payload = context.json(REPOSITORY)
    # Map source records to the schema below; retain the original record in raw.
    return payload['revision'], [normalize(item) for item in payload['families']]
```

`normalize` is the adapter's own mapping function, not a required framework class.
Sources without a stable revision can SHA-256 their acquired metadata; this is a
snapshot identifier, not a font-file checksum. Revisions must be hex SHA-1/SHA-256
for compatibility with immutable FontHub cache directories.

Only `id` and `family` are mandatory in an adapter record. The socket fills source, revision, empty lists and unknown optional fields automatically; return `raw` to preserve the complete parsed source record. The full normalized schema is:

- `id`: globally unique, stable, ASCII letters/digits/hyphens/underscores, <=200;
  use a source-specific prefix. Changing the display name must not change identity.
- `source`: the module's ID (filled automatically); `family`, `author`, `url`.
- `license`, `category`, `date_added`, `updated`: unknown values are null/empty.
- `axes`, `variants`, `subsets`, `declared_languages`, `styles`: lists; unknown
  styles are empty, not invented regular/400 entries.
- `files`: `{name, url, format}`; name is a safe basename. Optional `filename`,
  `build`, `artifact_revision`, `container="zip"`, `size` preserve useful evidence.
- `revision`: the collected snapshot's revision; `raw`: complete source record;
  `documents`: safe filename-to-text mapping; `field_states`: uncertainty flags.

`field_states.type=not_provided` prevents falsely classifying unknown metadata as
static. Missing language percentages remain unknown until FontHub analyzes a file.
Download URLs must be HTTPS and match adapter-declared host/path prefixes; the
service injects the policy into its manifest. A new source therefore needs no
hardcoded host switch in the FontHub download client.

## What the socket owns

`service.py`: API, durable jobs, auth, validation and atomic per-source publication.
`transport.py`: bounded HTTP/Git acquisition and original evidence retention.
`indexer_client.py`: common FontHub client, download cache and bounded ZIP handling.
`google_fonts.py` is a compatibility import, not another indexing implementation.

One worker runs at a time. Source cooldown is 60 seconds by default, independent
for each source. HTTP requests are spaced at least 0.3 seconds within a job;
429/selected 5xx receive at most one bounded retry. Long/non-numeric Retry-After
fails instead of blocking indefinitely. Network timeouts fail the job. HTTP
metadata is capped at 16 MiB, each binary at 100 MiB; ZIPs have a 500 MiB declared
extraction budget and 4000-font-member limit. Large Adobe packages may exceed these
limits and require manual acquisition. Archive import itself remains in FontHub.

HTTP responses (including HTTP error bodies) and checked-out Git metadata are retained under
`data/indexer/responses/<source>/<job>/`, with URL/headers or Git path/revision and
acquisition time. Failed parsing does not discard acquired evidence. Working Git
checkouts can advance while per-job evidence and SQLite snapshots remain retained.
Automatic cleanup/retention policies are not implemented.

Snapshots are identified by source + revision. Only that source's active pointer
changes on publication. All listing pages must succeed; empty/challenge responses,
a page-budget overflow or colliding record IDs fail without replacing the old index.
Sources configured for HTML have a 200-page acquisition budget.

The current socket supports catalogue snapshots. On-demand remote-search adapters
are a future capability, not emulated with partial snapshots.

## Tests and extension workflow

1. Add the adapter module and a small saved-response fixture.
2. Run `test_adapters.py` and `test_indexer.py`; test missing/changed data as well
   as successful parsing. Verify no binary is downloaded during collection.
3. Restart indexer, trigger that source's sync, inspect the job and saved evidence.
4. Verify search/detail and, when advertised, explicit preview/import in a temporary
   archive. `test_live_sources.py` does this for one Noto and Adobe font; acquisition
   is limited to the already-downloaded file when exercising the import endpoint.
5. Update this table and the README with actual live status and limitations.

The existing snapshot DB is migrated additively for source/job identity, then its
snapshot table is transactionally rebuilt with the composite key. Online backup
files with `pre-adapters` timestamps were created before deployment. No original
archive fonts are removed by this migration.

Git blob paths are pinned to commits. GitHub release assets are pinned to a release URL, but the publisher can replace an asset under that URL; no file checksum is invented where the source does not supply one. FontHub records actual original bytes by SHA-256 after download.

## Source language assertions versus measured coverage

FontHub normalizes `declared_languages` ISO 639-1/639-3 identifiers through
pycountry 24.6.1, preserving the original identifier and its script suffix.
The source snapshot stays unchanged; this interpretation happens in FontHub.
Unknown identifiers remain visible instead of being silently discarded.

Search language cells show green ✓ for a direct declaration matching the
language and script, yellow ≈ for a corresponding subset script, and ? for no evidence.
Only archive fonts display measured, colored percentages. Missing source metadata
is not evidence that a language is unsupported. `menu` is omitted from user-facing
subsets because it describes the font-family name used in a picker.

The language filter accepts direct language declarations and approximate subset
script matches against the selected language’s primary Hyperglot orthographies.
Approximate matches retain ≈; they are not verified language support. The exact sample-character filter still requires
analyzed archive files. The remote detail page separates “По данным источника”
(language names/original codes, subsets, no progress bars) from unavailable
measured coverage. Preview does not promote declarations to verified coverage.
Run `python test_source_languages.py` for normalization and offline search checks.

Unknown values in the search result table use ?, except sample and added-date cells, which retain an em dash.


## Inactive adapters and authoring checks

As of 2026-10-04 Google Fonts, Noto and Adobe are active. Font Library and Font
Squirrel have `ACTIVE=False` and `INACTIVE_REASON`: fixture parsers remain available,
but the service rejects sync with 409 before creating a job. FontHub disables
updates/activation and excludes them from filters and external search even if an
old snapshot exists. Link checks remain diagnostic. Enabling requires an active
implementation and a nonempty published index; removing the flag alone does not
prove a working adapter.

Copy `examples/adapters/example_json.py` into `indexer/adapters/`, give it a unique
ID and approved HTTPS hosts and implement `collect(context)`. Preserve raw fields
and documents; unknown values stay explicitly unknown. Collection must not download
font binaries. The example lives outside discovery and never registers in production.
Its revision deterministically hashes source JSON.

```powershell
.venv/Scripts/python.exe -m indexer.check examples.adapters.example_json --fixture examples/adapters/responses.json
```

Fixture mode maps exact approved URLs to JSON/text responses; uncaptured requests
fail. Real acquisition requires `python -m indexer.check ID --live`; local Git
fixtures use `--live --repository PATH`. Adapters are trusted Python, not a sandbox.
The command uses publication's `indexer/validation.py` normalizer and checks
revision, nonempty records, required fields and artifact policy, printing a count.
It never publishes. Cross-source ID collisions are checked only on publication.
Add malformed/empty response fixtures, then verify explicit import in an isolated
archive before documenting an adapter as ready.

`test_adapter_check.py` exercises captured-only metadata, empty catalogue rejection,
unapproved artifact hosts and inactive adapter rejection.
