# Running and operations

## Local Windows

`start.ps1` starts the local indexer in a hidden background process when no custom
indexer URL is set, then runs FontHub. Direct equivalents from the project root:

```powershell
.venv/Scripts/python.exe -m indexer.service
.venv/Scripts/python.exe app.py
```

Use separate terminals for the direct commands. Indexer: port 8766; FontHub: 8765.
Default binds are localhost. Neither service silently installs a network schedule.
Sources → Update index starts a sync for that particular source. Search can operate locally while it
runs. Search in every scope runs automatically against saved snapshots; the default UI scope is all. Sync, preview and import remain explicit actions. Filtering never starts source acquisition or font downloads.

The indexer requires Git and `indexer/requirements.txt`. FontHub uses root
requirements.txt. Indexer data defaults to data/indexer. Binary download cache
defaults to data/sources/google-fonts. Original archive is data/originals.

## Docker Compose

`docker compose up --build -d` creates two services with separate named volumes.
FontHub reaches indexer over the internal service name. Only FontHub port 8765
is mapped to host localhost. Exposing to a LAN is an explicit deployment choice;
configure access controls for a wider network.

## Configuration

| Variable | Owner / default |
|---|---|
| FONTHUB_INDEXER_URL | FontHub / http://127.0.0.1:8766 |
| FONTHUB_INDEXER_TOKEN | FontHub / unset |
| FONTHUB_DATA, FONTHUB_INBOX | FontHub / existing data paths |
| FONTHUB_HOST, FONTHUB_PORT | FontHub / 127.0.0.1, 8765 |
| FONT_INDEXER_DATA | Indexer / data/indexer |
| FONT_INDEXER_HOST, FONT_INDEXER_PORT | Indexer / 127.0.0.1, 8766 |
| FONT_INDEXER_TOKEN | Indexer / unset; matching API key when configured |
| FONT_INDEXER_MIN_INTERVAL | Indexer / 60 seconds between sync attempts |

Logs: `indexer.log` / `indexer-error.log` on local background startup. Service
logging includes job ID, phase, revision, family count and exceptions. API keys
are not logged. FontHub's existing server logs/import events remain separate.

## Backup and recovery

Back up archive SQLite using SQLite's backup API, originals, and indexer SQLite.
Revision documents/download caches can also be backed up; Git cache is rebuildable.
Stop services or use SQLite backup API for database-consistent copies. Restoring
only a DB without its original binaries is not a complete FontHub restore.

The implementation migration was preceded by an online SQLite backup in data/
with a timestamped pre-origins filename. Startup interrupted jobs become failed,
while previously published snapshots remain active. Re-run sync explicitly.

## Checks

```powershell
.venv/Scripts/python.exe tests/test_app.py
.venv/Scripts/python.exe tests/test_indexer.py
.venv/Scripts/python.exe tests/test_google_fonts.py
.venv/Scripts/python.exe tests/test_adapters.py
.venv/Scripts/python.exe tests/test_live_sources.py
.venv/Scripts/python.exe tests/test_source_zip.py
node tests/test_search.js
```

test_app checks archive/import/source separation. test_indexer uses a local Git
fixture and no upstream network. test_google_fonts uses the running indexer's
published real snapshot and downloads a small family into a temporary archive.
Docker validation on 2026-10-04 built both images, imported Arial, recreated both
containers without removing their volumes, and restored a verified backup.
The isolated project uses port 9875 and never mounts the working host archive.

For source capability limits and actual deployment results see [Adapters](Adapters.md). Source jobs are independent: a failed HTML source does not invalidate Google/Noto/Adobe snapshots. The Sources page can reconnect to an unfinished durable job after reload.


Search result samples for unimported source fonts display a centered em dash. The cell still links to the font detail page; its tooltip explains that the sample is unavailable until download. Archive font samples retain the chosen text and size.


The search result Source column displays escaped source names rather than emoji. Its width accommodates text; the tooltip retains the full source list.

Search sample cells reserve the same height for text, an empty sample and the unavailable em dash. The height follows the sample-size slider, with a 52px minimum to accommodate the remote-status label; content is vertically centered.


## Enabling sources

Internet sources have persistent search switches in FontHub's `source_settings`
SQLite table. The setting does not modify the indexer or start a network job.
A source needs a nonempty published index to be enabled; link HEAD availability
is insufficient. Existing ready indices default to enabled for compatibility.
A saved disable survives restarts and subsequent successful index updates.
A failed first sync keeps activation unavailable. Failed refreshes preserve a
usable previous snapshot. During an indexer outage, enabling is rejected while
turning a source off remains possible; cached indices keep their existing state.

Disabled/unindexed sources are omitted from search source radio buttons and
external results in every scope, including explicit old source URLs. Imported
archive fonts, provenance, previews and index snapshots are retained. Local
inbox/archive remains always enabled; this switch does not pause inbox scanning.
Other open search tabs reload filter choices on settings changes; browser-back
restoration detects changed source preferences. Completion polling invalidates
the short metadata cache so a newly obtained index unlocks the switch immediately.

API: `POST /api/sources/<id>/enabled` with boolean JSON `enabled`; missing/invalid
values return 400, activation without a usable index returns 409. Local is not
switchable. Run `python tests/test_source_settings.py` for state and search checks.

