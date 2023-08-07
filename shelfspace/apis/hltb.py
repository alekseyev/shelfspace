import datetime
import os
from fake_useragent import UserAgent

from shelfspace.apis.base import BaseAPI
from shelfspace.cache import cache
from shelfspace.estimations import estimate_from_hltb
from shelfspace.models import Entry, MediaType, Status


class HowlongAPI(BaseAPI):
    base_url = "https://howlongtobeat.com"
    user_id = os.environ.get("HLTB_USER_ID")

    def _headers(self):
        ua = UserAgent()
        return {
            "content-type": "application/json",
            "accept": "*/*",
            "User-Agent": ua.random.strip(),
            "referer": self.base_url,
        }

    def get_games_list(self) -> list[Entry]:
        lists = ["playing", "replays", "backlog", "completed", "retired"]
        game_list_result = self._post(
            f"/api/user/{self.user_id}/games/list",
            {
                "user_id": self.user_id,
                "lists": lists,
                "set_playstyle": "comp_all_h",
                "name": "",
                "platform": "",
                "storefront": "",
                "sortBy": "",
                "sortFlip": 0,
                "view": "",
                "random": 0,
                "limit": 1000,
                "currentUserHome": False,
            },
        )

        results = []
        for game in game_list_result["data"]["gamesList"]:
            game_type = MediaType.GAME
            if not game["list_backlog"] or game["list_playing"]:
                continue
            game_status = Status.CURRENT
            if game["list_backlog"]:
                game_status = Status.FUTURE
            if game["platform"] == "PC VR":
                game_type = MediaType.GAME_VR
            elif game["platform"] == "Mobile":
                game_type = MediaType.GAME_MOBILE
            game_data = self.get_game_data(game["game_id"])
            release_date = game_data["release_date"]
            results.append(
                Entry(
                    type=game_type,
                    name=game["custom_title"],
                    status=game_status,
                    estimated=game_data["estimation"],
                    spent=None,
                    notes=f"HLTB: {game['review_score_g']}",
                    release_date=release_date,
                )
            )

        return results

    def get_game_data(self, game_id) -> dict:
        cache_key = f"hltb:{game_id}"
        if cache_key in cache:
            return cache[cache_key]

        game_data = self._get(f"/_next/data/PulRqjuI9R3KSc-k9tS7i/game/{game_id}.json")
        game_data = game_data["pageProps"]["game"]["data"]["game"][0]
        estimation = None
        if val := game_data.get("comp_plus_avg"):
            estimation = estimate_from_hltb(val)

        data = {
            "platforms": game_data["profile_platform"].split(", "),
            "release_date": game_data["release_world"],
            "estimation": estimation,
        }
        cache[cache_key] = data

        return data
