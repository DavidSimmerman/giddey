from django.contrib import admin

from .models import Friendship, Player, Team, VsBattle, VsBattleRound

admin.site.register(Team)
admin.site.register(Player)
admin.site.register(Friendship)
admin.site.register(VsBattle)
admin.site.register(VsBattleRound)
