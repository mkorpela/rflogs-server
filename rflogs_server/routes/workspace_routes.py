from fastapi import APIRouter, Depends, HTTPException
from ..models import User, WorkspacePublic, WorkspaceUpdate
from ..database.users import get_workspace_by_owner_id, update_workspace
from .user_management import get_current_user
from ..logging_config import get_logger

logger = get_logger(__name__)

workspace_router = APIRouter()


@workspace_router.get(
    "/workspace", response_model=WorkspacePublic, include_in_schema=False
)
async def get_workspace(user: User = Depends(get_current_user)):
    """
    Retrieve the current user's workspace details.

    - **Returns:** Workspace information excluding sensitive fields.
    """
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    workspace = get_workspace_by_owner_id(user.id)
    if not workspace:
        raise HTTPException(status_code=404, detail="Workspace not found")

    return workspace


@workspace_router.patch("/workspace", include_in_schema=False)
async def update_workspace_settings(
    updated_workspace: WorkspaceUpdate, user: User = Depends(get_current_user)
):
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    workspace = get_workspace_by_owner_id(user.id)
    if not workspace:
        raise HTTPException(status_code=404, detail="Workspace not found")

    # Only allow updating certain fields
    allowed_fields = {
        "oidc_enabled",
        "oidc_provider_url",
        "oidc_client_id",
        "oidc_client_secret",
        "oidc_issuer_url",
    }

    update_data = updated_workspace.dict(exclude_unset=True)
    update_fields = {k: v for k, v in update_data.items() if k in allowed_fields}

    if not update_fields:
        raise HTTPException(status_code=400, detail="No valid fields to update")

    update_workspace(workspace.id, update_fields)

    return {"message": "Workspace settings updated successfully"}
