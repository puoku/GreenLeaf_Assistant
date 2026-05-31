from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore')

    bot_token: str
    database_url: str = 'sqlite+aiosqlite:///./greenleaf.db'
    openai_api_key: str | None = None
    openai_base_url: str = 'https://openrouter.ai/api/v1'
    openai_model: str = 'nvidia/nemotron-3-super-120b-a12b:free'
    llm_timeout_seconds: float = 12.0
    llm_retries: int = 2
    llm_retry_base_delay_seconds: float = 0.7
    llm_circuit_fail_threshold: int = 5
    llm_circuit_open_seconds: int = 60
    admin_username: str = 'admin'
    admin_password: str = 'change-me'
    human_handoff_minutes: int = 20
    partner_price_multiplier: float = 2.0
    webapp_host: str = '0.0.0.0'
    webapp_port: int = 8000


@lru_cache
def get_settings() -> Settings:
    return Settings()
