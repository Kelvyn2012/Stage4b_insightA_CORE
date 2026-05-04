"""
Stage 4B performance indexes.

Single-field indexes on name, age, and created_at already exist from the
initial migrations (via db_index=True / unique=True on those fields), so
they are not re-created here.

New in this migration:
  - idx_country_gender_age_group  (country_id, gender, age_group)
    Covers the most common three-filter analyst query pattern and is the
    primary index benefiting from Stage 4B query normalization.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0002_add_country_name_indexes_nullable_sample_size"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="profile",
            index=models.Index(
                fields=["country_id", "gender", "age_group"],
                name="idx_country_gender_age_group",
            ),
        ),
    ]
