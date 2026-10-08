from app.schemas.health import HealthResponse, DatabaseTestResponse


def test_health_response_schema():
    data = {"status": "healthy"}
    schema = HealthResponse(**data)
    assert schema.status == "healthy"


def test_database_test_response_schema():
    data = {"database": "connected", "message": "All good"}
    schema = DatabaseTestResponse(**data)
    assert schema.database == "connected"
    assert schema.message == "All good"
