"""
Test suite for the Intelligence Query Engine.

Covers:
  - Profile listing: filtering, combined filters, sorting, pagination
  - Natural language search (parser + endpoint)
  - Query validation (400 / 422 status codes)
  - Profile detail: retrieve and delete
  - Profile create (POST) — idempotency and validation
  - Pagination shape: total_pages, links
  - CSV export
"""
import uuid

from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient
from unittest.mock import patch

from api.models import Profile
from api.parser import parse_query
from users.models import AccessToken, User


# ── Fixtures ──────────────────────────────────────────────────────────────────

_uid_counter = 0


def make_user(role=User.ANALYST, is_active=True) -> User:
    global _uid_counter
    _uid_counter += 1
    return User.objects.create(
        github_id=f"gh_api_{_uid_counter}",
        username=f"api_user_{_uid_counter}",
        role=role,
        is_active=is_active,
    )


def make_profile(**kwargs) -> Profile:
    defaults = dict(
        name="test-profile",
        gender="male",
        gender_probability=0.95,
        age=30,
        age_group="adult",
        country_id="NG",
        country_name="Nigeria",
        country_probability=0.40,
    )
    defaults.update(kwargs)
    return Profile.objects.create(**defaults)


def authed_client(role=User.ANALYST) -> APIClient:
    """Return an APIClient with a valid Bearer token and API version header."""
    user = make_user(role=role)
    token = AccessToken.create_for_user(user)
    client = APIClient()
    client.credentials(
        HTTP_AUTHORIZATION=f"Bearer {token.token}",
        HTTP_X_API_VERSION="1",
    )
    return client


def admin_client() -> APIClient:
    return authed_client(role=User.ADMIN)


# ── Parser unit tests (no HTTP, no auth needed) ───────────────────────────────

class NLParserTests(TestCase):
    def _p(self, q):
        return parse_query(q)

    def test_young_males(self):
        self.assertEqual(
            self._p("young males"),
            {"gender": "male", "min_age": 16, "max_age": 24},
        )

    def test_females_above_30(self):
        self.assertEqual(
            self._p("females above 30"),
            {"gender": "female", "min_age": 30},
        )

    def test_people_from_angola(self):
        self.assertEqual(self._p("people from angola"), {"country_id": "AO"})

    def test_adult_males_from_kenya(self):
        self.assertEqual(
            self._p("adult males from kenya"),
            {"gender": "male", "age_group": "adult", "country_id": "KE"},
        )

    def test_male_and_female_teenagers_above_17(self):
        result = self._p("male and female teenagers above 17")
        self.assertNotIn("gender", result)
        self.assertEqual(result.get("age_group"), "teenager")
        self.assertEqual(result.get("min_age"), 17)

    def test_seniors_from_nigeria(self):
        result = self._p("seniors from nigeria")
        self.assertEqual(result["age_group"], "senior")
        self.assertEqual(result["country_id"], "NG")

    def test_women_under_25(self):
        result = self._p("women under 25")
        self.assertEqual(result["gender"], "female")
        self.assertEqual(result["max_age"], 25)

    def test_children(self):
        result = self._p("children from ghana")
        self.assertEqual(result["age_group"], "child")
        self.assertEqual(result["country_id"], "GH")

    def test_between_ages(self):
        result = self._p("adults aged 30 to 50")
        self.assertEqual(result["min_age"], 30)
        self.assertEqual(result["max_age"], 50)

    def test_unrecognised_returns_none(self):
        self.assertIsNone(self._p("xyzabc gibberish 9999"))

    def test_empty_string_returns_none(self):
        self.assertIsNone(self._p(""))

    def test_country_alias_usa(self):
        result = self._p("men from usa")
        self.assertEqual(result["country_id"], "US")

    def test_country_alias_south_africa(self):
        result = self._p("females from south africa")
        self.assertEqual(result["country_id"], "ZA")

    def test_uk_alias(self):
        result = self._p("adults from uk")
        self.assertEqual(result["country_id"], "GB")

    def test_older_than_phrase(self):
        result = self._p("men older than 40")
        self.assertEqual(result["min_age"], 40)
        self.assertEqual(result["gender"], "male")

    def test_teenagers_no_gender(self):
        result = self._p("teenagers")
        self.assertEqual(result["age_group"], "teenager")
        self.assertNotIn("gender", result)


# ── Profile list / filtering / sorting / pagination ───────────────────────────

