# server/rflogs_server/routes/public_routes.py

from urllib.parse import urlencode
from fastapi import APIRouter, HTTPException, Path, Query, Request
from typing import Dict, List, Optional

from ..database.users import get_workspace_by_id
from ..database.projects import get_project_by_id
from ..database.runs import (
    get_project_tags,
    get_run_info,
    list_project_runs,
    get_file_info,
)
from ..models import ProjectRunsResponse, RunInfo
from ..storage import StorageManager
from fastapi.responses import StreamingResponse
import os
import mimetypes

public_router = APIRouter()

PUBLIC_PROJECTS = {
    "hbeEbQe1QXebFO5fFH7VCQ": "Robot Framework",
    "dAHHzA7pRQCiUG9nroKAvQ": "Robot Framework Browser",
}


@public_router.get(
    "/public/projects/{project_id}/runs",
    response_model=ProjectRunsResponse,
    include_in_schema=False,
)
async def list_public_runs(
    request: Request,
    project_id: str = Path(..., description="Unique identifier of the project."),
    limit: Optional[int] = Query(
        20, ge=1, le=100, description="Maximum number of runs to return."
    ),
    offset: Optional[int] = Query(
        0, ge=0, description="Number of runs to skip for pagination."
    ),
):
    if project_id not in PUBLIC_PROJECTS:
        raise HTTPException(status_code=404, detail="Project not found")

    project = get_project_by_id(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Extract tag filters from query parameters
    tag_filters = {}
    for key, value in request.query_params.items():
        if key not in ["limit", "offset"] and value:
            tag_filters[key.lower()] = value

    runs, total_results = list_project_runs(
        project_id, tag_filters, limit=limit, offset=offset
    )

    next_link = None
    offset = offset or 0
    limit = limit or 0
    if offset + limit < total_results:
        query_params = dict(request.query_params)
        query_params["offset"] = str(offset + limit)
        query_params["limit"] = str(limit)
        next_url = request.url.replace(query=urlencode(query_params))
        next_link = str(next_url)

    return {
        "runs": runs,
        "name": PUBLIC_PROJECTS[project_id],
        "is_owner": False,
        "storage_used": 0,  # We don't expose storage info for public projects
        "total_results": total_results,
        "next": next_link,
    }


@public_router.get(
    "/public/runs/{run_id}", response_model=RunInfo, include_in_schema=False
)
async def get_public_run(
    run_id: str = Path(..., description="Unique identifier of the run."),
):
    run_info = get_run_info(run_id)
    if not run_info or run_info.project_id not in PUBLIC_PROJECTS:
        raise HTTPException(status_code=404, detail="Run not found")

    return run_info


@public_router.get(
    "/public/projects/{project_id}/tags",
    response_model=Dict[str, List[str]],
    include_in_schema=False,
)
async def get_public_project_tags(
    project_id: str = Path(..., description="Unique identifier of the project."),
):
    if project_id not in PUBLIC_PROJECTS:
        raise HTTPException(status_code=404, detail="Project not found")

    project = get_project_by_id(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    tags = get_project_tags(project_id)
    return tags


@public_router.get("/public/files/{run_id}/{file_path:path}", include_in_schema=False)
async def get_public_file(
    run_id: str = Path(..., description="Unique identifier of the run."),
    file_path: str = Path(
        ..., description="Path to the file within the run's directory."
    ),
):
    run_info = get_run_info(run_id)
    if not run_info or run_info.project_id not in PUBLIC_PROJECTS:
        raise HTTPException(status_code=404, detail="File not found")

    # Normalize and validate the file path
    file_path = os.path.normpath(file_path)
    if file_path.startswith("..") or os.path.isabs(file_path):
        raise HTTPException(status_code=400, detail="Invalid file path")

    file_info = get_file_info(run_id, file_path)
    if not file_info:
        raise HTTPException(status_code=404, detail="File not found")

    project = get_project_by_id(run_info.project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    workspace = get_workspace_by_id(project.workspace_id)

    if not workspace:
        raise HTTPException(status_code=404, detail="Workspace not found")

    storage_manager = StorageManager(
        workspace, backend=os.getenv("STORAGE_BACKEND", "s3")
    )
    file_obj = storage_manager.download_file(file_info.path)

    if file_obj:
        content_type, _ = mimetypes.guess_type(file_path)
        if content_type is None:
            content_type = "application/octet-stream"

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
