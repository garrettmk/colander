from app import celery


########################################################################################################################


class OpsTask(celery.Task):
    """Provides common behaviours and resources for the ops group of tasks."""

