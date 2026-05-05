# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Setup
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py seed_profiles           # idempotent seeder (2026 profiles)
python manage.py seed_profiles --clear   # force clean reseed

# Run dev server
python manage.py runserver

# Run all tests (118 tests across both apps)
python manage.py test api users

# Run a specific test class
python manage.py test api.tests.NLParserTests
python manage.py test api.tests.ProfileListTests
python manage.py test api.tests.ProfileSearchTests
python manage.py test users.tests.TokenLifecycleTests
python manage.py test users.tests.RoleEnforcementTests

# Run with pytest (configured in pytest.ini)
pytest
pytest api/tests.py -k "NLParser"
```

## Environment variables (`.env`)

```dotenv
DEBUG=True
SECRET_KEY=...
ALLOWED_HOSTS=*
DATABASE_URL=postgresql://user:password@host:5432/dbname

# GitHub OAuth
GITHUB_CLIENT_ID=...
GITHUB_CLIENT_SECRET=...
GITHUB_CALLBACK_URL=http://localhost:8000/auth/github/callback/

# Optional: Redis for production rate-limit cache
CACHE_URL=redis://localhost:6379/1
```

Without `DATABASE_URL`, the app falls back to a local PostgreSQL connection using `POSTGRES_DB`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`.

## Architecture

### Apps

**`api/`** — Profile data domain
- `models.py` — `Profile` model (UUID v7 primary key, composite indexes for common filter combos)
- `filters.py` — Translates raw query params into a validated ORM queryset
- `parser.py` — Rule-based NLP parser (`parse_query`) for the `/search` endpoint (no AI)
- `services.py` — `ProfileAggregatorService`: calls Genderize + Agify + Nationalize APIs to create a profile from a name
- `serializers.py` — `ProfileSerializer` (full) and `ProfileListSerializer` (list-optimised)
- `pagination.py` — Custom paginator with `total_pages` and HATEOAS links
- `cache.py` — Caching layer (see below)
- `ingestion.py` — Streaming CSV bulk-insert pipeline (see below)
- `views.py` — `ProfileView`, `ProfileDetailView`, `ProfileSearchView`, `ProfileExportView`
- `countries.py` — ISO country code ↔ name mapping + NLP lookup table

**`users/`** — Auth & identity domain
- `models.py` — `User`, `OAuthState`, `AccessToken`, `RefreshToken` (all UUID v7)
- `authentication.py` — `BearerTokenAuthentication` DRF class (opaque tokens, no JWTs)
- `permissions.py` — `IsActiveUser`, `IsAdminRole`, `IsAnalystOrAdmin`
- `throttling.py` — `AuthThrottle` (10/min per IP), `UserThrottle` (60/min per user)
- `middleware.py` — `APIVersionMiddleware` (requires `X-API-Version: 1` on all `/api/*` requests), `RequestLoggingMiddleware`
- `services.py` — `PKCEService`, `GitHubOAuthService`, `TokenService`

### URL layout

```
/admin/                    Django admin
/auth/github               Initiate GitHub OAuth + PKCE
/auth/github/callback/     Exchange code → access + refresh tokens
/auth/refresh/             Rotate tokens (single-use refresh token)
/auth/logout/              Revoke refresh token
/api/users/me/             Authenticated user info
/api/profiles/             List (GET, analyst+) / Create from name (POST, admin only)
/api/profiles/<uuid>/      Retrieve (GET) / Delete (DELETE, admin only)
/api/profiles/search/      NL search via ?q=
/api/profiles/export/      CSV download (GET, ?format=csv)
```

### Caching (`api/cache.py`)

All cache logic flows through one pipeline:

```
raw params → normalize_filters() → cache_key_for(prefix) → versioned_key() → Django cache
```

- `normalize_filters` drops unknown keys and coerces/clamps all values to a deterministic canonical form.
- `cache_key_for` SHA-256 hashes the sorted JSON, keeping the first 16 hex chars.
- `versioned_key` prepends `v{n}:` using a Redis counter (`profile_cache_version`). Any write operation calls `invalidate_profile_cache()`, which increments the counter — all prior keys become unreachable without iterating.
- TTL is 300 s. Caching applies to `ProfileView.get` and `ProfileSearchView.get`.
- Cache backend priority: Redis (`CACHE_URL`) → DB cache (`DATABASE_URL`) → LocMemCache (dev).

**Important**: validation runs before the cache check. Invalid params return 4xx and never populate the cache.

### CSV ingestion (`api/ingestion.py`)

`ingest_csv_stream(file_obj)` is a streaming, chunked pipeline:
- Wraps the binary upload in `io.TextIOWrapper` — never loads the full file into memory.
- Validates each row individually; bad rows are counted and skipped, never abort the upload.
- Required CSV columns: `name`, `gender`, `age`, `country_id`. Optional: `gender_probability`, `country_probability`, `sample_size`, `country_name`.
- Inserts in chunks of 1000 via `bulk_create(ignore_conflicts=True)`. Each chunk is its own `transaction.atomic()`.
- Returns a summary: `{total_rows, inserted, skipped, reasons}`.

### Token lifecycle

Tokens are opaque random strings stored in the database (no JWTs). Access tokens expire in 3 minutes; refresh tokens in 5 minutes. Refresh tokens are single-use — the old token is revoked atomically on rotation. Server-side revocation is always effective.

### Role enforcement

Role checks are done exclusively in `get_permissions()` on each view class, never in business logic. Default role on signup: `analyst`. Inactive users are rejected at the authentication layer (401), not the permission layer (403).

## Key design constraints

- `URL_FORMAT_OVERRIDE = None` in DRF settings — disables `?format=` for renderer negotiation to prevent conflict with the export endpoint's own `?format=csv` param. The export view also overrides `get_format_suffix()` to return `None`.
- All error responses follow `{"status": "error", "message": "..."}`.
- All success responses follow `{"status": "success", "data": ...}` (or with `page`/`total` etc. for lists).
- The `X-API-Version: 1` header is required on every `/api/*` request; `/auth/*` is exempt.
