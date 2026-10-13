"""Test that request data cannot forge database context or cross customers."""

import dataclasses
import importlib
import inspect
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from pydantic import BaseModel

from app.core.database_context import (
    AuthorizationMode,
    DatabaseContextError,
    DatabaseRequestContext,
    apply_database_context,
    validate_database_context,
)
from app.modules.customers.exports import (
    CustomerExportError,
    CustomerExportNotFoundError,
)
from app.modules.customers.retention import build_retention_context
from app.modules.customers.types import DataExportStatus
from app.modules.identity.grants import AuthGrantError
from app.modules.identity.types import AccountStatus
from tests.fakes.privacy import (
    COUNTRY_ID,
    EXPORT_A,
    EXPORT_B,
    PASSWORD,
    USER_A,
    USER_B,
    actor,
    build_closure_world,
    build_export_world,
)

APP_ROOT = Path(__file__).resolve().parents[2] / "app"
# ``action_class`` is intentionally absent: OtpVerifyRequest legitimately names
# the step-up grant action, which never feeds a DatabaseRequestContext (the
# HTTP-layer source scan below proves no endpoint builds one).
CONTEXT_FIELD_NAMES = frozenset(
    {
        "authorization_mode",
        "actor_user_id",
        "effective_user_id",
        "effective_country_id",
        "job_purpose",
        "correlation_id",
    }
)
SCHEMA_MODULES = (
    "app.modules.identity.schemas",
    "app.modules.customers.schemas",
    "app.modules.access.schemas",
)


class _RecordingSession:
    def __init__(self) -> None:
        self.calls: list[object] = []

    def execute(self, *args: object, **kwargs: object) -> None:
        self.calls.append(args)


@pytest.mark.parametrize(
    "body",
    [
        {"authorization_mode": "GLOBAL_OPERATOR", "effective_user_id": str(uuid4())},
        {"authorization_mode": "SYSTEM_JOB", "job_purpose": "RETENTION",
         "effective_user_id": str(uuid4()), "effective_country_id": str(uuid4())},
        {"authorization_mode": AuthorizationMode.SELF, "effective_user_id": str(uuid4())},
        {"authorization_mode": "ACCOUNT_CLOSURE", "effective_user_id": str(USER_B)},
    ],
)
def test_request_body_values_cannot_construct_a_valid_database_context(
    body: dict[str, object],
) -> None:
    """Reject string-typed body values and never touch the database."""
    context = DatabaseRequestContext(**body)
    with pytest.raises(DatabaseContextError):
        validate_database_context(context)
    session = _RecordingSession()
    with pytest.raises(DatabaseContextError):
        apply_database_context(session, context)  # type: ignore[arg-type]
    with pytest.raises(DatabaseContextError):
        apply_database_context(session, body)  # type: ignore[arg-type]
    assert session.calls == []


def test_unknown_body_keys_cannot_build_a_context() -> None:
    """Reject unexpected keys instead of ignoring them."""
    with pytest.raises(TypeError):
        DatabaseRequestContext(authorization_mode=AuthorizationMode.SELF, is_admin=True)  # type: ignore[call-arg]


def test_context_is_immutable_after_construction() -> None:
    """Prevent changing the authorization mode of a built context."""
    context = build_retention_context(COUNTRY_ID, uuid4())
    with pytest.raises(dataclasses.FrozenInstanceError):
        context.authorization_mode = AuthorizationMode.GLOBAL_OPERATOR  # type: ignore[misc]


def _request_models() -> list[type[BaseModel]]:
    models: list[type[BaseModel]] = []
    for module_name in SCHEMA_MODULES:
        module = importlib.import_module(module_name)
        for _, cls in inspect.getmembers(module, inspect.isclass):
            if issubclass(cls, BaseModel) and cls.__module__ == module_name:
                if cls.__name__.endswith("Request"):
                    models.append(cls)
    return models


def test_request_schemas_have_no_authorization_context_fields() -> None:
    """Keep context fields out of every request body and drop extras."""
    models = _request_models()
    assert len(models) >= 10
    for model in models:
        assert not CONTEXT_FIELD_NAMES & set(model.model_fields), model.__name__
        assert model.model_config.get("extra") != "allow", model.__name__


def test_http_layer_never_builds_or_applies_database_context() -> None:
    """Leave context construction to authorized services only."""
    forbidden = ("DatabaseRequestContext", "AuthorizationMode", "apply_database_context")
    for path in (APP_ROOT / "api").rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert not any(name in source for name in forbidden), path.name


