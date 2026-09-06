from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "option-agent"
    app_version: str = "0.2.0"
    environment: str = "dev"
    log_level: str = "INFO"
    database_url: str = "postgresql+psycopg://option_agent:change-me@localhost:5432/option_agent"
    redis_url: str = "redis://localhost:6379/0"

    # Groww Phase 1. Prefer an access token when available. Otherwise the
    # SDK can exchange API key + secret for an access token.
    groww_access_token: str = ""
    groww_api_key: str = ""
    groww_api_secret: str = ""

    # Reserved for later AI-agent phases.
    llm_api_key: str = ""

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False, extra="ignore")


settings = Settings()
