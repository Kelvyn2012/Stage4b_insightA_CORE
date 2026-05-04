"""
Query result caching utilities for the profiles endpoints.

Public API:
  normalize_filters(raw)          → canonical dict (same intent → same dict)
  cache_key_for(normalized, pfx)  → "{prefix}:{sha256[:16]}"
  versioned_key(key)              → "v{n}:{key}"
  get_cached(key)                 → cached value or None
  set_cached(key, value, ttl=300) → None
  invalidate_profile_cache()      → bumps version counter; all old keys become unreachable
"""
import hashlib
import json

from django.core.cache import cache

_VERSION_KEY = "profile_cache_version"
_DEFAULT_TTL = 300  # 5 minutes

_VALID_GENDERS = {"male", "female"}
_VALID_AGE_GROUPS = {"child", "teenager", "adult", "senior"}
_VALID_SORT_FIELDS = {"age", "created_at", "gender_probability"}
_VALID_ORDERS = {"asc", "desc"}


def normalize_filters(raw: dict) -> dict:
    """
    Normalize raw query params into a canonical form.
    Unknown keys are dropped. Invalid values are replaced with defaults or dropped.
    Deterministic: same intent always produces the same output dict.
    """
    out: dict = {}

    gender = raw.get("gender")
    if isinstance(gender, str):
        g = gender.strip().lower()
        if g in _VALID_GENDERS:
            out["gender"] = g

    age_group = raw.get("age_group")
    if isinstance(age_group, str):
        ag = age_group.strip().lower()
        if ag in _VALID_AGE_GROUPS:
            out["age_group"] = ag

    country_id = raw.get("country_id")
    if isinstance(country_id, str):
        c = country_id.strip().upper()
        if c:
            out["country_id"] = c

    min_age = _coerce_int(raw.get("min_age"))
    max_age = _coerce_int(raw.get("max_age"))
    if min_age is not None:
        min_age = max(0, min(150, min_age))
    if max_age is not None:
        max_age = max(0, min(150, max_age))
    if min_age is not None and max_age is not None and min_age > max_age:
        min_age = None
        max_age = None
    if min_age is not None:
        out["min_age"] = min_age
    if max_age is not None:
        out["max_age"] = max_age

    mgp = _coerce_float(raw.get("min_gender_probability"))
    if mgp is not None:
        out["min_gender_probability"] = round(max(0.0, min(1.0, mgp)), 4)

    mcp = _coerce_float(raw.get("min_country_probability"))
    if mcp is not None:
        out["min_country_probability"] = round(max(0.0, min(1.0, mcp)), 4)

    page = _coerce_int(raw.get("page"))
    out["page"] = max(1, page) if page is not None else 1

    limit = _coerce_int(raw.get("limit"))
    out["limit"] = max(1, min(50, limit)) if limit is not None else 10

    sort_by = raw.get("sort_by")
    out["sort_by"] = sort_by if isinstance(sort_by, str) and sort_by in _VALID_SORT_FIELDS else "created_at"

    order = raw.get("order")
    out["order"] = order if isinstance(order, str) and order in _VALID_ORDERS else "asc"

    return out


def cache_key_for(normalized: dict, prefix: str) -> str:
    """Deterministic cache key: SHA-256 of sorted JSON → first 16 hex chars."""
    payload = json.dumps(normalized, sort_keys=True)
    digest = hashlib.sha256(payload.encode()).hexdigest()[:16]
    return f"{prefix}:{digest}"


def _get_version() -> int:
    v = cache.get(_VERSION_KEY)
    if v is None:
        cache.set(_VERSION_KEY, 1, timeout=None)
        return 1
    return int(v)


def versioned_key(key: str) -> str:
    """Prepend current version; bumping the version makes all old keys unreachable."""
    return f"v{_get_version()}:{key}"


def get_cached(key: str):
    return cache.get(versioned_key(key))


def set_cached(key: str, value, ttl: int = _DEFAULT_TTL) -> None:
    cache.set(versioned_key(key), value, timeout=ttl)


def invalidate_profile_cache() -> None:
    """
    Version-counter invalidation: increment the shared version key so every
    previously cached key (which embeds the old version number) becomes
    unreachable without iterating or clearing the whole cache.
    """
    try:
        cache.incr(_VERSION_KEY)
    except ValueError:
        cache.set(_VERSION_KEY, 1, timeout=None)


# ── Coercion helpers ──────────────────────────────────────────────────────────

def _coerce_int(value) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_float(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
