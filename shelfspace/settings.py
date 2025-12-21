from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SET_")

    DEBUG: bool = False

    MONGO_URL: str = "mongodb://root:secret@localhost:4001"
    MONGO_DB: str = "shelfspace"

    AUTH_SECRET: str = "secret"

    TRAKT_CLIENT_ID: str = ""
    TRAKT_CLIENT_SECRET: str = ""

    HLTB_USER: str = ""
    GOODREADS_USER: str = ""


settings = Settings()
