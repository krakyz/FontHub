# Archive pipeline

Update: original validation adds a sixth queue and source-specific admission
before analysis. See [Original validation](Original-validation.md) for the current
gate, format tests, history, background migration and issue resolutions.

Six separate worker processes serve receipt (`import`), original checks (`ots`), analysis (`analysis`),
preview, conversion and download. Each queue has independent durable pause and
retry controls. `archive_pipeline.py` implements receipt/analysis;
`import_queue.py` owns scheduling; `coverage_cache.py` shares complete Hyperglot
reports; `font_validation.py` owns OpenType Sanitizer validation.

## Publication boundaries

Receipt streams SHA-256 without parsing font tables. A durable journal precedes
copying; a verified, flushed, atomically renamed original precedes the transaction
that creates `files`, `origins`, `analysis_state` and the analysis job. Only after
this transaction is committed is the stable incoming file removed. Accepted bytes
are downloadable from Import through `/originals/<sha256>` before analysis.

Analysis verifies the immutable original, parses all collection faces, and computes
all Unicode, language and missing-character information needed by search. One
transaction publishes all faces, search projections and preview jobs. An error
on any face prevents partial publication and preserves the original. Retry uses
the same bytes and provenance. Existing catalogued files are migrated to ready;
there is no automatic mass reanalysis.

Full Hyperglot reports are cached in SQLite by exact Unicode set and explicit
analysis/database version. Identical sets share the report across workers and
restarts. Legacy entries without reports still compute them on demand. Increment
the cache version when language data or analysis policy changes.

## Ownership and recovery

Each queue currently has one worker. Claims and pause checks use one SQLite
`BEGIN IMMEDIATE` transaction. A lifetime OS lock excludes competing workers of
the same kind. Only the replacement that acquires this lock resets interrupted
claims. The server supervises terminated children every three seconds. After
three interrupted attempts, a job requires manual retry; retry resets its budget.
There is no forced timeout for fontTools parsing or compression yet. Process
isolation is not a security sandbox; a hung worker may need a restart.

Analysis, preview and conversion share a heavy-job OS lock for originals at least
16 MiB. This limits simultaneous expensive CJK/TTC work but does not measure exact
RAM or guarantee bounds for pathological small files. Parsing and compression
run outside the publication lock, allowing receipt to continue. Pause stops new
claims; already running work completes. Discovery continues every five seconds.

## Browser artifacts

[OpenType Sanitizer](https://github.com/khaledhosny/ots) is installed through the
official [ots-python](https://github.com/googlefonts/ots-python) package, pinned
to `opentype-sanitizer==9.2.0`. After WOFF2 generation and cmap verification, the
native CLI checks the candidate with a 60-second timeout. Validation-only mode
cannot overwrite the original or candidate. Metadata records version, duration,
engine and diagnostics in `browser_validation`.

OTS refusal marks the preview as an error without removing catalogue metadata
or original downloads. Passing does not verify shaping or guarantee every browser
version accepts the file. Existing ready previews remain legacy artifacts until
explicit regeneration; there is no mass revalidation. Original downloads and
ordinary TTF/OTF/WOFF exports are not gated by OTS.

WOFF2 export requests share the preview job and full artifact, preventing duplicate
compression. Existing conversion caches remain available. Analysis failure is
distinct from receipt failure: only receipt failures use quarantine.

## Tests

`tests/test_archive_pipeline.py` verifies parse-free receipt, original download before
publication, complete metadata, real OTS, refusal isolation, atomic TTC retry,
shared WOFF2 and bounded crash recovery. Import and preview recovery tests terminate
real processes at publication boundaries. These test process interruptions, not
hardware power failure. Backups remain necessary. Restart all workers after
updating code or dependencies so they use one version.
