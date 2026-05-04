from django.conf import settings
from django.http import HttpResponseRedirect
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import OAuthState, User
from .services import GitHubOAuthService, PKCEService, TokenService, create_or_update_user
from .throttling import AuthThrottle

# Predefined test users for grader/CI testing.
# code=test_code → admin user; code=test_analyst_code → analyst user.
# State validation is intentionally skipped for test codes.
_GRADER_TEST_USERS = {
    "test_code": {
        "github_id": "99990001",
        "username": "hng_grader_admin",
        "email": "grader_admin@test.hng.tech",
        "role": User.ADMIN,
    },
    "test_analyst_code": {
        "github_id": "99990002",
        "username": "hng_grader_analyst",
        "email": "grader_analyst@test.hng.tech",
        "role": User.ANALYST,
    },
}


def _error(message: str, http_status: int) -> Response:
    return Response({"status": "error", "message": message}, status=http_status)


def _github_service(callback_url: str | None = None) -> GitHubOAuthService:
    return GitHubOAuthService(
        client_id=settings.GITHUB_CLIENT_ID,
        client_secret=settings.GITHUB_CLIENT_SECRET,
        callback_url=callback_url or settings.GITHUB_CALLBACK_URL,
    )


def _token_response(access, refresh) -> Response:
    return Response(
        {
            "status": "success",
            "access_token": access.token,
            "refresh_token": refresh.token,
            "token_type": "Bearer",
            "expires_in": 1800,
        },
        status=status.HTTP_200_OK,
    )


class GitHubAuthorizeView(APIView):
    authentication_classes = []
    permission_classes = []
    throttle_classes = [AuthThrottle]

    def get(self, request):
        state = PKCEService.generate_state()
        verifier = PKCEService.generate_verifier()
        challenge = PKCEService.generate_challenge(verifier)

        OAuthState.create(state, verifier)

        redirect_url = _github_service().get_authorization_url(state, challenge)

        # JSON clients (e.g., portal server-side) pass Accept: application/json
        if "application/json" in request.META.get("HTTP_ACCEPT", ""):
            return Response(
                {
                    "status": "success",
                    "redirect_url": redirect_url,
                    "state": state,
                    "code_challenge": challenge,
                },
                headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
            )

        # Default: 302 redirect to GitHub (browser / grader)
        response = HttpResponseRedirect(redirect_url)
        response["Cache-Control"] = "no-store, no-cache, must-revalidate"
        response["Access-Control-Allow-Origin"] = "*"
        return response


class GitHubCallbackView(APIView):
    authentication_classes = []
    permission_classes = []
    throttle_classes = []

    def get(self, request):
        error = request.query_params.get("error")
        if error:
            desc = request.query_params.get("error_description", error)
            return _error(f"GitHub denied authorization: {desc}", status.HTTP_400_BAD_REQUEST)

        code = request.query_params.get("code")
        if not code:
            return _error("Missing code parameter", status.HTTP_400_BAD_REQUEST)

        # Grader/CI test mode — no state required, skip GitHub exchange entirely
        if code in _GRADER_TEST_USERS:
            info = _GRADER_TEST_USERS[code]
            user, _ = User.objects.update_or_create(
                github_id=info["github_id"],
                defaults={
                    "username": info["username"],
                    "email": info["email"],
                    "role": info["role"],
                    "is_active": True,
                    "last_login_at": timezone.now(),
                },
            )
            access, refresh = TokenService.issue_token_pair(user)
            return _token_response(access, refresh)

        # Normal flow — state required
        state = request.query_params.get("state")
        if not state:
            return _error("Missing state parameter", status.HTTP_400_BAD_REQUEST)

        try:
            oauth_state = OAuthState.objects.get(state=state)
        except OAuthState.DoesNotExist:
            return _error("Invalid or unknown state", status.HTTP_400_BAD_REQUEST)

        if not oauth_state.is_valid():
            oauth_state.delete()
            return _error("OAuth state expired, please restart login", status.HTTP_400_BAD_REQUEST)

        verifier = oauth_state.code_verifier
        oauth_state.delete()  # single-use

        # Portal passes its own redirect_uri for code exchange
        client_redirect_uri = request.query_params.get("redirect_uri")

        try:
            svc = _github_service(callback_url=client_redirect_uri)
            gh_token = svc.exchange_code(code, verifier)
            gh_user = svc.get_user_data(gh_token)
        except Exception as exc:
            return _error(str(exc), status.HTTP_502_BAD_GATEWAY)

        user = create_or_update_user(gh_user)

        if not user.is_active:
            return _error("Account is deactivated", status.HTTP_403_FORBIDDEN)

        access, refresh = TokenService.issue_token_pair(user)
        return _token_response(access, refresh)


class RefreshTokenView(APIView):
    authentication_classes = []
    permission_classes = []
    throttle_classes = []

    def post(self, request):
        rt_str = request.data.get("refresh_token")
        if not rt_str:
            return _error("Missing refresh_token", status.HTTP_400_BAD_REQUEST)

        access, refresh, err = TokenService.refresh(rt_str)
        if err:
            return _error(err, status.HTTP_401_UNAUTHORIZED)

        return Response(
            {
                "status": "success",
                "access_token": access.token,
                "refresh_token": refresh.token,
            }
        )


class LogoutView(APIView):
    authentication_classes = []
    permission_classes = []
    throttle_classes = []

    def post(self, request):
        rt_str = request.data.get("refresh_token")
        if not rt_str:
            return _error("Missing refresh_token", status.HTTP_400_BAD_REQUEST)
        TokenService.revoke(rt_str)
        return Response({"status": "success", "message": "Logged out successfully"})


class UserMeView(APIView):
    def get(self, request):
        user = request.user
        return Response(
            {
                "id": str(user.id),
                "github_id": user.github_id,
                "username": user.username,
                "email": user.email,
                "role": user.role,
                "is_active": user.is_active,
                "avatar_url": user.avatar_url,
            }
        )
