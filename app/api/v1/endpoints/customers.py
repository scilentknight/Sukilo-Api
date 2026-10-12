"""Expose thin HTTP routes for customer self-service workflows."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.orm import Session

from app.core.auth_context import AuthContext
from app.core.database import get_db
from app.modules.access.dependencies import require_authenticated
from app.modules.customers.application import CustomerApplication
from app.modules.customers.identifier_change import (
    ConfirmIdentifierChange,
    IdentifierChangeError,
    RequestIdentifierChange,
)
from app.modules.customers.profile import ProfileUnavailableError
from app.modules.customers.schemas import (
    AddressResponse,
    AddressCreateRequest,
    AddressUpdateRequest,
    ConfirmIdentifierChangeRequest,
    ConsentListResponse,
    ConsentRecordResponse,
    CurrentSessionResponse,
    IdentifierChangeChallengeResponse,
    PreferenceResponse,
    PreferenceUpdateRequest,
    ProfileResponse,
    RecordConsentRequest,
    SetDefaultAddressRequest,
    UpdateMeRequest,
    decode_consent_cursor,
    encode_consent_cursor,
)
from app.modules.customers.service import (
    AddressConflictError,
    AddressNotFoundError,
    AddressValidationError,
    AddressView,
    ConsentValidationError,
    ConsentView,
    CreateAddress,
    CustomerServiceError,
    PreferenceConflictError,
    PreferenceValidationError,
    PreferenceView,
    RecordConsent,
    SetPreferences,
    UpdateAddress,
)
from app.modules.customers.types import ConsentSource
from app.modules.customers.profile import ProfileView

router = APIRouter(prefix="/me", tags=["Customer"])
CORRELATION_HEADER = "X-Correlation-ID"


def get_customer_application(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> CustomerApplication:
    """Build request-scoped customer services from application composition."""
    factory = getattr(request.app.state, "customer_application_factory", None)
    if factory is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Customer services are unavailable.",
        )
    return factory(db)


@router.get("", response_model=ProfileResponse)
def get_me(
    context: Annotated[AuthContext, Depends(require_authenticated)],
    application: Annotated[CustomerApplication, Depends(get_customer_application)],
) -> ProfileResponse:
    """Return the authenticated customer's masked profile."""
    try:
        return _profile_response(application.profile.get_profile(context))
    except ProfileUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Profile is unavailable.",
        ) from exc


@router.patch(
    "",
    response_model=IdentifierChangeChallengeResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def start_identifier_change(
    payload: UpdateMeRequest,
    response: Response,
    context: Annotated[AuthContext, Depends(require_authenticated)],
    db: Annotated[Session, Depends(get_db)],
    application: Annotated[CustomerApplication, Depends(get_customer_application)],
) -> IdentifierChangeChallengeResponse:
    """Start one owned identifier verification challenge."""
    try:
        command = RequestIdentifierChange(
            actor=context,
            new_email=str(payload.new_email) if payload.new_email else None,
            new_phone=payload.new_phone_e164,
            country_code=payload.country_code,
            locale_code=payload.locale_code,
        )
        challenge_id = application.identifier_changes.request_change(command)
        db.commit()
        _apply_correlation(response, context)
        return IdentifierChangeChallengeResponse(challenge_id=challenge_id)
    except Exception as exc:
        db.rollback()
        _raise_identifier_change_error(exc, context)


@router.post(
    "/identifier-change/confirm",
    status_code=status.HTTP_204_NO_CONTENT,
)
def confirm_identifier_change(
    payload: ConfirmIdentifierChangeRequest,
    response: Response,
    context: Annotated[AuthContext, Depends(require_authenticated)],
    db: Annotated[Session, Depends(get_db)],
    application: Annotated[CustomerApplication, Depends(get_customer_application)],
) -> None:
    """Confirm one verified identifier replacement."""
    try:
        command = ConfirmIdentifierChange(
            actor=context,
            new_email=str(payload.new_email) if payload.new_email else None,
            new_phone=payload.new_phone_e164,
            grant=payload.grant,
            country_code=payload.country_code,
            locale_code=payload.locale_code,
        )
        application.identifier_changes.confirm_change(command)
        db.commit()
        _apply_correlation(response, context)
    except Exception as exc:
        db.rollback()
        _raise_identifier_change_error(exc, context)


@router.get("/preferences", response_model=PreferenceResponse)
def get_preferences(
    context: Annotated[AuthContext, Depends(require_authenticated)],
    application: Annotated[CustomerApplication, Depends(get_customer_application)],
) -> PreferenceResponse:
    """Return the authenticated account's preference row."""
    view = application.preferences.get_preferences(context)
    if view is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Preferences are unavailable.",
        )
    return _preference_response(view)


