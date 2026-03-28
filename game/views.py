import json
import random

from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import Draft, DraftPick, Friendship, Player, SoloDraftProgress, VsBattle, VsBattleRound, VsDraftProgress

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


def _pending_count(user):
    """Count pending friend requests + challenges for the navbar badge."""
    if not user.is_authenticated:
        return 0
    return (
        Friendship.objects.filter(to_user=user, status="pending").count()
        + VsBattle.objects.filter(challenged=user, status="pending").count()
    )


GRID_CONNECTIONS = [
    (0, 1), (0, 2), (0, 3), (1, 4), (1, 5),
    (2, 3), (2, 6), (3, 6), (3, 8), (4, 5),
    (4, 7), (4, 8), (5, 7), (6, 8), (7, 8),
]


def _calc_dot_colors(drafted_slots):
    """Compute per-slot dot color classes from drafted_slots JSON dict."""
    # Chemistry per connection
    line_chems = []
    for a, b in GRID_CONNECTIONS:
        pa = drafted_slots.get(str(a))
        pb = drafted_slots.get(str(b))
        if not pa or not pb:
            line_chems.append((a, b, None))
            continue
        chem = 0
        if pa.get("draft_year") == pb.get("draft_year"):
            chem += 1
        if pa.get("team_division") and pb.get("team_division") and pa["team_division"] == pb["team_division"]:
            chem += 1
        if pa.get("team_name") == pb.get("team_name"):
            chem += 1
        pa_col = (pa.get("college") or "").strip()
        pb_col = (pb.get("college") or "").strip()
        if pa_col and pb_col and pa_col == pb_col:
            chem += 2
        line_chems.append((a, b, chem))

    dot_colors = {}
    for slot_str in drafted_slots:
        idx = int(slot_str)
        adjacent_count = 0
        chem_sum = 0
        for a, b, chem in line_chems:
            if a != idx and b != idx:
                continue
            if chem is None:
                continue
            adjacent_count += 1
            chem_sum += chem
        if adjacent_count == 0:
            color = "white"
        elif chem_sum < 2:
            color = "red"
        elif chem_sum < 4:
            color = "yellow"
        elif chem_sum < 6:
            color = "green"
        else:
            color = "blue"
        dot_colors[idx] = color
    return dot_colors


def home(request):
    if not request.user.is_authenticated and not request.session.get("guest"):
        return redirect("login")
    draft_progress = None
    dot_colors = {}
    if request.user.is_authenticated:
        try:
            prog = SoloDraftProgress.objects.get(user=request.user)
            draft_progress = {
                "drafted_slots": prog.drafted_slots,
            }
            dot_colors = _calc_dot_colors(prog.drafted_slots)
        except SoloDraftProgress.DoesNotExist:
            pass
    return render(request, "game/home.html", {
        "pending_count": _pending_count(request.user),
        "draft_progress_json": json.dumps(draft_progress),
        "dot_colors": dot_colors,
    })


def draft(request):
    return render(request, "game/draft.html")


def _player_matches_positions(player, available_positions):
    """Check if a player can play at least one of the available positions."""
    player_positions = {p.strip() for p in player.position.split("/")}
    return bool(player_positions & available_positions)


def _filter_by_positions(qs, available_positions):
    """Filter queryset to players who can play at least one available position."""
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
        duration_seconds=body.get("duration_seconds"),
    )
    for pick in body["picks"]:
        DraftPick.objects.create(
            draft=draft,
            player_id=pick["player_id"],
            round_number=pick["round_number"],
            slot_index=pick["slot_index"],
        )

    return JsonResponse({"id": draft.id})


# ── Solo draft progress (save/restore on refresh) ────────────────────


def api_draft_progress(request):
    if not request.user.is_authenticated:
        return JsonResponse({"error": "Not authenticated"}, status=401)

    if request.method == "GET":
        try:
            prog = SoloDraftProgress.objects.get(user=request.user)
        except SoloDraftProgress.DoesNotExist:
            return JsonResponse({"exists": False})
        return JsonResponse({
            "exists": True,
            "current_round": prog.current_round,
            "drafted_slots": prog.drafted_slots,
            "drafted_player_ids": prog.drafted_player_ids,
            "current_pool": prog.current_pool,
            "picked_this_round": prog.picked_this_round,
            "start_time": prog.start_time,
        })

    if request.method == "POST":
        try:
            body = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON"}, status=400)
        SoloDraftProgress.objects.update_or_create(
            user=request.user,
            defaults={
                "current_round": body["current_round"],
                "drafted_slots": body["drafted_slots"],
                "drafted_player_ids": body["drafted_player_ids"],
                "current_pool": body["current_pool"],
                "picked_this_round": body["picked_this_round"],
                "start_time": body["start_time"],
            },
        )
        return JsonResponse({"ok": True})

    if request.method == "DELETE":
        SoloDraftProgress.objects.filter(user=request.user).delete()
        return JsonResponse({"ok": True})

    return JsonResponse({"error": "Method not allowed"}, status=405)


@login_required(login_url="login")
def stats_view(request):
    return render(request, "game/stats.html", {
        "pending_count": _pending_count(request.user),
    })


@login_required(login_url="login")
def history_view(request):
    drafts = request.user.drafts.all()
    return render(request, "game/history.html", {
        "drafts": drafts,
        "pending_count": _pending_count(request.user),
    })


@login_required(login_url="login")
def draft_detail_view(request, draft_id):
    # Allow viewing opponent's draft if part of a VS battle
    draft = get_object_or_404(Draft, id=draft_id)
    if draft.user != request.user:
        is_participant = VsBattle.objects.filter(
            Q(challenger_draft=draft) | Q(challenged_draft=draft),
        ).filter(
            Q(challenger=request.user) | Q(challenged=request.user),
        ).exists()
        if not is_participant:
            return JsonResponse({"error": "Not found"}, status=404)
    return render(request, "game/draft_detail.html", {
        "draft": draft,
        "picks_json": json.dumps(_serialize_draft_picks(draft)),
    })


