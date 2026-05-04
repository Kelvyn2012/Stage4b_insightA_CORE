# Stage 4B Solution

## Part 1 — Query Performance

### Approach

**Redis query result caching** is added to `ProfileView.get` and `ProfileSearchView.get`.

- Validation runs first (invalid params still return 4xx; they never pollute the cache).
- After validation, the raw params are normalised via `normalize_filters` and hashed to a deterministic key.
- On cache hit the serialised response dict is returned directly; no SQL executes.
- On cache miss the queryset runs, the result is cached for 5 minutes (TTL=300 s), and the response is returned.
- Write operations (POST `/profiles`, DELETE `/profiles/<id>`, CSV upload) call `invalidate_profile_cache()`, which bumps a shared Redis counter (`profile_cache_version`). Old keys embed the previous version number and become unreachable without iterating or flushing the whole cache.

**New indexes** (migration `0003_stage4b_indexes`):

| Index | Fields | Rationale |
|-------|--------|-----------|
| `idx_country_gender_age_group` | `(country_id, gender, age_group)` | The most frequent three-filter analyst query; PostgreSQL can resolve it with a single index scan |
| *(already exist)* | `name`, `age`, `created_at` | Created as `db_index=True` / `unique=True` in the initial migration; no duplicate DDL needed |

The four Stage 3 composite indexes (`idx_gender_age_group`, `idx_gender_country`, `idx_country_age_group`, `idx_age_gender`) are preserved unchanged.

**Streaming CSV export**: `ProfileExportView` replaces `HttpResponse + StringIO` with `StreamingHttpResponse` + a Python generator that calls `queryset.iterator(chunk_size=500)`. The entire result set is never held in memory; Django writes each CSV row to the client as it is produced.

### Trade-offs

- Validation-before-cache (rather than cache-before-validation) adds a negligible ORM query-builder call on every request but keeps API error contracts intact for tests and clients.
- Caching the full response dict (including pagination links) is host-specific. This is acceptable for a single-origin Railway deployment; links are reconstructed cheaply if multi-origin is ever needed.
- Version-counter invalidation is O(1) and safe under concurrent writes; no `KEYS *` or `FLUSHDB` is called.

---

## Part 2 — Query Normalization

### Approach

`api/cache.py` implements the canonical normalisation pipeline.

```
raw params ──► normalize_filters() ──► canonical dict ──► cache_key_for() ──► versioned_key()
```

`normalize_filters` rules (per spec):

| Field | Rule |
|-------|------|
| `gender` | lowercase + strip; keep only `"male"` / `"female"`, drop otherwise |
| `age_group` | lowercase + strip; keep only valid values, drop otherwise |
| `country_id` | uppercase + strip; drop if empty |
| `min_age` / `max_age` | cast to int, clamp 0–150; drop both if min > max |
| probability fields | cast to float, round 4dp, clamp 0.0–1.0 |
| `page` | int, min 1, default 1 |
| `limit` | int, clamp 1–50, default 10 |
| `sort_by` | accept `"age"`, `"created_at"`, `"gender_probability"`; default `"created_at"` |
| `order` | accept `"asc"` / `"desc"`; default `"asc"` |
| all other keys | dropped |

`cache_key_for` serialises the normalised dict with `json.dumps(sort_keys=True)`, takes the SHA-256 digest, and returns the first 16 hex characters prefixed with the caller's namespace (`"profiles:list"` or `"profiles:search"`).

`versioned_key` prepends `v{n}:` where `n` is the current value of `profile_cache_version` in Redis. When that counter is incremented (on any write), all previously issued versioned keys refer to a different prefix and are never matched again.

### Trade-offs

- SHA-256 truncated to 64-bit (16 hex chars): birthday-collision probability at 10 M distinct queries is ~2.7 × 10⁻⁹ — negligible.
- Normalisation is intentionally lossy for invalid values (e.g., `sort_by=bad` → `sort_by=created_at`). Validation happens before the cache check so bad requests still get 422 responses and never return cached good data.

