import importlib
import sys
import types
import unittest
from unittest.mock import Mock, patch


def import_trakt_module():
    sys.modules.setdefault("loguru", types.SimpleNamespace(logger=Mock()))
    sys.modules.setdefault(
        "requests",
        types.SimpleNamespace(get=Mock(), post=Mock(), Response=object),
    )
    sys.modules.setdefault(
        "shelfspace.estimations",
        types.SimpleNamespace(estimate_episode=Mock(), estimation_from_minutes=Mock()),
    )
    sys.modules.setdefault(
        "shelfspace.models",
        types.SimpleNamespace(
            LegacyEntry=Mock(),
            MediaType=Mock(),
            Status=Mock(),
        ),
    )
    sys.modules.setdefault("shelfspace.cache", types.SimpleNamespace(cache={}))
    return importlib.import_module("shelfspace.apis.trakt")


class TraktAuthTest(unittest.TestCase):
    def setUp(self):
        self.trakt = import_trakt_module()

    def test_headers_omit_authorization_without_access_token(self):
        api = self.trakt.TraktAPI(
            client_id="client-id",
            access_token=None,
            refresh_token="refresh-token",
            client_secret="client-secret",
        )

        self.assertNotIn("Authorization", api._headers())

    def test_401_with_missing_refresh_token_raises_auth_error(self):
        response = Mock(status_code=401, text="unauthorized")

        api = self.trakt.TraktAPI(
            client_id="client-id",
            access_token="expired-token",
            refresh_token=None,
            client_secret="client-secret",
        )

        with patch.object(self.trakt.requests, "get", return_value=response):
            with self.assertRaisesRegex(
                self.trakt.TraktAuthError, "missing refresh_token"
            ):
                api._get("/users/me/watchlist")

    def test_401_refreshes_token_and_retries_with_new_authorization(self):
        unauthorized = Mock(status_code=401, text="unauthorized")
        ok = Mock(status_code=200)
        ok.json.return_value = [{"type": "movie"}]

        api = self.trakt.TraktAPI(
            client_id="client-id",
            access_token="expired-token",
            refresh_token="refresh-token",
            client_secret="client-secret",
        )

        with patch.object(
            self.trakt.requests, "get", side_effect=[unauthorized, ok]
        ) as mocked_get:
            with patch.object(
                api,
                "_refresh_token",
                return_value={
                    "access_token": "new-access-token",
                    "refresh_token": "new-refresh-token",
                },
            ):
                self.assertEqual(api._get("/users/me/watchlist"), [{"type": "movie"}])

        self.assertEqual(api.access_token, "new-access-token")
        self.assertEqual(api.refresh_token, "new-refresh-token")
        self.assertEqual(
            mocked_get.call_args_list[1].kwargs["headers"]["Authorization"],
            "Bearer new-access-token",
        )


if __name__ == "__main__":
    unittest.main()
