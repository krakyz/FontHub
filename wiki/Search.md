# SQLite search and benchmarks

The authoritative analysis remains `faces.metadata` JSON. `search_faces` is a
derived projection of names, style, weight, format, type, a Unicode-casefolded
search string, compact character ranges and representative ordering.
`search_languages` stores membership. Insert/update/delete triggers maintain
both projections in the same transaction as authoritative metadata changes.
Every writer uses the functions registered by `catalog_search.register`;
editing faces through an unconfigured external SQLite connection is unsupported.

First startup builds the projection from existing JSON and commits its migration
marker with the data. Interrupted construction is safe to replay. Existing
originals, metadata, source identities and public IDs are preserved.

SQLite filters candidate faces, groups by `(family, author, source)`, counts,
sorts and selects ten results with LIMIT/OFFSET. Only those families load full
JSON to calculate display fields and representative Hyperglot coverage. A matching
style restricts that family's aggregate, preserving previous filter behavior.
Character requirements use packed inclusive Unicode intervals instead of parsing
the entire JSON for every candidate. This is a lossless search projection.

FTS5 trigram narrows terms of at least three characters; `instr` verifies exact
substring semantics. Short terms and SQLite without trigram use `instr` alone.
Unicode casefold handles Cyrillic and characters such as German ß independently
of SQLite's ASCII-only built-in case conversion. Representative ordering preserves
weight/style/format/ID. Only the selected sort controls direction; unknown dates
remain last. Families and counts still require processing candidate rows: an
index does not make every query constant time.

External results continue to come from saved indexer snapshots, with no upstream
acquisition. They join a temporary result table for combined sorting/pagination;
this iteration does not move the remote client's snapshot cache into archive
SQLite. The same-source family-name heuristic suppresses already archived remote
offers; separate sources remain separate rows. Future profiling may justify
precomputed family projections or remote projection storage.

## Reproducible measurements

`python benchmark_search.py --output benchmarks/search-after.json` creates a
temporary synthetic archive: 10k/30k/100k faces, four faces per family, two sources,
mixed variable/static types and 95 ASCII characters. No font files are parsed.
Each query runs four times, reporting the first call and median of three repeats.
“First” means first endpoint call, not a freshly restarted OS or cold disk cache.

Before/after reports are in `benchmarks/search-before.json` and
`benchmarks/search-after.json`. Measured 2026-10-04 on this Windows host:

| Faces | All, previous / SQL | Substring, previous / SQL | Filtered, previous / SQL |
|---|---|---|---|
| 10,000 | 0.133 / 0.044 s | 0.020 / 0.010 s | 0.050 / 0.028 s |
| 30,000 | 0.692 / 0.181 s | 0.060 / 0.015 s | 0.240 / 0.091 s |
| 100,000 | 2.075 / 0.643 s | 0.135 / 0.015 s | 0.439 / 0.313 s |

Background Docker builds increased variability. These are illustrative local
measurements, not capacity guarantees. Large CJK metadata, actual import parsing,
remote indices, concurrent users and disk exhaustion need separate measurements.
The post-change report measures Python allocations for a subsequent all-results
request separately from timings (~72–73 KiB peak). This excludes SQLite native
allocations, process baseline, OS caches and first-use Hyperglot data; it is not
total service RAM usage. Current process RSS after seeding and requests was
51–57 MiB across these sizes, including native allocations, libraries and retained
Python allocator arenas from seeding. This is a benchmark-process snapshot, not
the RAM of a production server or a search-only increment. The former full-catalogue Python search cache is removed.

`test_search_sql.py` compares 45 queries against an independent Python reference,
including Unicode, punctuation, source/type/format/language/character filters,
aggregate fields, ordering, page clamping and transactional update/deletion.