---

## Part 3 — CSV Data Ingestion

### Approach

`api/ingestion.py` implements `ingest_csv_stream(file_obj)`.

**Memory model**: `io.TextIOWrapper` wraps the binary upload, `csv.DictReader` yields one row dict at a time. At most one chunk of 1 000 `Profile` instances is held in memory at once; the full file is never materialised.

**Chunk flush**:
```
Profile.objects.bulk_create(chunk, ignore_conflicts=True)
```
Each chunk is wrapped in its own `transaction.atomic()`. A DB error in chunk 500 does not roll back chunks 1–499. `ignore_conflicts=True` triggers `INSERT … ON CONFLICT DO NOTHING RETURNING *` on PostgreSQL; only actually-inserted rows are returned, so `len(chunk) - len(returned)` gives the exact `duplicate_name` count.

**Per-row validation** (bad rows are skipped and counted; the upload continues):

| Check | Reason recorded |
|-------|-----------------|
| `None` in keys or values (DictReader padding) | `malformed_row` |
| Any required field empty or absent | `missing_fields` |
| `gender` not `"male"` / `"female"` | `invalid_gender` |
| `age` non-integer or outside 0–150 | `invalid_age` |
| `name` already in DB | `duplicate_name` (silent DB conflict) |

**Cache invalidation**: called once after all chunks if `inserted > 0`.

### Concurrency safety

Each chunk is an independent transaction. Two concurrent uploads work on separate chunks; PostgreSQL serialises conflicts at the row level via `ON CONFLICT DO NOTHING`. There is no application-level lock.

---

## Before / After Query Performance

Estimates based on 1 M rows, remote PostgreSQL (Neon), ~5 ms base network RTT, no cache.

| Scenario | Before (Stage 3) | After (Stage 4B) | Saving |
|----------|-----------------|-----------------|--------|
| `GET /profiles` (no filter, page 1) — **cache hit** | ~80–120 ms | **< 5 ms** | ~95 % |
| `GET /profiles` (no filter, page 1) — **cold miss** | ~80–120 ms | ~80–120 ms | 0 % (first hit) |
| `GET /profiles?country_id=NG&gender=female&age_group=adult` (3-filter) | ~150–250 ms (seq scan possible) | ~30–60 ms (new composite index) → < 5 ms on cache hit | ~75–98 % |
| `GET /profiles/search?q=adult+males+from+kenya` — **cache hit** | ~80–150 ms | **< 5 ms** | ~97 % |
| `GET /profiles/export?format=csv` (1 M rows) | OOM risk (full table in RAM) | Constant ~50 MB peak (streaming, 500-row chunks) | — |
| `POST /profiles/upload` (50 000 rows) | N/A (new endpoint) | ~8–15 s, < 60 MB RAM | — |

Cache hit rate in steady-state analyst workloads (few unique filter combinations, high repeat rate) is typically > 80 %, giving effective P99 well under 50 ms.

---

## Failure & Edge-Case Handling

| Scenario | Handling |
|----------|----------|
| Redis unavailable | Django falls back to DB or locmem cache; the version key re-initialises on next write |
| CSV row missing required column | Skipped, counted as `missing_fields`; upload continues |
| CSV row with invalid age (e.g. `"abc"`) | Skipped, counted as `invalid_age` |
| Duplicate name in same upload batch | Handled silently by `ON CONFLICT DO NOTHING`; counted as `duplicate_name` |
| Duplicate name already in DB | Same as above |
| Mid-upload DB error on one chunk | That chunk is rolled back; all previous chunks remain committed |
| File > 150 MB | Rejected with 413 before streaming begins |
| Non-CSV content type and non-`.csv` extension | Rejected with 415 |
| Concurrent uploads | Safe — each chunk is an independent atomic transaction with no shared locks |
| Cache version key missing | Re-initialised to 1 on the next `incr` or `set` call |
