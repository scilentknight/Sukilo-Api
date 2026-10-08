from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str


class DatabaseTestResponse(BaseModel):
    database: str
    message: str | None = None
