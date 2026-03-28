from django.contrib import admin
from django.urls import path

from game import views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', views.home, name='home'),
    path('draft/', views.draft, name='draft'),
    path('api/random-players/', views.api_random_players, name='api_random_players'),
    path('login/', views.login_view, name='login'),
    path('guest/', views.guest_view, name='guest'),
    path('logout/', views.logout_view, name='logout'),
    path('api/save-draft/', views.api_save_draft, name='api_save_draft'),
    path('api/draft-progress/', views.api_draft_progress, name='api_draft_progress'),
    path('stats/', views.stats_view, name='stats'),
    path('history/', views.history_view, name='history'),
    path('history/<int:draft_id>/', views.draft_detail_view, name='draft_detail'),
    # Versus
    path('versus/', views.friends_view, name='versus'),
    path('versus/quick-match/', views.quick_match_view, name='quick_match'),
    path('versus/find-foe/', views.find_foe_view, name='find_foe'),
    path('challenge/join/<str:code>/', views.join_via_link_view, name='join_via_link'),
    # Friends API
    path('api/friend-request/', views.api_send_friend_request, name='api_send_friend_request'),
    path('api/friend-request/<int:friendship_id>/respond/', views.api_respond_friend_request, name='api_respond_friend_request'),
    path('api/friend/<int:user_id>/remove/', views.api_remove_friend, name='api_remove_friend'),
    path('api/search-users/', views.api_search_users, name='api_search_users'),
    path('api/search-users-paginated/', views.api_search_users_paginated, name='api_search_users_paginated'),
    # VS Battle
    path('api/challenge/', views.api_send_challenge, name='api_send_challenge'),
    path('api/challenge-any/', views.api_challenge_any_user, name='api_challenge_any_user'),
    path('api/challenge/<int:battle_id>/respond/', views.api_respond_challenge, name='api_respond_challenge'),
    path('vs/<int:battle_id>/draft/', views.vs_draft_view, name='vs_draft'),
    path('vs/<int:battle_id>/results/', views.vs_results_view, name='vs_results'),
    path('api/vs/<int:battle_id>/random-players/', views.api_vs_random_players, name='api_vs_random_players'),
    path('api/vs/<int:battle_id>/save-draft/', views.api_vs_save_draft, name='api_vs_save_draft'),
    path('api/vs/<int:battle_id>/status/', views.api_vs_status, name='api_vs_status'),
    path('api/vs/<int:battle_id>/draft-progress/', views.api_vs_draft_progress, name='api_vs_draft_progress'),
    # Quick Match (Public Lobby)
    path('api/public-challenge/', views.api_create_public_challenge, name='api_create_public_challenge'),
    path('api/public-challenge/<int:battle_id>/accept/', views.api_accept_public_challenge, name='api_accept_public_challenge'),
    path('api/public-challenge/<int:battle_id>/cancel/', views.api_cancel_public_challenge, name='api_cancel_public_challenge'),
    path('api/public-challenges/', views.api_list_public_challenges, name='api_list_public_challenges'),
    path('api/battle-history/', views.api_battle_history, name='api_battle_history'),
    path('api/in-progress/', views.api_in_progress, name='api_in_progress'),
    path('api/create-link-battle/', views.api_create_link_battle, name='api_create_link_battle'),
    path('api/cancel-challenge/<int:battle_id>/', views.api_cancel_challenge, name='api_cancel_challenge'),
]