@router.put("/preferences", response_model=PreferenceResponse)
def put_preferences(
    payload: PreferenceUpdateRequest,
    response: Response,
    context: Annotated[AuthContext, Depends(require_authenticated)],
    db: Annotated[Session, Depends(get_db)],
    application: Annotated[CustomerApplication, Depends(get_customer_application)],
) -> PreferenceResponse:
    """Replace complete market preferences."""
    try:
        command = SetPreferences(
            actor=context,
            country_code=payload.country_code,
            locale_code=payload.locale_code,
            currency_code=payload.currency_code,
            expected_version=payload.expected_version,
        )
        view = application.preferences.set_preferences(command)
        db.commit()
        _apply_correlation(response, context)
        return _preference_response(view)
    except Exception as exc:
        db.rollback()
        _raise_customer_mutation_error(exc, context)


@router.get("/consents", response_model=ConsentListResponse)
def list_consents(
    context: Annotated[AuthContext, Depends(require_authenticated)],
    application: Annotated[CustomerApplication, Depends(get_customer_application)],
    country_code: Annotated[str, Query(min_length=2, max_length=2)],
    history: Annotated[bool, Query()] = False,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
) -> ConsentListResponse:
    """Return current consent state or bounded history."""
    try:
        if history:
            return _consent_history(context, application, country_code, limit, cursor)
        rows = application.consents.list_current(context, country_code)
        return ConsentListResponse(
            items=[_consent_response(row) for row in rows],
            next_cursor=None,
        )
    except ConsentValidationError as exc:
        raise _read_error(exc) from exc


@router.post("/consents", response_model=ConsentRecordResponse, status_code=status.HTTP_201_CREATED)
def record_consent(
    payload: RecordConsentRequest,
    response: Response,
    context: Annotated[AuthContext, Depends(require_authenticated)],
    db: Annotated[Session, Depends(get_db)],
    application: Annotated[CustomerApplication, Depends(get_customer_application)],
) -> ConsentRecordResponse:
    """Append one explicit consent record."""
    try:
        command = RecordConsent(
            actor=context,
            country_code=payload.country_code,
            consent_type=payload.consent_type,
            policy_version=payload.policy_version,
            granted=payload.granted,
            source=ConsentSource.WEB,
        )
        view = application.consents.record(command)
        db.commit()
        _apply_correlation(response, context)
        return _consent_response(view)
    except Exception as exc:
        db.rollback()
        _raise_customer_mutation_error(exc, context)


@router.get("/addresses", response_model=list[AddressResponse])
def list_addresses(
    context: Annotated[AuthContext, Depends(require_authenticated)],
    application: Annotated[CustomerApplication, Depends(get_customer_application)],
    country_code: Annotated[str, Query(min_length=2, max_length=2)],
) -> list[AddressResponse]:
    """List active addresses for one owner and country."""
    try:
        rows = application.addresses.list_addresses(context, country_code)
        return [_address_response(row) for row in rows]
    except AddressValidationError as exc:
        raise _read_error(exc) from exc


@router.post("/addresses", response_model=AddressResponse, status_code=status.HTTP_201_CREATED)
def create_address(
    payload: AddressCreateRequest,
    response: Response,
    context: Annotated[AuthContext, Depends(require_authenticated)],
    db: Annotated[Session, Depends(get_db)],
    application: Annotated[CustomerApplication, Depends(get_customer_application)],
) -> AddressResponse:
    """Create one country-scoped address."""
    try:
        view = application.addresses.create_address(_create_address(context, payload))
        db.commit()
        _apply_correlation(response, context)
        return _address_response(view)
    except Exception as exc:
        db.rollback()
        _raise_customer_mutation_error(exc, context)


@router.get("/addresses/{address_id}", response_model=AddressResponse)
def get_address(
    address_id: UUID,
    context: Annotated[AuthContext, Depends(require_authenticated)],
    application: Annotated[CustomerApplication, Depends(get_customer_application)],
    country_code: Annotated[str, Query(min_length=2, max_length=2)],
) -> AddressResponse:
    """Return one active scoped address."""
    try:
        return _address_response(
            application.addresses.get_address(context, country_code, address_id)
        )
    except AddressNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Address is unavailable.",
        ) from exc
    except AddressValidationError as exc:
        raise _read_error(exc) from exc


