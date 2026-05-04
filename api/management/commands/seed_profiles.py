"""
Management command: seed_profiles

Loads 2026 profiles from api/fixtures/seed_profiles.json.
Idempotent: re-running never creates duplicates.

Usage:
    python manage.py seed_profiles
    python manage.py seed_profiles --clear   # wipe table first
"""
import json
from pathlib import Path

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

import uuid6

from api.models import Profile


FIXTURE_PATH = Path(__file__).resolve().parents[3] / "api" / "fixtures" / "seed_profiles.json"


class Command(BaseCommand):
    help = "Seed the database with 2026 demographic profiles (idempotent)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Delete all existing profiles before seeding",
        )

    def handle(self, *args, **options):
        if not FIXTURE_PATH.exists():
            self.stderr.write(self.style.ERROR(f"Fixture file not found: {FIXTURE_PATH}"))
            return

        with open(FIXTURE_PATH, encoding="utf-8") as fh:
            raw = json.load(fh)

        profiles_data = raw.get("profiles", raw) if isinstance(raw, dict) else raw

        if options["clear"]:
            deleted, _ = Profile.objects.all().delete()
            self.stdout.write(self.style.WARNING(f"Cleared {deleted} existing profiles."))

        existing_names = set(
            Profile.objects.values_list("name", flat=True)
        )

        to_create = []
        skipped = 0

        for entry in profiles_data:
            name = entry.get("name", "").strip()
            if not name or name in existing_names:
                skipped += 1
                continue

            to_create.append(
                Profile(
                    id=uuid6.uuid7(),
                    name=name,
                    gender=entry["gender"],
                    gender_probability=float(entry["gender_probability"]),
                    age=int(entry["age"]),
                    age_group=entry["age_group"],
                    country_id=entry["country_id"],
                    country_name=entry.get("country_name", ""),
                    country_probability=float(entry["country_probability"]),
                    # sample_size not present in seed data — nullable field
                    sample_size=entry.get("sample_size"),
                    created_at=timezone.now(),
                )
            )
            existing_names.add(name)

        if not to_create:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Nothing to insert — {skipped} profile(s) already exist."
                )
            )
            return

        with transaction.atomic():
            Profile.objects.bulk_create(to_create, batch_size=500)

        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded {len(to_create)} profile(s). "
                f"Skipped {skipped} duplicate(s). "
                f"Total in DB: {Profile.objects.count()}"
            )
        )
