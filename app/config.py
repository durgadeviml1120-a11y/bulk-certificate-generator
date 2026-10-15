from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. Override with CERT_* environment variables or a .env file."""

    model_config = SettingsConfigDict(env_file=".env", env_prefix="CERT_")

    database_url: str = "sqlite:///./certs.db"
    storage_dir: Path = Path("./storage")
    max_recipients_per_job: int = 10_000


settings = Settings()
