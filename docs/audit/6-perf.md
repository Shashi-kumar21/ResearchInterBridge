# 6-perf: cheap shortlist reads, cached ranking, closing-date check

Branch `perf/match-and-shortlist`. Follows the Owner priority (user-friendly,
low-latency, cheap to host and operate) and QA items 1, 2, n5 and n6.

## Shortlist listing (`shortlist.list_items`)

* No re-scoring and no full target loads. Two batched reads give each target's
  name, `last_checked_at` and, for programs, `closing_date`.
* `current` is true when `matching.inputs_fingerprint(...)` (new, public: the
  fingerprint a score would carry, computed from the target id and last-checked
  time without loading the target) equals the stored one, and, for a program,
  no stated closing date has passed since the snapshot was evaluated. A passed
  deadline therefore never stays "eligible" (§13.3).
* Score and eligibility are recomputed only by `refresh`.
* `GET /api/shortlist` no longer spends the `match.score` budget, so one
  Shortlist click costs one unit (QA n5 on #39).

## Ranked-list cache (`recommendations`)

* The ranked list for one student is cached in-process for 120 s (at most 256
  lists, oldest dropped first). "Load more" slices it instead of re-scoring up
  to 300 records.
* The key hashes the database file, user id, target type, filters, the profile
  fields a score reads, the student's shortlist statuses, `include_excluded`,
  the date, the weights and `SCORING_VERSION`. A profile or shortlist change is
  therefore a miss. A list is never served to another student.
* Only `(target id, MatchResult, shortlist status)` is cached, never the
  records. QA measured the first version (full faculty records with papers and
  abstracts) at about 32 MB per 300-member list, up to 8.2 GB per worker with
  256 entries (B1 on #41). The page's rows are now loaded afterwards as browse
  summaries (`include_publications=False`: highlights plus a count), which is
  also all a list row returns (n1). A full load still happens once per cache
  miss, because scoring needs every attributed paper (ADR-022, B1 on #34).
* Records newly fetched by the operator CLI appear within the TTL.
  `recommendations.clear_cache()` drops everything.

## Web (`/matches`)

* The Undo notice names the restored item (QA n6 on #39).

## Tests

* `tests/test_api_matching.py`: the cache holds no records and a page loads a
  summary for its rows only; paging re-scores nothing (scoring calls are
  counted), a profile change re-scores, a shortlist change shows on the next
  read, and the rate-limit test now expects unlimited listing.
* `tests/test_shortlist.py`: listing never loads a full target (loaders are
  patched to fail), and a passed closing date makes a program snapshot non-current
  until refresh.
* `web/components/match-browser.test.tsx`: the undo notice uses the item name.
