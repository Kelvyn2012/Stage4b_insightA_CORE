# Intelligence Query Engine — Secure Access & Multi-Interface Platform

**Insighta Labs+** — Stage 3 backend.

A production-ready Django REST Framework service that provides secure, multi-user access to 2026 demographic profiles with GitHub OAuth + PKCE authentication, token-based auth, role-based access control, API versioning, CSV export, rate limiting, and request logging.

---

## Stack

| Layer | Technology |
|---|---|
| Runtime | Python 3.11+ |
| Framework | Django 4.x + Django REST Framework 3.17+ |
| Database | PostgreSQL (Neon in production, local fallback) |
| Deployment | Gunicorn + WhiteNoise on Render/Railway |

---

## System Architecture

```
┌─────────────────────────────────────────────────────┐
│                  Single API Backend                 │
│                                                     │
│   /auth/*          /api/*                           │
│   GitHub OAuth     Profiles CRUD + Search + Export  │
│   Token refresh    Role-enforced endpoints          │
│   Logout           Paginated, filtered, versioned   │
└──────────────┬──────────────────────────────────────┘
               │
       RequestLoggingMiddleware
       APIVersionMiddleware (X-API-Version: 1 required)
               │
       ┌───────┴────────┐
       │ CLI clients    │ Web portal
       │ (Bearer token) │ (Bearer token)
       └────────────────┘
```

---

## CLI Usage

The CLI is a separate package (`insighta-cli`) installable globally:

```bash
pip install insighta-cli
```

Credentials are stored at `~/.insighta/credentials.json`. The CLI auto-refreshes the access token when it expires.

```bash
# Authenticate (opens GitHub OAuth in browser)
insighta login

# Sign out and revoke server-side token
insighta logout

# List profiles (all filters optional)
insighta profiles list
insighta profiles list --gender female --age-group adult --country NG
insighta profiles list --min-age 20 --max-age 40 --sort-by age --order desc
insighta profiles list --page 2 --limit 25

# Natural language search
insighta profiles search "young males from nigeria"
insighta profiles search "senior women from kenya"

# Get a single profile by UUID
insighta profiles get <uuid>

# Export to CSV (same filters as list; writes to stdout or --output FILE)
insighta profiles export --gender male --output males.csv

# Create a profile from external APIs (admin only)
insighta profiles create amara

# Delete a profile (admin only)
insighta profiles delete <uuid>
```

---

## Setup

### 1 · Clone and create virtualenv

```bash
git clone git@github.com:Kelvyn2012/Backend_Repository_Core_System.git
cd Backend_Repository_Core_System
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2 · Environment variables

Create `.env` in the project root:

```dotenv
DEBUG=True
SECRET_KEY=your-secret-key-here
ALLOWED_HOSTS=*
DATABASE_URL=postgresql://user:password@host:5432/dbname

# GitHub OAuth — required for auth flow
GITHUB_CLIENT_ID=your_github_app_client_id
GITHUB_CLIENT_SECRET=your_github_app_client_secret
GITHUB_CALLBACK_URL=http://localhost:8000/auth/github/callback/

# Optional: Redis for rate-limit cache in production
CACHE_URL=redis://localhost:6379/1
```

### 3 · Run migrations

```bash
python manage.py migrate
```

### 4 · Seed the database

```bash
python manage.py seed_profiles           # idempotent
python manage.py seed_profiles --clear   # force clean reseed
```

### 5 · Start development server

```bash
python manage.py runserver
```

### 6 · Run tests

```bash
python manage.py test api users                          # all 118 tests
python manage.py test api.tests.NLParserTests            # parser unit tests
python manage.py test api.tests.ProfileListTests         # filter/sort/pagination
python manage.py test api.tests.ProfileSearchTests       # search endpoint
python manage.py test users.tests.TokenLifecycleTests    # token rotation
python manage.py test users.tests.RoleEnforcementTests   # RBAC
```

---

## GitHub App Configuration

1. Create a GitHub OAuth App at `https://github.com/settings/developers`
2. Set **Homepage URL**: your deployment URL
3. Set **Authorization callback URL**: `https://your-domain.com/auth/github/callback/`
4. Copy Client ID and Client Secret → put in `.env`

---

## OAuth + PKCE Flow

