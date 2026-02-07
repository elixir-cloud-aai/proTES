"""Data models for middleware management API.

This module defines Pydantic models that match the OpenAPI specification
for the Middleware Management API. All models follow the finalized API
design from PR #1 (middleware-api-spec branch).
"""

from typing import List, Optional, Union, Literal

from pydantic import BaseModel, Field


# ============================================================================
# Source Configuration Models (Discriminated Union)
# ============================================================================

class MiddlewareSourceLocal(BaseModel):
    """Local package source configuration (deprecated - development only)."""

    type: Literal["local"]
    entry_point: str = Field(
        ...,
        description=(
            "Class path entry point (e.g., 'package.module.ClassName')"
        ),
        json_schema_extra={"example": (
            "pro_tes.plugins.middlewares.task_distribution.distance."
            "TaskDistributionDistance"
        )}
    )


class MiddlewareSourceGithub(BaseModel):
    """GitHub repository source configuration (recommended for production)."""

    type: Literal["github"]
    entry_point: str = Field(
        ...,
        description=(
            "Class path entry point (e.g., 'package.module.ClassName')"
        ),
        json_schema_extra={"example": "custom_middleware.LoadBalancer"}
    )
    repository: str = Field(
        ...,
        description="Git repository URL",
        pattern=r'^https://github\.com/.+\.git$',
        json_schema_extra={"example": "https://github.com/user/repo.git"}
    )
    version: Optional[str] = Field(
        None,
        description="Git tag or branch name",
        json_schema_extra={"example": "v1.0.0"}
    )


class MiddlewareSourcePypi(BaseModel):
    """PyPI package source configuration (recommended for production)."""

    type: Literal["pypi"]
    entry_point: str = Field(
        ...,
        description=(
            "Class path entry point (e.g., 'package.module.ClassName')"
        ),
        json_schema_extra={"example": "custom.Middleware"}
    )
    package: str = Field(
        ...,
        description="Package name from PyPI",
        json_schema_extra={"example": "protes-middleware-custom"}
    )
    version: Optional[str] = Field(
        None,
        description="Package version",
        json_schema_extra={"example": "1.0.0"}
    )


# Discriminated union of all source types
MiddlewareSource = Union[
    MiddlewareSourceLocal,
    MiddlewareSourceGithub,
    MiddlewareSourcePypi
]


# ============================================================================
# Request/Response Models
# ============================================================================

class MiddlewareCreate(BaseModel):
    """Request model for creating middleware."""

    name: Optional[str] = Field(
        None,
        min_length=1,
        max_length=255,
        description=(
            "Human-readable name. If not provided, "
            "derived from package/repo name."
        ),
        json_schema_extra={"example": "Distance-based Router"}
    )
    source: Union[MiddlewareSource, List[MiddlewareSource]] = Field(
        ...,
        description="Single source or array of sources for fallback groups"
    )
    order: Optional[int] = Field(
        0,
        ge=0,
        description=(
            "Execution order (0 = first). "
            "If not provided, defaults to 0."
        ),
        json_schema_extra={"example": 0}
    )
    config: Optional[dict] = Field(
        None,
        description="Middleware-specific configuration",
        json_schema_extra={"example": {"timeout": 30}, "retries": 3}
    )
    enabled: bool = Field(
        True,
        description="Whether the middleware should be active",
        json_schema_extra={"example": True}
    )


class MiddlewareUpdate(BaseModel):
    """Request model for updating middleware."""

    name: Optional[str] = Field(
        None,
        min_length=1,
        max_length=255,
        description="Human-readable name for the middleware",
        json_schema_extra={"example": "Distance-based Router v2"}
    )
    order: Optional[int] = Field(
        None,
        ge=0,
        description="Execution order",
        json_schema_extra={"example": 1}
    )
    config: Optional[dict] = Field(
        None,
        description="Middleware-specific configuration",
        json_schema_extra={"example": {"timeout": 60}, "retries": 5}
    )
    enabled: Optional[bool] = Field(
        None,
        description="Whether the middleware is active",
        json_schema_extra={"example": False}
    )


class MiddlewareConfig(BaseModel):
    """Complete middleware configuration (response model)."""

    id: str = Field(
        ...,
        alias="_id",
        description="Unique identifier (MongoDB ObjectId)",
        json_schema_extra={"example": "507f1f77bcf86cd799439011"}
    )
    name: Optional[str] = Field(
        None,
        description="Human-readable name for the middleware",
        json_schema_extra={"example": "Distance-based Router"}
    )
    source: Union[MiddlewareSource, List[MiddlewareSource]] = Field(
        ...,
        description="Single source or array of sources for fallback groups"
    )
    order: int = Field(
        ...,
        description="Execution order (0 = first)",
        json_schema_extra={"example": 0}
    )
    config: Optional[dict] = Field(
        None,
        description="Middleware-specific configuration",
        json_schema_extra={"example": {"timeout": 30}, "retries": 3}
    )
    enabled: bool = Field(
        ...,
        description="Whether the middleware is active",
        json_schema_extra={"example": True}
    )
    created_at: str = Field(
        ...,
        description="Creation timestamp",
        json_schema_extra={"example": "2026-01-24T10:30:00Z"}
    )
    updated_at: str = Field(
        ...,
        description="Last update timestamp",
        json_schema_extra={"example": "2026-01-24T10:30:00Z"}
    )

    class Config:
        """Pydantic model configuration."""

        populate_by_name = True


class PaginationInfo(BaseModel):
    """Pagination information following GA4GH guidelines."""

    page: int = Field(
        ...,
        description="Current page number (0-indexed)",
        json_schema_extra={"example": 0}
    )
    page_size: int = Field(
        ...,
        description="Number of results per page",
        json_schema_extra={"example": 50}
    )
    total: int = Field(
        ...,
        description="Total number of middlewares available",
        json_schema_extra={"example": 5}
    )
    total_pages: int = Field(
        ...,
        description="Total number of pages available",
        json_schema_extra={"example": 1}
    )


class MiddlewareList(BaseModel):
    """Response model for list of middlewares."""

    middlewares: List[dict] = Field(
        ...,
        description="Array of middleware configurations"
    )
    pagination: PaginationInfo = Field(
        ...,
        description="Pagination information"
    )


class MiddlewareCreateResponse(BaseModel):
    """Response model for middleware creation."""

    id: str = Field(
        ...,
        alias="_id",
        description="Unique identifier of created middleware",
        json_schema_extra={"example": "507f1f77bcf86cd799439011"}
    )
    order: int = Field(
        ...,
        description="Assigned execution order",
        json_schema_extra={"example": 0}
    )
    message: str = Field(
        ...,
        description="Success message",
        json_schema_extra={"example": "Middleware added successfully"}
    )

    class Config:
        """Pydantic model configuration."""

        populate_by_name = True


class MiddlewareOrder(BaseModel):
    """Request model for reordering middlewares."""

    ordered_ids: List[str] = Field(
        ...,
        min_length=1,
        description="Array of middleware IDs in desired execution order",
        json_schema_extra={
            "example": [
                "507f1f77bcf86cd799439011",
                "507f1f77bcf86cd799439012"
            ]
        }
    )


# ============================================================================
# MongoDB Document Model (Internal Use)
# ============================================================================

class MiddlewareDocument(BaseModel):
    """MongoDB document structure for middleware storage (internal use)."""

    name: Optional[str]
    source: Union[MiddlewareSource, List[MiddlewareSource]]
    order: int
    enabled: bool = True
    config: Optional[dict] = None
    created_at: str
    updated_at: str
