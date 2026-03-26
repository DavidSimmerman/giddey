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
    path('history/', views.history_view, name='history'),
    path('history/<int:draft_id>/', views.draft_detail_view, name='draft_detail'),
]