# ── Friends ──────────────────────────────────────────────────────────


def _get_friends(user):
    """Return list of User objects that are friends with the given user."""
    friendships = Friendship.objects.filter(
        Q(from_user=user) | Q(to_user=user), status="accepted"
    ).select_related("from_user", "to_user")
    friends = []
    for f in friendships:
        friends.append(f.to_user if f.from_user == user else f.from_user)
    return friends


@login_required(login_url="login")
def friends_view(request):
    user = request.user

    friends = _get_friends(user)

    pending_received = Friendship.objects.filter(
        to_user=user, status="pending"
    ).select_related("from_user")

    pending_sent = Friendship.objects.filter(
        from_user=user, status="pending"
    ).select_related("to_user")

    # VS battles
    challenges_received = VsBattle.objects.filter(
        challenged=user, status="pending"
    ).select_related("challenger")

    challenges_sent = VsBattle.objects.filter(
        challenger=user, status="pending", is_public=False
    ).select_related("challenged")

    my_public_challenge = VsBattle.objects.filter(
        challenger=user, is_public=True, status="pending"
    ).exists()

    active_battles_qs = VsBattle.objects.filter(
        Q(challenger=user) | Q(challenged=user), status="accepted"
    ).select_related("challenger", "challenged", "challenger_draft", "challenged_draft")

    active_battles = []
    for b in active_battles_qs:
        opponent = b.challenged if b.challenger == user else b.challenger
        h2h = _head_to_head(user, opponent)
        b.opponent = opponent
        b.h2h_record = f"{h2h['wins']}-{h2h['losses']}"
        my_draft = b.challenger_draft if b.challenger == user else b.challenged_draft
        b.is_my_turn = my_draft is None
        active_battles.append(b)
    active_battles.sort(key=lambda b: not b.is_my_turn)

    # Add h2h records to sent challenges
    challenges_sent_list = []
    for c in challenges_sent:
        h2h = _head_to_head(user, c.challenged)
        c.h2h_record = f"{h2h['wins']}-{h2h['losses']}"
        challenges_sent_list.append(c)
    challenges_sent = challenges_sent_list

    completed_battles = VsBattle.objects.filter(
        Q(challenger=user) | Q(challenged=user), status="completed"
    ).select_related(
        "challenger", "challenged", "challenger_draft", "challenged_draft"
    ).order_by("-created_at")[:25]

    # Annotate each completed battle with result info for the template
    battle_history = []
    h2h_cache = {}
    # Overall stats
    total_wins = total_losses = 0
    today = timezone.now().date()
    today_wins = today_losses = 0
    streak_count = 0
    streak_type = None

    # Process ALL completed battles (not just last 20) for stats
    all_completed = VsBattle.objects.filter(
        Q(challenger=user) | Q(challenged=user), status="completed"
    ).select_related(
        "challenger", "challenged", "challenger_draft", "challenged_draft"
    ).order_by("created_at")

    for b in all_completed:
        opponent = b.challenged if b.challenger == user else b.challenger
        my_draft = b.challenger_draft if b.challenger == user else b.challenged_draft
        opp_draft = b.challenged_draft if b.challenger == user else b.challenger_draft
        if not my_draft or not opp_draft:
            continue
        if my_draft.total_score > opp_draft.total_score:
            result = "W"
        elif my_draft.total_score < opp_draft.total_score:
            result = "L"
        elif (
            my_draft.duration_seconds is not None
            and opp_draft.duration_seconds is not None
            and my_draft.duration_seconds != opp_draft.duration_seconds
        ):
            result = "W" if my_draft.duration_seconds < opp_draft.duration_seconds else "L"
        else:
            result = "T"

        if result == "W":
            total_wins += 1
            if b.created_at.date() == today:
                today_wins += 1
        elif result == "L":
            total_losses += 1
            if b.created_at.date() == today:
                today_losses += 1

        if result == streak_type:
            streak_count += 1
        else:
            streak_type = result
            streak_count = 1

    overall_total = total_wins + total_losses
    overall_pct = f".{round(total_wins / overall_total * 1000):03d}" if overall_total else "0"
    today_total = today_wins + today_losses
    today_pct = f".{round(today_wins / today_total * 1000):03d}" if today_total else "0"
    streak_str = f"{streak_type}{streak_count}" if streak_type else "-"

    for b in completed_battles:
        opponent = b.challenged if b.challenger == user else b.challenger
        my_draft = b.challenger_draft if b.challenger == user else b.challenged_draft
        opp_draft = b.challenged_draft if b.challenger == user else b.challenger_draft
        if not my_draft or not opp_draft:
            continue
        if my_draft.total_score > opp_draft.total_score:
            result = "W"
        elif my_draft.total_score < opp_draft.total_score:
            result = "L"
        elif (
            my_draft.duration_seconds is not None
            and opp_draft.duration_seconds is not None
            and my_draft.duration_seconds != opp_draft.duration_seconds
        ):
            result = "W" if my_draft.duration_seconds < opp_draft.duration_seconds else "L"
        else:
            result = "T"
        point_diff = abs(my_draft.total_score - opp_draft.total_score)
        if opponent.id not in h2h_cache:
            h2h_cache[opponent.id] = _head_to_head(user, opponent)
        h2h = h2h_cache[opponent.id]
        battle_history.append({
            "id": b.id,
            "opponent": opponent,
            "my_score": my_draft.total_score,
            "opp_score": opp_draft.total_score,
            "result": result,
            "point_diff": point_diff,
            "h2h_record": f"{h2h['wins']}-{h2h['losses']}",
            "date": b.created_at,
        })

    return render(request, "game/friends.html", {
        "friends": friends,
        "pending_received": pending_received,
        "pending_sent": pending_sent,
        "challenges_received": challenges_received,
        "challenges_sent": challenges_sent,
        "active_battles": active_battles,
        "my_public_challenge": my_public_challenge,
        "battle_history": battle_history,
        "pending_count": _pending_count(user),
        "vs_stats": {
            "overall": f"{total_wins} - {total_losses}",
            "overall_pct": overall_pct,
            "today": f"{today_wins} - {today_losses}",
            "today_pct": today_pct,
            "streak": streak_str,
        },
    })


