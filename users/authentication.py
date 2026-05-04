from django.utils import timezone
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

from .models import AccessToken


class BearerTokenAuthentication(BaseAuthentication):
    def authenticate(self, request):
        auth_header = request.META.get("HTTP_AUTHORIZATION", "")
        if not auth_header.startswith("Bearer "):
            return None

        token_str = auth_header[7:].strip()
        if not token_str:
            return None

        try:
            token = AccessToken.objects.select_related("user").get(
                token=token_str,
                expires_at__gt=timezone.now(),
            )
        except AccessToken.DoesNotExist:
            raise AuthenticationFailed("Invalid or expired access token")

        user = token.user
        if not user.is_active:
            raise AuthenticationFailed("User account is inactive")

        return (user, token)

    def authenticate_header(self, request):
        return "Bearer"
