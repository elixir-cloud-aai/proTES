"""Controllers for middleware management API.

This module implements all REST API endpoints for managing middlewares
dynamically at runtime. All implementations match the finalized OpenAPI
specification from PR #1 (middleware-api-spec branch).
"""

import logging
import math
from datetime import datetime
from typing import Optional

from bson import ObjectId
from flask import current_app, request
from pymongo.errors import PyMongoError
from werkzeug.exceptions import InternalServerError

from pro_tes.exceptions import (
    BadRequest,
    MiddlewareNotFound,
    MiddlewareDuplicateName,
)
from pro_tes.api.middlewares.models import (
    MiddlewareCreate,
    MiddlewareUpdate,
)

logger = logging.getLogger(__name__)


def get_middleware_collection():
    """Get middleware collection from database."""
    return current_app.config.foca.db.dbs["taskStore"].collections[
        "middlewares"
    ].client


def _extract_entry_points(source):
    """Extract all entry points from a source (single or fallback group).

    Args:
        source: Single MiddlewareSource dict/object or list of
            MiddlewareSource dicts/objects

    Returns:
        List of entry point strings
    """
    if isinstance(source, list):
        return [
            (s.entry_point if hasattr(s, 'entry_point')
             else s.get("entry_point"))
            for s in source
            if ((hasattr(s, 'entry_point') and s.entry_point) or
                (hasattr(s, 'get') and s.get("entry_point")))
        ]
    # Handle both Pydantic objects and dicts
    if hasattr(source, 'entry_point'):
        return [source.entry_point] if source.entry_point else []
    return [source.get("entry_point")] if source.get("entry_point") else []


def _derive_name_from_source(source):
    """Derive middleware name from source configuration.

    Args:
        source: Single MiddlewareSource dict/object or list of
            MiddlewareSource dicts/objects

    Returns:
        Derived name string
    """
    if isinstance(source, list):
        # For fallback groups, use first source
        source = source[0]

    # Handle both Pydantic objects and dicts
    # Try to get package name
    package = (getattr(source, 'package', None)
               if hasattr(source, 'package')
               else (source.get('package')
                     if hasattr(source, 'get') else None))
    if package:
        return package

    # Try to get repository name
    repository = (getattr(source, 'repository', None)
                  if hasattr(source, 'repository')
                  else (source.get('repository')
                        if hasattr(source, 'get') else None))
    if repository:
        # Extract repo name from URL
        repo_name = repository.rstrip("/").rstrip(".git").split("/")[-1]
        return repo_name

    # Try to get entry_point
    entry_point = (getattr(source, 'entry_point', None)
                   if hasattr(source, 'entry_point')
                   else (source.get('entry_point')
                         if hasattr(source, 'get') else None))
    if entry_point:
        # Use last part of entry_point
        return entry_point.split(".")[-1]

    return "Unnamed Middleware"


def ListMiddlewares(
    page_size: int = 50,
    page: int = 0,
    sort_by: str = "order",
    enabled: Optional[bool] = None,
    source: Optional[str] = None,
) -> dict:
    """List all middlewares with pagination and filtering.

    Args:
        page_size: Maximum number of results to return per page.
        page: Page number to retrieve (0-indexed).
        sort_by: Field to sort by.
        enabled: Filter by enabled status.
        source: Filter by source type.

    Returns:
        Dictionary with middlewares list and pagination info.
    """
    try:
        collection = get_middleware_collection()

        filter_dict = {}
        if enabled is not None:
            filter_dict["enabled"] = enabled
        if source is not None:
            filter_dict["source.type"] = source

        # Calculate pagination
        skip = page * page_size

        cursor = collection.find(
            filter_dict
        ).sort(sort_by, 1).skip(skip).limit(page_size)

        middlewares = []
        for doc in cursor:
            doc["_id"] = str(doc["_id"])  # Convert ObjectId to string
            middlewares.append(doc)

        total = collection.count_documents(filter_dict)
        total_pages = math.ceil(total / page_size) if total > 0 else 0

        return {
            "middlewares": middlewares,
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total": total,
                "total_pages": total_pages
            }
        }
    except PyMongoError as e:
        logger.error(f"Database error: {e}")
        raise InternalServerError("Database operation failed")