@login_required(login_url="login")
@require_POST
def api_send_friend_request(request):
    body = json.loads(request.body)
    username = body.get("username", "").strip()
    if not username:
        return JsonResponse({"error": "Username required"}, status=400)
    if username == request.user.username:
        return JsonResponse({"error": "Cannot add yourself"}, status=400)
    try:
        target = User.objects.get(username=username)
    except User.DoesNotExist:
        return JsonResponse({"error": "User not found"}, status=404)

    # Check if friendship already exists in either direction
    existing = Friendship.objects.filter(
        Q(from_user=request.user, to_user=target)
        | Q(from_user=target, to_user=request.user)
    ).first()
    if existing:
        if existing.status == "accepted":
            return JsonResponse({"error": "Already friends"}, status=400)
        if existing.status == "pending":
            return JsonResponse({"error": "Request already pending"}, status=400)
        if existing.status == "declined":
            existing.status = "pending"
            existing.from_user = request.user
            existing.to_user = target
            existing.save()
            return JsonResponse({"status": "sent"})

    Friendship.objects.create(from_user=request.user, to_user=target)
    return JsonResponse({"status": "sent"})


@login_required(login_url="login")
@require_POST
def api_respond_friend_request(request, friendship_id):
    friendship = get_object_or_404(
        Friendship, id=friendship_id, to_user=request.user, status="pending"
    )
    body = json.loads(request.body)
    action = body.get("action")
    if action == "accept":
        friendship.status = "accepted"
        friendship.save()
        return JsonResponse({"status": "accepted"})
    else:
        friendship.status = "declined"
        friendship.save()
        return JsonResponse({"status": "declined"})


@login_required(login_url="login")
@require_POST
def api_remove_friend(request, user_id):
    target = get_object_or_404(User, id=user_id)
    friendship = get_object_or_404(
        Friendship,
        Q(from_user=request.user, to_user=target)
        | Q(from_user=target, to_user=request.user),
        status="accepted",
    )
    friendship.delete()
    return JsonResponse({"status": "removed"})


@login_required(login_url="login")
def api_search_users(request):
    q = request.GET.get("q", "").strip()
    if len(q) < 2:
        return JsonResponse([], safe=False)
    users = (
        User.objects.filter(username__icontains=q)
        .exclude(id=request.user.id)
        .values_list("username", flat=True)[:10]
    )
    return JsonResponse(list(users), safe=False)


# ── VS Battle ────────────────────────────────────────────────────────


@login_required(login_url="login")
@require_POST
def api_send_challenge(request):
    body = json.loads(request.body)
    user_id = body.get("user_id")
    try:
        target = User.objects.get(id=user_id)
    except User.DoesNotExist:
        return JsonResponse({"error": "User not found"}, status=404)

    # Verify friendship
    is_friend = Friendship.objects.filter(
        Q(from_user=request.user, to_user=target)
        | Q(from_user=target, to_user=request.user),
        status="accepted",
    ).exists()
    if not is_friend:
        return JsonResponse({"error": "Not friends"}, status=403)

    # Allow up to 3 pending invites per friend
    pending_count = VsBattle.objects.filter(
        challenger=request.user, challenged=target, status="pending", is_public=False
    ).count()
    if pending_count >= 3:
        return JsonResponse(
            {"error": "You already have 3 pending invites to this friend"},
            status=400,
        )

    battle = VsBattle.objects.create(challenger=request.user, challenged=target)
    return JsonResponse({"id": battle.id})


def _pick_player_for_tier(tier, base_qs, used_ids):
    """Pick a random player from a tier, with fallback to other tiers."""
    tier_qs = TIER_FILTERS[tier](base_qs).exclude(id__in=used_ids)
    if not tier_qs.exists():
        for fallback in ["gold", "silver", "platinum", "bronze"]:
            tier_qs = TIER_FILTERS[fallback](base_qs).exclude(id__in=used_ids)
            if tier_qs.exists():
                tier = fallback
                break
    player = tier_qs.order_by("?").first()
    return player, tier


def pre_generate_rounds(battle):
    """Pre-generate 9 rounds of 3 players for a VS battle."""
    used_ids = set()
    all_positions = {"SG", "PF", "SF", "PG", "C"}
    base_qs = Player.objects.select_related("team")
    filtered_qs = _filter_by_positions(base_qs, all_positions)

    for round_num in range(1, 10):
        players = []
        tiers = []
        for _ in range(3):
            tier = roll_tier(round_num)
            player, actual_tier = _pick_player_for_tier(
                tier, filtered_qs, used_ids
            )
            if player:
                players.append(player)
                tiers.append(actual_tier)
                used_ids.add(player.id)

        if len(players) == 3:
            VsBattleRound.objects.create(
                battle=battle,
                round_number=round_num,
                player1=players[0],
                player2=players[1],
                player3=players[2],
                tier1=tiers[0],
                tier2=tiers[1],
                tier3=tiers[2],
            )


@login_required(login_url="login")
@require_POST
def api_respond_challenge(request, battle_id):
    battle = get_object_or_404(
        VsBattle, id=battle_id, challenged=request.user, status="pending"
    )
    body = json.loads(request.body)
    action = body.get("action")
    if action == "accept":
        battle.status = "accepted"
        battle.save()
        pre_generate_rounds(battle)
        return JsonResponse({"status": "accepted", "battle_id": battle.id})
    else:
        battle.status = "declined"
        battle.save()
        return JsonResponse({"status": "declined"})


