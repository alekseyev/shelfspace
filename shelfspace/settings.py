from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SET_")

    DEBUG: bool = False

    MONGO_URL: str = "mongodb://root:secret@localhost:4001"
    MONGO_DB: str = "shelfspace"

    # Read access token (bearer) from https://www.themoviedb.org/settings/api
    TMDB_TOKEN: str = ""

    HLTB_USER: str = ""
    GOODREADS_USER: str = ""

    STEAM_API_KEY: str = ""
    STEAM_USER_ID: str = ""


settings = Settings()
