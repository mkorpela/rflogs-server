from datetime import datetime
import os
from fastapi import APIRouter, Body, Depends, HTTPException
from typing import Optional

from rflogs_server.database.users import (
    get_user_by_username,
    get_workspace_by_id,
    get_workspace_by_owner_id,
)

from rflogs_server.database.projects import (
    add_user_to_project,
    check_project_access,
    create_project,
    create_project_invitation,
    delete_project,
    get_project_by_id,
    list_user_projects,
    recreate_api_key,
    remove_user_project_access,
    update_project_in_db,
    user_has_project_access,
    get_active_projects_count,
)

from .user_management import get_current_session_user
from ..models import MAX_RETENTION_DAYS, Project, User
from ..storage import StorageManager
from ..logging_config import get_logger

logger = get_logger(__name__)

project_router = APIRouter()


@project_router.post("/projects", include_in_schema=False)
async def create_new_project(
    name: str = Body(...),
    public_access: bool = Body(False),
    retention_days: int = Body(MAX_RETENTION_DAYS),
    user: Optional[User] = Depends(get_current_session_user),
):
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    workspace = get_workspace_by_owner_id(user.id)
    if not workspace:
        raise HTTPException(status_code=404, detail="Workspace not found")

    if workspace.expiry_date is None:
        raise HTTPException(
            status_code=403, detail="Workspace not allowed to create projects"
        )

    # Check active projects limit
    active_projects_count = get_active_projects_count(workspace.id)
    if active_projects_count >= workspace.active_projects_limit:
        raise HTTPException(
            status_code=403,
            detail=f"Active projects limit reached ({workspace.active_projects_limit}). Cannot create more projects.",
        )

    # Validate retention_days
    if retention_days < 0 or retention_days > MAX_RETENTION_DAYS:
        raise HTTPException(
            status_code=400,
            detail=f"retention_days must be between 0 and {MAX_RETENTION_DAYS}",
        )

    project = Project(
        name=name,
        workspace_id=workspace.id,
        public_access=public_access,
        retention_days=retention_days,
        created_at=datetime.utcnow(),
    )
    created_project, api_key = create_project(project)
    logger.info("Project created", project_id=created_project.id, user=user)

    return {"project": created_project, "api_key": api_key}


@project_router.post(
    "/projects/{project_id}/regenerate-api-key", include_in_schema=False
)
async def regenerate_api_key(
    project_id: str,
    user: Optional[User] = Depends(get_current_session_user),
):
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    project = get_project_by_id(project_id)
    if not project or not check_project_access(project, user):
        raise HTTPException(status_code=404, detail="Project not found or unauthorized")

    api_key = recreate_api_key(project.id)
    if not api_key:
        raise HTTPException(status_code=500, detail="Internal server error")

    return {"project": project, "api_key": api_key}


@project_router.delete("/projects/{project_id}", include_in_schema=False)
async def delete_project_endpoint(
    project_id: str,
    user: Optional[User] = Depends(get_current_session_user),
):
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    project = get_project_by_id(project_id)
    if not project or not check_project_access(project, user):
        raise HTTPException(status_code=404, detail="Project not found or unauthorized")

    success, file_paths = delete_project(project_id)

    if success:
        workspace = get_workspace_by_id(project.workspace_id)
        if not workspace:
            raise HTTPException(status_code=500, detail="Failed to delete project")
        storage_manager = StorageManager(
            workspace, backend=os.getenv("STORAGE_BACKEND", "s3")
        )
        for file_path in file_paths:
            storage_manager.delete_file(file_path)

        logger.info(
            f"Project {project_id} and all associated data deleted successfully"
        )
        return {
            "message": f"Project {project_id} and all associated data deleted successfully"
        }
    else:
        raise HTTPException(status_code=500, detail="Failed to delete project")


@project_router.get("/projects", include_in_schema=False)
async def list_projects(user: Optional[User] = Depends(get_current_session_user)):
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    return {"projects": list_user_projects(user)}


@project_router.post("/projects/{project_id}/shared-users", include_in_schema=False)
async def add_shared_user(
    project_id: str,
    username: str = Body(..., embed=True),
    user: Optional[User] = Depends(get_current_session_user),
):
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    project = get_project_by_id(project_id)
    if not project or not check_project_access(project, user):
        raise HTTPException(status_code=404, detail="Project not found or unauthorized")

    shared_user = get_user_by_username(username)
    if shared_user:
        if user_has_project_access(project, shared_user):
            raise HTTPException(
                status_code=400, detail="User already has access to this project"
            )

        if add_user_to_project(project_id, shared_user.id):
            logger.info(f"Project {project_id} shared with existing user {username}")
            return {"message": f"Project shared with existing user {username}"}
        else:
            raise HTTPException(status_code=500, detail="Failed to share project")
    else:
        invitation = create_project_invitation(project_id, user.id, username)
        if invitation:
            logger.info(
                f"Invitation created for {username} to join project {project_id}"
            )
            return {
                "message": f"Invitation created for {username}. They will gain access upon registration."
            }
        else:
            raise HTTPException(status_code=500, detail="Failed to create invitation")


@project_router.delete(
    "/projects/{project_id}/shared-users/{username}", include_in_schema=False
)
async def remove_shared_user(
    project_id: str,
    username: str,
    user: Optional[User] = Depends(get_current_session_user),
):
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    project = get_project_by_id(project_id)
    if not project or not check_project_access(project, user):
        raise HTTPException(status_code=404, detail="Project not found or unauthorized")

    if remove_user_project_access(project_id, username):
        logger.info(f"Removed {username}'s access to project {project_id}")
        return {"message": f"Removed {username}'s access to the project"}
    else:
        raise HTTPException(
            status_code=404, detail="User not found or not associated with this project"
        )


@project_router.patch("/projects/{project_id}", include_in_schema=False)
async def update_project(
    project_id: str,
    payload: dict = Body(...),
    user: Optional[User] = Depends(get_current_session_user),
):
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    project = get_project_by_id(project_id)
    if not project or not check_project_access(project, user):
        raise HTTPException(status_code=404, detail="Project not found or unauthorized")

    update_fields = ["public_access", "retention_days"]
    update_data = {k: v for k, v in payload.items() if k in update_fields}

    if "retention_days" in update_data:
        try:
            retention_days = int(update_data["retention_days"])
            if retention_days <= 0:
                raise ValueError("Must be positive")
            if retention_days > MAX_RETENTION_DAYS:
                raise ValueError(f"Cannot exceed {MAX_RETENTION_DAYS}")
            update_data["retention_days"] = retention_days
        except ValueError as e:
            raise HTTPException(
                status_code=400, detail=f"Invalid retention_days: {str(e)}"
            )

    try:
        updated_project = update_project_in_db(project_id, update_data)
        return {"message": "Project updated successfully", "project": updated_project}
    except Exception as e:
        logger.error(f"Error updating project: {e}")
        raise HTTPException(status_code=500, detail="Failed to update project")
