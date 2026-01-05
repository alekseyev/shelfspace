"""Steam API client for fetching game data and playtime."""

import aiohttp
from shelfspace.apis.base import BaseAPI
from shelfspace.settings import settings


class SteamAPI(BaseAPI):
    """Steam API client."""

    base_url = "http://api.steampowered.com"

    def __init__(self):
        super().__init__()
        self.api_key = settings.STEAM_API_KEY
        self.steam_id = settings.STEAM_USER_ID

    async def get_owned_games(self) -> list[dict]:
        """
        Get all owned games with playtime for the configured Steam user.

        Returns:
            List of dicts with keys:
                - appid (int): Steam app ID
                - name (str): Game name
                - playtime_forever (int): Total playtime in minutes
                - playtime_2weeks (int): Playtime in last 2 weeks in minutes
                - img_icon_url (str): Icon URL
                - img_logo_url (str): Logo URL
        """
        url = f"{self.base_url}/IPlayerService/GetOwnedGames/v0001/"
        params = {
            "key": self.api_key,
            "steamid": self.steam_id,
            "include_appinfo": 1,
            "include_played_free_games": 1,
            "format": "json",
        }

        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as response:
                data = await response.json()
                return data.get("response", {}).get("games", [])

    async def get_game_details(self, appid: int) -> dict | None:
        """
        Get detailed information about a game from Steam store.

        Args:
            appid: Steam app ID

        Returns:
            Dict with game details or None if not found
        """
        url = "https://store.steampowered.com/api/appdetails"
        params = {"appids": appid}

        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as response:
                data = await response.json()
                if str(appid) in data and data[str(appid)].get("success"):
                    return data[str(appid)].get("data")
                return None