class ProfileListTests(TestCase):
    def setUp(self):
        self.client = authed_client()
        self.url = reverse("profiles")

        make_profile(name="alice", gender="female", age=25, age_group="adult",
                     country_id="GH", gender_probability=0.97, country_probability=0.55)
        make_profile(name="bob", gender="male", age=17, age_group="teenager",
                     country_id="NG", gender_probability=0.89, country_probability=0.30)
        make_profile(name="carol", gender="female", age=65, age_group="senior",
                     country_id="KE", gender_probability=0.92, country_probability=0.45)
        make_profile(name="david", gender="male", age=8, age_group="child",
                     country_id="NG", gender_probability=0.80, country_probability=0.20)
        make_profile(name="eve", gender="female", age=35, age_group="adult",
                     country_id="NG", gender_probability=0.99, country_probability=0.60)

    def _get(self, **params):
        return self.client.get(self.url, params)

    # ── Response shape ────────────────────────────────────────────────────────

    def test_response_shape(self):
        r = self._get()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["status"], "success")
        for key in ("page", "limit", "total", "total_pages", "links", "data"):
            self.assertIn(key, r.data)

    def test_links_shape(self):
        r = self._get(page=1, limit=2)
        links = r.data["links"]
        self.assertIn("self", links)
        self.assertIn("next", links)
        self.assertIn("prev", links)
        self.assertIsNone(links["prev"])
        self.assertIsNotNone(links["next"])

    def test_total_pages(self):
        r = self._get(limit=2)
        self.assertEqual(r.data["total_pages"], 3)  # 5 profiles, limit 2

    def test_total_count(self):
        r = self._get()
        self.assertEqual(r.data["total"], 5)

    # ── Filtering ─────────────────────────────────────────────────────────────

    def test_filter_by_gender_male(self):
        r = self._get(gender="male")
        self.assertEqual(r.data["total"], 2)
        names = {p["name"] for p in r.data["data"]}
        self.assertIn("bob", names)
        self.assertIn("david", names)

    def test_filter_by_gender_female(self):
        r = self._get(gender="female")
        self.assertEqual(r.data["total"], 3)

    def test_filter_gender_case_insensitive(self):
        r = self._get(gender="MALE")
        self.assertEqual(r.data["total"], 2)

    def test_filter_by_age_group(self):
        r = self._get(age_group="senior")
        self.assertEqual(r.data["total"], 1)
        self.assertEqual(r.data["data"][0]["name"], "carol")

    def test_filter_by_country_id(self):
        r = self._get(country_id="NG")
        self.assertEqual(r.data["total"], 3)

    def test_filter_by_country_id_case_insensitive(self):
        r = self._get(country_id="ng")
        self.assertEqual(r.data["total"], 3)

    def test_filter_min_age(self):
        r = self._get(min_age=30)
        self.assertEqual(r.data["total"], 2)

    def test_filter_max_age(self):
        r = self._get(max_age=20)
        self.assertEqual(r.data["total"], 2)

    def test_filter_min_max_age(self):
        r = self._get(min_age=16, max_age=40)
        self.assertEqual(r.data["total"], 3)

    def test_filter_min_gender_probability(self):
        r = self._get(min_gender_probability=0.95)
        self.assertEqual(r.data["total"], 2)

    def test_filter_min_country_probability(self):
        r = self._get(min_country_probability=0.50)
        self.assertEqual(r.data["total"], 2)

    # ── Combined filters ──────────────────────────────────────────────────────

    def test_combined_gender_country(self):
        r = self._get(gender="female", country_id="NG")
        self.assertEqual(r.data["total"], 1)
        self.assertEqual(r.data["data"][0]["name"], "eve")

    def test_combined_gender_age_group(self):
        r = self._get(gender="male", age_group="teenager")
        self.assertEqual(r.data["total"], 1)
        self.assertEqual(r.data["data"][0]["name"], "bob")

    def test_combined_gender_min_age_country(self):
        r = self._get(gender="male", min_age=5, country_id="NG")
        self.assertEqual(r.data["total"], 2)

    def test_combined_all_filters(self):
        r = self._get(
            gender="female", age_group="adult", country_id="NG",
            min_age=30, max_age=40, min_gender_probability=0.95,
        )
        self.assertEqual(r.data["total"], 1)
        self.assertEqual(r.data["data"][0]["name"], "eve")

    # ── Sorting ───────────────────────────────────────────────────────────────

    def test_sort_by_age_asc(self):
        r = self._get(sort_by="age", order="asc")
        ages = [p["age"] for p in r.data["data"]]
        self.assertEqual(ages, sorted(ages))

    def test_sort_by_age_desc(self):
        r = self._get(sort_by="age", order="desc")
        ages = [p["age"] for p in r.data["data"]]
        self.assertEqual(ages, sorted(ages, reverse=True))

    def test_sort_by_gender_probability_desc(self):
        r = self._get(sort_by="gender_probability", order="desc")
        probs = [p["gender_probability"] for p in r.data["data"]]
        self.assertEqual(probs, sorted(probs, reverse=True))

    # ── Pagination ────────────────────────────────────────────────────────────

    def test_pagination_default(self):
        r = self._get()
        self.assertEqual(r.data["page"], 1)
        self.assertEqual(r.data["limit"], 10)

    def test_pagination_limit(self):
        r = self._get(page=1, limit=2)
        self.assertEqual(r.data["limit"], 2)
        self.assertEqual(len(r.data["data"]), 2)
        self.assertEqual(r.data["total"], 5)

    def test_pagination_page_2(self):
        r = self._get(page=2, limit=2)
        self.assertEqual(r.data["page"], 2)
        self.assertEqual(len(r.data["data"]), 2)

    def test_pagination_last_page(self):
        r = self._get(page=3, limit=2)
        self.assertEqual(len(r.data["data"]), 1)

    def test_pagination_max_limit_capped(self):
        r = self._get(limit=100)
        self.assertEqual(r.data["limit"], 50)


