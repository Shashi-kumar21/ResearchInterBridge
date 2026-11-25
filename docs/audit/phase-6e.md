# Phase 6e: cross-user reuse of candidate sets (task.md §13.4, ADR-023)

Branch `feat/phase-6e-result-reuse`. Owner taxonomy decision: OECD Fields of
Science, second level (2026-11-24).

## What changed

* `taxonomy.py`: the 42 OECD FORD fields and an editable keyword map;
  `fields_for()` maps keywords to fields or `other`.
* `result_reuse.py`: `bucket_for()` (fields, degree level and countries,
  hashed; `None` for `other` only), membership upkeep, the member count, and
  `candidates()`, which reuses a stored id set (writing an audit event) or
  builds one from the bucket's fields.
* Migration 11 `result_set_reuse`: `profile_buckets` (per-user, cascade) and
  `bucket_result_sets` (shared, ids only, 6 h TTL).
* `recommendations._candidates()` uses the bucket set when k >= 3 and otherwise
  scores normally. It runs only on a ranked-list cache miss.
* QA n2 on #41: expired ranked lists are swept on every put, and
  `CACHE_MAX_ENTRIES` is 64 (about 64 MB worst case per worker at the measured
  1 MB per list).
* The account export gains `profile_bucket` (an id and timestamp only).

## QA checklist

1. Bucket features: fields, degree level and country only (test: name, email,
   GPA, skills and same-field keywords do not change the bucket id).
2. k >= 3: two students never store or reuse; the third builds the set; later
   requests reuse it.
3. Ids only: `bucket_result_sets` has no score or snapshot column, and two
   students on the same set get different scores.
4. Audit: actor `system`, metadata `query_fingerprint` (filters only),
   `target_type`, `reused_ids` and `bucket_members`; no email, name, keyword or
   user id.
5. Test 33: a reuse succeeds with `socket.connect`, `faculty.search_faculty` and
   `discovery.search_programs` patched to fail, and the response holds no data
   from the other profiles.
6. `other`: three `other`-only students store no bucket and never reuse.
7. Cost and latency (per match request on a ranked-list cache miss):
   * Normal (k < 3, or no bucket): 1 membership upsert + 1 count, then the
     same browse reads as before (up to 300 records with papers), scoring, and
     a summary load of the page rows.
   * First build (k >= 3, no stored set): the same as normal, plus one insert
     of the id list. Records are not loaded twice.
   * Reuse: 1 upsert + 1 count + 1 set read + 1 audit insert, then a full
     load of only the stored ids, scoring, and the page summary load. No
     browse pass over the whole cache, and no outbound call.
   * A ranked-list cache hit is unchanged: no scoring and no reuse logic.
   * No background job and no paid service; sets are built lazily and expire
     after 6 h.

## Tests

`tests/test_result_reuse.py` (16 cases): the bucket features, `other`-only,
taxonomy integrity and generic-word traps, k = 2, build then reuse,
acceptance test 33, per-student re-scoring, `other` never reuses, expired sets
are not reused, membership cascade and export, and the cache sweep. The
account export test expects `profile_bucket`.

## QA review of #42 (REQUEST_CHANGES), fixed on the branch

* B1: building a set no longer drops records whose areas are empty or map to
  `other`. Only a record whose areas all map to known fields outside the bucket
  is left out (`result_reuse._excluded_by_bucket`). Test: a student keeps the
  same list before and after the bucket reaches k = 3, apart from that one
  known-disjoint record.
* n1: `record_membership` reads first and writes only when the bucket changed.
  Test: an unchanged bucket keeps its `updated_at`.
* n2 (not changed): the audit row keeps `reused_ids`, because §13.4 item 5
  names them. The list is capped at 300 ids. A count and hash instead would
  need an Owner decision against the spec.

