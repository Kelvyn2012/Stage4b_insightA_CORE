from django.core.paginator import InvalidPage
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response


class ProfilePagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = "limit"
    max_page_size = 50
    page_query_param = "page"

    def paginate_queryset(self, queryset, request, view=None):
        page_size = self.get_page_size(request)
        if not page_size:
            return None

        paginator = self.django_paginator_class(queryset, page_size)
        page_number = self.get_page_number(request, paginator)

        try:
            self.page = paginator.page(page_number)
        except InvalidPage:
            last = max(paginator.num_pages, 1)
            self.page = paginator.page(last)

        self.request = request
        return list(self.page)

    def get_paginated_response(self, data):
        total = self.page.paginator.count
        limit = self.get_page_size(self.request)
        total_pages = self.page.paginator.num_pages

        return Response(
            {
                "status": "success",
                "page": self.page.number,
                "limit": limit,
                "total": total,
                "total_pages": total_pages,
                "links": {
                    "self": self._self_link(),
                    "next": self.get_next_link(),
                    "prev": self.get_previous_link(),
                },
                "data": data,
            }
        )

    def _self_link(self) -> str:
        request = self.request
        url = request.build_absolute_uri(request.path)
        qs = request.META.get("QUERY_STRING", "")
        return f"{url}?{qs}" if qs else url

    def get_paginated_response_schema(self, schema):
        return {
            "type": "object",
            "properties": {
                "status": {"type": "string"},
                "page": {"type": "integer"},
                "limit": {"type": "integer"},
                "total": {"type": "integer"},
                "total_pages": {"type": "integer"},
                "links": {
                    "type": "object",
                    "properties": {
                        "self": {"type": "string"},
                        "next": {"type": ["string", "null"]},
                        "prev": {"type": ["string", "null"]},
                    },
                },
                "data": schema,
            },
        }
