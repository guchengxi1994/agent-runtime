from __future__ import annotations

from .models import PermissionPolicy, UserContext


def is_allowed(policy: PermissionPolicy, user: UserContext) -> bool:
    if policy.tenant_ids and user.tenant_id not in policy.tenant_ids:
        return False
    if policy.roles and not set(policy.roles).intersection(user.roles):
        return False
    if policy.scopes and not set(policy.scopes).issubset(set(user.scopes)):
        return False
    return True

