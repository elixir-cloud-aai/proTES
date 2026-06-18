"""Controller implementations for middleware management endpoints.

These functions are referenced from the OpenAPI spec via
`x-openapi-router-controller: pro_tes.api.middlewares.controllers`.
"""
from math import ceil
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from bson.objectid import ObjectId
from flask import current_app
from werkzeug.exceptions import BadRequest, NotFound

logger = logging.getLogger(__name__)


def _collection():
    return (
        current_app.config.foca.db.dbs["taskStore"]  # type: ignore
        .collections["middlewares"]
        .client
    )


def _doc_to_response(doc: Dict[str, Any]) -> Dict[str, Any]:
    if doc is None:
        return {}
    result = {k: v for (k, v) in doc.items() if k != "_id"}
    if "_id" in doc and doc["_id"] is not None:
        result["id"] = str(doc["_id"])
    return result


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def ListMiddlewares(page_size: int = 50,
                    page: int = 0,
                    sort_by: str = "order",
                    sort_order: str = "asc",
                    source: Optional[str] = None) -> Dict[str, Any]:
    """List middlewares with pagination and sorting."""
    if page_size < 1 or page_size > 100:
        raise BadRequest("`page_size` must be between 1 and 100")
    if page < 0:
        raise BadRequest("`page` must be >= 0")

    coll = _collection()

    query: Dict[str, Any] = {}
    if source:
        query["source.type"] = source

    total = coll.count_documents(query)
    direction = 1 if sort_order == "asc" else -1
    cursor = coll.find(
        query,
        {
            "_id": True,
            "name": True,
            "source": True,
            "order": True,
            "config": True,
            "created_at": True,
            "updated_at": True,
        },
    )
    cursor = cursor.sort(sort_by, direction)
    cursor = cursor.skip(page * page_size).limit(page_size)

    middlewares = [_doc_to_response(doc) for doc in cursor]

    total_pages = ceil(total / page_size) if total > 0 else 0
    return {
        "middlewares": middlewares,
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": total_pages,
        },
    }


def AddMiddleware(body: Dict[str, Any]) -> tuple:
    """Add a middleware to the end of the execution stack."""
    if not isinstance(body, dict):
        raise BadRequest("Request body must be an object")

    coll = _collection()

    # Prepare document
    now = _utc_now()
    doc: Dict[str, Any] = {}
    doc.update(body)
    doc.pop("_id", None)
    doc.pop("id", None)

    source_val = doc.get("source")
    if isinstance(source_val, dict) and source_val.get("entry_point"):
        doc["class_path"] = source_val["entry_point"]
    elif isinstance(source_val, list):
        entry_points = [
            s["entry_point"]
            for s in source_val
            if isinstance(s, dict) and s.get("entry_point")
        ]
        doc["class_path"] = "|".join(entry_points) if entry_points else None

    if not doc.get("class_path"):
        raise BadRequest("`source.entry_point` is required")

    doc["created_at"] = now
    doc["updated_at"] = now

    # assign order = max(order)+1
    last = coll.find({}, {"order": True}).sort("order", -1).limit(1)
    try:
        last_doc = next(last, None)
    except Exception:
        last_doc = None
    doc["order"] = 0 if not last_doc else int(last_doc.get("order", 0)) + 1

    # Insert
    try:
        res = coll.insert_one(doc)
    except Exception:
        logger.exception("Failed to insert middleware")
        raise

    return (
        {
            "id": str(res.inserted_id),
            "order": doc["order"],
            "message": "Middleware added successfully",
        },
        201,
    )


def GetMiddleware(middleware_id: str) -> Dict[str, Any]:
    """Retrieve middleware details by id."""
    try:
        oid = ObjectId(middleware_id)
    except Exception:
        raise BadRequest("Invalid middleware id")

    coll = _collection()
    doc = coll.find_one(
        {"_id": oid},
        {
            "_id": True,
            "name": True,
            "source": True,
            "order": True,
            "config": True,
            "created_at": True,
            "updated_at": True,
        },
    )
    if doc is None:
        raise NotFound(f"Middleware with ID '{middleware_id}' not found")
    return _doc_to_response(doc)


def UpdateMiddleware(
    middleware_id: str,
    body: Dict[str, Any],
) -> Dict[str, Any]:
    """Update middleware partially (only `name` and `config`)."""
    if not isinstance(body, dict):
        raise BadRequest("Request body must be an object")
    allowed = {"name", "config"}
    update_fields = {
        k: v for (k, v) in body.items() if k in allowed
    }
    if not update_fields:
        raise BadRequest("Only `name` and `config` can be updated")

    try:
        oid = ObjectId(middleware_id)
    except Exception:
        raise BadRequest("Invalid middleware id")

    update_fields["updated_at"] = _utc_now()
    coll = _collection()
    updated = coll.find_one_and_update(
        {"_id": oid},
        {"$set": update_fields},
        projection={
            "_id": True,
            "name": True,
            "source": True,
            "order": True,
            "config": True,
            "created_at": True,
            "updated_at": True,
        },
        return_document=True,
    )
    if updated is None:
        raise NotFound(f"Middleware with ID '{middleware_id}' not found")
    return _doc_to_response(updated)


def DeleteMiddleware(middleware_id: str) -> tuple:
    """Delete middleware by id."""
    try:
        oid = ObjectId(middleware_id)
    except Exception:
        raise BadRequest("Invalid middleware id")

    coll = _collection()
    res = coll.delete_one({"_id": oid})
    if res.deleted_count == 0:
        raise NotFound(f"Middleware with ID '{middleware_id}' not found")
    # Return empty body with 204 status code (Connexion will use this)
    return ("", 204)


def ReorderMiddlewares(body: Dict[str, Any]) -> Dict[str, Any]:
    """Reorder middleware stack by ordered IDs."""
    if not isinstance(body, dict) or "ordered_ids" not in body:
        raise BadRequest("Request body must contain `ordered_ids` array")
    ordered_ids = body["ordered_ids"]
    if not isinstance(ordered_ids, list) or len(ordered_ids) == 0:
        raise BadRequest("`ordered_ids` must be a non-empty array of ids")

    coll = _collection()
    existing = list(coll.find({}, {"_id": True}))
    existing_ids = {str(d["_id"]): d for d in existing}

    if len(ordered_ids) != len(set(ordered_ids)):
        raise BadRequest("`ordered_ids` must not contain duplicate ids")

    if set(ordered_ids) != set(existing_ids.keys()):
        raise BadRequest(
            "`ordered_ids` must contain exactly all middleware ids"
        )

    updated_docs: List[Dict[str, Any]] = []
    now = _utc_now()
    for idx, mid in enumerate(ordered_ids):
        try:
            oid = ObjectId(mid)
        except Exception:
            raise BadRequest(f"Invalid id in ordered_ids: {mid}")
        coll.update_one(
            {"_id": oid},
            {"$set": {"order": idx, "updated_at": now}},
        )
        doc = coll.find_one(
            {"_id": oid},
            {
                "_id": True,
                "name": True,
                "source": True,
                "order": True,
                "config": True,
                "created_at": True,
                "updated_at": True,
            },
        )
        updated_docs.append(_doc_to_response(doc))

    return {
        "message": "Middleware stack reordered successfully",
        "middlewares": updated_docs,
    }