def AddMiddleware() -> tuple:
    """Add a new middleware to the execution stack.

    Returns:
        Tuple of response dict and HTTP status code.
    """
    try:
        collection = get_middleware_collection()
        data = request.json

        middleware = MiddlewareCreate(**data)

        # Derive name if not provided
        name = middleware.name
        if not name:
            name = _derive_name_from_source(middleware.source)

        # Check for duplicate name
        if name:
            existing = collection.find_one({"name": name})
            if existing:
                raise MiddlewareDuplicateName(
                    f"Middleware with name '{name}' already exists"
                )

        # Check for duplicate entry_point
        entry_points = _extract_entry_points(middleware.source)
        for entry_point in entry_points:
            existing_ep = collection.find_one(
                {"source.entry_point": entry_point}
            )
            if existing_ep:
                raise BadRequest(
                    f"Middleware with entry_point "
                    f"'{entry_point}' already exists"
                )

        # Handle order assignment
        order = middleware.order if middleware.order is not None else 0

        if order is not None:
            # Shift existing middlewares at this position or higher
            collection.update_many(
                {"order": {"$gte": order}},
                {"$inc": {"order": 1}}
            )

        now = datetime.utcnow().isoformat() + "Z"

        # Convert Pydantic model source to dict for MongoDB storage
        source_data = middleware.source
        if isinstance(source_data, list):
            source_data = [
                s.model_dump() if hasattr(s, 'model_dump') else s
                for s in source_data
            ]
        elif hasattr(source_data, 'model_dump'):
            source_data = source_data.model_dump()

        doc = {
            "name": name,
            "source": source_data,
            "order": order,
            "enabled": middleware.enabled,
            "config": middleware.config,
            "created_at": now,
            "updated_at": now
        }

        result = collection.insert_one(doc)
        middleware_id = str(result.inserted_id)

        logger.info(f"Created middleware: {name} (ID: {middleware_id})")

        return {
            "_id": middleware_id,
            "order": order,
            "message": "Middleware created successfully"
        }, 201

    except (BadRequest, MiddlewareDuplicateName):
        raise
    except Exception as e:
        logger.error(f"Error creating middleware: {e}")
        raise InternalServerError("Failed to create middleware")


def GetMiddleware(middleware_id: str) -> dict:
    """Get middleware details by ID.

    Args:
        middleware_id: Middleware identifier.

    Returns:
        Middleware configuration dict.
    """
    try:
        collection = get_middleware_collection()

        if not ObjectId.is_valid(middleware_id):
            raise BadRequest("Invalid middleware ID format")

        document = collection.find_one({"_id": ObjectId(middleware_id)})

        if document is None:
            raise MiddlewareNotFound(
                f"Middleware with ID '{middleware_id}' not found"
            )

        # Convert ObjectId to string for JSON serialization
        document["_id"] = str(document["_id"])

        return document

    except (BadRequest, MiddlewareNotFound):
        raise
    except Exception as e:
        logger.error(f"Error retrieving middleware: {e}")
        raise InternalServerError("Failed to retrieve middleware")


def UpdateMiddleware(middleware_id: str) -> dict:
    """Update middleware configuration.

    Args:
        middleware_id: Middleware identifier.

    Returns:
        Updated middleware configuration.
    """
    try:
        collection = get_middleware_collection()

        if not ObjectId.is_valid(middleware_id):
            raise BadRequest("Invalid middleware ID format")

        existing = collection.find_one({"_id": ObjectId(middleware_id)})
        if not existing:
            raise MiddlewareNotFound(
                f"Middleware with ID '{middleware_id}' not found"
            )

        data = request.json
        update_data = MiddlewareUpdate(**data)

        update_dict = {}

        if update_data.name is not None:
            if update_data.name != existing.get("name"):
                name_exists = collection.find_one({"name": update_data.name})
                if name_exists:
                    raise MiddlewareDuplicateName(
                        f"Middleware with name "
                        f"'{update_data.name}' already exists"
                    )
            update_dict["name"] = update_data.name

        if (update_data.order is not None and
                update_data.order != existing["order"]):
            old_order = existing["order"]
            new_order = update_data.order

            if new_order > old_order:
                collection.update_many(
                    {"order": {"$gt": old_order, "$lte": new_order}},
                    {"$inc": {"order": -1}}
                )
            else:
                collection.update_many(
                    {"order": {"$gte": new_order, "$lt": old_order}},
                    {"$inc": {"order": 1}}
                )

            update_dict["order"] = new_order

        if update_data.config is not None:
            update_dict["config"] = update_data.config

        if update_data.enabled is not None:
            update_dict["enabled"] = update_data.enabled

        update_dict["updated_at"] = datetime.utcnow().isoformat() + "Z"

        collection.update_one(
            {"_id": ObjectId(middleware_id)},
            {"$set": update_dict}
        )

        updated_doc = collection.find_one({"_id": ObjectId(middleware_id)})
        if updated_doc:
            updated_doc["_id"] = str(updated_doc["_id"])

        logger.info(f"Updated middleware: {middleware_id}")

        return updated_doc

    except (BadRequest, MiddlewareNotFound):
        raise
    except Exception as e:
        logger.error(f"Error updating middleware: {e}")
        raise InternalServerError("Failed to update middleware")