@router.patch("/addresses/{address_id}", response_model=AddressResponse)
def update_address(
    address_id: UUID,
    payload: AddressUpdateRequest,
    response: Response,
    context: Annotated[AuthContext, Depends(require_authenticated)],
    db: Annotated[Session, Depends(get_db)],
    application: Annotated[CustomerApplication, Depends(get_customer_application)],
    country_code: Annotated[str, Query(min_length=2, max_length=2)],
) -> AddressResponse:
    """Replace one address with optimistic concurrency."""
    try:
        command = _update_address(context, country_code, address_id, payload)
        view = application.addresses.update_address(command)
        db.commit()
        _apply_correlation(response, context)
        return _address_response(view)
    except Exception as exc:
        db.rollback()
        _raise_customer_mutation_error(exc, context)


@router.delete("/addresses/{address_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_address(
    address_id: UUID,
    response: Response,
    context: Annotated[AuthContext, Depends(require_authenticated)],
    db: Annotated[Session, Depends(get_db)],
    application: Annotated[CustomerApplication, Depends(get_customer_application)],
    country_code: Annotated[str, Query(min_length=2, max_length=2)],
    expected_version: Annotated[int, Query(ge=1)],
) -> None:
    """Soft-delete one address."""
    try:
        application.addresses.delete_address(
            context,
            country_code,
            address_id,
            expected_version,
        )
        db.commit()
        _apply_correlation(response, context)
    except Exception as exc:
        db.rollback()
        _raise_customer_mutation_error(exc, context)


@router.post("/addresses/{address_id}/default", response_model=AddressResponse)
def set_default_address(
    address_id: UUID,
    payload: SetDefaultAddressRequest,
    response: Response,
    context: Annotated[AuthContext, Depends(require_authenticated)],
    db: Annotated[Session, Depends(get_db)],
    application: Annotated[CustomerApplication, Depends(get_customer_application)],
    country_code: Annotated[str, Query(min_length=2, max_length=2)],
) -> AddressResponse:
    """Select one eligible default address."""
    try:
        view = application.addresses.set_default(
            context,
            country_code,
            address_id,
            payload.kind,
        )
        db.commit()
        _apply_correlation(response, context)
        return _address_response(view)
    except Exception as exc:
        db.rollback()
        _raise_customer_mutation_error(exc, context)


def _consent_history(
    context: AuthContext,
    application: CustomerApplication,
    country_code: str,
    limit: int,
    cursor: str | None,
) -> ConsentListResponse:
    decoded = None
    if cursor is not None:
        try:
            decoded = decode_consent_cursor(cursor)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Consent cursor is invalid.",
            ) from exc
    try:
        page = application.consents.list_history(
            context,
            country_code,
            limit=limit,
            cursor=decoded,
        )
    except ConsentValidationError as exc:
        raise _read_error(exc) from exc
    next_cursor = None
    if page.next_cursor is not None:
        next_cursor = encode_consent_cursor(*page.next_cursor)
    return ConsentListResponse(
        items=[_consent_response(row) for row in page.items],
        next_cursor=next_cursor,
    )


def _profile_response(view: ProfileView) -> ProfileResponse:
    preferences = (
        _preference_response(view.preferences)
        if view.preferences is not None
        else None
    )
    session = view.current_session
    return ProfileResponse(
        user_id=view.user_id,
        status=view.status,
        email_masked=view.email_masked,
        phone_masked=view.phone_masked,
        email_verified=view.email_verified,
        phone_verified=view.phone_verified,
        created_at=view.created_at,
        last_login_at=view.last_login_at,
        preferences=preferences,
        current_session=CurrentSessionResponse(
            session_id=session.session_id,
            device_id=session.device_id,
            device_name=session.device_name,
            issued_at=session.issued_at,
            is_current=session.is_current,
        ),
    )


def _preference_response(view: PreferenceView) -> PreferenceResponse:
    return PreferenceResponse(
        user_id=view.user_id,
        country_id=view.country_id,
        country_code=view.country_code,
        locale_code=view.locale_code,
        currency_code=view.currency_code,
        marketing_email=view.marketing_email,
        marketing_sms=view.marketing_sms,
        version=view.version,
    )


