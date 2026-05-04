"""
Filter, sort, and validate query parameters for the profiles endpoint.

build_profile_queryset(queryset, params) → (filtered_qs, error_dict | None)

error_dict shapes:
  {"status": "error", "message": "...", "_status_code": 400 | 422}
"""
from django.db.models import QuerySet

VALID_GENDERS = {"male", "female"}
VALID_AGE_GROUPS = {"child", "teenager", "adult", "senior"}
VALID_SORT_FIELDS = {"age", "created_at", "gender_probability"}
VALID_ORDER_VALUES = {"asc", "desc"}


def _err(msg: str, code: int) -> dict:
    return {"status": "error", "message": msg, "_status_code": code}


def _parse_float(value: str, name: str):
    """Return (float, None) or (None, error_dict)."""
    try:
        f = float(value)
        if not (0.0 <= f <= 1.0):
            return None, _err(f"'{name}' must be between 0 and 1", 422)
        return f, None
    except (TypeError, ValueError):
        return None, _err("Invalid query parameters", 422)


def _parse_int(value: str, name: str):
    """Return (int, None) or (None, error_dict)."""
    try:
        return int(value), None
    except (TypeError, ValueError):
        return None, _err("Invalid query parameters", 422)


def build_profile_queryset(queryset: QuerySet, params: dict):
    """
    Apply filters and sorting from *params* to *queryset*.

    Returns (queryset, None) on success,
            (None, error_dict)  on validation failure.
    """
    # ── Gender ──────────────────────────────────────────────────────────────
    gender = params.get("gender")
    if gender is not None:
        if not isinstance(gender, str) or gender.strip() == "":
            return None, _err("Invalid query parameters", 422)
        gender = gender.strip().lower()
        if gender not in VALID_GENDERS:
            return None, _err("Invalid query parameters", 422)
        queryset = queryset.filter(gender=gender)

    # ── Age group ────────────────────────────────────────────────────────────
    age_group = params.get("age_group")
    if age_group is not None:
        if not isinstance(age_group, str) or age_group.strip() == "":
            return None, _err("Invalid query parameters", 422)
        age_group = age_group.strip().lower()
        if age_group not in VALID_AGE_GROUPS:
            return None, _err("Invalid query parameters", 422)
        queryset = queryset.filter(age_group=age_group)

    # ── Country ──────────────────────────────────────────────────────────────
    country_id = params.get("country_id")
    if country_id is not None:
        if not isinstance(country_id, str) or country_id.strip() == "":
            return None, _err("Invalid query parameters", 422)
        queryset = queryset.filter(country_id__iexact=country_id.strip())

    # ── Age range ────────────────────────────────────────────────────────────
    min_age_raw = params.get("min_age")
    if min_age_raw is not None:
        min_age, err = _parse_int(min_age_raw, "min_age")
        if err:
            return None, err
        queryset = queryset.filter(age__gte=min_age)

    max_age_raw = params.get("max_age")
    if max_age_raw is not None:
        max_age, err = _parse_int(max_age_raw, "max_age")
        if err:
            return None, err
        queryset = queryset.filter(age__lte=max_age)

    # ── Probability thresholds ───────────────────────────────────────────────
    mgp_raw = params.get("min_gender_probability")
    if mgp_raw is not None:
        mgp, err = _parse_float(mgp_raw, "min_gender_probability")
        if err:
            return None, err
        queryset = queryset.filter(gender_probability__gte=mgp)

    mcp_raw = params.get("min_country_probability")
    if mcp_raw is not None:
        mcp, err = _parse_float(mcp_raw, "min_country_probability")
        if err:
            return None, err
        queryset = queryset.filter(country_probability__gte=mcp)

    # ── Sorting ──────────────────────────────────────────────────────────────
    sort_by = params.get("sort_by")
    order = params.get("order", "asc")

    if sort_by is not None:
        if sort_by not in VALID_SORT_FIELDS:
            return None, _err("Invalid query parameters", 422)
        if order not in VALID_ORDER_VALUES:
            return None, _err("Invalid query parameters", 422)
        field = f"-{sort_by}" if order == "desc" else sort_by
        queryset = queryset.order_by(field)
    elif order != "asc" and params.get("sort_by") is None:
        # order without sort_by is meaningless but not an error; ignore it
        pass

    return queryset, None
