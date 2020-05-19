"""Function for Registering MongoDB with a Flask app instance."""

import os

import logging
from typing import Dict

from flask import Flask
from flask_pymongo import ASCENDING, PyMongo

from foca.config.config_parser import get_conf
from pro_tes.ga4gh.tes.endpoints.get_service_info import get_service_info


# Get logger instance
logger = logging.getLogger(__name__)


def register_mongodb(app: Flask) -> Flask:
    """Instantiates database and initializes collections."""
    config = app.config

    # Instantiante PyMongo client
    mongo = create_mongo_client(
        app=app,
        config=config,
    )

    # Add database
    db = mongo.db[os.environ.get('MONGO_DBNAME', get_conf(config, 'database', 'name'))]

    # Add database collection for '/service-info'
    collection_service_info = mongo.db['service-info']
    logger.debug("Added database collection 'service_info'.")

    # Add database collection for '/runs'
    collection_runs = mongo.db['tasks']
    collection_runs.create_index([
            ('task_id', ASCENDING),
            ('celery_id', ASCENDING),
        ],
        unique=True,
        sparse=True
    )
    logger.debug("Added database collection 'tasks'.")

    # Add database and collections to app config
    config['database']['database'] = db
    config['database']['collections'] = dict()
    config['database']['collections']['tasks'] = collection_runs
    config['database']['collections']['service_info'] = collection_service_info
    app.config = config

    # Initialize service info
    logger.debug('Initializing service info...')
    get_service_info(config, silent=True)

    return app


def create_mongo_client(
    app: Flask,
    config: Dict,
):
    """Register MongoDB uri and credentials."""
    # Set authentication
    username = os.getenv('MONGO_USERNAME', '')
    password = os.getenv('MONGO_PASSWORD', '')
    if username:
        auth = '{username}:{password}@'.format(
            username=username,
            password=password,
        )
    else:
        auth = ''

    # Compile Mongo URI string
    app.config['MONGO_URI'] = 'mongodb://{auth}{host}:{port}/{dbname}'.format(
        host=os.getenv('MONGO_HOST', get_conf(config, 'database', 'host')),
        port=os.getenv('MONGO_PORT', get_conf(config, 'database', 'port')),
        dbname=os.getenv('MONGO_DBNAME', get_conf(config, 'database', 'name')),
        auth=auth
    )

    # Instantiate MongoDB client
    mongo = PyMongo(app)
    logger.info(
        (
            "Registered database at '{mongo_uri}' with Flask application."
        ).format(
            mongo_uri=app.config['MONGO_URI']
        )
    )

    # Return Mongo client
    return mongo
