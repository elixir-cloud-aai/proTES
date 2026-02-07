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
        description="Class path entry point (e.g., 'package.module.ClassName')",
        example="pro_tes.plugins.middlewares.task_distribution.distance.TaskDistributionDistance"
    )


class MiddlewareSourceGithub(BaseModel):
    """GitHub repository source configuration (recommended for production)."""
    
    type: Literal["github"]
    entry_point: str = Field(
        ...,
        description="Class path entry point (e.g., 'package.module.ClassName')",
        example="custom_middleware.LoadBalancer"
    )
    repository: str = Field(
        ...,
        description="Git repository URL",
        pattern=r'^https://github\.com/.+\.git$',
        example="https://github.com/user/repo.git"
    )
    version: Optional[str] = Field(
        None,
        description="Git tag or branch name",
        example="v1.0.0"
    )


class MiddlewareSourcePypi(BaseModel):
    """PyPI package source configuration (recommended for production)."""
    
    type: Literal["pypi"]
    entry_point: str = Field(
        ...,
        description="Class path entry point (e.g., 'package.module.ClassName')",
        example="custom.Middleware"
    )
    package: str = Field(
        ...,
        description="Package name from PyPI",
        example="protes-middleware-custom"
    )
    version: Optional[str] = Field(
        None,
        description="Package version",
        example="1.0.0"
    )


# Discriminated union of all source types
MiddlewareSource = Union[MiddlewareSourceLocal, MiddlewareSourceGithub, MiddlewareSourcePypi]


# ============================================================================
# Request/Response Models
# ============================================================================

class MiddlewareCreate(BaseModel):
    """Request model for creating middleware."""
    
    name: Optional[str] = Field(
        None,
        min_length=1,
        max_length=255,
        description="Human-readable name. If not provided, derived from package/repo name.",
        example="Distance-based Router"
    )
    source: Union[MiddlewareSource, List[MiddlewareSource]] = Field(
        ...,
        description="Single source or array of sources for fallback groups"
    )
    order: Optional[int] = Field(
        0,
        ge=0,
        description="Execution order (0 = first). If not provided, defaults to 0.",
        example=0
    )
    config: Optional[dict] = Field(
        None,
        description="Middleware-specific configuration",
        example={"timeout": 30, "retries": 3}
    )
    enabled: bool = Field(
        True,
        description="Whether the middleware should be active",
        example=True
    )


class MiddlewareUpdate(BaseModel):
    """Request model for updating middleware."""
    
    name: Optional[str] = Field(
        None,
        min_length=1,
        max_length=255,
        description="Human-readable name for the middleware",
        example="Distance-based Router v2"
    )
    order: Optional[int] = Field(
        None,
        ge=0,
        description="Execution order",
        example=1
    )
    config: Optional[dict] = Field(
        None,
        description="Middleware-specific configuration",
        example={"timeout": 60, "retries": 5}
    )
    enabled: Optional[bool] = Field(
        None,
        description="Whether the middleware is active",
        example=False
    )


class MiddlewareConfig(BaseModel):
    """Complete middleware configuration (response model)."""
    
    id: str = Field(
        ...,
        alias="_id",
        description="Unique identifier (MongoDB ObjectId)",
        example="507f1f77bcf86cd799439011"
    )
    name: Optional[str] = Field(
        None,
        description="Human-readable name for the middleware",
        example="Distance-based Router"
    )
    source: Union[MiddlewareSource, List[MiddlewareSource]] = Field(
        ...,
        description="Single source or array of sources for fallback groups"
    )
    order: int = Field(
        ...,
        description="Execution order (0 = first)",
        example=0
    )
    config: Optional[dict] = Field(
        None,
        description="Middleware-specific configuration",
        example={"timeout": 30, "retries": 3}
    )
    enabled: bool = Field(
        ...,
        description="Whether the middleware is active",
        example=True
    )
    created_at: str = Field(
        ...,
        description="Creation timestamp",
        example="2026-01-24T10:30:00Z"
    )
    updated_at: str = Field(
        ...,
        description="Last update timestamp",
        example="2026-01-24T10:30:00Z"
    )
    
    class Config:
        populate_by_name = True


class PaginationInfo(BaseModel):
    """Pagination information following GA4GH guidelines."""
    
    page: int = Field(
        ...,
        description="Current page number (0-indexed)",
        example=0
    )
    page_size: int = Field(
        ...,
        description="Number of results per page",
        example=50
    )
    total: int = Field(
        ...,
        description="Total number of middlewares available",
        example=5
    )
    total_pages: int = Field(
        ...,
        description="Total number of pages available",
        example=1
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
        example="507f1f77bcf86cd799439011"
    )
    order: int = Field(
        ...,
        description="Assigned execution order",
        example=0
    )
    message: str = Field(
        ...,
        description="Success message",
        example="Middleware added successfully"
    )
    
    class Config:
        populate_by_name = True


class MiddlewareOrder(BaseModel):
    """Request model for reordering middlewares."""
    
    ordered_ids: List[str] = Field(
        ...,
        min_items=1,
        description="Array of middleware IDs in desired execution order",
        example=["507f1f77bcf86cd799439011", "507f1f77bcf86cd799439012"]
    )


# ============================================================================
# MongoDB Document Model (Internal Use)
# ============================================================================

class MiddlewareDocument(BaseModel):
    """MongoDB document structure for middleware storage (internal use only)."""
    
    name: Optional[str]
    source: Union[MiddlewareSource, List[MiddlewareSource]]
    order: int
    enabled: bool = True
    config: Optional[dict] = None
    created_at: str
    updated_at: str
