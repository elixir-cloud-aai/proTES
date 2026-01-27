"""Controllers for middleware management API."""

import logging
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
    MiddlewareDuplicateClassPath,
    MiddlewareValidationError,
    MiddlewareCodeFetchError
)
from pro_tes.api.middlewares.models import (
    MiddlewareCreate,
    MiddlewareUpdate,
)
from pro_tes.api.middlewares.validation import validate_middleware_code

logger = logging.getLogger(__name__)


def get_middleware_collection():
    """Get middleware collection from database."""
    return current_app.config.foca.db.dbs["taskStore"].collections[
        "middlewares"
    ].client


def ListMiddlewares(
    limit: int = 50,
    offset: int = 0,
    sort_by: str = "order",
    enabled: Optional[bool] = None,
    source: Optional[str] = None,
) -> dict:
    """List all middlewares with pagination and filtering.
    
    Args:
        limit: Maximum number of results to return.
        offset: Number of results to skip.
        sort_by: Field to sort by.
        enabled: Filter by enabled status.
        source: Filter by source type.
        
    Returns:
        Dictionary with middlewares list and total count.
    """
    try:
        collection = get_middleware_collection()
        
        filter_dict = {}
        if enabled is not None:
            filter_dict["enabled"] = enabled
        if source is not None:
            filter_dict["source"] = source
        
        # Exclude soft-deleted middlewares
        filter_dict["deleted_at"] = {"$exists": False}
        
        cursor = collection.find(
            filter_dict
        ).sort(sort_by, 1).skip(offset).limit(limit)
        
        middlewares = []
        for doc in cursor:
            doc["_id"] = str(doc["_id"])  # Convert ObjectId to string
            middlewares.append(doc)
        
        total = collection.count_documents(filter_dict)
        
        return {
            "middlewares": middlewares,
            "total": total,
            "limit": limit,
            "offset": offset
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
        
        existing = collection.find_one({"name": middleware.name})
        if existing:
            raise MiddlewareDuplicateName(f"Middleware with name '{middleware.name}' already exists")
        
        class_path_str = (
            middleware.class_path if isinstance(middleware.class_path, str)
            else str(middleware.class_path)
        )
        existing_path = collection.find_one({"class_path": class_path_str})
        if existing_path:
            raise MiddlewareDuplicateClassPath(
                f"Middleware with class_path '{class_path_str}' already exists"
            )
        
        if middleware.order is None:
            max_doc = collection.find_one(sort=[("order", -1)])
            order = (max_doc["order"] + 1) if max_doc else 0
        else:
            order = middleware.order
            collection.update_many(
                {"order": {"$gte": order}},
                {"$inc": {"order": 1}}
            )
        
        source = "github" if middleware.github_url else "local"
        now = datetime.utcnow().isoformat() + "Z"
        
        doc = {
            "name": middleware.name,
            "class_path": middleware.class_path,
            "order": order,
            "enabled": middleware.enabled,
            "config": middleware.config,
            "source": source,
            "github_url": middleware.github_url,
            "created_at": now,
            "updated_at": now
        }
        
        result = collection.insert_one(doc)
        middleware_id = str(result.inserted_id)
        
        logger.info(f"Created middleware: {middleware.name} (ID: {middleware_id})")
        
        return {
            "_id": middleware_id,
            "order": order,
            "message": "Middleware created successfully"
        }, 201
        
    except BadRequest:
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
            raise MiddlewareNotFound(f"Middleware with ID '{middleware_id}' not found")
        
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
            raise MiddlewareNotFound(f"Middleware with ID '{middleware_id}' not found")
        
        data = request.json
        update_data = MiddlewareUpdate(**data)
        
        update_dict = {}
        
        if update_data.name is not None:
            if update_data.name != existing["name"]:
                name_exists = collection.find_one({"name": update_data.name})
                if name_exists:
                    raise BadRequest(
                        f"Middleware with name '{update_data.name}' already exists"
                    )
            update_dict["name"] = update_data.name
        
        if update_data.order is not None and update_data.order != existing["order"]:
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


def DeleteMiddleware(middleware_id: str, force: bool = False) -> tuple:
    """Delete middleware (soft or hard delete).
    
    Args:
        middleware_id: Middleware identifier.
        force: If True, perform hard delete.
        
    Returns:
        Empty tuple with status code 204.
    """
    try:
        collection = get_middleware_collection()
        
        if not ObjectId.is_valid(middleware_id):
            raise BadRequest("Invalid middleware ID format")
        
        middleware = collection.find_one({"_id": ObjectId(middleware_id)})
        if not middleware:
            raise MiddlewareNotFound(f"Middleware with ID '{middleware_id}' not found")
        
        if force:
            deleted_order = middleware["order"]
            collection.delete_one({"_id": ObjectId(middleware_id)})
            collection.update_many(
                {"order": {"$gt": deleted_order}},
                {"$inc": {"order": -1}}
            )
            logger.info(f"Hard deleted middleware: {middleware_id}")
        else:
            collection.update_one(
                {"_id": ObjectId(middleware_id)},
                {
                    "$set": {
                        "enabled": False,
                        "deleted_at": datetime.utcnow().isoformat() + "Z"
                    }
                }
            )
            logger.info(f"Soft deleted middleware: {middleware_id}")
        
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
        
        middleware_ids = data.get("ordered_ids", [])
        
        if not middleware_ids:
            raise BadRequest("ordered_ids array is required")
        
        if len(middleware_ids) != len(set(middleware_ids)):
            raise BadRequest("Duplicate middleware IDs in array")
        
        # Only count active (non-deleted) middlewares
        active_filter = {"deleted_at": {"$exists": False}}
        total_count = collection.count_documents(active_filter)
        if len(middleware_ids) != total_count:
            raise BadRequest(
                f"Array must contain all {total_count} active middlewares"
            )
        
        for middleware_id in middleware_ids:
            if not ObjectId.is_valid(middleware_id):
                raise BadRequest(f"Invalid middleware ID: {middleware_id}")
            
            exists = collection.find_one({"_id": ObjectId(middleware_id)})
            if not exists:
                raise MiddlewareNotFound(f"Middleware with ID '{middleware_id}' not found")
        
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


def ValidateMiddleware() -> dict:
    """Validate middleware code without creating it.
    
    Returns:
        Validation results.
    """
    try:
        data = request.json
        
        class_path = data.get("class_path")
        code = data.get("code")
        github_url = data.get("github_url")
        
        if not class_path and not code and not github_url:
            raise BadRequest("Either class_path, code, or github_url must be provided")
        
        # Fetch code from GitHub if github_url is provided
        if github_url and not code:
            try:
                import requests
                response = requests.get(github_url, timeout=10)
                response.raise_for_status()
                code = response.text
            except Exception as e:
                raise MiddlewareCodeFetchError(f"Failed to fetch code from GitHub: {str(e)}")
        
        result = validate_middleware_code(code=code, class_path=class_path)
        
        return result
        
    except BadRequest:
        raise
    except Exception as e:
        logger.error(f"Error validating middleware: {e}")
        raise InternalServerError("Validation failed")
