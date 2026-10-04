# Import management and background previews

The current implementation is documented in [Pipeline](Pipeline.md): five
independent queues, parse-free receipt, atomic complete analysis and OTS-checked
browser artifacts. The notes below describe the preceding four-queue design.

## Ownership and persistent state

The `/imports` page manages four queues in the archive SQLite database. Each has
one worker process. Import validates and archives originals, analyzes faces and
computes coverage. Preview independently generates WOFF2. Conversion exports requested downloads
through a third worker; see [Font exports](Font-exports.md). A large collection or
slow compression does not block subsequent originals from reaching the catalogue.
Metadata analysis can still take time for large Unicode fonts; the current phase
and face number are shown rather than a speculative completion percentage.

`import_queue.py` contains scheduling; `app.py` contains filesystem and font work.
`work_queue` stores job identity, source, input path, observed size/mtime, phase,
status, attempt count, timestamps, error and result link. `queue_control` persists
pauses. `queue_migrations` makes historical queue initialization run once.

Import identity is the resolved staging path. Preview identity is the physical
face ID, shared across source instances. States are queued → running → done/error.
Changed input paths can be rediscovered as new work. Manual retry resets an error
job to queued without discarding its attempt history. Completed-job counters refer
to queue history, not the total number of fonts in the archive.

## Discovery, ordering and controls

Inbox is scanned recursively every five seconds, including while paused. Known
font extensions are accepted; recently modified files wait three seconds. Use
`.part` during long transfers and rename only after finishing. Jobs are claimed
in enqueue order (creation time and ID). This is not alphabetical filename order.
Uploads and commit-pinned external downloads enter the same queue. Downloads
publish complete staging files before enqueueing and preserve their source.

Pause prevents claiming the next job. A running job finishes normally. Import
and preview can be paused independently, and settings survive process/container
restarts. The UI polls every three seconds, filters by kind/state and displays
25 jobs per page. Error rows expose the error and a retry action; missing inputs
cannot be retried until restored. No failed font is retried in an endless loop.

The initial migration exposes historical quarantine files as error jobs with a
local source when their original provenance is unavailable. It also queues older
catalogue faces that lack previews. Newly failed imports retain their source.

## Original transaction and interruption

The existing `import_jobs` journal remains separate from scheduling. A verified
SHA-256 original is published before metadata commits. Faces, origins, search
projection and preview jobs commit together. Inbox is removed only after that
commit and only if it has not changed during analysis. Changed inputs are retained
and deferred. Parse failures move the input to quarantine while retaining details.

Each worker holds its own lifetime OS lock. Only its owner resets interrupted
running jobs to queued on startup, preventing another process from stealing live
work. Import recovery uses the original journal; a committed file is not imported
again merely because cleanup was interrupted. Backup/restore includes both queue
state and controls and relocates input paths for the restored data directory.

## Preview publication and unavailable data

The preview worker verifies the original hash, repeats the tolerant cmap reader
and writes `<face>.queue.part`. It validates the resulting cmap before publication.
The archive lock covers atomic WOFF2 publication and metadata/queue completion.
After a crash, an already published valid preview is reused without recompression;
private partial files are cleaned by the preview worker alone.

A preview failure does not remove the original, coverage or catalogue record.
Some damaged glyph tables cannot be converted despite readable character maps;
such jobs expose their actual converter error and remain available for retry.
Search displays a dash until a preview exists. An open font detail page detects
completion without resetting the user's test text, size, color or axes.

## API

| Method and path | Purpose |
|---|---|
| GET `/api/imports?kind=import&status=active&page=1` | Consistent counters, controls, running jobs and paginated history |
| POST `/api/imports/control` | JSON `kind`: import/preview/convert/download/all and boolean `paused` |
| POST `/api/imports/<id>/retry` | Retry an error job; 409 if its input is missing or state is incompatible |
| POST `/api/scan` | Discover inbox entries without parsing fonts inline |
| POST `/api/upload` | Stage uploaded originals for queue processing |
| GET `/api/fonts/<id>/preview-status` | Available/status/error for detail-page polling |

Explicit source-family import returns HTTP **202** with `queued: true` and
`url: /imports`; the caller must not treat this as completed analysis.

## Verification

`test_import_queue.py` covers pauses, restart state, concurrent exclusive claims,
source-preserving external staging, error/retry and relocated restore paths.
`test_import_recovery.py` kills imports after copy, analysis and catalogue commit.
`test_preview_recovery.py` kills previews after partial writing and publication,
checking reusable output and preserved originals. `test_queued_cmap.py` checks
malformed format-4 cmap through both stages. `test_app.py` explicitly drains preview
work before checking browser binaries. These simulate process crashes, not every
possible storage/power failure; keep independent backups.

An isolated Docker Compose run (`fonthub-check`, port 9875) additionally verified
queued Arial Bold import, independent paused preview, preserved queue/control
state after forced container recreation and successful WOFF2 publication after
resume. Test containers were stopped; named volumes retained. Browser verification
covered live counters, error filtering and a clean console in the dark theme.

Queue tables use fixed header-defined column proportions, including empty states.
The current-job description reserves three lines, so filename/stage changes do
not shift the processing panel. Long names and diagnostic text wrap inside their
columns; narrow viewports scroll the table horizontally.

The processing table retains its last-row bottom borders above the pause note.
The note has no separate top rule or paragraph margins, avoiding a gap and a
duplicated separator; the jobs table also retains a continuous bottom separator above pagination.

Queue pagination matches search: text links, a sliding window of ten page
numbers, first/last page shortcuts and previous/next navigation. The current
page is marked with `aria-current`; polling retains the selected page.

Downloads have their own cancellation and rate-limit policy; see
[Download queue](Download-queue.md). Completed downloads with import intent
enter the original import queue without bypassing its pause control.
