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
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="players")
    draft_year = models.PositiveIntegerField()
    rating = models.PositiveIntegerField()
    college = models.CharField(max_length=200)

    def __str__(self):
        return f"{self.first_name} {self.last_name}"
