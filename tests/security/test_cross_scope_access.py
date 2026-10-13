"""Test cross-account and cross-country authorization denial."""

from uuid import UUID

import pytest

from app.modules.access.policies import (
    ResourceNotFoundPolicyError,
    require_address_owner,
    require_self,
    require_session_owner,
)


@pytest.mark.parametrize(
    "policy",
    [require_self, require_session_owner, require_address_owner],
)
def test_ownership_policies_hide_another_users_resource(policy) -> None:
    """Return not-found policy behavior for cross-account probes."""
    with pytest.raises(ResourceNotFoundPolicyError):
        policy(UUID(int=1), UUID(int=2))


@pytest.mark.parametrize(
    "policy",
    [require_self, require_session_owner, require_address_owner],
)
def test_ownership_policies_allow_same_account(policy) -> None:
    """Allow an authenticated owner to reach their own resource."""
    policy(UUID(int=1), UUID(int=1))
