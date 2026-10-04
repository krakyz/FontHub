See [Adapters](Adapters.md) for the shared socket and all additional sources. This page describes Google Fonts specifically.

# Google Fonts indexer

## Repository and scope

The source is `https://github.com/google/fonts.git`. Git fetches one shallow
revision and sparse checkout hydrates only METADATA.pb, DESCRIPTION.en_us.html,
OFL.txt, LICENSE.txt and UFL.txt in family folders. TTF/OTF binaries are not
checked out by the indexer. A Git operation can internally issue several HTTP
requests; it is not described as literally one upstream request.

Git is used instead of GitHub's paginated per-family API and the undocumented
fonts.google.com JSON endpoint. No API key is required for this public Git source.

## Job lifecycle

`queued → running → completed/failed`. Jobs are persisted before starting a worker
thread. Phases cover fetch, parsing and publication. Only one sync may execute at
a time. New sync requests return 409 while busy; a minimum 60-second interval
between attempts returns 429. Configure it with FONT_INDEXER_MIN_INTERVAL.

Search, source status and family detail routes read SQLite only. They do not
fetch Git or font files. Retrying a failed job is explicit; no automatic retry
storm or background network schedule is installed.

If restarted mid-job, the service marks its nonterminal jobs failed. The previous
active snapshot remains available. A user can start another sync. This is durable
job status/recovery, not resumption of a half-executed Git command.

## Atomic snapshots and provenance

The adapter obtains an exact Git revision, validates every supported family
METADATA.pb, and stages all records in memory. Families, snapshot metadata and
the active-revision pointer are written in one SQLite transaction. A parse,
network or validation error never publishes a partial index or removes records
from the previous snapshot.

Snapshot families are identified by source directory (`gf-<directory>`), not by
display name. Previous snapshots are retained and detail requests may explicitly
select their revision. Retention cleanup is not implemented; back up the DB and
monitor its growth. Git's local shallow checkout is only a working acquisition
cache; historical normalized records/raw documents remain in SQLite.

Each family contains normalized name, designer, license code, category,
date_added, declared subsets/languages, variant list, axes, file manifest and
repository URL. Unknown protobuf fields are retained in the parsed raw object.
The complete original METADATA.pb and available documents are retained too.

The generic textproto parser preserves repeated fields as lists and nested
messages. It rejects unsupported syntax; it does not silently skip unfamiliar
tokens. Unknown field *names* are accepted. Raw data can be reparsed later.
HTML descriptions are stored as documents, never automatically executed/rendered.

Files are returned as HTTPS raw.githubusercontent.com URLs containing the exact
snapshot commit. The source project's `source.commit`, if present in metadata,
is a different upstream revision and remains in raw data. The Git commit is not
presented as a SHA-256 checksum of a binary font.

## Unknown and verified information

Declared subsets/languages are source assertions. The indexer does not analyze
fonts or compute language percentages. Family `updated` is null with
`field_states.updated=not_provided`; snapshot creation time is a local acquisition
time, not the last change date of every family. `date_added` is source-provided.

The manifest gives advertised formats and paths, not independently verified
coverage, size, features or file hashes. FontHub computes properties after
download. The prototype does not yet expose a per-field conflict/quality ledger
or canonical cross-source family matching. Preserving raw documents allows that
to be added without discarding observations.

## Download/cache boundary

Opening/searching remote results does not download a font binary. A remote family
page has an explicit preview button. Import is another explicit button. FontHub
fetches documents and a pinned manifest and caches them under family/revision.
Binaries are checked with FontTools, limited to 100 MiB and kept per revision.
Only the official pinned raw GitHub path is accepted for Google font downloads.

Remote family pages use the same two-column layout as archive detail pages.
The specimen controls are shared in `templates/font_preview.html`; size, text
and contrast presets work before/after explicitly loading the first family file.
The indexer's pinned raw font records supply advertised styles, weights and
filenames, and available license documents remain readable. Coverage search,
Unicode, verified file properties, individual archive downloads and ZIP downloads
are explicitly unavailable until import. No unknown count becomes a zero or a
claim of missing glyphs. A remote preview alone does not mark these as analyzed.

FontHub caches indexer responses in process for 5 seconds; if the service fails,
already cached records may be shown with an explicit unavailable/stale message.
A freshly started FontHub has no persisted external metadata fallback. Local
archive search must continue even when the indexer is stopped.