# ── Validation tests ──────────────────────────────────────────────────────────

class ProfileValidationTests(TestCase):
    def setUp(self):
        self.client = authed_client()
        self.url = reverse("profiles")

    def test_invalid_gender_422(self):
        r = self.client.get(self.url, {"gender": "robot"})
        self.assertEqual(r.status_code, 422)
        self.assertEqual(r.data["status"], "error")

    def test_invalid_age_group_422(self):
        r = self.client.get(self.url, {"age_group": "boomer"})
        self.assertEqual(r.status_code, 422)

    def test_invalid_min_age_422(self):
        r = self.client.get(self.url, {"min_age": "notanumber"})
        self.assertEqual(r.status_code, 422)

    def test_invalid_max_age_422(self):
        r = self.client.get(self.url, {"max_age": "abc"})
        self.assertEqual(r.status_code, 422)

    def test_invalid_gender_probability_422(self):
        r = self.client.get(self.url, {"min_gender_probability": "abc"})
        self.assertEqual(r.status_code, 422)

    def test_invalid_sort_by_422(self):
        r = self.client.get(self.url, {"sort_by": "name"})
        self.assertEqual(r.status_code, 422)

    def test_invalid_order_422(self):
        r = self.client.get(self.url, {"sort_by": "age", "order": "random"})
        self.assertEqual(r.status_code, 422)

    def test_error_response_shape(self):
        r = self.client.get(self.url, {"gender": "xyz"})
        self.assertIn("status", r.data)
        self.assertIn("message", r.data)
        self.assertEqual(r.data["status"], "error")


# ── Natural language search endpoint ─────────────────────────────────────────

class ProfileSearchTests(TestCase):
    def setUp(self):
        self.client = authed_client()
        self.url = reverse("profiles-search")

        make_profile(name="amara", gender="female", age=20, age_group="adult",
                     country_id="AO", country_name="Angola")
        make_profile(name="kwame", gender="male", age=17, age_group="teenager",
                     country_id="GH", country_name="Ghana")
        make_profile(name="nneka", gender="female", age=70, age_group="senior",
                     country_id="NG", country_name="Nigeria")
        make_profile(name="tunde", gender="male", age=45, age_group="adult",
                     country_id="KE", country_name="Kenya")
        make_profile(name="mercy", gender="female", age=22, age_group="adult",
                     country_id="KE", country_name="Kenya")

    def _search(self, q, **extra):
        return self.client.get(self.url, {"q": q, **extra})

    def test_missing_q_400(self):
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 400)

    def test_empty_q_400(self):
        r = self.client.get(self.url, {"q": ""})
        self.assertEqual(r.status_code, 400)

    def test_unrecognised_query(self):
        r = self._search("xyzfoobar123")
        self.assertEqual(r.data["status"], "error")
        self.assertEqual(r.data["message"], "Unable to interpret query")

    def test_young_males(self):
        r = self._search("young males")
        self.assertEqual(r.data["status"], "success")
        for p in r.data["data"]:
            self.assertEqual(p["gender"], "male")
            self.assertGreaterEqual(p["age"], 16)
            self.assertLessEqual(p["age"], 24)

    def test_people_from_angola(self):
        r = self._search("people from angola")
        self.assertEqual(r.data["status"], "success")
        for p in r.data["data"]:
            self.assertEqual(p["country_id"], "AO")

    def test_adult_males_from_kenya(self):
        r = self._search("adult males from kenya")
        self.assertEqual(r.data["status"], "success")
        for p in r.data["data"]:
            self.assertEqual(p["gender"], "male")
            self.assertEqual(p["age_group"], "adult")
            self.assertEqual(p["country_id"], "KE")

    def test_seniors(self):
        r = self._search("seniors")
        self.assertEqual(r.data["total"], 1)
        self.assertEqual(r.data["data"][0]["name"], "nneka")

    def test_search_pagination(self):
        r = self._search("females", page=1, limit=2)
        self.assertIn("page", r.data)
        self.assertIn("total", r.data)
        self.assertIn("total_pages", r.data)
        self.assertEqual(r.data["limit"], 2)


