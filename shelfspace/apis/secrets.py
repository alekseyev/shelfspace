"""Encapsulates secrets management for API authentication."""

import json
import os
from pathlib import Path


SECRETS_FILE = Path(__file__).parent.parent.parent / "secrets.json"


def _load_secrets_file() -> dict:
    """Load secrets from secrets.json file.

    Returns:
        Dictionary containing all secrets, or empty dict if file doesn't exist
    """
    if not SECRETS_FILE.exists():
        return {}

    with open(SECRETS_FILE) as f:
        return json.load(f)


def _save_secrets_file(secrets: dict) -> None:
    """Save secrets to secrets.json file.

    Args:
        secrets: Dictionary to save to file
    """
    SECRETS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(SECRETS_FILE, "w") as f:
        json.dump(secrets, f, indent=2)


def get_trakt_secrets() -> dict[str, str | None]:
    """Get Trakt API secrets from environment and secrets.json file.

    Static credentials (client_id, client_secret) come from environment variables.
    Dynamic tokens (access_token, refresh_token) come from secrets.json.

    Returns:
        dict with keys: client_id, access_token, refresh_token, client_secret
    """
    secrets = _load_secrets_file()
    trakt_secrets = secrets.get("trakt", {})

    return {
        "client_id": os.environ["TRAKT_CLIENT_ID"],
        "access_token": trakt_secrets.get("access_token"),
        "refresh_token": trakt_secrets.get("refresh_token"),
        "client_secret": os.environ["TRAKT_CLIENT_SECRET"],
    }


def save_trakt_secrets(
    access_token: str,
    refresh_token: str,
) -> None:
    """Save Trakt API tokens to secrets.json file.

    Only the dynamic tokens are saved. Static credentials (client_id, client_secret)
    should be kept in environment variables.

    Args:
        access_token: Trakt API access token
        refresh_token: Trakt API refresh token
    """
    secrets = _load_secrets_file()
    secrets["trakt"] = {
        "access_token": access_token,
        "refresh_token": refresh_token,
    }
    _save_secrets_file(secrets)

