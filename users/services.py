import base64
import hashlib
import secrets

import httpx
from django.utils import timezone

from .models import AccessToken, OAuthState, RefreshToken, User


class PKCEService:
    @staticmethod
    def generate_verifier() -> str:
        return secrets.token_urlsafe(43)  # 43 chars → 256-bit entropy

    @staticmethod
    def generate_challenge(verifier: str) -> str:
        digest = hashlib.sha256(verifier.encode("ascii")).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")

    @staticmethod
    def generate_state() -> str:
        return secrets.token_urlsafe(32)


class GitHubOAuthService:
    _AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
    _TOKEN_URL = "https://github.com/login/oauth/access_token"
    _USER_URL = "https://api.github.com/user"

    def __init__(self, client_id: str, client_secret: str, callback_url: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self.callback_url = callback_url

    def get_authorization_url(self, state: str, code_challenge: str) -> str:
        params = {
            "client_id": self.client_id,
            "redirect_uri": self.callback_url,
            "state": state,
            "scope": "read:user user:email",
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        return f"{self._AUTHORIZE_URL}?{qs}"

    def exchange_code(self, code: str, code_verifier: str) -> str:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(
                self._TOKEN_URL,
                headers={"Accept": "application/json"},
                data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "code": code,
                    "redirect_uri": self.callback_url,
                    "code_verifier": code_verifier,
                },
            )
            resp.raise_for_status()
            data = resp.json()
        if "error" in data:
            desc = data.get("error_description", data["error"])
            raise ValueError(f"GitHub token exchange failed: {desc}")
        return data["access_token"]

    def get_user_data(self, gh_access_token: str) -> dict:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(
                self._USER_URL,
                headers={
                    "Authorization": f"Bearer {gh_access_token}",
                    "Accept": "application/vnd.github+json",
                },
            )
            resp.raise_for_status()
        return resp.json()


class TokenService:
    @staticmethod
    def issue_token_pair(user: User) -> tuple[AccessToken, RefreshToken]:
        access = AccessToken.create_for_user(user)
        refresh = RefreshToken.create_for_user(user)
        return access, refresh

    @staticmethod
    def refresh(refresh_token_str: str) -> tuple[AccessToken | None, RefreshToken | None, str | None]:
        try:
            rt = RefreshToken.objects.select_related("user").get(token=refresh_token_str)
        except RefreshToken.DoesNotExist:
            return None, None, "Invalid refresh token"

        if not rt.is_valid():
            return None, None, "Refresh token expired or already used"

        user = rt.user
        if not user.is_active:
            return None, None, "User account is inactive"

        rt.is_revoked = True
        rt.save(update_fields=["is_revoked"])

        access, refresh = TokenService.issue_token_pair(user)
        return access, refresh, None

    @staticmethod
    def revoke(refresh_token_str: str) -> bool:
        updated = RefreshToken.objects.filter(
            token=refresh_token_str, is_revoked=False
        ).update(is_revoked=True)
        return updated > 0


def create_or_update_user(github_user_data: dict) -> User:
    github_id = str(github_user_data["id"])
    user, _ = User.objects.update_or_create(
        github_id=github_id,
        defaults={
            "username": github_user_data.get("login", ""),
            "email": github_user_data.get("email") or "",
            "avatar_url": github_user_data.get("avatar_url") or "",
            "last_login_at": timezone.now(),
        },
    )
    return user
