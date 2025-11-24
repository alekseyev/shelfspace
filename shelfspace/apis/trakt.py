import logging
from datetime import date, datetime

import requests

from shelfspace.apis.base import BaseAPI
from shelfspace.estimations import estimate_episode, estimation_from_minutes
from shelfspace.models import Entry, LegacyEntry, MediaType, Status
from shelfspace.cache import cache


logger = logging.getLogger(__name__)


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
        data = self._post(
            "/oauth/token",
            {
                "refresh_token": self.refresh_token,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "redirect_uri": "urn:ietf:wg:oauth:2.0:oob",
                "grant_type": "refresh_token",
            },
            headers={"Content-Type": "application/json"},
        )

        return {
            "access_token": data["access_token"],
            "refresh_token": data["refresh_token"],
        }

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

    def watchlist_movies(self) -> list[Entry]:
        data = self._get("/users/me/watchlist")
        results = []
        for item in data:
            if item["type"] != "movie":
                continue

            item_id = item["movie"]["ids"]["trakt"]
            movie_data = self.get_movie_data(item_id)
            if movie_data["release_date"]:
                release_date = datetime.fromisoformat(movie_data["release_date"])
            else:
                release_date = None

            results.append(
                Entry(
                    type=MediaType.MOVIE,
                    name=item["movie"]["title"],
                    estimated=int(movie_data["runtime"])
                    if movie_data["runtime"]
                    else None,
                    spent=None,
                    status=Status.FUTURE,
                    release_date=release_date,
                    rating=int(movie_data["rating"]),
                    metadata={
                        "trakt_id": item_id,
                    },
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

    def get_series_data(self, show_id: str) -> list[dict]:
        cache_key = f"trakt:show:{show_id}"
        if cache_key in cache:
            return cache[cache_key]

        result = []
        seasons_data = self._get(f"/shows/{show_id}/seasons", {"extended": "full"})

        for season in seasons_data:
            season_number = season["number"]
            episode_count = season["episode_count"]

            episodes = []
            total_est = 0
            for ep_index in range(1, episode_count + 1):
                episode_data = self._get(
                    f"/shows/{show_id}/seasons/{season_number}/episodes/{ep_index}",
                    {"extended": "full"},
                )
                est = (
                    estimate_episode(episode_data["runtime"])
                    if episode_data["runtime"]
                    else 0
                )
                episodes.append(
                    {
                        "runtime": episode_data["runtime"],
                        "est": est / 60,
                        "first_aired": episode_data["first_aired"],
                    }
                )
                total_est += est

            result.append(
                {
                    "number": season_number,
                    "total_est": total_est / 60,
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
                    "first_aired": item["first_aired"],
                    "season": item["episode"]["season"],
                    "episode": item["episode"]["number"],
                    "runtime": item["episode"]["runtime"] or item["show"]["runtime"],
                }
            )

        return result
