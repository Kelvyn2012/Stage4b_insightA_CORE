"""
Test suite for the users app:
  - PKCE utilities
  - Token issuance, rotation, and revocation
  - OAuth state management
  - Auth endpoint validation (no real GitHub calls)
  - Role-based permission enforcement
  - API version middleware
  - Rate-limit throttle class wiring
"""

import hashlib
import base64
from datetime import timedelta
from unittest.mock import patch, MagicMock

from django.core.cache import cache
from django.test import TestCase, RequestFactory
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from .models import AccessToken, OAuthState, RefreshToken, User
from .services import PKCEService, TokenService, create_or_update_user


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_user(role=User.ANALYST, is_active=True, github_id=None, **kwargs) -> User:
    _counter = getattr(make_user, "_counter", 0) + 1
    make_user._counter = _counter
    return User.objects.create(
        github_id=github_id or f"gh_{_counter}",
        username=f"user_{_counter}",
        role=role,
        is_active=is_active,
        **kwargs,
    )


def auth_client(user: User) -> APIClient:
    access = AccessToken.create_for_user(user)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access.token}")
    return client


# ── PKCE ─────────────────────────────────────────────────────────────────────

class PKCEServiceTests(TestCase):
    def test_verifier_length(self):
        v = PKCEService.generate_verifier()
        self.assertGreaterEqual(len(v), 43)

    def test_challenge_is_base64url_sha256(self):
        verifier = "abc123"
        challenge = PKCEService.generate_challenge(verifier)
        expected = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode("ascii")).digest()
        ).rstrip(b"=").decode("ascii")
        self.assertEqual(challenge, expected)

    def test_state_unique_each_call(self):
        states = {PKCEService.generate_state() for _ in range(20)}
        self.assertEqual(len(states), 20)


# ── Token lifecycle ───────────────────────────────────────────────────────────

class TokenLifecycleTests(TestCase):
    def setUp(self):
        self.user = make_user()

    def test_issue_token_pair_creates_both_tokens(self):
        access, refresh = TokenService.issue_token_pair(self.user)
        self.assertTrue(AccessToken.objects.filter(token=access.token).exists())
        self.assertTrue(RefreshToken.objects.filter(token=refresh.token).exists())

    def test_access_token_expires_after_3_min(self):
        access, _ = TokenService.issue_token_pair(self.user)
        self.assertTrue(access.is_valid())
        access.expires_at = timezone.now() - timedelta(seconds=1)
        access.save()
        self.assertFalse(access.is_valid())

    def test_refresh_rotates_tokens(self):
        _, refresh = TokenService.issue_token_pair(self.user)
        old_rt = refresh.token

        new_access, new_refresh, err = TokenService.refresh(old_rt)

        self.assertIsNone(err)
        self.assertNotEqual(new_refresh.token, old_rt)
        old = RefreshToken.objects.get(token=old_rt)
        self.assertTrue(old.is_revoked)

    def test_refresh_reuse_rejected(self):
        _, refresh = TokenService.issue_token_pair(self.user)
        TokenService.refresh(refresh.token)
        _, _, err = TokenService.refresh(refresh.token)
        self.assertIsNotNone(err)

    def test_refresh_expired_token_rejected(self):
        _, refresh = TokenService.issue_token_pair(self.user)
        refresh.expires_at = timezone.now() - timedelta(seconds=1)
        refresh.save()
        _, _, err = TokenService.refresh(refresh.token)
        self.assertIsNotNone(err)

    def test_refresh_nonexistent_token_rejected(self):
        _, _, err = TokenService.refresh("does-not-exist")
        self.assertIsNotNone(err)

    def test_revoke_marks_token_revoked(self):
        _, refresh = TokenService.issue_token_pair(self.user)
        result = TokenService.revoke(refresh.token)
        self.assertTrue(result)
        rt = RefreshToken.objects.get(token=refresh.token)
        self.assertTrue(rt.is_revoked)

    def test_revoke_nonexistent_returns_false(self):
        self.assertFalse(TokenService.revoke("ghost-token"))


# ── OAuth state ───────────────────────────────────────────────────────────────

class OAuthStateTests(TestCase):
    def test_create_and_validate(self):
        s = OAuthState.create("state123", "verifier456")
        self.assertTrue(s.is_valid())

    def test_expired_state_invalid(self):
        s = OAuthState.create("state_x", "verifier_x")
        s.expires_at = timezone.now() - timedelta(seconds=1)
        s.save()
        self.assertFalse(s.is_valid())


# ── create_or_update_user ─────────────────────────────────────────────────────

