from app import celery


@celery.task()
def dummy_task(message):
    print(message)
