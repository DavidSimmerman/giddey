from collections import defaultdict

from django.http import JsonResponse
from django.shortcuts import render

from .models import Player, Team


def home(request):
    teams = Team.objects.prefetch_related("players").order_by("division", "city")
    divisions = defaultdict(list)
    for team in teams:
        divisions[team.division].append(team)
    return render(request, "game/home.html", {"divisions": dict(divisions)})


def draft(request):
    return render(request, "game/draft.html")


def api_random_players(request):
    exclude_raw = request.GET.get("exclude", "")
    exclude_ids = [int(x) for x in exclude_raw.split(",") if x.strip()]
    players = (
        Player.objects.exclude(id__in=exclude_ids)
        .select_related("team")
        .order_by("?")[:3]
    )
    data = []
    for p in players:
        data.append(
            {
                "id": p.id,
                "first_name": p.first_name,
                "last_name": p.last_name,
                "image": p.image,
                "position": p.position,
                "rating": p.rating,
                "team_name": f"{p.team.city} {p.team.name}",
                "team_logo": p.team.logo_image,
                "team_division": p.team.division,
                "draft_year": p.draft_year,
            }
        )
    return JsonResponse(data, safe=False)
