# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies (activate venv first)
source venv/bin/activate
pip install -r requirements.txt

# Run migrations
python manage.py migrate

# Generate migrations after model changes
python manage.py makemigrations

# Seed the database (idempotent; --clear to wipe and reseed)
python manage.py seed_profiles
python manage.py seed_profiles --clear

# Start dev server
python manage.py runserver

# Lint (errors only, then style warnings)
flake8 . --count --select=E9,F63,F7,F82 --show-source --statistics
flake8 . --count --exit-zero --max-complexity=10 --max-line-length=127 --statistics

# Run all tests (either runner works; CI uses pytest)
python manage.py test api users
pytest --cov=. --cov-report=term-missing -v

# Run a specific test class
python manage.py test api.tests.NLParserTests
python manage.py test api.tests.ProfileListTests
python manage.py test api.tests.ProfileSearchTests
python manage.py test users.tests.TokenLifecycleTests
python manage.py test users.tests.RoleEnforcementTests
python manage.py test users.tests.APIVersionMiddlewareTests
```

## Environment variables

Create `.env` in the project root:

```dotenv
DEBUG=True
SECRET_KEY=your-secret-key-here
ALLOWED_HOSTS=*
DATABASE_URL=postgresql://user:password@host:5432/dbname
GITHUB_CLIENT_ID=your_github_app_client_id
GITHUB_CLIENT_SECRET=your_github_app_client_secret
GITHUB_CALLBACK_URL=http://localhost:8000/auth/github/callback/
# Optional — Redis for distributed rate limiting
CACHE_URL=redis://localhost:6379/1
```

Without `DATABASE_URL`, the app falls back to individual vars: `POSTGRES_DB`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`.

## Architecture

Django project module: `genderize_project/`. Two apps:

- **`users/`** — authentication, tokens, RBAC
- **`api/`** — profile data, filtering, NL search

### Request flow

```
Every /api/* request:
  RequestLoggingMiddleware → APIVersionMiddleware (X-API-Version: 1 required)
  → BearerTokenAuthentication → IsActiveUser / IsAdminRole permission
  → view handler

/auth/* endpoints:
  authentication_classes = []  (no auth required)
  permission_classes = []
  throttle_classes = [AuthThrottle]  (10 req/min per IP)
```

### URL routing

```
/auth/github/              → GitHubAuthorizeView   (GET)
/auth/github/callback/     → GitHubCallbackView    (GET)
/auth/refresh/             → RefreshTokenView      (POST)
/auth/logout/              → LogoutView            (POST)

/api/users/me/             → UserMeView            (GET, any authenticated user)
/api/profiles/export/      → ProfileExportView     (GET, analyst+)
/api/profiles/search/      → ProfileSearchView     (GET, analyst+)
/api/profiles/             → ProfileView           (GET analyst+, POST admin)
/api/profiles/<uuid:id>/   → ProfileDetailView     (GET analyst+, DELETE admin)
```

Note: `UserMeView` is imported from `users.views` and wired in `api/urls.py`, not `users/urls.py`.

URL order in `api/urls.py` matters — `export/` and `search/` must be before `<uuid:id>/`.

### Key modules

- **[users/models.py](users/models.py)** — `User`, `OAuthState`, `AccessToken`, `RefreshToken`. Token TTLs defined at module level: access=30min, refresh=1hr, state=10min. All IDs are UUID v7 (`uuid6.uuid7`), not Django's default UUID4.

- **[users/services.py](users/services.py)** — `PKCEService` (code_verifier/challenge generation), `GitHubOAuthService` (concurrent token exchange + user fetch), `TokenService` (issue_token_pair, refresh, revoke), `create_or_update_user`.

- **[users/authentication.py](users/authentication.py)** — `BearerTokenAuthentication`: reads `Authorization: Bearer <token>`, validates against `AccessToken` table, returns `(user, token)`.

