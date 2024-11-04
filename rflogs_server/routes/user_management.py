import os
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, FastAPI, HTTPException, Request, Security
from fastapi.responses import RedirectResponse
from fastapi.security import APIKeyHeader

from ..auth.oidc import create_auth_provider
from ..database.projects import verify_api_key
from ..database.users import create_or_update_user, get_user_by_id, get_workspace_by_id
from ..logging_config import get_logger
from ..models import User

logger = get_logger(__name__)

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
router = APIRouter()


async def get_current_session_user(request: Request) -> Optional[User]:
    """Get user from session."""
    user_id = request.session.get("user_id")
    user_type = request.session.get("user_type")

    if not user_id and not user_type:
        logger.warning("Unauthenticated access attempt", path=request.url.path)
        return None

    if user_id:
        user = get_user_by_id(user_id)
        if not user:
            logger.warning("User not found in database", user_id=user_id)
            raise HTTPException(status_code=401, detail="Invalid user_id")
        logger.info("User fetched from database", user=user.username)
        return user

    elif user_type == "guest":
        # Handle guest/OIDC session
        expiration_timestamp = request.session.get("guest_session_expires")
        if (
            expiration_timestamp
            and datetime.utcnow().timestamp() > expiration_timestamp
        ):
            request.session.clear()
            logger.info("Guest session expired")
            return None

        workspace_id = request.session.get("guest_workspace_id")
        workspace = get_workspace_by_id(workspace_id)
        if not workspace:
            logger.warning(
                "Workspace not found for guest user", workspace_id=workspace_id
            )
            return None

        user = User(
            id=request.session.get("guest_user_id") or "",
            username=request.session.get("guest_username"),
            email=request.session.get("guest_email"),
            created_at=datetime.utcnow(),
        )
        logger.info("Guest user created from session", user=user.username)
        request.state.workspace = workspace
        return user


async def get_current_user(
    request: Request, api_key: Optional[str] = Security(api_key_header)
) -> Optional[User]:
    """Get current user from API key or session."""
    if api_key:
        logger.info("User with API key")
        verify_result = verify_api_key(api_key)
        if not verify_result:
            logger.warning("Invalid API key")
            raise HTTPException(status_code=401, detail="Invalid API Key")
        project, workspace = verify_result
        user = get_user_by_id(workspace.owner_id)
        if not user:
            logger.warning("User not found for the given API key")
            raise HTTPException(status_code=401, detail="Invalid API Key")
        request.state.project = project
        request.state.workspace = workspace
        return user
    else:
        return await get_current_session_user(request)


@router.get("/login/{workspace_id}")
async def login(workspace_id: str, request: Request, next: Optional[str] = None):
    """Initiate login process for a workspace."""
    workspace = get_workspace_by_id(workspace_id)
    if not workspace or not workspace.oidc_enabled:
        raise HTTPException(
            status_code=400, detail="Login not available for this workspace"
        )

    try:
        auth_provider = create_auth_provider(workspace)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Generate state for CSRF protection
    state = os.urandom(16).hex()
    request.session["oauth_state"] = state
    if next:
        request.session["next_url"] = next

    callback_url = request.url_for("oauth_callback", workspace_id=workspace_id)
    login_url = await auth_provider.get_login_url(str(callback_url), state)

    return RedirectResponse(url=login_url)


@router.get("/oauth/callback/{workspace_id}")
async def oauth_callback(
    workspace_id: str,
    request: Request,
    code: str,
    state: str,
):
    """Handle OAuth callback."""
    if state != request.session.get("oauth_state"):
        raise HTTPException(status_code=400, detail="Invalid state parameter")

    workspace = get_workspace_by_id(workspace_id)
    if not workspace or not workspace.oidc_enabled:
        raise HTTPException(status_code=400, detail="Invalid workspace")

    try:
        auth_provider = create_auth_provider(workspace)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    callback_url = request.url_for("oauth_callback", workspace_id=workspace_id)

    try:
        user_info = await auth_provider.verify_callback(code, state, str(callback_url))
    except Exception as e:
        logger.error(f"Auth callback failed: {e}")
        raise HTTPException(status_code=400, detail="Authentication failed")

    # Create or update user
    user = create_or_update_user(
        sub=user_info["sub"],
        username=user_info.get("username"),
        email=user_info.get("email"),
    )

    # Set up session
    request.session["user_id"] = user.id
    request.session.pop("oauth_state", None)

    # Redirect to original URL or home
    next_url = request.session.pop("next_url", "/")
    return RedirectResponse(url=next_url)


@router.get("/logout")
async def logout(request: Request):
    """Log out current user."""
    request.session.clear()
    return {"message": "Logged out successfully"}


def init_user_management(app: FastAPI):
    """Initialize user management routes."""
    app.include_router(router, prefix="/api/auth", tags=["auth"])
