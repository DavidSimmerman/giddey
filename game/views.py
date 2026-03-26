import json
import random

from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .models import Draft, DraftPick, Player

# Tier odds per round: (platinum, gold, silver, bronze)
# Hero % is merged into platinum since we don't have hero tier yet.
ROUND_ODDS = {
    1: (82, 18, 0, 0),
    2: (52, 45, 3, 0),
    3: (12, 65, 23, 0),
    4: (10, 45, 40, 5),
    5: (10, 25, 60, 5),
    6: (9, 10, 50, 31),
    7: (9, 10, 41, 40),
    8: (7, 7, 21, 65),
    9: (6, 6, 18, 70),
}


def roll_tier(round_num):
    """Roll a random tier based on the round's odds."""
    odds = ROUND_ODDS.get(round_num, ROUND_ODDS[9])
    platinum, gold, silver, _bronze = odds
    roll = random.randint(1, 100)
    if roll <= platinum:
        return "platinum"
    if roll <= platinum + gold:
        return "gold"
    if roll <= platinum + gold + silver:
        return "silver"
    return "bronze"


TIER_FILTERS = {
    "platinum": lambda qs: qs.filter(rating__gte=88),
    "gold": lambda qs: qs.filter(rating__gte=82, rating__lt=88),
    "silver": lambda qs: qs.filter(rating__gte=76, rating__lt=82),
    "bronze": lambda qs: qs.filter(rating__lt=76),
}


def home(request):
    if not request.user.is_authenticated and not request.session.get("guest"):
        return redirect("login")
    return render(request, "game/home.html")


def draft(request):
    return render(request, "game/draft.html")


def _player_matches_positions(player, available_positions):
    """Check if a player can play at least one of the available positions."""
    player_positions = {p.strip() for p in player.position.split("/")}
    return bool(player_positions & available_positions)


def _filter_by_positions(qs, available_positions):
    """Filter queryset to players who can play at least one available position."""
    from django.db.models import Q

    q = Q()
    for pos in available_positions:
        q |= Q(position__contains=pos)
    return qs.filter(q)


def _serialize_player(p):
    return {
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
        "college": p.college,
    }


def api_random_players(request):
    exclude_raw = request.GET.get("exclude", "")
    exclude_ids = [int(x) for x in exclude_raw.split(",") if x.strip()]
    round_num = int(request.GET.get("round", 1))
    positions_raw = request.GET.get("positions", "")
    available_positions = {p.strip() for p in positions_raw.split(",") if p.strip()}

    base_qs = Player.objects.exclude(id__in=exclude_ids).select_related("team")
    if available_positions:
        base_qs = _filter_by_positions(base_qs, available_positions)

    data = []
    picked_ids = set(exclude_ids)

    for _ in range(3):
        tier = roll_tier(round_num)
        tier_qs = TIER_FILTERS[tier](base_qs).exclude(id__in=picked_ids)

        # Fallback: if no players in the rolled tier, try adjacent tiers
        if not tier_qs.exists():
            for fallback_tier in ["gold", "silver", "platinum", "bronze"]:
                tier_qs = TIER_FILTERS[fallback_tier](base_qs).exclude(
                    id__in=picked_ids
                )
                if tier_qs.exists():
                    break

        player = tier_qs.order_by("?").first()
        if player:
            data.append(_serialize_player(player))
            picked_ids.add(player.id)

    return JsonResponse(data, safe=False)


def login_view(request):
    if request.method == "POST":
        action = request.POST.get("action")
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")

        if action == "register":
            password2 = request.POST.get("password2", "")
            if not username or not password:
                return render(request, "game/login.html", {"error": "All fields are required.", "tab": "register"})
            if password != password2:
                return render(request, "game/login.html", {"error": "Passwords do not match.", "tab": "register"})
            if User.objects.filter(username=username).exists():
                return render(request, "game/login.html", {"error": "Username already taken.", "tab": "register"})
            user = User.objects.create_user(username=username, password=password)
            login(request, user)
            return redirect("home")

        else:
            user = authenticate(request, username=username, password=password)
            if user is None:
                return render(request, "game/login.html", {"error": "Invalid username or password.", "tab": "login"})
            login(request, user)
            return redirect("home")

    return render(request, "game/login.html", {"tab": "login"})


def guest_view(request):
    request.session["guest"] = True
    return redirect("home")


def logout_view(request):
    logout(request)
    request.session.flush()
    return redirect("login")


@require_POST
def api_save_draft(request):
    if not request.user.is_authenticated:
        return JsonResponse({"error": "Not authenticated"}, status=401)

    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    draft = Draft.objects.create(
        user=request.user,
        talent_score=body["talent_score"],
        chemistry_score=body["chemistry_score"],
        total_score=body["total_score"],
        optimal_score=body["optimal_score"],
    )
    for pick in body["picks"]:
        DraftPick.objects.create(
            draft=draft,
            player_id=pick["player_id"],
            round_number=pick["round_number"],
            slot_index=pick["slot_index"],
        )

    return JsonResponse({"id": draft.id})


@login_required(login_url="login")
def history_view(request):
    drafts = request.user.drafts.all()
    return render(request, "game/history.html", {"drafts": drafts})


@login_required(login_url="login")
def draft_detail_view(request, draft_id):
    draft = get_object_or_404(Draft, id=draft_id, user=request.user)
    picks = draft.picks.select_related("player", "player__team").all()
    slot_positions = ["SG", "PF", "SF", "PG", "PG", "SF", "PF", "SG", "C"]
    for pick in picks:
        pick.slot_position = slot_positions[pick.slot_index]
    accuracy = round((draft.total_score / draft.optimal_score) * 100) if draft.optimal_score > 0 else 100
    return render(request, "game/draft_detail.html", {
        "draft": draft,
        "picks": picks,
        "accuracy": accuracy,
    })
