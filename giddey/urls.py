from django.contrib import admin
from django.urls import path

from game import views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', views.home, name='home'),
    path('draft/', views.draft, name='draft'),
    path('api/random-players/', views.api_random_players, name='api_random_players'),
]