# ── Profile detail tests ──────────────────────────────────────────────────────

class ProfileDetailTests(TestCase):
    def setUp(self):
        self.analyst_client = authed_client(User.ANALYST)
        self.admin = admin_client()
        self.profile = make_profile(name="test-detail")
        self.url = reverse("profile-detail", kwargs={"id": self.profile.id})

    def test_get_success(self):
        r = self.analyst_client.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["status"], "success")
        self.assertEqual(r.data["data"]["name"], "test-detail")

    def test_get_not_found_404(self):
        url = reverse("profile-detail", kwargs={"id": uuid.uuid4()})
        r = self.analyst_client.get(url)
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.data["status"], "error")

    def test_analyst_cannot_delete(self):
        r = self.analyst_client.delete(self.url)
        self.assertEqual(r.status_code, 403)

    def test_admin_delete_success(self):
        r = self.admin.delete(self.url)
        self.assertEqual(r.status_code, 204)
        self.assertEqual(Profile.objects.count(), 0)

    def test_admin_delete_not_found_404(self):
        url = reverse("profile-detail", kwargs={"id": uuid.uuid4()})
        r = self.admin.delete(url)
        self.assertEqual(r.status_code, 404)


# ── Profile create (POST) ─────────────────────────────────────────────────────

class ProfileCreateTests(TestCase):
    def setUp(self):
        self.admin = admin_client()
        self.analyst = authed_client(User.ANALYST)
        self.url = reverse("profiles")

    def test_analyst_cannot_create(self):
        r = self.analyst.post(self.url, {"name": "test"}, format="json")
        self.assertEqual(r.status_code, 403)

    @patch("api.services.ProfileAggregatorService.fetch_and_process_data")
    def test_admin_create_success(self, mock_fetch):
        mock_fetch.return_value = {
            "name": "jane",
            "gender": "female",
            "gender_probability": 0.99,
            "sample_size": 5000,
            "age": 28,
            "age_group": "adult",
            "country_id": "GH",
            "country_name": "Ghana",
            "country_probability": 0.42,
        }
        r = self.admin.post(self.url, {"name": "jane"}, format="json")
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.data["status"], "success")
        self.assertEqual(r.data["data"]["country_name"], "Ghana")

    @patch("api.services.ProfileAggregatorService.fetch_and_process_data")
    def test_idempotency(self, mock_fetch):
        make_profile(name="jane")
        r = self.admin.post(self.url, {"name": "JANE"}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["message"], "Profile already exists")
        mock_fetch.assert_not_called()

    def test_missing_name_400(self):
        r = self.admin.post(self.url, {}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_empty_name_400(self):
        r = self.admin.post(self.url, {"name": ""}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_whitespace_name_400(self):
        r = self.admin.post(self.url, {"name": "   "}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_non_string_name_422(self):
        r = self.admin.post(self.url, {"name": 42}, format="json")
        self.assertEqual(r.status_code, 422)


# ── CSV export ────────────────────────────────────────────────────────────────

class ProfileExportTests(TestCase):
    def setUp(self):
        self.client = authed_client()
        self.url = reverse("profiles-export")
        make_profile(name="exp-alice", gender="female", age=25, age_group="adult",
                     country_id="GH", gender_probability=0.97, country_probability=0.55)
        make_profile(name="exp-bob", gender="male", age=17, age_group="teenager",
                     country_id="NG", gender_probability=0.89, country_probability=0.30)

    def test_returns_csv_content_type(self):
        r = self.client.get(self.url, {"format": "csv"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/csv", r["Content-Type"])

    def test_content_disposition_header(self):
        r = self.client.get(self.url, {"format": "csv"})
        self.assertIn("attachment", r["Content-Disposition"])
        self.assertIn("profiles_", r["Content-Disposition"])

    def test_csv_contains_header_row(self):
        r = self.client.get(self.url, {"format": "csv"})
        content = r.content.decode()
        self.assertIn("name", content)
        self.assertIn("gender", content)
        self.assertIn("country_id", content)

    def test_csv_contains_profile_data(self):
        r = self.client.get(self.url, {"format": "csv"})
        content = r.content.decode()
        self.assertIn("exp-alice", content)
        self.assertIn("exp-bob", content)

    def test_filters_applied_to_export(self):
        r = self.client.get(self.url, {"format": "csv", "gender": "female"})
        content = r.content.decode()
        self.assertIn("exp-alice", content)
        self.assertNotIn("exp-bob", content)

    def test_invalid_format_400(self):
        r = self.client.get(self.url, {"format": "json"})
        self.assertEqual(r.status_code, 400)
