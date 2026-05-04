import secrets
from datetime import timedelta

import uuid6
from django.db import models
from django.utils import timezone

ACCESS_TOKEN_TTL = timedelta(minutes=30)
REFRESH_TOKEN_TTL = timedelta(hours=1)
OAUTH_STATE_TTL = timedelta(minutes=10)


def _token():
    return secrets.token_urlsafe(40)


class User(models.Model):
    ADMIN = "admin"
    ANALYST = "analyst"
    ROLE_CHOICES = [(ADMIN, "Admin"), (ANALYST, "Analyst")]

    id = models.UUIDField(primary_key=True, default=uuid6.uuid7, editable=False)
    github_id = models.CharField(max_length=50, unique=True, db_index=True)
    username = models.CharField(max_length=150)
    email = models.CharField(max_length=254, blank=True)
    avatar_url = models.URLField(blank=True)
    role = models.CharField(max_length=10, choices=ROLE_CHOICES, default=ANALYST)
    is_active = models.BooleanField(default=True)
    last_login_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "users"

    # Required by DRF's IsAuthenticated permission
    @property
    def is_authenticated(self):
        return True

    @property
    def is_anonymous(self):
        return False

    def __str__(self):
        return self.username


class OAuthState(models.Model):
    state = models.CharField(max_length=128, unique=True)
    code_verifier = models.CharField(max_length=256)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()

    class Meta:
        db_table = "oauth_states"

    @classmethod
    def create(cls, state: str, code_verifier: str) -> "OAuthState":
        return cls.objects.create(
            state=state,
            code_verifier=code_verifier,
            expires_at=timezone.now() + OAUTH_STATE_TTL,
        )

    def is_valid(self) -> bool:
        return self.expires_at > timezone.now()


class AccessToken(models.Model):
    token = models.CharField(max_length=128, unique=True, default=_token)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="access_tokens")
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()

    class Meta:
        db_table = "access_tokens"

    @classmethod
    def create_for_user(cls, user: User) -> "AccessToken":
        return cls.objects.create(
            user=user,
            expires_at=timezone.now() + ACCESS_TOKEN_TTL,
        )

    def is_valid(self) -> bool:
        return self.expires_at > timezone.now()


class RefreshToken(models.Model):
    token = models.CharField(max_length=128, unique=True, default=_token)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="refresh_tokens")
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    is_revoked = models.BooleanField(default=False)

    class Meta:
        db_table = "refresh_tokens"

    @classmethod
    def create_for_user(cls, user: User) -> "RefreshToken":
        return cls.objects.create(
            user=user,
            expires_at=timezone.now() + REFRESH_TOKEN_TTL,
        )

    def is_valid(self) -> bool:
        return not self.is_revoked and self.expires_at > timezone.now()
