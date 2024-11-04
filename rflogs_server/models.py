from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_RETENTION_DAYS = 180  # 6 months


class FileInfo(BaseModel):
    id: str = Field(
        ...,
        description="Unique identifier of the file.",
        json_schema_extra={"example": "file123"},
    )
    name: str = Field(
        ..., description="Original filename.", json_schema_extra={"example": "log.html"}
    )
    path: str = Field(
        ...,
        description="Storage path of the file.",
        json_schema_extra={"example": "abc123/log.html"},
    )
    size: int = Field(
        ...,
        description="Size of the file in bytes.",
        json_schema_extra={"example": 102400},
    )
    created_at: Optional[datetime] = Field(
        None,
        description="Timestamp when the file was uploaded.",
        json_schema_extra={"example": "2023-10-01T12:34:56Z"},
    )


class WorkspaceConfig(BaseModel):
    storage_limit_bytes: int = 20 * 1024**3  # 20 GB default
    active_projects_limit: int = 10  # Default project limit


class Workspace(BaseModel):
    id: str
    name: str
    owner_id: str
    created_at: datetime
    storage_limit_bytes: int = WorkspaceConfig().storage_limit_bytes
    active_projects_limit: int = WorkspaceConfig().active_projects_limit
    oidc_enabled: Optional[bool] = False
    oidc_provider_url: Optional[str] = None
    oidc_client_id: Optional[str] = None
    oidc_issuer_url: Optional[str] = None
    bucket_name: Optional[str] = None  # For storage backend
    oidc_client_secret: Optional[str] = None  # For OIDC auth


class WorkspacePublic(BaseModel):
    id: str
    name: str
    owner_id: str
    storage_limit_bytes: int
    active_projects_limit: int
    oidc_enabled: Optional[bool] = False
    oidc_provider_url: Optional[str] = None
    oidc_client_id: Optional[str] = None
    oidc_issuer_url: Optional[str] = None


class WorkspaceUpdate(BaseModel):
    oidc_enabled: Optional[bool] = None
    oidc_provider_url: Optional[str] = None
    oidc_client_id: Optional[str] = None
    oidc_client_secret: Optional[str] = None
    oidc_issuer_url: Optional[str] = None


class User(BaseModel):
    id: str
    username: Optional[str] = None
    email: Optional[str] = None
    created_at: datetime


class Project(BaseModel):
    id: Optional[str] = None
    name: str
    workspace_id: str
    public_access: bool
    retention_days: int
    created_at: Optional[datetime] = None
    shared_with: List[str] = []

    @field_validator("retention_days")
    def validate_retention_days(cls, v):
        if v < 0:
            raise ValueError("retention_days must be non-negative")
        if v > MAX_RETENTION_DAYS:
            raise ValueError(f"retention_days cannot exceed {MAX_RETENTION_DAYS}")
        return v


class TimingStats(BaseModel):
    total_time: float = Field(..., description="Total execution time in seconds")
    call_count: int = Field(..., description="Number of calls")
    average_time: float = Field(..., description="Average execution time in seconds")
    median_time: float = Field(..., description="Median execution time in seconds")
    std_deviation: float = Field(
        ..., description="Standard deviation of execution time in seconds"
    )


class ParsedRunStats(BaseModel):
    total_tests: int = Field(..., description="Total number of tests executed.")
    passed: int = Field(..., description="Number of tests that passed.")
    failed: int = Field(..., description="Number of tests that failed.")
    skipped: int = Field(..., description="Number of tests that were skipped.")
    verdict: str = Field(
        ..., description="Overall verdict of the run. Values: 'pass' or 'fail'."
    )
    start_time: Optional[datetime] = Field(None, description="Start time of the run.")
    end_time: Optional[datetime] = Field(None, description="End time of the run.")
    failed_test_names: List[str] = Field(
        default_factory=list, description="Names of the failed tests."
    )
    timing_stats: Dict[str, Dict[str, TimingStats]] = Field(
        default_factory=dict,
        description="Timing statistics for suites, tests, and keywords.",
    )


class RunCreate(BaseModel):
    tags: Optional[List[str]] = Field(
        default=[],
        description="List of tags associated with the run.",
        json_schema_extra={"example": ["release_v1.0", "integration_test"]},
    )


class RunInfo(BaseModel):
    id: str = Field(..., description="Unique identifier of the run.")
    project_id: str = Field(..., description="Unique identifier of the project.")
    project_name: str = Field(..., description="Project name that this run is part of.")
    public_access: bool = Field(
        ..., description="Indicates if the run is publicly accessible."
    )
    files: List[FileInfo] = Field(
        ..., description="List of files associated with the run."
    )
    created_at: datetime = Field(..., description="Timestamp when the run was created.")
    total_tests: Optional[int] = Field(
        None, description="Total number of tests executed."
    )
    passed: Optional[int] = Field(None, description="Number of tests that passed.")
    failed: Optional[int] = Field(None, description="Number of tests that failed.")
    skipped: Optional[int] = Field(
        None, description="Number of tests that were skipped."
    )
    verdict: Optional[str] = Field(None, description="Overall verdict of the run.")
    tags: Dict[str, str] = Field(
        default_factory=dict,
        description="Key-value pairs of tags associated with the run.",
    )
    start_time: Optional[datetime] = Field(None, description="Start time of the run.")
    end_time: Optional[datetime] = Field(None, description="End time of the run.")
    failed_test_names: List[str] = Field(
        default_factory=list, description="Names of the failed tests."
    )
    timing_stats: Dict[str, Dict[str, TimingStats]] = Field(default_factory=dict)


class ProjectInvitation(BaseModel):
    id: str
    project_id: str
    inviter_id: str
    invitee_username: str
    created_at: datetime
    expires_at: datetime


class ProjectRunsResponse(BaseModel):
    runs: List[RunInfo] = Field(..., description="List of runs within the project.")
    name: str = Field(..., description="Name of the project.")
    is_owner: bool = Field(
        ..., description="Indicates if the authenticated user is the owner."
    )
    storage_used: int = Field(
        ..., description="Total storage used by the project in bytes."
    )
    total_results: int = Field(
        ..., description="Total number of runs matching the query."
    )
    next: Optional[str] = Field(None, description="URL for the next page of results.")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "runs": [],
                "name": "My Project",
                "is_owner": True,
                "storage_used": 104857600,
                "total_results": 25,
                "next": "/api/projects/proj123/runs?offset=10&limit=10",
            }
        }
    )
