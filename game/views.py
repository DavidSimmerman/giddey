from collections import defaultdict

from django.shortcuts import render

from .models import Team


def home(request):
    teams = Team.objects.prefetch_related("players").order_by("division", "city")
    divisions = defaultdict(list)
    for team in teams:
        divisions[team.division].append(team)
    return render(request, "game/home.html", {"divisions": dict(divisions)})