@login_required(login_url="login")
def _serialize_draft_picks(draft):
    """Serialize a draft's picks as a list of dicts for the JS results screen."""
    picks = draft.picks.select_related("player", "player__team").all()
    return [
        {
            "id": p.player.id,
            "first_name": p.player.first_name,
            "last_name": p.player.last_name,
            "position": p.player.position,
            "rating": p.player.rating,
            "draft_year": p.player.draft_year,
            "college": p.player.college,
            "image": p.player.image,
            "team_logo": p.player.team.logo_image,
            "team_name": str(p.player.team),
            "team_division": p.player.team.division,
            "slot_index": p.slot_index,
            "round_number": p.round_number,
        }
        for p in picks
    ]


@login_required(login_url="login")
def vs_draft_view(request, battle_id):
    battle = get_object_or_404(
        VsBattle,
        Q(challenger=request.user) | Q(challenged=request.user),
        id=battle_id,
        status="accepted",
    )
    # Redirect if user already drafted
    my_draft = (
        battle.challenger_draft if request.user == battle.challenger
        else battle.challenged_draft
    )
    if my_draft:
        if battle.status == "completed":
            return redirect("vs_results", battle_id=battle.id)
        # Show results screen while waiting for opponent
        opponent = battle.challenged if request.user == battle.challenger else battle.challenger
        my_picks = list(
            my_draft.picks.select_related("player", "player__team").order_by("round_number")
        )
        my_slot_info = _calc_slot_info(my_picks)
        for pick in my_picks:
            info = my_slot_info.get(pick.slot_index, {})
            pick.slot_score = info.get("score", pick.player.talent_bonus)
            pick.dot_color = info.get("dot_color", "white")

        my_accuracy = (
            round((my_draft.total_score / my_draft.optimal_score) * 100)
            if my_draft.optimal_score > 0 else 100
        )

        def fmt_time(secs):
            if secs is None:
                return "--:--"
            return f"{secs // 60}:{secs % 60:02d}"

        round_pairs = [(i + 1, my_picks[i] if i < len(my_picks) else None, None) for i in range(len(my_picks))]

        return render(request, "game/vs_results.html", {
            "battle": battle,
            "me": request.user,
            "opponent": opponent,
            "my_draft": my_draft,
            "opp_draft": None,
            "my_accuracy": my_accuracy,
            "opp_accuracy": None,
            "h2h": _head_to_head(request.user, opponent),
            "i_won": False,
            "is_tie": False,
            "point_diff": 0,
            "won_by_time": False,
            "round_pairs": round_pairs,
            "my_time": fmt_time(my_draft.duration_seconds),
            "opp_time": "–",
            "my_talent_better": False,
            "opp_talent_better": False,
            "my_chem_better": False,
            "opp_chem_better": False,
            "my_time_better": False,
            "opp_time_better": False,
            "waiting": True,
        })
    return render(request, "game/vs_draft.html", {"battle": battle})


@login_required(login_url="login")
def api_vs_random_players(request, battle_id):
    battle = get_object_or_404(
        VsBattle,
        Q(challenger=request.user) | Q(challenged=request.user),
        id=battle_id,
        status="accepted",
    )
    round_num = int(request.GET.get("round", 1))
    exclude_raw = request.GET.get("exclude", "")
    exclude_ids = [int(x) for x in exclude_raw.split(",") if x.strip()]
    positions_raw = request.GET.get("positions", "")
    available_positions = {p.strip() for p in positions_raw.split(",") if p.strip()}

    vs_round = get_object_or_404(
        VsBattleRound, battle=battle, round_number=round_num
    )
    pre_players = [vs_round.player1, vs_round.player2, vs_round.player3]
    pre_tiers = [vs_round.tier1, vs_round.tier2, vs_round.tier3]

    data = []
    used_ids = set(exclude_ids)

    for i, player in enumerate(pre_players):
        # Check if pre-generated player is valid for this user's constraints
        needs_replace = player.id in used_ids
        if not needs_replace and available_positions:
            needs_replace = not _player_matches_positions(player, available_positions)

        if needs_replace:
            base_qs = Player.objects.exclude(id__in=used_ids).select_related("team")
            if available_positions:
                base_qs = _filter_by_positions(base_qs, available_positions)
            replacement, _ = _pick_player_for_tier(pre_tiers[i], base_qs, used_ids)
            if replacement:
                data.append(_serialize_player(replacement))
                used_ids.add(replacement.id)
        else:
            data.append(_serialize_player(player))
            used_ids.add(player.id)

    return JsonResponse(data, safe=False)


@login_required(login_url="login")
@require_POST
def api_vs_save_draft(request, battle_id):
    battle = get_object_or_404(
        VsBattle,
        Q(challenger=request.user) | Q(challenged=request.user),
        id=battle_id,
        status="accepted",
    )
    body = json.loads(request.body)

    draft = Draft.objects.create(
        user=request.user,
        talent_score=body["talent_score"],
        chemistry_score=body["chemistry_score"],
        total_score=body["total_score"],
        optimal_score=body["optimal_score"],
        duration_seconds=body.get("duration_seconds"),
    )
    for pick in body["picks"]:
        DraftPick.objects.create(
            draft=draft,
            player_id=pick["player_id"],
            round_number=pick["round_number"],
            slot_index=pick["slot_index"],
        )

    if request.user == battle.challenger:
        battle.challenger_draft = draft
    else:
        battle.challenged_draft = draft

    if battle.challenger_draft and battle.challenged_draft:
        battle.status = "completed"
    battle.save()

    return JsonResponse({
        "id": draft.id,
        "battle_complete": battle.status == "completed",
    })


@login_required(login_url="login")
def api_vs_status(request, battle_id):
    battle = get_object_or_404(
        VsBattle,
        Q(challenger=request.user) | Q(challenged=request.user),
        id=battle_id,
    )
    return JsonResponse({
        "status": battle.status,
        "challenger_done": battle.challenger_draft is not None,
        "challenged_done": battle.challenged_draft is not None,
    })


