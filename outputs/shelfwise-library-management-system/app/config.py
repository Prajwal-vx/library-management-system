from pydantic_settings import BaseSettings, SettingsConfigDict
import secrets


class Settings(BaseSettings):
    secret_key: str = secrets.token_urlsafe(48)
    database_url: str = "sqlite:///./library.db"
    upload_dir: str = "./data/softcopies"
    access_token_minutes: int = 30
    loan_days: int = 14
    fine_rate_per_day: float = 1.0
    fine_cap: float = 50.0
    max_upload_mb: int = 20
    cookie_secure: bool = False
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
