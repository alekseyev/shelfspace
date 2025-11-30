from datetime import date, datetime

from loguru import logger
import requests

from shelfspace.apis.base import BaseAPI
from shelfspace.estimations import estimate_episode, estimation_from_minutes
from shelfspace.models import LegacyEntry, MediaType, Status
from shelfspace.cache import cache


class TraktAPI(BaseAPI):
    base_url = "https://api.trakt.tv"

    def __init__(
        self,
        client_id: str,
        access_token: str,
        refresh_token: str | None = None,
        client_secret: str | None = None,
    ):
        self.client_id = client_id
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.client_secret = client_secret

    def _refresh_token(self) -> dict:
        # https://trakt.docs.apiary.io/#reference/authentication-oauth/get-token
        response = requests.post(
            f"{self.base_url}/oauth/token",
            {
                "refresh_token": self.refresh_token,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "redirect_uri": "urn:ietf:wg:oauth:2.0:oob",
                "grant_type": "refresh_token",
            },
            headers={"Content-Type": "application/json"},
        )

        if response.status_code != 200:
            raise Exception(
                f"Failed to refresh Trakt API tokens: {response.status_code} {response.text}"
            )

        return response.json()

    def _headers(self) -> dict:
        return {
            "Content-type": "application/json",
            "trakt-api-key": self.client_id,
            "trakt-api-version": "2",
            "Authorization": f"Bearer {self.access_token}",
        }

    def _handle_token_refresh(self) -> None:
        """Refresh access token and update instance with new tokens."""
        tokens = self._refresh_token()
        self.access_token = tokens["access_token"]
        self.refresh_token = tokens["refresh_token"]
        logger.info("Trakt API tokens refreshed successfully")

    def _get_tokens(self) -> dict:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
        }

    def _make_request_with_retry(self, method: str, url: str, **kwargs) -> dict:
        """Make HTTP request with automatic token refresh on 401.

        Args:
            method: HTTP method ('get' or 'post')
            url: API endpoint URL
            **kwargs: Additional arguments for requests method

        Returns:
            JSON response from API
        """
        if "headers" not in kwargs or kwargs["headers"] is None:
            kwargs["headers"] = self._headers()

        full_url = self.base_url + url
        request_method = getattr(requests, method)
        response = request_method(full_url, **kwargs)

        if response.status_code == 401:
            self._handle_token_refresh()
            kwargs["headers"] = self._headers()
            response = request_method(full_url, **kwargs)

        if response.status_code != 200:
            raise Exception(
                f"Failed to make {method} request to {full_url}: {response.status_code} {response.text}"
            )

        return response.json()

    def _get(self, url: str, params: dict = {}, headers: dict | None = None) -> dict:
        """Override _get to handle 401 errors with token refresh."""
        return self._make_request_with_retry("get", url, headers=headers, params=params)

    def _post(self, url: str, params: dict = {}, headers: dict | None = None) -> dict:
        """Override _post to handle 401 errors with token refresh."""
        return self._make_request_with_retry("post", url, headers=headers, json=params)

    def watchlist_movies_legacy(self) -> list[LegacyEntry]:
        data = self._get("/users/me/watchlist")
        results = []
        for item in data:
            if item["type"] != "movie":
                continue

            item_id = item["movie"]["ids"]["trakt"]
            movie_data = self.get_movie_data(item_id)
            if movie_data["release_date"] and movie_data["release_date"] > "2025":
                release_date = movie_data["release_date"]
            elif item["movie"]:
                release_date = str(item["movie"]["year"])
            else:
                release_date = None

            results.append(
                LegacyEntry(
                    type=MediaType.MOVIE,
                    name=item["movie"]["title"],
                    estimated=estimation_from_minutes(int(movie_data["runtime"]))
                    if movie_data["runtime"]
                    else None,
                    spent=None,
                    status=Status.FUTURE,
                    release_date=release_date,
                    rating=int(movie_data["rating"]),
                    metadata={
                        "trakt_id": item_id,
                        "trakt_status": movie_data["status"],
                    },
                )
            )

        return results

    def get_movies(self, list_slug: str | None = None) -> list[dict]:
        """Fetch movies from a list or watchlist.

        Args:
            list_slug: Custom list slug. If None, fetches from watchlist.
        """
        if list_slug:
            data = self._get(f"/users/me/lists/{list_slug}/items")
        else:
            data = self._get("/users/me/watchlist")

        results = []
        for item in data:
            if item["type"] != "movie":
                continue

            results.append(
                dict(
                    name=item["movie"]["title"],
                    trakt_id=item["movie"]["ids"]["trakt"],
                    slug=item["movie"]["ids"]["slug"],
                )
            )

        return results

    def get_shows(self, list_slug: str | None = None) -> list[dict]:
        """Fetch shows from a list or watchlist.

        Args:
            list_slug: Custom list slug. If None, fetches from watchlist.
        """
        if list_slug:
            data = self._get(f"/users/me/lists/{list_slug}/items")
        else:
            data = self._get("/users/me/watchlist")

        results = []
        for item in data:
            if item["type"] != "show":
                continue

            results.append(
                dict(
                    name=item["show"]["title"],
                    trakt_id=item["show"]["ids"]["trakt"],
                    slug=item["show"]["ids"]["slug"],
                )
            )

        return results

    def get_movie_data(self, movie_id: str) -> dict:
        cache_key = f"trakt:movie:{movie_id}"
        if cache_key in cache:
            return cache[cache_key]

        movie_data = self._get(f"/movies/{movie_id}", {"extended": "full"})

        data = {
            "title": movie_data["title"],
            "release_date": movie_data["released"],
            "runtime": movie_data["runtime"],
            "status": movie_data["status"],
            "rating": movie_data["rating"] * 10,
        }

        cache[cache_key] = data

        return data

    def watchlist_series(self) -> list[LegacyEntry]:
        data = self._get("/users/me/watchlist")
        results = []
        for item in data:
            if item["type"] != "show":
                continue

            item_id = item["show"]["ids"]["trakt"]
            item_title = item["show"]["title"]

            seasons = self.get_series_data(item_id)
            for season in seasons:
                release_date = None
                if not season["number"]:
                    continue
                if not season["episodes"] or not season["episodes"][0]["first_aired"]:
                    continue
                aired_date = datetime.fromisoformat(
                    season["episodes"][0]["first_aired"][:-1]
                )
                if aired_date > datetime(2022, 1, 1):
                    release_date = aired_date.strftime("%Y-%m-%d")
                else:
                    release_date = str(aired_date.year)
                if len(seasons) > 1:
                    item_title = f"{item['show']['title']} S{season['number']}"
                results.append(
                    LegacyEntry(
                        type=MediaType.SERIES,
                        name=item_title,
                        estimated=season["total_est"] or None,
                        status=Status.FUTURE,
                        release_date=release_date,
                        metadata={
                            "trakt_id": item_id,
                        },
                    )
                )

        return results

    def get_seasons_summary(self, show_id: str) -> list[dict]:
        """Fetch season metadata without episode details.

        Returns list of dicts with season number and episode count.
        """
        cache_key = f"trakt:show_seasons:{show_id}"
        if cache_key in cache:
            return cache[cache_key]

        seasons_data = self._get(f"/shows/{show_id}/seasons", {"extended": "full"})
        result = [
            {"number": s["number"], "episode_count": s["episode_count"]}
            for s in seasons_data
        ]

        cache[cache_key] = result
        return result

    def get_season_episodes(self, show_id: str, season_number: int) -> list[dict]:
        """Fetch all episodes for a season in a single API call.

        Returns list of episode dicts with number, runtime, est, first_aired.
        """
        cache_key = f"trakt:show_season:{show_id}:{season_number}"
        if cache_key in cache:
            return cache[cache_key]

        episodes_data = self._get(
            f"/shows/{show_id}/seasons/{season_number}", {"extended": "full"}
        )

        episodes = []
        for ep in episodes_data:
            est = estimate_episode(ep["runtime"]) if ep["runtime"] else 0
            episodes.append(
                {
                    "number": ep["number"],
                    "runtime": ep["runtime"],
                    "est": est / 60,
                    "first_aired": ep["first_aired"],
                }
            )

        cache[cache_key] = episodes
        return episodes

    def get_episode_data(
        self, show_id: str, season_number: int, episode_number: int
    ) -> dict:
        """Fetch data for a single episode."""
        cache_key = f"trakt:episode:{show_id}:{season_number}:{episode_number}"
        if cache_key in cache:
            return cache[cache_key]

        episode_data = self._get(
            f"/shows/{show_id}/seasons/{season_number}/episodes/{episode_number}",
            {"extended": "full"},
        )

        est = (
            estimate_episode(episode_data["runtime"]) if episode_data["runtime"] else 0
        )
        result = {
            "number": episode_number,
            "runtime": episode_data["runtime"],
            "est": est / 60,
            "first_aired": episode_data["first_aired"],
        }

        cache[cache_key] = result
        return result

    def get_series_data(self, show_id: str) -> list[dict]:
        """Fetch full series data with all episodes.

        Uses batch endpoint for episodes (1 API call per season instead of per episode).
        """
        cache_key = f"trakt:show:{show_id}"
        if cache_key in cache:
            return cache[cache_key]

        result = []
        seasons = self.get_seasons_summary(show_id)

        for season in seasons:
            season_number = season["number"]
            episodes = self.get_season_episodes(show_id, season_number)

            total_est = sum(ep["est"] for ep in episodes)

            result.append(
                {
                    "number": season_number,
                    "total_est": total_est,
                    "episodes": episodes,
                }
            )

        cache[cache_key] = result

        return result

    def get_upcoming_episodes(self, days=49) -> list[dict]:
        calendar_data = self._get(
            f"/calendars/my/shows/{date.today().strftime('%Y-%m-%d')}/{days}"
        )

        result = []
        for item in calendar_data:
            result.append(
                {
                    "title": item["show"]["title"],
                    "show_trakt_id": item["show"]["ids"]["trakt"],
                    "show_slug": item["show"]["ids"]["slug"],
                    "first_aired": item["first_aired"],
                    "season": item["episode"]["season"],
                    "episode": item["episode"]["number"],
                    "runtime": item["episode"]["runtime"] or item["show"]["runtime"],
                }
            )

        return result