### Sources page layout

The internet-source table puts identity, search activation, index readiness,
publication time and actions in separate columns. Repository links use short
labels; raw URLs no longer dominate the table. A live summary counts enabled
sources and ready indices. Link checks and retained upstream errors live in
expandable diagnostics, because link availability is not index readiness.
Failed refreshes can display a ready previous index alongside error diagnostics.
Sync labels distinguish initial acquisition from refreshing an existing snapshot.
Local inbox remains a separate compact panel with collapsed import instructions.
The layout uses native tables, checkboxes, buttons and details in both themes;
narrow screens scroll the table horizontally instead of squeezing its controls.


## Durable import journal and portable backups

The [Import queue](Import-queue.md) page describes independent import and preview
workers, persistent pauses, retry controls and preview crash recovery.

`import_jobs` records hash, source, input path, filename, target and phase before
copying. The original is SHA-256 checked, fsynced and atomically renamed before
any catalogue reference is committed. Metadata, faces, origins, preview queue
jobs and the catalogued journal phase commit in one
SQLite transaction (`WAL`, `synchronous=FULL`). Only then is inbox removed and
its journal cleared. An OS lock serializes import, reanalysis, recovery and archive
backup across processes; death releases it automatically. Do not delete
`import.lock` to unlock an archive.

Startup replays unfinished jobs from the input or a verified archived copy.
Parser failures remain failed, preventing endless retry; quarantine/history
preserve diagnosis. Orphan originals are not deleted. Tests kill a child process
after copy, after analysis and after catalogue commit. These prove process-crash
recovery; storage hardware and power loss still require backups.

`archive_backup.py backup --data data --output backups/archive.zip` uses SQLite's
online backup API, holds the archive lock and stores originals, previews,
quarantine, internal inbox, source cache and root JSON settings. Temporary files
are excluded. Stop incoming transfers while backing up; external inbox directories
are outside this backup. Back up the indexer separately: `--kind indexer --data
data/indexer`. It includes SQLite and response evidence; regenerable Git caches
are excluded. Finish sync before capturing a complete evidence set. Backup
filenames must be new. The two service backups are independent snapshots.

`archive_backup.py restore --input backups/archive.zip --data restored-data`
checks every member's SHA-256, SQLite integrity and original hashes, rewrites
original/journal paths (Windows or Linux) to the target, then publishes the
staging directory. Existing directories, corruption and unsafe ZIP paths are
rejected. Restore both roots, then configure their respective environment variables.
External inbox paths stay explicit. Browser language/theme/sample preferences
are not server data and are excluded. Restoring never launches upstream downloads.

Compose health checks wait for the indexer before starting FontHub.
`FONTHUB_HTTP_PORT` changes the host port (8765 by default). `docker compose down`
retains named volumes; `down -v` destroys them. Keep backups outside those volumes.
Default Compose creates separate data; migrating a host archive requires explicit
backup/restore, not merely rebuilding an image.


Validation commands for this iteration: `tests/test_import_recovery.py`,
`tests/test_search_sql.py`, `tests/test_backup.py`, `tests/test_adapter_check.py`, existing archive,
source/language/indexer/ZIP checks and `node tests/test_search.js`. An indexer backup
was also restored from the actual published snapshots into a new check directory.
Archive migration retained 3644 files and 3694 analyzed faces before the scanner
resumed pending inbox work. Timestamped pre-migration copies are in `backups/`;
this directory is excluded from Git and Docker build context.


### Backups from Compose volumes

The utility is included in both images. Finish source synchronization first,
then create and copy backups out of the containers:

```powershell
docker compose exec -T fonthub python archive_backup.py backup --data /data --output /tmp/archive.zip
docker compose cp fonthub:/tmp/archive.zip backups/archive.zip
docker compose exec -T indexer python archive_backup.py backup --kind indexer --data /data --output /tmp/indexer.zip
docker compose cp indexer:/tmp/indexer.zip backups/indexer.zip
```

Use fresh filenames for each run; `/tmp` is not persistent storage. Restore to a
new data root while its service is stopped, then configure/mount that root. The
restore command deliberately cannot overwrite a populated volume.

Twelve additional SQL/reference queries over the real 3694-face catalogue matched
counts and selected IDs, including scripts, formats, source, characters and sort.


The full pre-search-migration archive ZIP contains 37,713 members, about 14.9 GB
uncompressed / 10.2 GB compressed, including pending inbox. Every member's SHA-256
and the backed-up SQLite database integrity were verified; the report is retained
in `backups/verification-2026-10-04.json`. The scanner resumed after release of
the backup lock and committed additional files. Generating WOFF2 for the first
23 MB Arial Unicode font took several minutes, so a short “file count must grow”
timeout is not a reliable liveness check for large fonts. This iteration changes
search performance and crash recovery, not preview compression's CPU cost.

Compose healthcheck индексатора передаёт API-ключ из FONT_INDEXER_TOKEN внутри контейнера, поэтому проверка готовности работает и при включённой авторизации. CI проверяет запуск обоих контейнеров с тестовым ключом.
