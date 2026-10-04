# Queued source downloads

## User workflow

The Import page has a fourth independent queue, Downloads. Pause does not affect
original analysis, previews or conversion. Requests deduplicate by source,
family, pinned revision and artifact filename. One job may satisfy both direct
user download and archive import. Already cached complete files are served
immediately; metadata-only page views do not acquire binary files.

A source download link opens a waiting page if needed. It shows byte counts,
errors, cancellation and a link to the queue. Readiness triggers attachment
while the page remains open; a ready link remains available on return. Requests
for importing a family enqueue its artifacts and return HTTP 202 immediately
after obtaining the manifest. Completed downloads hand off files to the ordinary
import queue, preserving source. ZIP source packages are extracted within the
existing bounded extraction rules for import; direct user downloads remain ZIP.

A failed job exposes Retry in the queue even when its expected input file does
not yet exist. Cancelled jobs are available in the Cancelled filter; another
explicit download/import request can requeue them. Cancellation is cooperative
between received network chunks. A stalled socket may take up to its 30-second
timeout to stop. After the atomic import handoff completes, cancellation cannot
undo those imports. A shared job's cancellation cancels the download for both
intents and clears the pending import intent; it never deletes archived originals or already prepared cache bytes.

## Persistent ownership and recovery

`font_downloads.py` owns download orchestration and HTTP waiting/attachment routes.
`download_tasks` stores the pinned adapter record, artifact and import/cancel flags;
`work_queue` stores lifecycle. `download_cooldowns` records source retry deadlines.
The worker runs under its lifetime OS lock; interrupted running jobs requeue at
startup without changing revision or source. Completed cache bytes are reused.
Archive backups include source cache and all three tables; restore relocates the
work queue's expected cache paths. No extra indexer adapter API is required.

Import intent and queue completion are serialized in one SQLite writer transaction.
A request arriving during completion either joins that handoff or requeues a
cache-only handoff. Deterministic staging names and the durable import queue make
repeated processing safe. The network connection never runs inside the archive
lock; only extraction/staging and catalogue handoff use it. Original SHA-256
validation and parsing still belong to import, not the transport worker.

The existing bounded transport now reads 64 KiB chunks and reports progress.
Artifact name, URL policy, HTTPS redirect rules and font/ZIP validation remain
mandatory. Temporary cache publication never exposes incomplete files. One worker
allows at most one active transfer globally, hence at most one per source.
HTTP 429/503 pauses that source according to Retry-After (seconds or HTTP date),
clamped to 1 second–24 hours; missing/invalid values use 60 seconds. Other sources
can proceed. Automatic rate-limit attempts stop after three; other errors require
manual retry. Partial-transfer resume and scheduled source crawling are not added.
The explicit specimen-preview endpoint retains its existing bounded acquisition;
this queue covers user artifact downloads and archive import requests.

| Endpoint | Purpose |
|---|---|
| GET `/sources/<source>/fonts/<family>/download/<index>?revision=...` | Explicit shared job or immediate cached attachment |
| GET `/api/downloads/<id>` | Read state, bytes/detail and ready attachment URL |
| GET `/downloads/<id>/file` | Cached attachment only; never starts network traffic |
| POST `/api/downloads/<id>/cancel` | Cancel queued or running transfer |
| POST `/api/sources/<source>/import/<family>` | Enqueue artifact jobs with import intent |
| POST `/api/imports/<id>/retry` | Retry errors without requiring a downloaded file |

## Verification

`tests/test_download_queue.py` checks no inline network acquisition, shared intents,
source-preserving import handoff while import is paused, cached attachments,
queued/running cancellation, missing-input retry, bounded cooldown attempts,
recovery and portable restoration. `tests/test_google_fonts.py` exercises real pinned
Google Fonts artifacts through the queue. Conversion/import regression tests
verify that the new kind leaves existing queues and immutable originals intact.

Live verification acquired Abel from Google Fonts through the running localhost
queue and validated the attachment character map. Browser filtering displayed the
completed download and ready link without console errors. An isolated Linux Docker
worker acquired the same pinned artifact using a seeded immutable adapter manifest
(the test indexer had no published family snapshot). Transport chunk/cancellation
tests also prove that an interrupted transfer never publishes its target.

Docker verification also confirmed that the download pause survives forced
container recreation. Named test volumes were retained after stopping containers.