```
Client                    Backend                    GitHub
  │                          │                          │
  │  GET /auth/github/        │                          │
  ├─────────────────────────►│                          │
  │◄─────────────────────────┤ {redirect_url, state,    │
  │  redirect_url            │  code_challenge}         │
  │                          │                          │
  │  Redirect user browser ─────────────────────────►  │
  │                          │  ?client_id=...          │
  │                          │  &state=...              │
  │                          │  &code_challenge=...     │
  │                          │                          │
  │                          │◄─────────────────────────┤
  │                          │  ?code=...&state=...     │
  │  GET /auth/github/callback/                         │
  │  ?code=...&state=...     │                          │
  ├─────────────────────────►│                          │
  │                          │  Exchange code + verifier│
  │                          ├─────────────────────────►│
  │                          │◄─────────────────────────┤
  │                          │  GitHub access token     │
  │                          │  Get user data           │
  │                          │  Create/update User      │
  │◄─────────────────────────┤                          │
  │  {access_token,          │                          │
  │   refresh_token}         │                          │
```

**PKCE implementation:**
- `code_verifier` = 43-byte random URL-safe string
- `code_challenge` = BASE64URL(SHA-256(code_verifier))
- `code_challenge_method` = S256
- State and verifier are stored in the `oauth_states` table (10-minute TTL, single-use)

---

## Token Lifecycle

```
Issue           Access Token  ──► expires in 3 minutes
                Refresh Token ──► expires in 5 minutes

Refresh         POST /auth/refresh/  { "refresh_token": "..." }
                ├── Validates refresh token (not revoked, not expired)
                ├── Revokes old refresh token (single-use enforcement)
                └── Issues new access token + new refresh token

Logout          POST /auth/logout/   { "refresh_token": "..." }
                └── Marks refresh token as revoked server-side
```

Tokens are opaque random strings stored in the database. No JWTs. Server-side revocation is always effective.

---

## Role Enforcement

| Role | Access |
|---|---|
| `admin` | Full CRUD: list, retrieve, create, delete, export |
| `analyst` | Read-only: list, retrieve, search, export |

Default role on registration: **analyst**.

Role enforcement is implemented via DRF `permission_classes` on each view. Never scattered across business logic:

```python
# Example — POST /api/profiles/ is admin-only
def get_permissions(self):
    if self.request.method == "POST":
        return [IsAdminRole()]
    return [IsActiveUser()]
```

**Inactive users** (is_active=False) are rejected at the authentication layer (401), not the permission layer.

---

## API Versioning

All requests to `/api/*` must include:

```
X-API-Version: 1
```

If missing, the `APIVersionMiddleware` returns:

```json
{ "status": "error", "message": "API version header required" }
```

HTTP 400. `/auth/*` endpoints are exempt.

---

## Rate Limiting

| Endpoint group | Limit | Scope |
|---|---|---|
| `/auth/*` | 10 req/min | per IP |
| `/api/*` | 60 req/min | per authenticated user |

Returns HTTP 429 when exceeded. Backed by Django's cache (LocMemCache in dev, Redis in production via `CACHE_URL`).

---

## API Reference

### Authentication

All `/api/*` endpoints require:
```
Authorization: Bearer <access_token>
X-API-Version: 1
```

---

### `GET /auth/github/`

Initiates OAuth flow. Returns a redirect URL for the client to open in a browser.

```json
{
  "status": "success",
  "redirect_url": "https://github.com/login/oauth/authorize?...",
  "state": "...",
  "code_challenge": "..."
}
```

---

### `GET /auth/github/callback/`

Exchange GitHub authorization code for tokens. Called by GitHub after user approves.

```json
{
  "status": "success",
  "access_token": "...",
  "refresh_token": "...",
  "token_type": "Bearer",
  "expires_in": 180
}
```

---

### `POST /auth/refresh/`

Rotate tokens. Old refresh token is revoked on use.

**Request:**
```json
{ "refresh_token": "..." }
```

**Response:**
```json
{
  "status": "success",
  "access_token": "...",
  "refresh_token": "..."
}
```

---

### `POST /auth/logout/`

Revoke a refresh token server-side.

```json
{ "refresh_token": "..." }
```

---

### `GET /api/profiles/`

Paginated, filtered, sorted list (analyst+).

#### Filters

| Parameter | Type | Example |
|---|---|---|
| `gender` | string | `male`, `female` |
| `age_group` | string | `child`, `teenager`, `adult`, `senior` |
| `country_id` | string | `NG` |
| `min_age` / `max_age` | integer | `25`, `60` |
| `min_gender_probability` | float 0–1 | `0.8` |
| `min_country_probability` | float 0–1 | `0.5` |

#### Sort

| Parameter | Values | Default |
|---|---|---|
| `sort_by` | `age`, `created_at`, `gender_probability` | `-created_at` |
| `order` | `asc`, `desc` | `asc` |

#### Pagination

| Parameter | Default | Max |
|---|---|---|
| `page` | `1` | — |
| `limit` | `10` | `50` |

#### Response

```json
{
  "status": "success",
  "page": 1,
  "limit": 10,
  "total": 2026,
  "total_pages": 203,
  "links": {
    "self": "http://...",
    "next": "http://...",
    "prev": null
  },
  "data": [...]
}
```