@login_required(login_url="login")
def api_vs_draft_progress(request, battle_id):
    battle = get_object_or_404(
        VsBattle,
        Q(challenger=request.user) | Q(challenged=request.user),
        id=battle_id,
        status="accepted",
    )

    if request.method == "GET":
        try:
            prog = VsDraftProgress.objects.get(battle=battle, user=request.user)
        except VsDraftProgress.DoesNotExist:
            return JsonResponse({"exists": False})
        return JsonResponse({
            "exists": True,
            "current_round": prog.current_round,
            "drafted_slots": prog.drafted_slots,
            "drafted_player_ids": prog.drafted_player_ids,
            "current_pool": prog.current_pool,
            "picked_this_round": prog.picked_this_round,
            "start_time": prog.start_time,
        })

    if request.method == "POST":
        try:
            body = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON"}, status=400)
        VsDraftProgress.objects.update_or_create(
            battle=battle,
            user=request.user,
            defaults={
                "current_round": body["current_round"],
                "drafted_slots": body["drafted_slots"],
                "drafted_player_ids": body["drafted_player_ids"],
                "current_pool": body["current_pool"],
                "picked_this_round": body["picked_this_round"],
                "start_time": body.get("start_time"),
            },
        )
        return JsonResponse({"ok": True})

    if request.method == "DELETE":
        VsDraftProgress.objects.filter(battle=battle, user=request.user).delete()
        return JsonResponse({"ok": True})

    return JsonResponse({"error": "Method not allowed"}, status=405)


def _head_to_head(user, opponent):
    """Compute head-to-head record between two users."""
    battles = VsBattle.objects.filter(
        Q(challenger=user, challenged=opponent)
        | Q(challenger=opponent, challenged=user),
        status="completed",
    ).select_related("challenger_draft", "challenged_draft").order_by("created_at")

    wins = losses = ties = 0
    today = timezone.now().date()
    today_wins = today_losses = today_ties = 0
    streak_count = 0
    streak_type = None

    for b in battles:
        my_draft = b.challenger_draft if b.challenger == user else b.challenged_draft
        opp_draft = b.challenged_draft if b.challenger == user else b.challenger_draft
        if not my_draft or not opp_draft:
            continue

        if my_draft.total_score > opp_draft.total_score:
            result = "W"
        elif my_draft.total_score < opp_draft.total_score:
            result = "L"
        elif (
            my_draft.duration_seconds is not None
            and opp_draft.duration_seconds is not None
            and my_draft.duration_seconds != opp_draft.duration_seconds
        ):
            result = "W" if my_draft.duration_seconds < opp_draft.duration_seconds else "L"
        else:
            result = "T"

        if result == "W":
            wins += 1
            if b.created_at.date() == today:
                today_wins += 1
        elif result == "L":
            losses += 1
            if b.created_at.date() == today:
                today_losses += 1
        else:
            ties += 1
            if b.created_at.date() == today:
                today_ties += 1

        if result == streak_type:
            streak_count += 1
        else:
            streak_type = result
            streak_count = 1

    overall_score = wins + ties + losses
    streak_str = f"{streak_type}{streak_count}" if streak_type else "-"

    return {
        "wins": wins,
        "losses": losses,
        "ties": ties,
        "overall": f"{wins} - {losses}",
        "today": f"{today_wins} - {today_losses}",
        "streak": streak_str,
        "total_my_score": sum(
            (b.challenger_draft if b.challenger == user else b.challenged_draft).total_score
            for b in battles
            if (b.challenger_draft and b.challenged_draft)
        ),
        "total_opp_score": sum(
            (b.challenged_draft if b.challenger == user else b.challenger_draft).total_score
            for b in battles
            if (b.challenger_draft and b.challenged_draft)
        ),
    }


GRID_CONNECTIONS = [
    (0, 1), (0, 2), (0, 3), (1, 4), (1, 5), (2, 3), (2, 6),
    (3, 6), (3, 8), (4, 5), (4, 7), (4, 8), (5, 7), (6, 8), (7, 8),
]


def _calc_slot_info(picks):
    """Calculate talent + dot bonus and dot color for each slot."""
    slots = {}
    for pick in picks:
        slots[pick.slot_index] = pick.player

    line_chems = {}
    for a, b in GRID_CONNECTIONS:
        pa, pb = slots.get(a), slots.get(b)
        if not pa or not pb:
            line_chems[(a, b)] = None
            continue
        chem = 0
        if pa.draft_year == pb.draft_year:
            chem += 1
        if pa.team.division and pb.team.division and pa.team.division == pb.team.division:
            chem += 1
        if pa.team_id == pb.team_id:
            chem += 1
        if pa.college and pb.college and pa.college.strip() and pb.college.strip() and pa.college == pb.college:
            chem += 2
        line_chems[(a, b)] = chem

    slot_info = {}
    for slot_idx, player in slots.items():
        talent = player.talent_bonus
        chem_sum = 0
        adjacent_count = 0
        for (a, b), chem in line_chems.items():
            if a != slot_idx and b != slot_idx:
                continue
            if chem is None:
                continue
            adjacent_count += 1
            chem_sum += chem

        if adjacent_count == 0:
            dot_bonus, dot_color = 0, "white"
        elif chem_sum < 2:
            dot_bonus, dot_color = 0, "red"
        elif chem_sum < 4:
            dot_bonus, dot_color = 6, "yellow"
        elif chem_sum < 6:
            dot_bonus, dot_color = 11, "green"
        else:
            dot_bonus, dot_color = 15, "blue"

        slot_info[slot_idx] = {
            "score": talent + dot_bonus,
            "dot_color": dot_color,
        }

    return slot_info