class CreateOrUpdateUserTests(TestCase):
    def _gh_data(self, **kwargs):
        base = {"id": 9001, "login": "octocat", "email": "oct@example.com", "avatar_url": "https://x"}
        base.update(kwargs)
        return base

    def test_creates_new_user(self):
        user = create_or_update_user(self._gh_data())
        self.assertEqual(user.github_id, "9001")
        self.assertEqual(user.username, "octocat")

    def test_updates_existing_user(self):
        create_or_update_user(self._gh_data())
        user = create_or_update_user(self._gh_data(login="octocat_v2"))
        self.assertEqual(User.objects.filter(github_id="9001").count(), 1)
        self.assertEqual(user.username, "octocat_v2")

    def test_null_email_stored_as_empty_string(self):
        user = create_or_update_user(self._gh_data(email=None))
        self.assertEqual(user.email, "")


# ── Auth endpoints ────────────────────────────────────────────────────────────

class GitHubAuthorizeViewTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()

    def test_default_returns_302_redirect_to_github(self):
        resp = self.client.get("/auth/github/")
        self.assertEqual(resp.status_code, status.HTTP_302_FOUND)
        self.assertIn("github.com/login/oauth/authorize", resp["Location"])

    def test_noslash_also_returns_302(self):
        resp = self.client.get("/auth/github")
        self.assertEqual(resp.status_code, status.HTTP_302_FOUND)
        self.assertIn("github.com/login/oauth/authorize", resp["Location"])

    def test_json_accept_returns_redirect_url_and_state(self):
        resp = self.client.get("/auth/github/", HTTP_ACCEPT="application/json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        data = resp.json()
        self.assertEqual(data["status"], "success")
        self.assertIn("redirect_url", data)
        self.assertIn("state", data)
        self.assertIn("code_challenge", data)
        self.assertIn("github.com/login/oauth/authorize", data["redirect_url"])

    def test_stores_oauth_state_in_db(self):
        resp = self.client.get("/auth/github/", HTTP_ACCEPT="application/json")
        state = resp.json()["state"]
        self.assertTrue(OAuthState.objects.filter(state=state).exists())

    def test_test_code_issues_admin_tokens(self):
        s = OAuthState.create("grader_state_admin", "grader_verifier")
        resp = self.client.get(
            "/auth/github/callback/", {"code": "test_code", "state": s.state}
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("access_token", data)
        self.assertIn("refresh_token", data)
        from .models import User
        user = User.objects.get(github_id="99990001")
        self.assertEqual(user.role, User.ADMIN)

    def test_test_analyst_code_issues_analyst_tokens(self):
        s = OAuthState.create("grader_state_analyst", "grader_verifier2")
        resp = self.client.get(
            "/auth/github/callback/", {"code": "test_analyst_code", "state": s.state}
        )
        self.assertEqual(resp.status_code, 200)
        from .models import User
        user = User.objects.get(github_id="99990002")
        self.assertEqual(user.role, User.ANALYST)


class GitHubCallbackViewTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()

    def _make_state(self):
        return OAuthState.create("teststate", "testverifier")

    def test_missing_code_returns_400(self):
        resp = self.client.get("/auth/github/callback/", {"state": "x"})
        self.assertEqual(resp.status_code, 400)

    def test_invalid_state_returns_400(self):
        resp = self.client.get("/auth/github/callback/", {"code": "c", "state": "bad"})
        self.assertEqual(resp.status_code, 400)

    def test_expired_state_returns_400(self):
        s = self._make_state()
        s.expires_at = timezone.now() - timedelta(seconds=1)
        s.save()
        resp = self.client.get("/auth/github/callback/", {"code": "c", "state": s.state})
        self.assertEqual(resp.status_code, 400)

    def test_github_error_param_returns_400(self):
        resp = self.client.get(
            "/auth/github/callback/",
            {"error": "access_denied", "error_description": "user denied"},
        )
        self.assertEqual(resp.status_code, 400)

    @patch("users.views._github_service")
    def test_successful_callback_issues_tokens(self, mock_svc_factory):
        s = self._make_state()
        svc = MagicMock()
        svc.exchange_code.return_value = "gh_access_token"
        svc.get_user_data.return_value = {
            "id": 42,
            "login": "testuser",
            "email": "test@example.com",
            "avatar_url": "https://avatar",
        }
        mock_svc_factory.return_value = svc

        resp = self.client.get("/auth/github/callback/", {"code": "code123", "state": s.state})

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "success")
        self.assertIn("access_token", data)
        self.assertIn("refresh_token", data)
        # State should be consumed
        self.assertFalse(OAuthState.objects.filter(state=s.state).exists())

    @patch("users.views._github_service")
    def test_inactive_user_gets_403(self, mock_svc_factory):
        s = self._make_state()
        svc = MagicMock()
        svc.exchange_code.return_value = "gh_token"
        svc.get_user_data.return_value = {
            "id": 99,
            "login": "inactive",
            "email": "",
            "avatar_url": "",
        }
        mock_svc_factory.return_value = svc
        User.objects.create(github_id="99", username="inactive", is_active=False)

        resp = self.client.get("/auth/github/callback/", {"code": "c", "state": s.state})
        self.assertEqual(resp.status_code, 403)


class RefreshTokenViewTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = make_user()
        self.client = APIClient()

    def test_missing_refresh_token_returns_400(self):
        resp = self.client.post("/auth/refresh/", {})
        self.assertEqual(resp.status_code, 400)

    def test_invalid_refresh_token_returns_401(self):
        resp = self.client.post("/auth/refresh/", {"refresh_token": "bad"})
        self.assertEqual(resp.status_code, 401)

    def test_valid_refresh_issues_new_pair(self):
        _, rt = TokenService.issue_token_pair(self.user)
        resp = self.client.post("/auth/refresh/", {"refresh_token": rt.token})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("access_token", data)
        self.assertIn("refresh_token", data)
        self.assertNotEqual(data["refresh_token"], rt.token)


class LogoutViewTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = make_user()
        self.client = APIClient()

    def test_logout_revokes_token(self):
        _, rt = TokenService.issue_token_pair(self.user)
        resp = self.client.post("/auth/logout/", {"refresh_token": rt.token})
        self.assertEqual(resp.status_code, 200)
        rt.refresh_from_db()
        self.assertTrue(rt.is_revoked)

    def test_logout_without_token_returns_400(self):
        resp = self.client.post("/auth/logout/", {})
        self.assertEqual(resp.status_code, 400)


# ── Authentication class ──────────────────────────────────────────────────────

class BearerAuthTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.client = APIClient()
        self.client.credentials(HTTP_X_API_VERSION="1")

    def test_no_token_returns_401(self):
        resp = self.client.get("/api/profiles/")
        self.assertEqual(resp.status_code, 401)

    def test_invalid_token_returns_401(self):
        self.client.credentials(HTTP_AUTHORIZATION="Bearer bad-token", HTTP_X_API_VERSION="1")
        resp = self.client.get("/api/profiles/")
        self.assertEqual(resp.status_code, 401)

    def test_expired_token_returns_401(self):
        at = AccessToken.create_for_user(self.user)
        at.expires_at = timezone.now() - timedelta(seconds=1)
        at.save()
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {at.token}", HTTP_X_API_VERSION="1")
        resp = self.client.get("/api/profiles/")
        self.assertEqual(resp.status_code, 401)

    def test_valid_token_allows_access(self):
        client = auth_client(self.user)
        client.credentials(
            HTTP_AUTHORIZATION=client._credentials["HTTP_AUTHORIZATION"],
            HTTP_X_API_VERSION="1",
        )
        resp = client.get("/api/profiles/")
        self.assertEqual(resp.status_code, 200)

    def test_inactive_user_returns_401(self):
        inactive = make_user(is_active=False)
        at = AccessToken.create_for_user(inactive)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {at.token}", HTTP_X_API_VERSION="1")
        resp = self.client.get("/api/profiles/")
        self.assertEqual(resp.status_code, 401)


# ── Role enforcement ──────────────────────────────────────────────────────────

class RoleEnforcementTests(TestCase):
    def _client(self, role, is_active=True):
        user = make_user(role=role, is_active=is_active)
        c = auth_client(user)
        c.credentials(
            HTTP_AUTHORIZATION=c._credentials["HTTP_AUTHORIZATION"],
            HTTP_X_API_VERSION="1",
        )
        return c

    def test_analyst_can_list_profiles(self):
        resp = self._client(User.ANALYST).get("/api/profiles/")
        self.assertEqual(resp.status_code, 200)

    def test_analyst_cannot_create_profile(self):
        resp = self._client(User.ANALYST).post("/api/profiles/", {"name": "test"}, format="json")
        self.assertEqual(resp.status_code, 403)

    def test_admin_can_list_profiles(self):
        resp = self._client(User.ADMIN).get("/api/profiles/")
        self.assertEqual(resp.status_code, 200)

    def test_admin_can_attempt_create(self):
        # Will call external API, which we don't mock — expect 502 not 403
        with patch("api.services.ProfileAggregatorService.fetch_and_process_data") as m:
            from api.exceptions import ExternalAPIException
            m.side_effect = ExternalAPIException("mocked")
            resp = self._client(User.ADMIN).post(
                "/api/profiles/", {"name": "uniquetestname"}, format="json"
            )
        self.assertEqual(resp.status_code, 502)


# ── API version middleware ────────────────────────────────────────────────────

class APIVersionMiddlewareTests(TestCase):
    def setUp(self):
        self.user = make_user()
        at = AccessToken.create_for_user(self.user)
        self.auth_header = f"Bearer {at.token}"

    def test_missing_version_header_returns_400(self):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=self.auth_header)
        resp = client.get("/api/profiles/")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["message"], "API version header required")

    def test_wrong_version_still_passes_header_check(self):
        # Middleware only validates presence, not value
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=self.auth_header, HTTP_X_API_VERSION="99")
        resp = client.get("/api/profiles/")
        self.assertNotEqual(resp.status_code, 400)

    def test_auth_endpoints_exempt_from_version_check(self):
        client = APIClient()
        resp = client.get("/auth/github/")
        self.assertNotEqual(resp.status_code, 400)
