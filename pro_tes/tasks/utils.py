# """Utility functions for Celery background tasks."""

# TODO: commented until task_run_progress is functional


# import logging
# from typing import Union

# from pymongo import collection as Collection

# import pro_tes.database.db_utils as db_utils


# # Get logger instance
# logger = logging.getLogger(__name__)


# def set_task_state(
#     collection: Collection,
#     task_id: str,
#     worker_id: Union[None, str] = None,
#     state: str = 'UNKNOWN',
# ):
#     """Set/update state of task associated with worker task."""
#     if not worker_id:
#         document = collection.find_one(
#             filter={'run_id': task_id},
#             projection={
#                 'worker_id': True,
#                 '_id': False,
#             }
#         )
#         worker_id = document['worker_id']
#     try:
#         document = db_utils.update_task_state(
#             collection=collection,
#             worker_id=worker_id,
#             state=state,
#         )
#     except Exception as e:
#         document = False
#         logger.error(
#             (
#                 "Database error. Could not update state of task '{task_id}' "
#                 "(worker id: '{worker_id}') to state '{state}'. Original \
#                                error "
#                 "message: {type}: {msg}"
#             ).format(
#                 task_id=task_id,
#                 worker_id=worker_id,
#                 state=state,
#                 type=type(e).__name__,
#                 msg=e,
#             )
#         )
#     finally:
#         if document:
#             logger.info(
#                 (
#                     "State of task '{task_id}' (worker id: '{worker_id}') "
#                     "changed to '{state}'."
#                 ).format(
#                     task_id=task_id,
#                     worker_id=worker_id,
#                     state=state,
#                 )
#             )