---

### `GET /api/profiles/search?q=<query>`

Natural language search (rule-based, no AI). Supports the same pagination params.

#### Supported patterns

| Query | Filters |
|---|---|
| `"young males"` | gender=male, min_age=16, max_age=24 |
| `"females above 30"` | gender=female, min_age=30 |
| `"people from angola"` | country_id=AO |
| `"adult males from kenya"` | gender=male, age_group=adult, country_id=KE |
| `"seniors from nigeria"` | age_group=senior, country_id=NG |
| `"adults aged 30 to 50"` | min_age=30, max_age=50 |

Returns `422` with `"Unable to interpret query"` when no filter can be extracted.

---

### `GET /api/profiles/export/?format=csv`

Exports filtered profiles as CSV. Accepts same filter + sort params as `/api/profiles/`. (analyst+)

```
Content-Type: text/csv
Content-Disposition: attachment; filename="profiles_<timestamp>.csv"
```

---

### `GET /api/profiles/<uuid>/`

Retrieve a single profile (analyst+). Returns `404` if not found.

---

### `DELETE /api/profiles/<uuid>/`

Delete a profile (admin only). Returns `204 No Content`.

---

### `POST /api/profiles/`

Create a profile by name via external API aggregation — Genderize + Agify + Nationalize. (admin only)

**Idempotent** — returns `200` with existing data if the name already exists.

```json
{ "name": "amara" }
```

---

## Error Responses

All errors follow:

```json
{ "status": "error", "message": "<description>" }
```

| Condition | HTTP |
|---|---|
| Missing/invalid param | 400 / 422 |
| Unauthorized | 401 |
| Forbidden (role/inactive) | 403 |
| Not found | 404 |
| External API failure | 502 |
| Rate limit exceeded | 429 |

---

## Database Schema

### profiles

```
id                  UUID v7, primary key
name                VARCHAR(255), unique, indexed
gender              VARCHAR(10)
gender_probability  FLOAT
sample_size         INT, nullable
age                 INT
age_group           VARCHAR(10)
country_id          VARCHAR(10)
country_name        VARCHAR(100)
country_probability FLOAT
created_at          TIMESTAMPTZ auto
```

Composite indexes: `(gender, age_group)`, `(gender, country_id)`, `(country_id, age_group)`, `(age, gender)`.

### users

```
id              UUID v7, primary key
github_id       VARCHAR(50), unique
username        VARCHAR(150)
email           VARCHAR(254)
avatar_url      URLField
role            VARCHAR(10)  [admin | analyst]
is_active       BOOLEAN  default=True
last_login_at   TIMESTAMPTZ nullable
created_at      TIMESTAMPTZ auto
```

---

## Deployment

```
# Procfile (Render / Railway)
release: python manage.py collectstatic --noinput && python manage.py migrate
web:     gunicorn genderize_project.wsgi --bind 0.0.0.0:$PORT --log-level debug --capture-output
```

After first deploy, seed via the service shell:

```bash
python manage.py seed_profiles
```

**Production checklist:**
- Set `DATABASE_URL`
- Set `GITHUB_CLIENT_ID` and `GITHUB_CLIENT_SECRET`
- Set `GITHUB_CALLBACK_URL` to the deployed URL
- Set `CACHE_URL` (Redis) for distributed rate limiting
- Set `SECRET_KEY` to a secure value
- Set `DEBUG=False`
- Set `ALLOWED_HOSTS` to your domain

---

## Project Layout

```
api/
├── countries.py          ISO code ↔ name mapping + NLP lookup table
├── filters.py            Filter/sort/validate query params
├── models.py             Profile model (UUID v7, indexed fields)
├── pagination.py         Paginator (includes total_pages + links)
├── parser.py             Rule-based NLP query parser
├── serializers.py        ProfileSerializer / ProfileListSerializer
├── services.py           External API aggregation (Genderize+Agify+Nationalize)
├── views.py              ProfileView / ProfileDetailView / ProfileSearchView / ProfileExportView
├── urls.py               URL routing
├── tests.py              118 tests
├── fixtures/
│   └── seed_profiles.json
└── management/commands/
    └── seed_profiles.py

users/
├── models.py             User / OAuthState / AccessToken / RefreshToken
├── authentication.py     BearerTokenAuthentication (DRF class)
├── permissions.py        IsActiveUser / IsAdminRole / IsAnalystOrAdmin
├── throttling.py         AuthThrottle / UserThrottle
├── middleware.py         APIVersionMiddleware / RequestLoggingMiddleware
├── services.py           PKCEService / GitHubOAuthService / TokenService
├── views.py              OAuth + token views
├── urls.py
└── tests.py              users app test suite
```