@login_required(login_url="login")
def vs_results_view(request, battle_id):
    battle = get_object_or_404(
        VsBattle,
        Q(challenger=request.user) | Q(challenged=request.user),
        id=battle_id,
        status="completed",
    )

    me = request.user
    opponent = battle.challenged if me == battle.challenger else battle.challenger
    my_draft = battle.challenger_draft if me == battle.challenger else battle.challenged_draft
    opp_draft = battle.challenged_draft if me == battle.challenger else battle.challenger_draft

    my_picks = list(
        my_draft.picks.select_related("player", "player__team").order_by("round_number")
    )
    opp_picks = list(
        opp_draft.picks.select_related("player", "player__team").order_by("round_number")
    )

    h2h = _head_to_head(me, opponent)

    my_accuracy = (
        round((my_draft.total_score / my_draft.optimal_score) * 100)
        if my_draft.optimal_score > 0 else 100
    )
    opp_accuracy = (
        round((opp_draft.total_score / opp_draft.optimal_score) * 100)
        if opp_draft.optimal_score > 0 else 100
    )

    if my_draft.total_score != opp_draft.total_score:
        i_won = my_draft.total_score > opp_draft.total_score
        is_tie = False
    elif my_draft.duration_seconds is not None and opp_draft.duration_seconds is not None and my_draft.duration_seconds != opp_draft.duration_seconds:
        # Tiebreaker: faster time wins
        i_won = my_draft.duration_seconds < opp_draft.duration_seconds
        is_tie = False
    else:
        i_won = False
        is_tie = True
    point_diff = abs(my_draft.total_score - opp_draft.total_score)

    # Compute per-slot scores and dot colors
    my_slot_info = _calc_slot_info(my_picks)
    opp_slot_info = _calc_slot_info(opp_picks)

    for pick in my_picks:
        info = my_slot_info.get(pick.slot_index, {})
        pick.slot_score = info.get("score", pick.player.talent_bonus)
        pick.dot_color = info.get("dot_color", "white")
    for pick in opp_picks:
        info = opp_slot_info.get(pick.slot_index, {})
        pick.slot_score = info.get("score", pick.player.talent_bonus)
        pick.dot_color = info.get("dot_color", "white")

    # Pair up picks by round for the draft summary
    round_pairs = []
    for i in range(len(my_picks)):
        my_p = my_picks[i] if i < len(my_picks) else None
        opp_p = opp_picks[i] if i < len(opp_picks) else None
        round_pairs.append((i + 1, my_p, opp_p))

    def fmt_time(secs):
        if secs is None:
            return "--:--"
        return f"{secs // 60}:{secs % 60:02d}"

    # Per-category winners (for green highlighting)
    my_talent_better = my_draft.talent_score > opp_draft.talent_score
    opp_talent_better = opp_draft.talent_score > my_draft.talent_score
    my_chem_better = my_draft.chemistry_score > opp_draft.chemistry_score
    opp_chem_better = opp_draft.chemistry_score > my_draft.chemistry_score

    # Time comparison (lower is better)
    my_time_secs = my_draft.duration_seconds
    opp_time_secs = opp_draft.duration_seconds
    my_time_better = (
        my_time_secs is not None and opp_time_secs is not None
        and my_time_secs < opp_time_secs
    )
    opp_time_better = (
        my_time_secs is not None and opp_time_secs is not None
        and opp_time_secs < my_time_secs
    )

    # For the banner: if scores tied, show time diff instead of point diff
    won_by_time = point_diff == 0 and not is_tie

    return render(request, "game/vs_results.html", {
        "battle": battle,
        "me": me,
        "opponent": opponent,
        "my_draft": my_draft,
        "opp_draft": opp_draft,
        "my_accuracy": my_accuracy,
        "opp_accuracy": opp_accuracy,
        "h2h": h2h,
        "i_won": i_won,
        "is_tie": is_tie,
        "point_diff": point_diff,
        "won_by_time": won_by_time,
        "round_pairs": round_pairs,
        "my_time": fmt_time(my_draft.duration_seconds),
        "opp_time": fmt_time(opp_draft.duration_seconds),
        "my_talent_better": my_talent_better,
        "opp_talent_better": opp_talent_better,
        "my_chem_better": my_chem_better,
        "opp_chem_better": opp_chem_better,
        "my_time_better": my_time_better,
        "opp_time_better": opp_time_better,
    })


# ── Quick Match (Public Lobby) ────────────────────────────────────


@login_required(login_url="login")
def quick_match_view(request):
    user = request.user
    # Count user's pending public challenges
    my_challenge_count = VsBattle.objects.filter(
        challenger=user, is_public=True, status="pending"
    ).count()
    # First page of public challenges
    public_challenges = VsBattle.objects.filter(
        is_public=True, status="pending"
    ).select_related("challenger").order_by("-created_at")[:25]

    challenges_data = []
    for c in public_challenges:
        challenges_data.append({
            "id": c.id,
            "username": c.challenger.username,
            "is_mine": c.challenger == user,
        })

    return render(request, "game/quick_match.html", {
        "challenges": challenges_data,
        "my_challenge_count": my_challenge_count,
        "pending_count": _pending_count(user),
    })


@login_required(login_url="login")
@require_POST
def api_create_public_challenge(request):
    user = request.user
    # Allow up to 3 pending public challenges at a time
    existing_count = VsBattle.objects.filter(
        challenger=user, is_public=True, status="pending"
    ).count()
    if existing_count >= 3:
        return JsonResponse(
            {"error": "You already have 3 pending public challenges"},
            status=400,
        )
    battle = VsBattle.objects.create(
        challenger=user, challenged=None, is_public=True, status="pending"
    )
    return JsonResponse({"id": battle.id})


