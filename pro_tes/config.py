import os

from pro_tes.config.config_parser import get_conf
from pro_tes.config.app_config import parse_app_config

# Source the WES config for defaults
flask_config = parse_app_config(config_var='WES_CONFIG')

# Gunicorn number of workers and threads
workers = int(os.environ.get('GUNICORN_PROCESSES', '3'))
threads = int(os.environ.get('GUNICORN_THREADS', '1'))

forwarded_allow_ips = '*'

# Gunicorn bind address
bind = '{address}:{port}'.format(
        address=get_conf(flask_config, 'server', 'host'),
        port=get_conf(flask_config, 'server', 'port'),
    )

# Source the environment variables for the Gunicorn workers
raw_env = [
    "WES_CONFIG=%s" % os.environ.get('WES_CONFIG', ''),
]
