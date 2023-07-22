import os
from fake_useragent import UserAgent

from shelfspace.apis.base import BaseAPI
from shelfspace.models import Entry, MediaType, Status


class HowlongAPI(BaseAPI):
    base_url = "https://howlongtobeat.com/api"
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
        lists = [
                    "playing",
                    "replays",
                    "backlog",
                    "completed",
                    "retired"
                ]
        game_list_result = self._post(
            f"/user/{self.user_id}/games/list",
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
            cached=True,
        )

        results = []
        for game in game_list_result["data"]["gamesList"]:
            game_type = MediaType.GAME
            game_status = Status.DONE
            if game["list_playing"] or game["list_replay"]:
                game_status = Status.CURRENT
            elif game["list_backlog"]:
                game_status = Status.FUTURE
            if game["platform"] == "PC VR":
                game_type = MediaType.GAME_VR
            results.append(
                Entry(
                    type=game_type,
                    name=game["custom_title"],
                    status=game_status,
                    estimated=None,
                    spent=None,
                    notes=f"HLTB: {game['review_score_g']}",                
                )
            )

        return results
