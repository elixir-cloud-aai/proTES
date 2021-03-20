from typing import (Any, Mapping, Optional, Union)

from pymongo import collection as Collection
from pymongo.collection import ReturnDocument


def update_task_state(
    collection: Collection,
    worker_id: Union[None, str],
    state: str = 'UNKNOWN'
) -> Optional[Mapping[Any, Any]]:
    """Updates state of task and returns document."""
    return collection.find_one_and_update(
        {'worker_id': worker_id},
        {'$set': {'task.state': state}},
        return_document=ReturnDocument.AFTER
    )


def upsert_fields_in_root_object(
    collection: Collection,
    worker_id: str,
    root: str,
    **kwargs
) -> Optional[Mapping[Any, Any]]:
    """Inserts (or updates) fields in(to) the same root (object) field and
    returns document.
    """
    if root:
        filter_set = {
            '.'.join([root, key]):
                value for (key, value) in kwargs.items()
        }
    else:
        filter_set = kwargs
    return collection.find_one_and_update(
        {'worker_id': worker_id},
        {'$set': filter_set},
        return_document=ReturnDocument.AFTER
    )