@login_required(login_url="login")
@require_POST
def api_accept_public_challenge(request, battle_id):
    with transaction.atomic():
        battle = (
            VsBattle.objects.select_for_update()
            .filter(id=battle_id, is_public=True, status="pending")
            .first()
        )
        if not battle:
            return JsonResponse(
                {"error": "Challenge no longer available"}, status=404
            )
        if battle.challenger == request.user:
            return JsonResponse(
                {"error": "Cannot accept your own challenge"}, status=400
            )
        battle.challenged = request.user
        battle.status = "accepted"
        battle.save()
        pre_generate_rounds(battle)
    return JsonResponse({"battle_id": battle.id})


@login_required(login_url="login")
@require_POST
def api_cancel_public_challenge(request, battle_id):
    battle = get_object_or_404(
        VsBattle,
        id=battle_id,
        challenger=request.user,
        is_public=True,
        status="pending",
    )
    battle.status = "cancelled"
    battle.save()
    return JsonResponse({"status": "cancelled"})


@login_required(login_url="login")
def api_list_public_challenges(request):
    user = request.user
    offset = int(request.GET.get("offset", 0))
    limit = int(request.GET.get("limit", 25))
    challenges = VsBattle.objects.filter(
        is_public=True, status="pending"
    ).select_related("challenger").order_by("-created_at")[offset:offset + limit + 1]

    challenges = list(challenges)
    has_more = len(challenges) > limit
    challenges = challenges[:limit]

    items = []
    for c in challenges:
        items.append({
            "id": c.id,
            "username": c.challenger.username,
            "is_mine": c.challenger == user,
        })

    # Check if user's public challenge was accepted (for redirect)
    my_accepted = VsBattle.objects.filter(
        challenger=user, is_public=True, status="accepted"
    ).order_by("-created_at").first()

    return JsonResponse({
        "challenges": items,
        "has_more": has_more,
        "my_challenge_accepted": my_accepted is not None,
        "accepted_battle_id": my_accepted.id if my_accepted else None,
    })


def _battle_result(user, battle):
    """Compute W/L/T result for a completed battle from user's perspective."""
    my_draft = battle.challenger_draft if battle.challenger == user else battle.challenged_draft
    opp_draft = battle.challenged_draft if battle.challenger == user else battle.challenger_draft
    if not my_draft or not opp_draft:
        return None, 0
    if my_draft.total_score > opp_draft.total_score:
        result = "W"
    elif my_draft.total_score < opp_draft.total_score:
        result = "L"
    elif (
        my_draft.duration_seconds is not None
        and opp_draft.duration_seconds is not None
        and my_draft.duration_seconds != opp_draft.duration_seconds
    ):
        result = "W" if my_draft.duration_seconds < opp_draft.duration_seconds else "L"
    else:
        result = "T"
    point_diff = abs(my_draft.total_score - opp_draft.total_score)
    return result, point_diff


@login_required(login_url="login")
def api_battle_history(request):
    user = request.user
    offset = int(request.GET.get("offset", 0))
    limit = int(request.GET.get("limit", 25))

    battles = VsBattle.objects.filter(
        Q(challenger=user) | Q(challenged=user), status="completed"
    ).select_related(
        "challenger", "challenged", "challenger_draft", "challenged_draft"
    ).order_by("-created_at")[offset:offset + limit + 1]

    battles = list(battles)
    has_more = len(battles) > limit
    battles = battles[:limit]

    h2h_cache = {}
    items = []
    for b in battles:
        opponent = b.challenged if b.challenger == user else b.challenger
        result, point_diff = _battle_result(user, b)
        if result is None:
            continue
        if opponent.id not in h2h_cache:
            h2h_cache[opponent.id] = _head_to_head(user, opponent)
        h2h = h2h_cache[opponent.id]
        items.append({
            "id": b.id,
            "opponent": opponent.username,
            "result": result,
            "point_diff": point_diff,
            "h2h_record": f"{h2h['wins']}-{h2h['losses']}",
        })

    return JsonResponse({"items": items, "has_more": has_more})


@login_required(login_url="login")
def api_in_progress(request):
    user = request.user
    offset = int(request.GET.get("offset", 0))
    limit = int(request.GET.get("limit", 25))

    # Build unified sorted list: your_turn battles, public challenge, waiting battles, invite sent
    items = []

    active_battles_qs = VsBattle.objects.filter(
        Q(challenger=user) | Q(challenged=user), status="accepted"
    ).select_related("challenger", "challenged", "challenger_draft", "challenged_draft")

    my_turn = []
    waiting = []
    for b in active_battles_qs:
        opponent = b.challenged if b.challenger == user else b.challenger
        h2h = _head_to_head(user, opponent)
        my_draft = b.challenger_draft if b.challenger == user else b.challenged_draft
        entry = {
            "type": "your_turn" if my_draft is None else "waiting",
            "id": b.id,
            "opponent": opponent.username,
            "h2h_record": f"{h2h['wins']}-{h2h['losses']}",
            "url": f"/vs/{b.id}/draft/",
        }
        if my_draft is None:
            my_turn.append(entry)
        else:
            waiting.append(entry)

    items.extend(my_turn)

    # Public challenge
    my_public = VsBattle.objects.filter(
        challenger=user, is_public=True, status="pending"
    ).first()
    if my_public:
        items.append({
            "type": "public",
            "id": my_public.id,
            "opponent": "Open in Lobby",
            "h2h_record": "",
            "url": "/versus/quick-match/",
        })

    items.extend(waiting)

    # Invite sent (direct challenges with a target user)
    challenges_sent = VsBattle.objects.filter(
        challenger=user, status="pending", is_public=False,
        challenged__isnull=False, link_code__isnull=True,
    ).select_related("challenged")
    for c in challenges_sent:
        h2h = _head_to_head(user, c.challenged)
        items.append({
            "type": "invite_sent",
            "id": c.id,
            "opponent": c.challenged.username,
            "h2h_record": f"{h2h['wins']}-{h2h['losses']}",
            "url": None,
        })

    # Link battles (sent via link, no challenged user yet)
    link_battles = VsBattle.objects.filter(
        challenger=user, status="pending", is_public=False,
        link_code__isnull=False,
    )
    for lb in link_battles:
        items.append({
            "type": "link_sent",
            "id": lb.id,
            "opponent": "Sent via Link",
            "h2h_record": "",
            "sub_label": "Private Match",
            "url": None,
        })

    total = len(items)
    page = items[offset:offset + limit]
    has_more = (offset + limit) < total

    return JsonResponse({"items": page, "has_more": has_more})


