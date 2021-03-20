"""Entry point for Celery workers."""

from pro_tes.config.app_config import parse_app_config
from foca.factories.celery_app import create_celery_app
from foca.factories.connexion_app import create_connexion_app


# Parse app configuration
config = parse_app_config(config_var='TES_CONFIG')

# Create Celery app
celery = create_celery_app(create_connexion_app(config).app)
