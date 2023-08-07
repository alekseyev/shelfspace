from datetime import datetime
import os
from shelfspace.apis.base import BaseAPI
from shelfspace.estimations import estimate_episode, estimation_from_minutes
from shelfspace.models import Entry, MediaType, Status
from shelfspace.cache import cache


class TraktAPI(BaseAPI):
    base_url = "https://api.trakt.tv"
    client_id = os.environ.get("TRAKT_CLIENT_ID")
    access_token = os.environ.get("TRAKT_ACCESS_TOKEN")

    def refresh_token(self) -> dict:
        # https://trakt.docs.apiary.io/#reference/authentication-oauth/get-token
        data = self._post(
            "/oauth/token",
            {
                "refresh_token": os.environ.get("TRAKT_REFRESH_TOKEN"),
                "client_id": os.environ.get("TRAKT_CLIENT_ID"),
                "client_secret": os.environ.get("TRAKT_CLIENT_SECRET"),
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

    def watchlist_movies(self):
        data = self._get("/users/me/watchlist")
        results = []
        for item in data:
            if item["type"] != "movie":
                continue

            item_id = item["movie"]["ids"]["trakt"]
            movie_data = self.get_movie_data(item_id)
            if movie_data["release_date"] and movie_data["release_date"] > "2022":
                release_date = movie_data["release_date"]
            elif item["movie"]:
                release_date = item["movie"]["year"]
            else:
                release_date = None

            results.append(
                Entry(
                    type=MediaType.MOVIE,
                    name=item["movie"]["title"],
                    estimated=estimation_from_minutes(int(movie_data["runtime"]))
                    if movie_data["runtime"]
                    else None,
                    spent=None,
                    status=Status.FUTURE,
                    release_date=release_date,
                    rating=movie_data["rating"],
                    metadata={
                        "trakt_id": item_id,
                        "trakt_status": movie_data["status"],
                    },
                )
            )

        return results

    def get_movie_data(self, movie_id: str):
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

    def watchlist_series(self):
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
                    Entry(
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
