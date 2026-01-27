"""Data models for middleware management."""

from typing import List, Optional, Union

from pydantic import BaseModel


class MiddlewareDocument(BaseModel):
    """MongoDB document structure for middleware storage."""
    
    name: str
    class_path: Union[str, List[str]]
    order: int
    enabled: bool = True
    config: Optional[dict] = None
    source: str
    github_url: Optional[str] = None
    created_at: str
    updated_at: str
    deleted_at: Optional[str] = None


class MiddlewareCreate(BaseModel):
    """Request model for creating middleware."""
    
    name: str
    class_path: Union[str, List[str]]
    order: Optional[int] = None
    enabled: bool = True
    config: Optional[dict] = None
    github_url: Optional[str] = None


class MiddlewareUpdate(BaseModel):
    """Request model for updating middleware."""
    
    name: Optional[str] = None
    order: Optional[int] = None
    config: Optional[dict] = None
    enabled: Optional[bool] = None


class MiddlewareList(BaseModel):
    """Response model for list of middlewares."""
    
    middlewares: List[dict]
    total: int


class MiddlewareCreateResponse(BaseModel):
    """Response model for middleware creation."""
    
    _id: str
    order: int
    message: str


class MiddlewareOrder(BaseModel):
    """Request model for reordering middlewares."""
    
    ordered_ids: List[str]


class ValidationRequest(BaseModel):
    """Request model for code validation."""
    
    class_path: Optional[str] = None
    code: Optional[str] = None
    github_url: Optional[str] = None


class ValidationResponse(BaseModel):
    """Response model for code validation."""
    
    valid: bool
    message: str
    errors: Optional[List[dict]] = []
    warnings: Optional[List[dict]] = []