def _consent_response(view: ConsentView) -> ConsentRecordResponse:
    return ConsentRecordResponse(
        consent_id=view.consent_id,
        user_id=view.user_id,
        country_id=view.country_id,
        consent_type=view.consent_type,
        policy_version=view.policy_version,
        granted=view.granted,
        source=view.source.value,
        recorded_at=view.recorded_at,
        correlation_id=view.correlation_id,
    )


def _address_response(view: AddressView) -> AddressResponse:
    return AddressResponse(
        address_id=view.address_id,
        user_id=view.user_id,
        country_id=view.country_id,
        geography_area_id=view.geography_area_id,
        recipient_name=view.recipient_name,
        phone_e164=view.phone_e164,
        address_line_1=view.address_line_1,
        address_line_2=view.address_line_2,
        landmark=view.landmark,
        city_text=view.city_text,
        postal_code=view.postal_code,
        latitude=view.latitude,
        longitude=view.longitude,
        address_type=view.address_type,
        is_shipping=view.is_shipping,
        is_billing=view.is_billing,
        is_default_shipping=view.is_default_shipping,
        is_default_billing=view.is_default_billing,
        created_at=view.created_at,
        updated_at=view.updated_at,
        version=view.version,
    )


def _create_address(
    context: AuthContext,
    payload: AddressCreateRequest,
) -> CreateAddress:
    return CreateAddress(
        actor=context,
        country_code=payload.country_code,
        geography_area_id=payload.geography_area_id,
        recipient_name=payload.recipient_name,
        phone=payload.phone,
        address_line_1=payload.address_line_1,
        address_line_2=payload.address_line_2,
        landmark=payload.landmark,
        city_text=payload.city_text,
        postal_code=payload.postal_code,
        latitude=payload.latitude,
        longitude=payload.longitude,
        address_type=payload.address_type,
        is_shipping=payload.is_shipping,
        is_billing=payload.is_billing,
        is_default_shipping=payload.is_default_shipping,
        is_default_billing=payload.is_default_billing,
    )


def _update_address(
    context: AuthContext,
    country_code: str,
    address_id: UUID,
    payload: AddressUpdateRequest,
) -> UpdateAddress:
    return UpdateAddress(
        actor=context,
        country_code=country_code,
        geography_area_id=payload.geography_area_id,
        recipient_name=payload.recipient_name,
        phone=payload.phone,
        address_line_1=payload.address_line_1,
        address_line_2=payload.address_line_2,
        landmark=payload.landmark,
        city_text=payload.city_text,
        postal_code=payload.postal_code,
        latitude=payload.latitude,
        longitude=payload.longitude,
        address_type=payload.address_type,
        is_shipping=payload.is_shipping,
        is_billing=payload.is_billing,
        is_default_shipping=payload.is_default_shipping,
        is_default_billing=payload.is_default_billing,
        address_id=address_id,
        expected_version=payload.expected_version,
    )


def _apply_correlation(response: Response, context: AuthContext) -> None:
    response.headers[CORRELATION_HEADER] = str(context.correlation_id)


def _correlation_headers(context: AuthContext) -> dict[str, str]:
    return {CORRELATION_HEADER: str(context.correlation_id)}


def _read_error(error: CustomerServiceError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Request could not be completed.",
    )


def _raise_identifier_change_error(
    error: Exception,
    context: AuthContext,
) -> None:
    headers = _correlation_headers(context)
    if isinstance(error, IdentifierChangeError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": error.code, "message": "Request could not be completed."},
            headers=headers,
        ) from error
    if isinstance(error, CustomerServiceError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Request could not be completed.",
            headers=headers,
        ) from error
    raise error


def _raise_customer_mutation_error(
    error: Exception,
    context: AuthContext,
) -> None:
    headers = _correlation_headers(context)
    if isinstance(error, (PreferenceConflictError, AddressConflictError)):
        raise _version_conflict(headers) from error
    if isinstance(error, AddressNotFoundError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Address is unavailable.",
            headers=headers,
        ) from error
    if isinstance(error, (PreferenceValidationError, ConsentValidationError, AddressValidationError)):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Request could not be completed.",
            headers=headers,
        ) from error
    if isinstance(error, CustomerServiceError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Request could not be completed.",
            headers=headers,
        ) from error
    raise error


def _version_conflict(headers: dict[str, str]) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "code": "CUSTOMER_VERSION_CONFLICT",
            "message": "Resource version is stale.",
        },
        headers=headers,
    )
