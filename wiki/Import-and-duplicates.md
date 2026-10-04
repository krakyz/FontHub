# Source instances and duplicates

## Identity rules

An archive instance is `(binary SHA-256, source)`. The same bytes imported from a
different source are a separate instance and a separate search result. Those
cross-source matches are low-priority duplicates; they never disappear through
automatic merging. Physical storage remains content-addressed and may share
the immutable original and derived preview.

| Tables | Role |
|---|---|
| files | One original path/name per binary SHA-256 |
| faces | Derived face metadata per physical binary and collection index |
| origins | Source instances, keyed by (hash, source), with instance-added date |

First import creates all three. Reimport of identical bytes from the same source
is ignored as a duplicate. Import from a new source creates an origins row and
reuses analysis. Different bytes/versions/formats remain distinct physical files.
Filename alone is not identity. The first archived filename remains the physical
download filename even when another source supplies a different filename.

This rule intentionally does not distinguish two different providers configured
under the same source ID: source-instance granularity is currently the fixed
source registry. Supporting multiple user-configured repositories requires new
source IDs, not weakening SHA-256 deduplication.

## UI and exports

Search groups within `(family, author, source)` only. This still summarizes formats
and styles from the same source in one family result. Families from different
sources are separate rows with their own source labels. Family tables and family
ZIP downloads include only files belonging to the selected source instance.

Public IDs are `<physical-face-id>~<source-id>`. The preview itself is stored under
the physical face ID. Old links without `~source` remain readable and select a
surviving origin, but are ambiguous if multiple origins exist; new links always
include the source.

Changing the source in the detail UI relabels one origin for the whole binary,
including all TTC faces. It does not relabel other source instances. If the target
source already has an instance, the change returns 409 and preserves both.

## Migration and limitations

On first startup the existing files.source/added_at values populate origins. A
migration marker prevents deleted/relabelled origins from being recreated on
restart. Legacy files.source is kept for compatibility but is no longer the
authoritative instance property. Back up catalog.sqlite before deploying.

Internet records become source-qualified archive instances when imported.
Google Fonts already represented in the archive under that same source is omitted
from external results by family name; another source never suppresses it.
That suppression is a family-level heuristic, not proof that every revision or
style is installed. Tracking partial families/version availability is future work.

Full manifests and documents are retained in FontHub's revision download cache.
The archive schema does not yet have a queryable field-level provenance ledger;
it records source membership and binary identity. Reanalysis changes derived
metadata only and preserves all source instances and archived original bytes.

## Queued imports

Local uploads, recursive inbox discovery and explicit external downloads enter
the same durable queue. External import returns HTTP 202 and redirects to
`/imports`; successful download alone does not mean analysis has finished.
Preview jobs are keyed by physical face ID, so source instances share one WOFF2
without merging their catalogue rows. [Queue lifecycle](Import-queue.md).
