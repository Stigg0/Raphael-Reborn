import hmac
import logging
from typing import Callable

from fastapi import Depends, HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from config import Settings, get_settings

logger = logging.getLogger(__name__)
_bearer = HTTPBearer()


def _parse_key_map(api_keys: str) -> dict[str, str]:
    """Parse 'role:key,role:key' → {key: role}."""
    result: dict[str, str] = {}
    for pair in api_keys.split(","):
        pair = pair.strip()
        if ":" not in pair:
            continue
        role, key = pair.split(":", 1)
        result[key.strip()] = role.strip()
    return result


def require_role(*allowed_roles: str) -> Callable:
    """FastAPI dependency factory — validates Bearer token and checks role.

    admin role implicitly satisfies any role requirement.
    """
    async def _check(
        credentials: HTTPAuthorizationCredentials = Security(_bearer),
        settings: Settings = Depends(get_settings),
    ) -> str:
        key_map = _parse_key_map(settings.api_keys)
        token = credentials.credentials
        role: str | None = None
        for k, r in key_map.items():
            if hmac.compare_digest(k.encode(), token.encode()):
                role = r
                break
        if role is None or (role not in allowed_roles and role != "admin"):
            logger.warning("Rejected API request — invalid or insufficient key")
            raise HTTPException(status_code=403, detail="Forbidden")
        return role

    return _check
