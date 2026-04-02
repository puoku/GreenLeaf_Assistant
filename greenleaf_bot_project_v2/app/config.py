from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore')

    bot_token: str
    bot_username: str = 'GreenLeafBot'
    database_url: str = 'sqlite+aiosqlite:///./greenleaf.db'
    openai_api_key: str | None = None
    openai_model: str = 'gpt-5-mini'
    admin_username: str = 'admin'
    admin_password: str = 'change-me'
    store_name: str = 'GreenLeaf Assistant'
    store_address: str = 'Город Новосибирск, Советский район, Проспект Строителей 13.'
    store_schedule: str = 'Вт-Суб с 13:00 до 20:00. Воскресенье и понедельник — выходной.'
    store_contacts: str = '+79130615296 Жазгул'
    manager_chat_id: int = 0
    manager_topic_id: int | None = None
    manager_response_minutes: int = 15
    timezone: str = 'Asia/Novosibirsk'
    partner_price_multiplier: float = 2.0
    secret_key: str = 'change-this-secret'
    use_webhook: bool = False
    webhook_base_url: str | None = None
    webapp_host: str = '0.0.0.0'
    webapp_port: int = 8000


@lru_cache
def get_settings() -> Settings:
    return Settings()
