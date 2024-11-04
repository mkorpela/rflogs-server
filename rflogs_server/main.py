import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import unquote, urlencode

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import RedirectResponse

from .database.connection import run_migrations
from .database.users import get_workspace_by_id
from .logging_config import get_logger
from .models import User
from .oidc_utils import (
    create_oidc_login_url,
    exchange_code_for_token,
    verify_oidc_token,
)
from .routes.projects_management import project_router
from .routes.public_routes import public_router
from .routes.runs_routes import init_runs
from .routes.user_management import get_current_user, init_user_management
from .routes.workspace_routes import workspace_router

logger = get_logger(__name__)

logger.info("Migrations check")
run_migrations()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup logic
    logger.info("Server starting up")
    yield
    # Shutdown logic
    logger.info("Server shutting down")


app = FastAPI(
    title="RFLogs API",
    description="API for managing Robot Framework log files",
    version="1.0.0",
    lifespan=lifespan,
)


def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    openapi_schema = get_openapi(
        title="RFLogs API",
        version="1.0.0",
        summary="API for managing Robot Framework log files",
        description="This API allows you to manage and interact with Robot Framework log files.",
        routes=app.routes,
    )
    # Add global security scheme
    openapi_schema["components"]["securitySchemes"] = {
        "APIKeyHeader": {"type": "apiKey", "in": "header", "name": "X-API-Key"}
    }
    openapi_schema["security"] = [{"APIKeyHeader": []}]
    app.openapi_schema = openapi_schema
    return app.openapi_schema


app.openapi = custom_openapi

init_user_management(app)
init_runs(app)
app.include_router(project_router, prefix="/api")
app.include_router(workspace_router, prefix="/api")
app.include_router(public_router, prefix="/api")

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/oidc/{workspace_id}/login", include_in_schema=False)
async def oidc_login(workspace_id: str, request: Request, next: Optional[str] = None):
    workspace = get_workspace_by_id(workspace_id)
    if not workspace or not workspace.oidc_enabled:
        raise HTTPException(
            status_code=400, detail="OIDC login not available for this workspace"
        )

    callback_url = request.url_for("oidc_callback", workspace_id=workspace_id)
    if next:
        callback_url = f"{callback_url}?{urlencode({'next': next})}"

    login_url, state, nonce = create_oidc_login_url(workspace, str(callback_url))

    # Store state, nonce, and next URL in session for verification in callback
    request.session["oidc_state"] = state
    request.session["oidc_nonce"] = nonce
    if next:
        request.session["oidc_next"] = unquote(next)

    return RedirectResponse(url=login_url)


@app.get("/oidc/{workspace_id}/callback", include_in_schema=False)
async def oidc_callback(workspace_id: str, code: str, state: str, request: Request):
    logger.info(f"OIDC callback received for workspace: {workspace_id}")
    workspace = get_workspace_by_id(workspace_id)
    if not workspace or not workspace.oidc_enabled:
        logger.error(f"OIDC not available for workspace: {workspace_id}")
        raise HTTPException(
            status_code=400, detail="OIDC not available for this workspace"
        )

    # Verify state
    if state != request.session.get("oidc_state"):
        logger.error("Invalid state in OIDC callback")
        raise HTTPException(status_code=400, detail="Invalid state")

    callback_url = request.url_for("oidc_callback", workspace_id=workspace_id)
    logger.info(f"Exchanging code for token with callback URL: {callback_url}")
    try:
        token_response = await exchange_code_for_token(
            workspace, code, str(callback_url)
        )
    except Exception as e:
        logger.exception("Error exchanging code for token")
        raise HTTPException(
            status_code=400, detail=f"Failed to exchange code for token: {str(e)}"
        )

    # Verify ID token and extract user info
    id_token = token_response.get("id_token")
    if not id_token:
        logger.error("No ID token received in token response")
        raise HTTPException(status_code=400, detail="No ID token received")

    # Get the nonce from the session
    expected_nonce = request.session.get("oidc_nonce")
    try:
        user_info = await verify_oidc_token(id_token, workspace, expected_nonce)
    except Exception as e:
        logger.exception("Error verifying OIDC token")
        raise HTTPException(
            status_code=400, detail=f"Error verifying OIDC token: {str(e)}"
        )

    # Clear the state and nonce from the session
    del request.session["oidc_state"]
    del request.session["oidc_nonce"]

    logger.info("OIDC login successful")

    # Store essential information in the session
    request.session["user_type"] = "guest"
    request.session["guest_workspace_id"] = workspace_id
    request.session["guest_user_id"] = user_info.get("sub")
    request.session["guest_username"] = user_info.get("preferred_username")
    request.session["guest_email"] = user_info.get("email")
    request.session["guest_session_expires"] = (
        datetime.utcnow() + timedelta(hours=1)
    ).timestamp()

    # Redirect to the original requested URL if available
    next_url = request.session.pop("oidc_next", None)
    if next_url:
        return RedirectResponse(url=next_url, status_code=302)
    else:
        return RedirectResponse(url="/", status_code=302)


@app.get("/oidc/{workspace_id}/verify", include_in_schema=False)
async def verify_oidc(workspace_id: str, user: User = Depends(get_current_user)):
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    workspace = get_workspace_by_id(workspace_id)
    if not workspace:
        raise HTTPException(status_code=404, detail="Workspace not found")

    # TODO: is this the correct thing to do?
    if user.id != workspace.owner_id:
        raise HTTPException(status_code=403, detail="Access denied")

    return {"message": "OIDC verification successful", "user": user}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
