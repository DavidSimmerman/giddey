import re
import time

import requests
from bs4 import BeautifulSoup
from django.core.management.base import BaseCommand

from game.models import Player, Team

CURRENT_SEASON_YEAR = 2026

BASE_URL = "https://www.2kratings.com"

NBA_TEAMS = {
    "hawks": {"city": "Atlanta", "name": "Hawks", "division": "Southeast"},
    "celtics": {"city": "Boston", "name": "Celtics", "division": "Atlantic"},
    "nets": {"city": "Brooklyn", "name": "Nets", "division": "Atlantic"},
    "hornets": {"city": "Charlotte", "name": "Hornets", "division": "Southeast"},
    "bulls": {"city": "Chicago", "name": "Bulls", "division": "Central"},
    "cavaliers": {"city": "Cleveland", "name": "Cavaliers", "division": "Central"},
    "mavericks": {"city": "Dallas", "name": "Mavericks", "division": "Southwest"},
    "nuggets": {"city": "Denver", "name": "Nuggets", "division": "Northwest"},
    "pistons": {"city": "Detroit", "name": "Pistons", "division": "Central"},
    "warriors": {"city": "Golden State", "name": "Warriors", "division": "Pacific"},
    "rockets": {"city": "Houston", "name": "Rockets", "division": "Southwest"},
    "pacers": {"city": "Indiana", "name": "Pacers", "division": "Central"},
    "clippers": {"city": "Los Angeles", "name": "Clippers", "division": "Pacific"},
    "lakers": {"city": "Los Angeles", "name": "Lakers", "division": "Pacific"},
    "grizzlies": {"city": "Memphis", "name": "Grizzlies", "division": "Southwest"},
    "heat": {"city": "Miami", "name": "Heat", "division": "Southeast"},
    "bucks": {"city": "Milwaukee", "name": "Bucks", "division": "Central"},
    "timberwolves": {"city": "Minnesota", "name": "Timberwolves", "division": "Northwest"},
    "pelicans": {"city": "New Orleans", "name": "Pelicans", "division": "Southwest"},
    "knicks": {"city": "New York", "name": "Knicks", "division": "Atlantic"},
    "thunder": {"city": "Oklahoma City", "name": "Thunder", "division": "Northwest"},
    "magic": {"city": "Orlando", "name": "Magic", "division": "Southeast"},
    "sixers": {"city": "Philadelphia", "name": "76ers", "division": "Atlantic"},
    "76ers": {"city": "Philadelphia", "name": "76ers", "division": "Atlantic"},
    "suns": {"city": "Phoenix", "name": "Suns", "division": "Pacific"},
    "blazers": {"city": "Portland", "name": "Trail Blazers", "division": "Northwest"},
    "kings": {"city": "Sacramento", "name": "Kings", "division": "Pacific"},
    "spurs": {"city": "San Antonio", "name": "Spurs", "division": "Southwest"},
    "raptors": {"city": "Toronto", "name": "Raptors", "division": "Atlantic"},
    "jazz": {"city": "Utah", "name": "Jazz", "division": "Northwest"},
    "wizards": {"city": "Washington", "name": "Wizards", "division": "Southeast"},
}


