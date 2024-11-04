import mimetypes
import os
from datetime import datetime
from typing import Dict, List, Optional
from urllib.parse import quote, urlencode

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Body,
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Path,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import RedirectResponse, StreamingResponse
from pydantic import BaseModel

from rflogs_server.database.projects import (
    add_user_to_project,
    get_project_by_id,
    get_project_storage_usage,
    user_has_project_access,
)
from rflogs_server.database.runs import (
    add_file_to_run,
    create_run_info,
    delete_run_info,
    delete_runs_and_files,
    get_file_info,
    get_project_tags,
    get_run_info,
    get_runs_and_files_to_purge,
    list_project_runs,
)
from rflogs_server.database.users import get_workspace_by_id
from rflogs_server.output_service import parse_output_xml_background

from ..logging_config import get_logger
from ..models import Project, ProjectRunsResponse, RunCreate, RunInfo, User, Workspace
from ..storage import StorageManager
from .user_management import get_current_user

logger = get_logger(__name__)

run_router = APIRouter()
public_router = APIRouter()


class RunResponse(BaseModel):
    run_id: str


@run_router.post("/runs", response_model=RunResponse)
async def create_run(
    request: Request,
    run_data: RunCreate = Body(..., description="Run creation data."),
    user: User = Depends(get_current_user),
):
    """
    Create a new run within a project.

    - **Requires:** Valid API Key in the `X-API-Key` header.
    - **Request Body:** `RunCreate` model containing run details.
    - **Returns:** `RunResponse` model with the new `run_id`.
    """
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    project = request.state.project
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    workspace = get_workspace_by_id(project.workspace_id)
    if (
        not workspace
        or user.id != workspace.owner_id
        or not workspace.expiry_date
        or workspace.expiry_date < datetime.utcnow()
    ):
        raise HTTPException(status_code=403, detail="Not authorized to create run")

    try:
        run = create_run_info(user, project, run_data)
    except ValueError as e:
        logger.error(f"Run creation failed: {e}")
        raise HTTPException(status_code=400, detail=str(e))

    logger.info("Run created", run_id=run.id, project_id=project.id, user=user)
    return RunResponse(run_id=run.id)