- **[users/permissions.py](users/permissions.py)** — `IsActiveUser` (base, any authenticated active user), `IsAdminRole` (role=admin), `IsAnalystOrAdmin` (role in admin/analyst). All extend each other; never scatter role checks into view logic.

- **[users/middleware.py](users/middleware.py)** — `APIVersionMiddleware` checks `HTTP_X_API_VERSION` on all non-`/auth/` and non-`/admin/` requests. `RequestLoggingMiddleware` logs method/path/status/ms for every request.

- **[users/throttling.py](users/throttling.py)** — `AuthThrottle` (10/min per IP), `UserThrottle` (60/min per authenticated user). Applied via `throttle_classes` on view classes.

- **[api/filters.py](api/filters.py)** — `build_profile_queryset(queryset, params)` validates and applies all query params. Returns `(queryset, None)` or `(None, error_dict)`. The `error_dict` carries `_status_code` for the caller.

- **[api/parser.py](api/parser.py)** — Pure regex + lookup NL parser. `parse_query(q)` returns a filter-params dict or `None`. `"young"` → min_age=16, max_age=24. Both genders together → no gender filter.

- **[api/services.py](api/services.py)** — `ProfileAggregatorService.fetch_and_process_data(name)` fires three external API calls concurrently via `ThreadPoolExecutor` + `httpx.Client`. Raises `ExternalAPIException` or `InvalidProfileDataException` on failure → caller maps to 502. Both exception classes live in **[api/exceptions.py](api/exceptions.py)**.

- **[api/pagination.py](api/pagination.py)** — Extended `ProfilePagination` adds `total_pages` and `links` (self/next/prev) to the response envelope.

- **[api/countries.py](api/countries.py)** — ISO 3166-1 alpha-2 code ↔ country name mapping. Used by `ProfileAggregatorService` to resolve names and by `parse_query` for NL country lookups.

### Age group boundaries

`ProfileAggregatorService._age_group(age)` maps: child ≤12, teenager ≤19, adult ≤59, senior >59.

### Critical settings note

`URL_FORMAT_OVERRIDE = None` is set in `REST_FRAMEWORK` to prevent DRF 3.17's content negotiator from intercepting `?format=csv` on the export endpoint. Without this, DRF raises `Http404` because no `csv` renderer is registered.

### Grader/CI test mode

`GitHubCallbackView` has a hardcoded bypass: if `code` is `test_code` (admin) or `test_analyst_code` (analyst), state validation is skipped and a real user+token pair is created without hitting GitHub. These users have fixed `github_id` values (`99990001`, `99990002`). Use these codes in integration tests that need a seeded logged-in user without a real OAuth round-trip.

### Cache table

When `DATABASE_URL` is set but `CACHE_URL` is not, the app uses `django.core.cache.backends.db.DatabaseCache`. Run `python manage.py createcachetable` once after migrations, or throttling will raise `ProgrammingError`.

### Token rotation contract

`RefreshToken` is single-use. On `POST /auth/refresh/`:
1. Validate token is not revoked and not expired
2. Set `is_revoked=True` on the old token
3. Issue new access + refresh pair

Any attempt to reuse a refresh token returns 401.

### POST /api/profiles/ idempotency

`name` is normalized to `strip().lower()`. Pre-check via `Profile.objects.get(name=normalized)` returns 200 if found. `IntegrityError` on `create()` is caught as a race-condition fallback and also returns 200.

### Test patterns

All HTTP tests that hit `/api/*` need both credentials in `APIClient`:
```python
client.credentials(
    HTTP_AUTHORIZATION=f"Bearer {token.token}",
    HTTP_X_API_VERSION="1",
)
```

Auth endpoint tests that use `AuthThrottle` must call `cache.clear()` in `setUp()` to prevent throttle accumulation across tests.

Tests that mock `ProfileAggregatorService` must patch `api.views.ProfileAggregatorService` (where it is imported), not `api.services.ProfileAggregatorService`.
