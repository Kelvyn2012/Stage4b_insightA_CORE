"""
CSV ingestion pipeline for the POST /api/profiles/upload/ endpoint.

Design:
  - io.TextIOWrapper streams the binary upload without loading it into memory
  - Rows are validated individually; one bad row never aborts the upload
  - Valid rows accumulate in a chunk of 1000 and are inserted via bulk_create
  - Each chunk is its own transaction; a failed chunk doesn't roll back earlier ones
  - ignore_conflicts=True lets PostgreSQL skip duplicate names at the DB level;
    the count difference (chunk size – returned rows) is reported as duplicate_name
  - Cache is invalidated after any successful insert
"""
import csv
import io

from django.db import transaction

from .models import Profile

_CHUNK_SIZE = 1000
_REQUIRED_FIELDS = ("name", "gender", "age", "country_id")
_VALID_GENDERS = {"male", "female"}


def _age_group(age: int) -> str:
    if age <= 12:
        return "child"
    if age <= 19:
        return "teenager"
    if age <= 59:
        return "adult"
    return "senior"


def ingest_csv_stream(file_obj) -> dict:
    """
    Stream-parse *file_obj* (binary file-like) and bulk-insert Profile rows.

    Returns:
        {
            "status": "success",
            "total_rows": int,
            "inserted": int,
            "skipped": int,
            "reasons": {reason: count, ...}
        }
    """
    # Seek to start in case the caller left the position elsewhere.
    try:
        file_obj.seek(0)
    except Exception:
        pass

    text_stream = io.TextIOWrapper(file_obj, encoding="utf-8", errors="replace")
    reader = csv.DictReader(text_stream)

    total_rows = 0
    inserted = 0
    skipped = 0
    reasons: dict[str, int] = {}
    chunk: list[Profile] = []

    def _skip(reason: str) -> None:
        nonlocal skipped
        skipped += 1
        reasons[reason] = reasons.get(reason, 0) + 1

    def _flush() -> None:
        nonlocal inserted, skipped
        if not chunk:
            return
        with transaction.atomic():
            created = Profile.objects.bulk_create(list(chunk), ignore_conflicts=True)
        actual = len(created)
        dups = len(chunk) - actual
        inserted += actual
        if dups > 0:
            skipped += dups
            reasons["duplicate_name"] = reasons.get("duplicate_name", 0) + dups
        chunk.clear()

    for row in reader:
        total_rows += 1

        # Malformed row: DictReader puts extra values under key None (too many
        # columns) or None values for missing columns (too few columns).
        if None in row or None in row.values():
            _skip("malformed_row")
            continue

        # Required fields
        name = (row.get("name") or "").strip()
        gender_raw = (row.get("gender") or "").strip().lower()
        age_raw = (row.get("age") or "").strip()
        country_id = (row.get("country_id") or "").strip().upper()

        if not name or not gender_raw or not age_raw or not country_id:
            _skip("missing_fields")
            continue

        if gender_raw not in _VALID_GENDERS:
            _skip("invalid_gender")
            continue

        try:
            age = int(age_raw)
            if not 0 <= age <= 150:
                raise ValueError
        except (ValueError, OverflowError):
            _skip("invalid_age")
            continue

        # Optional fields — coerce gracefully, never raise
        try:
            gender_probability = float(row.get("gender_probability") or 0)
        except (ValueError, TypeError):
            gender_probability = 0.0

        try:
            country_probability = float(row.get("country_probability") or 0)
        except (ValueError, TypeError):
            country_probability = 0.0

        try:
            sp = row.get("sample_size")
            sample_size = int(sp) if sp and str(sp).strip() else None
        except (ValueError, TypeError):
            sample_size = None

        country_name = (row.get("country_name") or "").strip()

        chunk.append(Profile(
            name=name,
            gender=gender_raw,
            gender_probability=gender_probability,
            sample_size=sample_size,
            age=age,
            age_group=_age_group(age),
            country_id=country_id,
            country_name=country_name,
            country_probability=country_probability,
        ))

        if len(chunk) >= _CHUNK_SIZE:
            _flush()

    _flush()

    # Detach the text wrapper without closing the underlying binary file —
    # Django owns the upload file lifecycle.
    try:
        text_stream.detach()
    except Exception:
        pass

    if inserted > 0:
        from .cache import invalidate_profile_cache
        invalidate_profile_cache()

    return {
        "status": "success",
        "total_rows": total_rows,
        "inserted": inserted,
        "skipped": skipped,
        "reasons": reasons,
    }
