from django.conf import settings
from django.db import models


class Friendship(models.Model):
    from_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="friendships_sent",
    )
    to_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="friendships_received",
    )
    status = models.CharField(
        max_length=10,
        choices=[
            ("pending", "Pending"),
            ("accepted", "Accepted"),
            ("declined", "Declined"),
        ],
        default="pending",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("from_user", "to_user")

    def __str__(self):
        return f"{self.from_user} → {self.to_user} ({self.status})"


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

    @property
    def tier(self):
        if self.rating >= 88:
            return "platinum"
        if self.rating >= 82:
            return "gold"
        if self.rating >= 76:
            return "silver"
        return "bronze"

    @property
    def talent_bonus(self):
        if self.rating >= 88:
            return 11
        if self.rating >= 82:
            return 8
        if self.rating >= 76:
            return 5
        return 3

    @property
    def star_count(self):
        if self.rating >= 88:
            return 4
        if self.rating >= 82:
            return 3
        if self.rating >= 76:
            return 2
        return 1

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
    duration_seconds = models.IntegerField(null=True, blank=True)

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


class SoloDraftProgress(models.Model):
    """Stores in-progress solo draft state so refreshes don't lose data."""
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="solo_draft_progress",
    )
    current_round = models.IntegerField(default=1)
    drafted_slots = models.JSONField(default=dict)
    drafted_player_ids = models.JSONField(default=list)
    current_pool = models.JSONField(default=list)
    picked_this_round = models.BooleanField(default=False)
    start_time = models.BigIntegerField()  # JS Date.now() timestamp

    def __str__(self):
        return f"Draft progress for {self.user} – round {self.current_round}"


class VsDraftProgress(models.Model):
    """Stores in-progress VS draft state so refreshes don't lose data."""
    battle = models.ForeignKey(
        "VsBattle",
        on_delete=models.CASCADE,
        related_name="draft_progress",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="vs_draft_progress",
    )
    current_round = models.IntegerField(default=1)
    drafted_slots = models.JSONField(default=dict)
    drafted_player_ids = models.JSONField(default=list)
    current_pool = models.JSONField(default=list)
    picked_this_round = models.BooleanField(default=False)
    start_time = models.BigIntegerField(null=True, blank=True)

    class Meta:
        unique_together = [("battle", "user")]

    def __str__(self):
        return f"VS draft progress for {self.user} in battle {self.battle_id} – round {self.current_round}"


class VsBattle(models.Model):
    challenger = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="battles_sent",
    )
    challenged = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="battles_received",
    )
    status = models.CharField(
        max_length=20,
        choices=[
            ("pending", "Pending"),
            ("accepted", "Accepted"),
            ("completed", "Completed"),
            ("declined", "Declined"),
            ("cancelled", "Cancelled"),
        ],
        default="pending",
    )
    challenger_draft = models.OneToOneField(
        Draft,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="battle_as_challenger",
    )
    challenged_draft = models.OneToOneField(
        Draft,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="battle_as_challenged",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.challenger} vs {self.challenged} ({self.status})"


class VsBattleRound(models.Model):
    battle = models.ForeignKey(
        VsBattle, on_delete=models.CASCADE, related_name="rounds"
    )
    round_number = models.IntegerField()
    player1 = models.ForeignKey(
        Player, on_delete=models.CASCADE, related_name="+"
    )
    player2 = models.ForeignKey(
        Player, on_delete=models.CASCADE, related_name="+"
    )
    player3 = models.ForeignKey(
        Player, on_delete=models.CASCADE, related_name="+"
    )
    tier1 = models.CharField(max_length=10)
    tier2 = models.CharField(max_length=10)
    tier3 = models.CharField(max_length=10)

    class Meta:
        unique_together = ("battle", "round_number")
        ordering = ["round_number"]

    def __str__(self):
        return f"Battle {self.battle.id} – Round {self.round_number}"
