"""Define fixed bindings between candidate OTPs and change grants."""

from dataclasses import dataclass

from app.modules.identity.types import GrantPurpose, OtpChannel, OtpPurpose


@dataclass(frozen=True, slots=True)
class IdentifierChangeBinding:
    """Describe the only grant a candidate OTP purpose may produce."""

    otp_purpose: OtpPurpose
    grant_purpose: GrantPurpose
    action_class: str
    channel: OtpChannel


EMAIL_CHANGE_BINDING = IdentifierChangeBinding(
    otp_purpose=OtpPurpose.VERIFY_EMAIL,
    grant_purpose=GrantPurpose.CHANGE_EMAIL,
    action_class="email.change",
    channel=OtpChannel.EMAIL,
)
PHONE_CHANGE_BINDING = IdentifierChangeBinding(
    otp_purpose=OtpPurpose.VERIFY_PHONE,
    grant_purpose=GrantPurpose.CHANGE_PHONE,
    action_class="phone.change",
    channel=OtpChannel.SMS,
)
_BINDINGS = {
    EMAIL_CHANGE_BINDING.otp_purpose: EMAIL_CHANGE_BINDING,
    PHONE_CHANGE_BINDING.otp_purpose: PHONE_CHANGE_BINDING,
}


def binding_for_otp_purpose(
    purpose: OtpPurpose,
) -> IdentifierChangeBinding | None:
    """Return the fixed change binding for an OTP purpose.

    Args:
        purpose: OTP purpose supplied by the verification request.

    Returns:
        The matching binding, or `None` when the purpose is not a candidate
        identifier verification.
    """
    return _BINDINGS.get(purpose)
