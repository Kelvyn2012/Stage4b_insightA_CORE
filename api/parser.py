"""
Rule-based natural language query parser for the /api/profiles/search endpoint.

No AI, no LLMs, no external NLP libraries — purely deterministic regex + lookup.

parse_query(q) → dict of filter params, or None if nothing meaningful is found.

Supported patterns
──────────────────
Gender tokens     : male, males, men, man, female, females, women, woman
Age group tokens  : child, children, kid(s), teenager(s), teen(s), adult(s),
                    senior(s), elderly
Special token     : young  → min_age=16, max_age=24
Age phrases       : above X, older than X, over X  → min_age=X
                    below X, under X, younger than X → max_age=X
                    between X and Y / aged X to Y   → min_age=X, max_age=Y
Country phrase    : from <country> / in <country>   → country_id
                    or bare country name anywhere

Rule: if BOTH male and female tokens are present, no gender filter is applied.

Returns None when no meaningful filters could be extracted.
"""
import re
from typing import Optional

from .countries import COUNTRY_LOOKUP

# ── Token maps ────────────────────────────────────────────────────────────────

_MALE_TOKENS = {"male", "males", "men", "man"}
_FEMALE_TOKENS = {"female", "females", "women", "woman"}

_AGE_GROUP_MAP: dict[str, str] = {
    "child": "child",
    "children": "child",
    "kid": "child",
    "kids": "child",
    "teenager": "teenager",
    "teenagers": "teenager",
    "teen": "teenager",
    "teens": "teenager",
    "adult": "adult",
    "adults": "adult",
    "senior": "senior",
    "seniors": "senior",
    "elderly": "senior",
    "old": "senior",
}

# Sorted longest-first so multi-word country names are matched before substrings
_SORTED_COUNTRY_KEYS = sorted(COUNTRY_LOOKUP.keys(), key=len, reverse=True)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _word_in(word: str, text_tokens: set) -> bool:
    return word in text_tokens


def _extract_country(q: str) -> Optional[str]:
    """
    Try to find a country reference in *q* (lowercase string).

    Priority:
    1. "from <country>" or "in <country>"
    2. Bare country name anywhere in the string
    """
    # Remove connector stop words that might confuse matching
    for prep in ("from ", "in "):
        idx = q.find(prep)
        if idx != -1:
            candidate = q[idx + len(prep):].strip()
            # Try progressively shorter prefixes (handles trailing words)
            words = candidate.split()
            for length in range(len(words), 0, -1):
                phrase = " ".join(words[:length])
                if phrase in COUNTRY_LOOKUP:
                    return COUNTRY_LOOKUP[phrase]

    # Bare country name scan (longest match first)
    for country_key in _SORTED_COUNTRY_KEYS:
        pattern = r"\b" + re.escape(country_key) + r"\b"
        if re.search(pattern, q):
            return COUNTRY_LOOKUP[country_key]

    return None


def _extract_ages(q: str) -> dict:
    """Return min_age / max_age extracted from age phrases."""
    result: dict = {}

    # between X and Y  /  aged X to Y  /  X to Y years
    between = re.search(
        r"\b(?:between|aged?)\s+(\d+)\s+(?:and|to)\s+(\d+)", q
    )
    if between:
        result["min_age"] = int(between.group(1))
        result["max_age"] = int(between.group(2))
        return result

    above = re.search(r"\b(?:above|older\s+than|over)\s+(\d+)", q)
    if above:
        result["min_age"] = int(above.group(1))

    below = re.search(r"\b(?:below|under|younger\s+than)\s+(\d+)", q)
    if below:
        result["max_age"] = int(below.group(1))

    return result


# ── Public API ────────────────────────────────────────────────────────────────

def parse_query(q: str) -> Optional[dict]:
    """
    Parse *q* into a filter-params dict.

    Returns None if no meaningful filter can be extracted.
    """
    if not q or not q.strip():
        return None

    normalised = q.strip().lower()
    # Replace punctuation (except apostrophes in country names) with spaces
    normalised = re.sub(r"[^\w\s']", " ", normalised)
    tokens: set[str] = set(normalised.split())

    filters: dict = {}

    # ── "young" special token ────────────────────────────────────────────────
    if "young" in tokens:
        filters["min_age"] = 16
        filters["max_age"] = 24

    # ── Age phrases (may override "young" min/max if more specific) ──────────
    age_filters = _extract_ages(normalised)
    if age_filters:
        filters.update(age_filters)

    # ── Age group ────────────────────────────────────────────────────────────
    for token, group in _AGE_GROUP_MAP.items():
        if re.search(r"\b" + re.escape(token) + r"\b", normalised):
            filters["age_group"] = group
            break

    # ── Gender ───────────────────────────────────────────────────────────────
    male_found = bool(_MALE_TOKENS & tokens)
    female_found = bool(_FEMALE_TOKENS & tokens)

    # Both genders mentioned together → no gender filter (e.g. "male and female teenagers")
    if male_found and not female_found:
        filters["gender"] = "male"
    elif female_found and not male_found:
        filters["gender"] = "female"

    # ── Country ──────────────────────────────────────────────────────────────
    country_code = _extract_country(normalised)
    if country_code:
        filters["country_id"] = country_code

    # ── Guard: must yield at least one meaningful filter ─────────────────────
    if not filters:
        return None

    return filters