class Command(BaseCommand):
    help = "Scrape NBA player and team data from 2kratings.com"

    def handle(self, *args, **options):
        self.scraper = requests.Session()
        self.scraper.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        })

        # Step 1: Scrape the current-teams page table for the 30 team links
        self.stdout.write("Fetching current team links...")
        team_links = self.get_team_links()
        self.stdout.write(f"  Found {len(team_links)} teams")

        # Clear existing data
        Player.objects.all().delete()
        Team.objects.all().delete()

        # Step 3 & 4: Scrape each team and its players
        for i, team_url in enumerate(team_links, 1):
            self.stdout.write(f"\n[{i}/{len(team_links)}] Scraping {team_url}")
            try:
                self.scrape_team(team_url, NBA_TEAMS)
            except Exception as e:
                self.stderr.write(f"  ERROR scraping team: {e}")
            time.sleep(1)

        self.stdout.write(
            self.style.SUCCESS(
                f"\nDone! Teams: {Team.objects.count()}, "
                f"Players: {Player.objects.count()}"
            )
        )

    def get_team_links(self):
        """Get the 30 current team links from the table on the current-teams page."""
        r = self.scraper.get(f"{BASE_URL}/current-teams")
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        table = soup.find("table")
        if not table:
            raise RuntimeError("Could not find teams table")

        links = set()
        for a in table.select("a[href*='/teams/']"):
            href = a.get("href", "")
            if not href.startswith("http"):
                href = BASE_URL + href
            links.add(href)

        return sorted(links)

    def scrape_team(self, team_url, nba_teams):
        """Scrape a team page and all its players."""
        r = self.scraper.get(team_url)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        # Team name from h1
        h1 = soup.find("h1")
        full_name = h1.get_text(strip=True) if h1 else ""
        self.stdout.write(f"  Team: {full_name}")

        # Team logo - match the full team name in the data-src URL
        # e.g. "atlanta-hawks" -> "Atlanta-Hawks" in the logo filename
        logo_url = ""
        team_slug = team_url.split("/teams/")[-1].rstrip("/")
        # Convert slug to title case for URL matching (atlanta-hawks -> Atlanta-Hawks)
        slug_title = "-".join(w.capitalize() for w in team_slug.split("-"))
        for img in soup.select("img[data-src]"):
            data_src = img.get("data-src", "")
            if slug_title in data_src and "Logo" in data_src:
                logo_url = data_src
                if not logo_url.startswith("http"):
                    logo_url = BASE_URL + logo_url
                break

        # City and division from NBA API
        # NBA API uses short slugs (e.g. "thunder") while 2kratings uses
        # full slugs (e.g. "oklahoma-city-thunder"), so match by checking
        # the slug ends with "-{api_slug}" or equals it exactly
        nba_info = {}
        for api_slug, info in nba_teams.items():
            if team_slug == api_slug or team_slug.endswith(f"-{api_slug}"):
                nba_info = info
                break
        city = nba_info.get("city", "")
        division = nba_info.get("division", "")
        name = nba_info.get("name", "")

        # Fallback: split h1 into city/name if NBA API didn't have it
        if not city and full_name:
            parts = full_name.split()
            # Most teams: last word is the name, rest is the city
            # Exceptions like "Trail Blazers", "76ers" handled by NBA API
            name = parts[-1]
            city = " ".join(parts[:-1])

        team = Team.objects.create(
            city=city,
            name=name,
            division=division,
            logo_image=logo_url,
        )

        # Player links from the roster table
        roster_heading = soup.find(
            string=re.compile(r"team roster", re.IGNORECASE)
        )
        if not roster_heading:
            self.stderr.write("  Could not find roster table")
            return

        table = roster_heading.find_parent().find_next("table")
        if not table:
            self.stderr.write("  Could not find roster table element")
            return

        player_urls = set()
        for a in table.select("a[href]"):
            href = a.get("href", "")
            if not href.startswith("http"):
                href = BASE_URL + href
            # Player links look like https://www.2kratings.com/player-name
            # Skip links to /lists/, /countries/, /teams/ etc
            if "/lists/" in href or "/countries/" in href or "/teams/" in href:
                continue
            if re.match(r"^https://www\.2kratings\.com/[\w-]+$", href):
                player_urls.add(href)

        self.stdout.write(f"  Found {len(player_urls)} players")

        for j, player_url in enumerate(sorted(player_urls), 1):
            try:
                self.scrape_player(player_url, team)
            except Exception as e:
                self.stderr.write(
                    f"    ERROR scraping player {player_url}: {e}"
                )
            time.sleep(1)

    def scrape_player(self, player_url, team):
        """Scrape a player page and create a Player object."""
        r = self.scraper.get(player_url)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        text = soup.get_text()

        # Name from h1
        h1 = soup.find("h1")
        full_name = h1.get_text(strip=True) if h1 else ""
        name_parts = full_name.split(None, 1)
        first_name = name_parts[0] if name_parts else ""
        last_name = name_parts[1] if len(name_parts) > 1 else ""

        # Player image from .profile-photo class
        image_url = ""
        profile_img = soup.select_one("img.profile-photo")
        if profile_img:
            image_url = profile_img.get("src", "")

        # Position
        position = ""
        pos_match = re.search(
            r"Position:\s*((?:PG|SG|SF|PF|C)\s*(?:/\s*(?:PG|SG|SF|PF|C))?)",
            text,
        )
        if pos_match:
            position = pos_match.group(1).strip()

        # Rating
        rating = 0
        rating_match = re.search(r"(\d{2})\s*OVERA", text)
        if rating_match:
            rating = int(rating_match.group(1))

        # College / Prior to NBA
        college = ""
        prior_match = re.search(
            r"Prior to\s+NBA:\s*(.+?)(?:Compare|Badges|\d{2}\s*OVERA)",
            text,
            re.DOTALL,
        )
        if prior_match:
            college = prior_match.group(1).strip()

        # Draft year from years in the NBA
        draft_year = 0
        years_match = re.search(r"Year\(s\) in the NBA:\s*(\d+)", text)
        if years_match:
            years_in_nba = int(years_match.group(1))
            draft_year = CURRENT_SEASON_YEAR - years_in_nba

        Player.objects.create(
            first_name=first_name,
            last_name=last_name,
            image=image_url,
            position=position,
            team=team,
            draft_year=draft_year,
            rating=rating,
            college=college,
        )
        self.stdout.write(
            f"    {first_name} {last_name} | {position} | {rating} OVR | {college}"
        )
