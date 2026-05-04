import json
import logging
import time

logger = logging.getLogger("api.requests")

# Paths that don't need the X-API-Version header
_VERSION_EXEMPT_PREFIXES = ("/auth/", "/admin/")


class APIVersionMiddleware:
    """Reject any request to /api/* that lacks X-API-Version: 1."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not any(request.path.startswith(p) for p in _VERSION_EXEMPT_PREFIXES):
            version = request.META.get("HTTP_X_API_VERSION")
            if not version:
                from django.http import JsonResponse

                return JsonResponse(
                    {"status": "error", "message": "API version header required"},
                    status=400,
                )
        return self.get_response(request)


class RequestLoggingMiddleware:
    """Log method, path, status, and elapsed ms for every request."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        start = time.monotonic()
        response = self.get_response(request)
        elapsed_ms = (time.monotonic() - start) * 1000
        logger.info(
            "%s %s %s %.1fms",
            request.method,
            request.path,
            response.status_code,
            elapsed_ms,
        )
        return response
