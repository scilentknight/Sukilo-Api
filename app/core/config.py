from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    APP_NAME: str = "Sukilo API"
    APP_ENV: str = "development"
    DEBUG: bool = True
    DATABASE_URL: str
    FRONTEND_URL: str = "http://localhost:3000"
    QDRANT_URL: str = "http://localhost:6333"
    QDRANT_COLLECTION: str = "product_images"
    DINOV2_MODEL: str = "dinov2_vitl14"
    ENABLE_VECTOR_STACK: bool = True
    UPLOAD_DIR: str = "./data/uploads"
    MAX_UPLOAD_MB: int = 10
    SEARCH_MIN_SIMILARITY: float = 0.50
    CORS_ORIGINS: list[str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://192.168.1.65:3000",
    ]

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )


settings = Settings()
