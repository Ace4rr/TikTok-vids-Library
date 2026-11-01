"""Роутеры API. Собираются в main.create_app."""

from . import events, inbox, media, search, system, taxonomy, videos

all_routers = [inbox.router, videos.router, taxonomy.router, media.router, search.router, system.router, events.router]