# ── Find a Foe ──────────────────────────────────────────────────────


@login_required(login_url="login")
def find_foe_view(request):
    user = request.user
    friends = _get_friends(user)
    friend_ids = {f.id for f in friends}

    # Friends with h2h records
    friends_with_records = []
    for f in friends:
        h2h = _head_to_head(user, f)
        friends_with_records.append({
            "id": f.id,
            "username": f.username,
            "record": f"{h2h['wins']}-{h2h['losses']}",
        })

    # Top 10 rivals: non-friends ordered by number of completed battles
    battles = VsBattle.objects.filter(
        Q(challenger=user) | Q(challenged=user),
        status="completed",
    ).select_related("challenger", "challenged")

    rival_counts = {}
    for b in battles:
        opp = b.challenged if b.challenger == user else b.challenger
        if opp and opp.id not in friend_ids and opp.id != user.id:
            rival_counts[opp.id] = rival_counts.get(opp.id, 0) + 1

    top_rival_ids = sorted(rival_counts, key=rival_counts.get, reverse=True)[:10]
    rival_users = {u.id: u for u in User.objects.filter(id__in=top_rival_ids)}

    top_rivals = []
    for rank, uid in enumerate(top_rival_ids, 1):
        rival = rival_users[uid]
        h2h = _head_to_head(user, rival)
        top_rivals.append({
            "rank": rank,
            "id": rival.id,
            "username": rival.username,
            "record": f"{h2h['wins']}-{h2h['losses']}",
        })

    return render(request, "game/find_foe.html", {
        "friends_with_records": friends_with_records,
        "top_rivals": top_rivals,
        "pending_count": _pending_count(user),
    })


@login_required(login_url="login")
def api_search_users_paginated(request):
    q = request.GET.get("q", "").strip()
    offset = int(request.GET.get("offset", 0))
    limit = 50
    if len(q) < 1:
        return JsonResponse({"users": [], "has_more": False})
    users = list(
        User.objects.filter(username__icontains=q)
        .exclude(id=request.user.id)
        .values("id", "username")[offset:offset + limit + 1]
    )
    has_more = len(users) > limit
    users = users[:limit]
    return JsonResponse({"users": users, "has_more": has_more})


@login_required(login_url="login")
@require_POST
def api_challenge_any_user(request):
    body = json.loads(request.body)
    user_id = body.get("user_id")
    try:
        target = User.objects.get(id=user_id)
    except User.DoesNotExist:
        return JsonResponse({"error": "User not found"}, status=404)
    if target == request.user:
        return JsonResponse({"error": "Cannot challenge yourself"}, status=400)
    pending = VsBattle.objects.filter(
        challenger=request.user, challenged=target,
        status="pending", is_public=False,
    ).count()
    if pending >= 3:
        return JsonResponse(
            {"error": "You already have 3 pending invites to this user"},
            status=400,
        )
    battle = VsBattle.objects.create(
        challenger=request.user, challenged=target, is_public=False,
    )
    return JsonResponse({"id": battle.id})


# ── Send Link ───────────────────────────────────────────────────────


@login_required(login_url="login")
@require_POST
def api_create_link_battle(request):
    # Only allow 1 pending link invite at a time
    existing = VsBattle.objects.filter(
        challenger=request.user, status="pending",
        is_public=False, link_code__isnull=False,
    ).exists()
    if existing:
        return JsonResponse(
            {"error": "You already have a pending link invite"},
            status=400,
        )
    from django.utils.crypto import get_random_string
    chars = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    code = get_random_string(6, chars)
    while VsBattle.objects.filter(link_code=code).exists():
        code = get_random_string(6, chars)
    battle = VsBattle.objects.create(
        challenger=request.user,
        challenged=None,
        is_public=False,
        status="pending",
        link_code=code,
    )
    return JsonResponse({"id": battle.id, "link_code": code})


@login_required(login_url="login")
@require_POST
def api_cancel_challenge(request, battle_id):
    """Cancel any pending challenge (public, private, or link) owned by the user."""
    battle = get_object_or_404(
        VsBattle, id=battle_id, challenger=request.user, status="pending",
    )
    battle.status = "cancelled"
    battle.save()
    return JsonResponse({"status": "cancelled"})


# ── Join via Link ───────────────────────────────────────────────────


def join_via_link_view(request, code):
    battle = VsBattle.objects.filter(link_code=code).first()
    if not battle or battle.status != "pending" or battle.challenged is not None:
        return render(request, "game/link_expired.html")

    user = request.user

    if not user.is_authenticated:
        from django.utils.crypto import get_random_string
        guest_username = f"guest_{get_random_string(6, 'abcdefghijklmnopqrstuvwxyz0123456789')}"
        guest_user = User.objects.create_user(username=guest_username)
        guest_user.set_unusable_password()
        guest_user.save()
        login(request, guest_user)
        request.session["is_guest_user"] = True
        user = guest_user

    if battle.challenger == user:
        return redirect("quick_match")

    with transaction.atomic():
        battle_locked = (
            VsBattle.objects.select_for_update()
            .filter(id=battle.id, status="pending", challenged__isnull=True)
            .first()
        )
        if not battle_locked:
            return render(request, "game/link_expired.html")
        battle_locked.challenged = user
        battle_locked.status = "accepted"
        battle_locked.save()
        pre_generate_rounds(battle_locked)

    return redirect("vs_draft", battle_id=battle.id)
