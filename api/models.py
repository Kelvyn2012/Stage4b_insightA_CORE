import uuid6
from django.db import models
from django.utils import timezone


class Profile(models.Model):
    AGE_GROUP_CHOICES = [
        ("child", "Child"),
        ("teenager", "Teenager"),
        ("adult", "Adult"),
        ("senior", "Senior"),
    ]
    GENDER_CHOICES = [
        ("male", "Male"),
        ("female", "Female"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid6.uuid7, editable=False)
    name = models.CharField(max_length=255, unique=True, db_index=True)
    gender = models.CharField(max_length=10, choices=GENDER_CHOICES, db_index=True)
    gender_probability = models.FloatField(db_index=True)
    sample_size = models.IntegerField(null=True, blank=True)
    age = models.IntegerField(db_index=True)
    age_group = models.CharField(max_length=10, choices=AGE_GROUP_CHOICES, db_index=True)
    country_id = models.CharField(max_length=10, db_index=True)
    country_name = models.CharField(max_length=100, default="", blank=True)
    country_probability = models.FloatField(db_index=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["gender", "age_group"], name="idx_gender_age_group"),
            models.Index(fields=["gender", "country_id"], name="idx_gender_country"),
            models.Index(fields=["country_id", "age_group"], name="idx_country_age_group"),
            models.Index(fields=["age", "gender"], name="idx_age_gender"),
        ]

    def __str__(self):
        return f"{self.name} ({self.gender}, {self.age_group}) [{self.country_id}]"