def _confirm_token(world: object, user_id: UUID) -> str:
    return world.service.request(actor(user_id), PASSWORD, "s" * 40).close_grant  # type: ignore[attr-defined]


def test_closure_context_comes_from_the_authenticated_actor_only() -> None:
    """Derive the closure context from the actor, not from request input."""
    world = build_closure_world()
    token = _confirm_token(world, USER_A)
    world.service.confirm(actor(USER_A), token)
    context = world.contexts[0]
    assert context.authorization_mode is AuthorizationMode.ACCOUNT_CLOSURE
    assert context.effective_user_id == USER_A and context.actor_user_id == USER_A
    assert context.job_purpose is None


def test_customer_a_closure_never_reads_or_changes_customer_b() -> None:
    """Touch only customer A's account, countries, and addresses."""
    world = build_closure_world()
    token = _confirm_token(world, USER_A)
    world.service.confirm(actor(USER_A), token)
    assert set(world.repository.locked_for) == {USER_A}
    assert set(world.repository.listed_for) == {USER_A}
    assert set(world.accounts.requested) == {USER_A}
    b_account = world.accounts.by_id[USER_B]
    assert b_account.status == AccountStatus.ACTIVE and b_account.email == "bob@example.com"
    b_address = next(item for item in world.repository.addresses if item.user_id == USER_B)
    assert b_address.address_line_1 == "2 Lake Road" and b_address.version == 1
    assert world.accounts.by_id[USER_A].status == AccountStatus.CLOSED


def test_customer_b_cannot_confirm_with_customer_a_closure_grant() -> None:
    """Reject a closure grant presented by another customer."""
    world = build_closure_world()
    token = _confirm_token(world, USER_A)
    with pytest.raises(AuthGrantError):
        world.service.confirm(actor(USER_B), token)
    assert world.contexts == []
    assert world.accounts.by_id[USER_A].status == AccountStatus.ACTIVE
    assert world.accounts.by_id[USER_B].status == AccountStatus.ACTIVE


def test_closure_preview_lists_only_the_actors_scope() -> None:
    """Return notices only for countries owned by the requester."""
    world = build_closure_world()
    world.service.request(actor(USER_A), PASSWORD, "s" * 40)
    assert world.repository.listed_for == [USER_A]


def test_customer_a_cannot_read_customer_b_export_resources() -> None:
    """Hide B's export status, download grant, and download from A."""
    world = build_export_world()
    service = world.service
    with pytest.raises(CustomerExportNotFoundError):
        service.get_status(actor(USER_A), EXPORT_B)
    with pytest.raises(CustomerExportNotFoundError):
        service.create_download_grant(actor(USER_A), EXPORT_B)
    own_grant = service.create_download_grant(actor(USER_A), EXPORT_A)
    with pytest.raises(CustomerExportNotFoundError):
        service.download(actor(USER_A), EXPORT_B, own_grant)
    request_b = world.repository.requests[EXPORT_B]
    assert request_b.status == DataExportStatus.READY and request_b.downloaded_at is None
    assert all(record[0].effective_user_id != USER_B for record in world.audit.records)


def test_customer_a_cannot_use_customer_b_download_grant() -> None:
    """Reject a download grant issued to another customer."""
    world = build_export_world()
    grant_b = world.service.create_download_grant(actor(USER_B), EXPORT_B)
    with pytest.raises((AuthGrantError, CustomerExportNotFoundError)):
        world.service.download(actor(USER_A), EXPORT_B, grant_b)
    assert world.repository.requests[EXPORT_B].status == DataExportStatus.READY


def test_export_job_context_is_bound_to_one_user_country_and_purpose() -> None:
    """Refuse job contexts of the wrong mode, purpose, or user."""
    world = build_export_world()
    pending = world.repository.requests[EXPORT_B]
    pending.status = DataExportStatus.PENDING
    wrong_contexts = [
        DatabaseRequestContext(
            authorization_mode=AuthorizationMode.SELF,
            effective_user_id=USER_B,
            effective_country_id=COUNTRY_ID,
        ),
        build_retention_context(COUNTRY_ID, uuid4(), effective_user_id=USER_B),
        DatabaseRequestContext(
            authorization_mode=AuthorizationMode.SYSTEM_JOB,
            job_purpose="CUSTOMER_EXPORT",
            effective_user_id=USER_A,
            effective_country_id=COUNTRY_ID,
        ),
    ]
    for context in wrong_contexts:
        with pytest.raises(CustomerExportError):
            world.service.claim(EXPORT_B, context)
    assert pending.status == DataExportStatus.PENDING
