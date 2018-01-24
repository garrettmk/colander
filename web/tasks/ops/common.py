from app import celery_app


########################################################################################################################


class OpsTask(celery_app.Task):
    """Provides common behaviours and resources for the ops group of tasks."""

