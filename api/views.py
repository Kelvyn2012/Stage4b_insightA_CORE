import csv
from io import StringIO

from django.db import IntegrityError
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from users.permissions import IsActiveUser, IsAdminRole

from .exceptions import ExternalAPIException, InvalidProfileDataException
from .filters import build_profile_queryset
from .models import Profile
from .pagination import ProfilePagination
from .parser import parse_query
from .serializers import ProfileListSerializer, ProfileSerializer
from .services import ProfileAggregatorService


# ── Helpers ───────────────────────────────────────────────────────────────────

def _error(message: str, http_status: int) -> Response:
    return Response({"status": "error", "message": message}, status=http_status)


def _paginate(request, queryset):
    paginator = ProfilePagination()
    page = paginator.paginate_queryset(queryset, request)
    serializer = ProfileListSerializer(page, many=True)
    return paginator.get_paginated_response(serializer.data)


# ── Views ─────────────────────────────────────────────────────────────────────

class ProfileView(APIView):
    """
    GET  /api/profiles  — filtered, sorted, paginated list (analyst+)
    POST /api/profiles  — create profile via external API aggregation (admin only)
    """

    def get_permissions(self):
        if self.request.method == "POST":
            return [IsAdminRole()]
        return [IsActiveUser()]

    def get(self, request):
        queryset = Profile.objects.all()
        queryset, err = build_profile_queryset(queryset, request.query_params)
        if err:
            return _error(err["message"], err["_status_code"])
        return _paginate(request, queryset)

    def post(self, request):
        if "name" not in request.data:
            return _error("Missing 'name' field", status.HTTP_400_BAD_REQUEST)

        name = request.data.get("name")

        if name == "" or (isinstance(name, str) and not name.strip()):
            return _error("Name cannot be empty", status.HTTP_400_BAD_REQUEST)

        if not isinstance(name, str):
            return _error("Name must be a string", status.HTTP_422_UNPROCESSABLE_ENTITY)

        normalized = name.strip().lower()

        try:
            profile = Profile.objects.get(name=normalized)
            return Response(
                {
                    "status": "success",
                    "message": "Profile already exists",
                    "data": ProfileSerializer(profile).data,
                },
                status=status.HTTP_200_OK,
            )
        except Profile.DoesNotExist:
            pass

        try:
            data = ProfileAggregatorService.fetch_and_process_data(normalized)
        except ExternalAPIException as exc:
            return _error(str(exc), status.HTTP_502_BAD_GATEWAY)
        except InvalidProfileDataException as exc:
            return _error(str(exc), status.HTTP_502_BAD_GATEWAY)
        except Exception:
            return _error(
                "Unexpected error while fetching external data",
                status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        try:
            profile = Profile.objects.create(**data)
            return Response(
                {"status": "success", "data": ProfileSerializer(profile).data},
                status=status.HTTP_201_CREATED,
            )
        except IntegrityError:
            profile = Profile.objects.get(name=normalized)
            return Response(
                {
                    "status": "success",
                    "message": "Profile already exists",
                    "data": ProfileSerializer(profile).data,
                },
                status=status.HTTP_200_OK,
            )


class ProfileDetailView(APIView):
    """
    GET    /api/profiles/<uuid:id>   — any authenticated user
    DELETE /api/profiles/<uuid:id>   — admin only
    """

    def get_permissions(self):
        if self.request.method == "DELETE":
            return [IsAdminRole()]
        return [IsActiveUser()]

    def _get_profile(self, pk):
        try:
            return Profile.objects.get(id=pk), None
        except Profile.DoesNotExist:
            return None, _error("Profile not found", status.HTTP_404_NOT_FOUND)

    def get(self, request, id):
        profile, err = self._get_profile(id)
        if err:
            return err
        return Response(
            {"status": "success", "data": ProfileSerializer(profile).data},
            status=status.HTTP_200_OK,
        )

    def delete(self, request, id):
        profile, err = self._get_profile(id)
        if err:
            return err
        profile.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class ProfileSearchView(APIView):
    """GET /api/profiles/search?q=<natural-language-query>"""

    permission_classes = [IsActiveUser]

    def get(self, request):
        q = request.query_params.get("q", "").strip()

        if not q:
            return _error("Missing or empty 'q' parameter", status.HTTP_400_BAD_REQUEST)

        filters = parse_query(q)
        if filters is None:
            return Response(
                {"status": "error", "message": "Unable to interpret query"},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        queryset = Profile.objects.all()
        queryset, err = build_profile_queryset(queryset, filters)
        if err:
            return _error(err["message"], err["_status_code"])

        return _paginate(request, queryset)


class ProfileExportView(APIView):
    """
    GET /api/profiles/export?format=csv

    Applies the same filters/sorting as /api/profiles and streams a CSV.
    """

    permission_classes = [IsActiveUser]

    def get_format_suffix(self, **kwargs):
        # Prevent DRF from treating ?format= as a renderer-negotiation parameter.
        # We read it ourselves and always return CSV.
        return None

    _FIELDS = [
        "id",
        "name",
        "gender",
        "gender_probability",
        "age",
        "age_group",
        "country_id",
        "country_name",
        "country_probability",
        "created_at",
    ]

    def get(self, request):
        fmt = request.query_params.get("format", "csv")
        if fmt != "csv":
            return _error("Only format=csv is supported", status.HTTP_400_BAD_REQUEST)

        queryset = Profile.objects.all()
        queryset, err = build_profile_queryset(queryset, request.query_params)
        if err:
            return _error(err["message"], err["_status_code"])

        buf = StringIO()
        writer = csv.writer(buf)
        writer.writerow(self._FIELDS)
        for profile in queryset:
            writer.writerow(
                [
                    str(profile.id),
                    profile.name,
                    profile.gender,
                    profile.gender_probability,
                    profile.age,
                    profile.age_group,
                    profile.country_id,
                    profile.country_name,
                    profile.country_probability,
                    profile.created_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
                ]
            )

        timestamp = timezone.now().strftime("%Y%m%d_%H%M%S")
        response = HttpResponse(buf.getvalue(), content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="profiles_{timestamp}.csv"'
        return response