def DeleteMiddleware(middleware_id: str) -> tuple:
    """Delete middleware (hard delete only - soft delete removed).

    Args:
        middleware_id: Middleware identifier.

    Returns:
        Empty tuple with status code 204.
    """
    try:
        collection = get_middleware_collection()

        if not ObjectId.is_valid(middleware_id):
            raise BadRequest("Invalid middleware ID format")

        middleware = collection.find_one({"_id": ObjectId(middleware_id)})
        if not middleware:
            raise MiddlewareNotFound(
                f"Middleware with ID '{middleware_id}' not found"
            )

        deleted_order = middleware["order"]

        # Hard delete (soft delete feature removed per PR #1 review)
        collection.delete_one({"_id": ObjectId(middleware_id)})

        # Shift down middlewares with higher order
        collection.update_many(
            {"order": {"$gt": deleted_order}},
            {"$inc": {"order": -1}}
        )

        logger.info(f"Deleted middleware: {middleware_id}")

        return "", 204

    except (BadRequest, MiddlewareNotFound):
        raise
    except Exception as e:
        logger.error(f"Error deleting middleware: {e}")
        raise InternalServerError("Failed to delete middleware")


def ReorderMiddlewares() -> dict:
    """Reorder the entire middleware stack.

    Returns:
        Success message with updated middleware list.
    """
    try:
        collection = get_middleware_collection()
        data = request.json

        # Use correct field name from OpenAPI spec
        middleware_ids = data.get("ordered_ids", [])

        if not middleware_ids:
            raise BadRequest("ordered_ids array is required")

        if len(middleware_ids) != len(set(middleware_ids)):
            raise BadRequest("Duplicate middleware IDs in array")

        # Count total middlewares (no soft delete filter needed)
        total_count = collection.count_documents({})
        if len(middleware_ids) != total_count:
            raise BadRequest(
                f"Array must contain all {total_count} middlewares"
            )

        for middleware_id in middleware_ids:
            if not ObjectId.is_valid(middleware_id):
                raise BadRequest(f"Invalid middleware ID: {middleware_id}")

            exists = collection.find_one({"_id": ObjectId(middleware_id)})
            if not exists:
                raise MiddlewareNotFound(
                    f"Middleware with ID '{middleware_id}' not found"
                )

        now = datetime.utcnow().isoformat() + "Z"
        for new_order, middleware_id in enumerate(middleware_ids):
            collection.update_one(
                {"_id": ObjectId(middleware_id)},
                {"$set": {"order": new_order, "updated_at": now}}
            )

        middlewares = list(collection.find({}).sort("order", 1))
        # Convert ObjectIds to strings
        for mw in middlewares:
            mw["_id"] = str(mw["_id"])

        logger.info("Reordered middleware stack")

        return {
            "message": "Middleware stack reordered successfully",
            "middlewares": middlewares
        }

    except (BadRequest, MiddlewareNotFound):
        raise
    except Exception as e:
        logger.error(f"Error reordering middlewares: {e}")
        raise InternalServerError("Failed to reorder middlewares")


# Note: ValidateMiddleware endpoint removed per PR #1 review comments
# The validation endpoint was removed from the OpenAPI spec and
# should not be implemented
