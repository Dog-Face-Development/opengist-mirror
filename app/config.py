from pydantic_settings import BaseSettings, SettingsConfigDict


class AppConfig(BaseSettings):
    app_name: str = "OpenGist Gist Mirror"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    database_url: str = "sqlite:///data/app.db"
    request_timeout_seconds: int = 30

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = AppConfig()

