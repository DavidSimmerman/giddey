from django.conf import settings
from django.db import models


class Team(models.Model):
    city = models.CharField(max_length=100)
    name = models.CharField(max_length=100)
    division = models.CharField(max_length=100)
    logo_image = models.URLField()

    def __str__(self):
        return f"{self.city} {self.name}"


class Player(models.Model):
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    image = models.URLField()
    position = models.CharField(max_length=10, default="")
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="players")
    draft_year = models.PositiveIntegerField()
    rating = models.PositiveIntegerField()
    college = models.CharField(max_length=200)

    def __str__(self):
        return f"{self.first_name} {self.last_name}"


class Draft(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="drafts"
    )
    completed_at = models.DateTimeField(auto_now_add=True)
    talent_score = models.IntegerField()
    chemistry_score = models.IntegerField()
    total_score = models.IntegerField()
    optimal_score = models.IntegerField()

    class Meta:
        ordering = ["-completed_at"]

    def __str__(self):
        return f"Draft by {self.user} – {self.total_score} pts"


class DraftPick(models.Model):
    draft = models.ForeignKey(Draft, on_delete=models.CASCADE, related_name="picks")
    player = models.ForeignKey(Player, on_delete=models.CASCADE)
    round_number = models.IntegerField()
    slot_index = models.IntegerField()

    class Meta:
        ordering = ["round_number"]

    def __str__(self):
        return f"Round {self.round_number}: {self.player}"