@run_router.delete("/runs/{run_id}")
async def delete_run(
    run_id: str,
    user: User = Depends(get_current_user),
):
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    run_info = get_run_info(run_id)
    if not run_info:
        raise HTTPException(status_code=404, detail="Run not found")

    project = get_project_by_id(run_info.project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    workspace = get_workspace_by_id(project.workspace_id)
    if not workspace or user.id != workspace.owner_id:
        raise HTTPException(status_code=403, detail="Not authorized to delete this run")

    storage_manager = StorageManager(
        workspace, backend=os.getenv("STORAGE_BACKEND", "s3")
    )
    for file_info in run_info.files:
        storage_manager.delete_file(file_info.path)

    if delete_run_info(run_id):
        logger.info(f"Run {run_id} deleted successfully")
        return {"message": f"Run {run_id} deleted successfully"}
    else:
        raise HTTPException(status_code=500, detail="Failed to delete run")


@run_router.get("/runs/{run_id}", response_model=RunInfo)
async def get_run(
    request: Request,
    run_id: str = Path(..., description="Unique identifier of the run."),
    user: User = Depends(get_current_user),
):
    """
    Retrieve details of a specific run.

    - **Requires:** Valid API Key in the `X-API-Key` header.
    - **Path Parameter:** `run_id` of the run to retrieve.
    - **Returns:** `RunInfo` model containing run details.
    """
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    run_info = get_run_info(run_id)
    if not run_info:
        logger.warning(
            "Run access attempt for non-existent run",
            run_id=run_id,
            user=user,
        )
        raise HTTPException(status_code=404, detail="Run not found")

    if hasattr(request.state, "project"):
        if run_info.project_id != request.state.project.id:
            raise HTTPException(status_code=403, detail="Access denied")
    else:
        project = get_project_by_id(run_info.project_id)
        if not project:
            logger.warning(
                "Run access attempt for non-existent project",
                run_id=run_id,
                project_id=run_info.project_id,
                user=user,
            )
            raise HTTPException(status_code=404, detail="Project not found")

        has_access = ensure_user_has_access(
            project, get_workspace_by_id(project.workspace_id), user
        )

        if not has_access:
            logger.warning(
                "Unauthorized run access attempt", project_id=project.id, user=user
            )
            raise HTTPException(status_code=403, detail="Access denied")

    logger.info("Run accessed", run_id=run_id, user=user)
    return run_info


def ensure_user_has_access(project: Project, workspace: Workspace, user: User) -> bool:
    if workspace.oidc_enabled and user:
        return True
    if user_has_project_access(project, user):
        return True
    if project.public_access:
        logger.info("Granting project access for user", project=project, user=user)
        # Automatically add the user to the project's access list
        return bool(add_user_to_project(project.id or "", user.id, role="member"))
    return False


@run_router.get("/projects/{project_id}/runs", response_model=ProjectRunsResponse)
async def list_runs(
    request: Request,
    project_id: str = Path(..., description="Unique identifier of the project."),
    user: User = Depends(get_current_user),
    limit: Optional[int] = Query(
        10,
        ge=1,
        description="Maximum number of runs to return (minimum: 1).",
        examples=[10],
    ),
    offset: Optional[int] = Query(
        0,
        ge=0,
        description="Number of runs to skip for pagination (minimum: 0).",
        examples=[0],
    ),
):
    """
    Retrieve a list of runs within a specific project.

    - **Requires:** Valid API Key in the `X-API-Key` header.
    - **Path Parameter:** `project_id` of the project.
    - **Query Parameters:** `limit`, `offset`, and tag filters.
    - **Returns:** `ProjectRunsResponse` model containing the list of runs.
    """
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    project = get_project_by_id(project_id)
    if not project:
        logger.warning(
            "Runs access attempt for non-existent project",
            project_id=project_id,
            user=user,
        )
        raise HTTPException(status_code=404, detail="Project not found")

    if hasattr(request.state, "project"):
        if project.id != request.state.project.id:
            raise HTTPException(status_code=403, detail="Access denied")

    workspace = get_workspace_by_id(project.workspace_id)
    has_access = ensure_user_has_access(project, workspace, user)

    if not has_access:
        logger.warning(
            "Unauthorized runs access attempt", project_id=project_id, user=user
        )
        raise HTTPException(status_code=403, detail="Access denied")

    # Reserved query parameters
    reserved_params = ["limit", "offset"]

    # Build tag filters from query parameters
    tag_filters = {}
    for key, value in request.query_params.items():
        if key not in reserved_params and value:
            tag_filters[key.lower()] = value

    logger.info("Listing runs", project_id=project_id, tags=tag_filters)
    runs, total_results = list_project_runs(
        project_id, tag_filters, limit=limit, offset=offset
    )
    storage_used = get_project_storage_usage(project_id)
    logger.info(
        "Runs listed",
        project_id=project_id,
        user=user,
        run_count=len(runs),
        workspace=workspace,
    )

    # Build 'next' link if there are more results
    next_link = None
    offset = offset or 0
    limit = limit or 10
    if offset + limit < total_results:
        # Build the next URL
        query_params = dict(request.query_params)
        query_params["offset"] = str(offset + limit)
        query_params["limit"] = str(limit)
        next_url = request.url.replace(query=urlencode(query_params))
        next_link = str(next_url)

    return {
        "runs": runs,
        "name": project.name,
        "is_owner": bool(workspace and workspace.owner_id == user.id),
        "storage_used": storage_used,
        "total_results": total_results,
        "next": next_link,
    }


@run_router.get("/projects/{project_id}/tags", response_model=Dict[str, List[str]])
async def list_project_tags(
    project_id: str = Path(..., description="Unique identifier of the project."),
    user: User = Depends(get_current_user),
):
    """
    Retrieve all unique tag keys and their possible values within a specific project.

    - **Requires:** Valid API Key in the `X-API-Key` header.
    - **Path Parameter:** `project_id` of the project.
    - **Returns:** Dictionary with tag keys and list of their values.
    """
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    project = get_project_by_id(project_id)
    if not project:
        logger.warning(
            "Runs access attempt for non-existent project",
            project_id=project_id,
            user=user,
        )
        raise HTTPException(status_code=404, detail="Project not found")

    workspace = get_workspace_by_id(project.workspace_id)
    has_access = ensure_user_has_access(project, workspace, user)

    if not has_access:
        logger.warning(
            "Unauthorized runs access attempt", project_id=project_id, user=user
        )
        raise HTTPException(status_code=403, detail="Access denied")

    return get_project_tags(project_id)


@run_router.post("/runs/{run_id}/upload")
async def upload_file(
    request: Request,
    background_tasks: BackgroundTasks,
    run_id: str = Path(..., description="Unique identifier of the run."),
    file: UploadFile = File(..., description="The file to upload."),
    is_output_file: bool = Form(
        False, description="Indicates if this is the output.xml file"
    ),
    user: User = Depends(get_current_user),
):
    """
    Upload a file (e.g., log, report) to an existing run.

    - **Requires:** Valid API Key in the `X-API-Key` header.
    - **Path Parameter:** `run_id` of the run to upload the file to.
    - **Form Data Parameters:**
      - `file` to upload.
      - `is_output_file` (boolean) to indicate if this is the output.xml file.
    - **Returns:** Confirmation message and file details.
    """
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    run_info = get_run_info(run_id)
    project = request.state.project
    if not (run_info and project and project.id == run_info.project_id):
        raise HTTPException(status_code=404, detail="Run not found")

    workspace = get_workspace_by_id(project.workspace_id)
    if (
        not workspace
        or user.id != workspace.owner_id
        or workspace.expiry_date < datetime.utcnow()
    ):
        raise HTTPException(status_code=403, detail="Workspace expired")

    file_path = file.filename or "noname"
    file_path = os.path.normpath(file_path)
    if file_path.startswith("..") or os.path.isabs(file_path):
        raise HTTPException(status_code=400, detail="Invalid file path")

    existing_file = get_file_info(run_id, file_path)
    if existing_file:
        raise HTTPException(
            status_code=400,
            detail=f"File '{file_path}' already exists in this run",
        )

    object_name = f"{run_id}/{file_path}"

    storage_manager = StorageManager(
        workspace, backend=os.getenv("STORAGE_BACKEND", "s3")
    )
    upload_success, file_size = storage_manager.upload_file(file.file, object_name)

    current_storage_usage = get_project_storage_usage(project.id)
    new_total_storage = current_storage_usage + file_size
    if new_total_storage > workspace.storage_limit_bytes:
        storage_manager.delete_file(object_name)
        raise HTTPException(
            status_code=403,
            detail="Storage limit exceeded. Cannot upload the file.",
        )

    if upload_success and file_size is not None:
        add_file_to_run(run_info, file_path, object_name, file_size)
        file_url = f"/files/{object_name}"
        logger.info(
            "File uploaded",
            run_id=run_id,
            file_name=file_path,
            file_size=file_size,
            user=user,
        )

        # If this is the output file, start background task to parse it
        if is_output_file:
            background_tasks.add_task(
                parse_output_xml_background, workspace, run_id, object_name
            )

        return {
            "file_name": file_path,
            "file_url": file_url,
            "file_size": file_size,
            "message": "File uploaded successfully",
        }
    else:
        raise HTTPException(
            status_code=500, detail="Failed to upload file or retrieve file size"
        )


@public_router.get("/files/{run_id}/{file_path:path}")
async def get_file(
    request: Request,
    run_id: str = Path(..., description="Unique identifier of the run."),
    file_path: str = Path(
        ..., description="Path to the file within the run's directory."
    ),
    user: Optional[User] = Depends(get_current_user),
):
    """
    Retrieve (download) a file associated with a run.

    - **Requires:** Valid API Key in the `X-API-Key` header.
    - **Path Parameters:** `run_id` and `file_path`.
    - **Returns:** Streaming content of the file.
    """
    if user is None:
        run_info = get_run_info(run_id)
        if not run_info:
            raise HTTPException(status_code=404, detail="Run not found")

        project = get_project_by_id(run_info.project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")

        workspace = get_workspace_by_id(project.workspace_id)
        if workspace.oidc_enabled:
            # Construct OIDC login URL with 'next' parameter
            oidc_login_url = (
                f"/oidc/{workspace.id}/login?next={quote(str(request.url))}"
            )
            return RedirectResponse(url=oidc_login_url, status_code=302)
        else:
            return RedirectResponse(
                url=f"/login_explanation.html?next={quote(str(request.url))}",
                status_code=302,
            )

    run_info = get_run_info(run_id)
    if not run_info:
        raise HTTPException(status_code=404, detail="Run not found")

    # Check if the request is using API key authentication
    if hasattr(request.state, "project"):
        if run_info.project_id != request.state.project.id:
            raise HTTPException(status_code=403, detail="Access denied")
        workspace = get_workspace_by_id(request.state.project.workspace_id)
    else:
        project = get_project_by_id(run_info.project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")

        workspace = get_workspace_by_id(project.workspace_id)
        has_access = ensure_user_has_access(project, workspace, user)

        if not has_access:
            raise HTTPException(status_code=403, detail="Access denied")

    # Normalize and validate the file path
    file_path = os.path.normpath(file_path)
    if file_path.startswith("..") or os.path.isabs(file_path):
        raise HTTPException(status_code=400, detail="Invalid file path")

    # Retrieve file info from the database
    file_info = get_file_info(run_id, file_path)
    if not file_info:
        raise HTTPException(status_code=404, detail="File not found")

    storage_manager = StorageManager(
        workspace, backend=os.getenv("STORAGE_BACKEND", "s3")
    )
    file_obj = storage_manager.download_file(file_info.path)

    if file_obj:
        # Determine content type
        content_type, _ = mimetypes.guess_type(file_path)
        if content_type is None:
            content_type = "application/octet-stream"

        # Function to stream the content
        def iterfile():
            yield from iter(lambda: file_obj.read(8192), b"")

        return StreamingResponse(
            iterfile(),
            media_type=content_type,
            headers={
                "Content-Disposition": f'inline; filename="{os.path.basename(file_path)}"'
            },
        )
    else:
        raise HTTPException(status_code=404, detail="File not found")


def purge_old_runs():
    runs_and_files = get_runs_and_files_to_purge()

    # Organize runs and files by workspace_id
    runs_by_workspace = {}
    files_by_workspace = {}

    for item in runs_and_files:
        workspace_id = item["workspace_id"]
        run_id = item["run_id"]
        file_path = item["file_path"]

        if workspace_id not in runs_by_workspace:
            runs_by_workspace[workspace_id] = set()
            files_by_workspace[workspace_id] = []

        if run_id:
            runs_by_workspace[workspace_id].add(run_id)
        if file_path:
            files_by_workspace[workspace_id].append(file_path)

    # Delete files from storage per workspace
    for workspace_id, file_paths in files_by_workspace.items():
        workspace = get_workspace_by_id(workspace_id)
        if not workspace:
            logger.error(f"No workspace found for {workspace_id}")
            continue
        storage_manager = StorageManager(
            workspace, backend=os.getenv("STORAGE_BACKEND", "s3")
        )
        for path in file_paths:
            try:
                storage_manager.delete_file(path)
                logger.info(f"Deleted file {path} from workspace {workspace_id}")
            except Exception as e:
                logger.error(
                    f"Failed to delete file {path} from workspace {workspace_id}: {e}"
                )

    # Delete runs and files from database
    all_run_ids = set()
    for run_ids in runs_by_workspace.values():
        all_run_ids.update(run_ids)

    if all_run_ids:
        delete_runs_and_files(list(all_run_ids))

    logger.info("Old runs purged successfully")


def init_runs(app: FastAPI):
    app.include_router(run_router, prefix="/api")
    app.include_router(public_router)
