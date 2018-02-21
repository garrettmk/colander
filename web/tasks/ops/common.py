import sqlalchemy
import pymysql
import requests
from app import celery_app


########################################################################################################################


class OpsTask(celery_app.Task):
    """Provides common behaviours and resources for the ops group of tasks."""

    # Despite the implication in the Celery docs, defining these options as class attributes doesn't work.
    # They have to be passed to the task() decorator.
    options = {
        'default_retry_delay': 10,
        'retry_backoff': 5,  # Listed in the docs, though not actually implemented...
        'max_retries': 3,
        'autoretry_for': (
            sqlalchemy.exc.InternalError,
            pymysql.err.InternalError,
            requests.exceptions.ConnectionError,
            requests.exceptions.Timeout
        )
    }
