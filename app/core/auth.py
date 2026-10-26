from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from supabase_auth.errors import AuthApiError, AuthInvalidJwtError, AuthSessionMissingError

from core.supabase import auth_client


def require_user(request: Request) -> str:
    authorization = request.headers.get("authorization", "")
    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Supabase access token",
        )

    access_token = authorization.removeprefix("Bearer ").strip()
    try:
        user = auth_client().auth.get_user(access_token).user
    except (AuthApiError, AuthInvalidJwtError, AuthSessionMissingError) as exc:
        if getattr(exc, 'status', 401) >= 500:
            raise HTTPException(503, 'Authentication service is temporarily unavailable. Please retry.') from None
        raise HTTPException(401, 'Invalid or expired Supabase access token') from None
    except Exception:
        # A network/provider outage is not an expired session.
        raise HTTPException(503, 'Authentication service is temporarily unavailable. Please retry.') from None


    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired Supabase access token",
        )

    request.state.access_token = access_token
    return str(user.id)


CurrentUser = Annotated[str, Depends(require_user)]