import os
basedir = os.path.abspath(os.path.dirname(__file__))


class Config:
    SECRET_KEY = os.environ.get('FLASK_SECRET_KEY', 'not a good key')
    MAX_PAGE_ITEMS = 10

    SQLALCHEMY_TRACK_MODIFICATIONS = True
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URI', 'mysql://')

    BROKER_URL = os.environ.get('CELERY_REDIS_URL', 'redis://')
    CELERY_RESULT_BACKEND = os.environ.get('CELERY_REDIS_URL', 'redis://')
    CELERY_TASK_RESULT_EXPIRES = 60 * 60
    task_store_errors_even_if_ignored = True
    task_compression = 'gzip'
    result_compression = 'gzip'
    CELERY_IMPORTS = [
        'tasks.mws.products',
        'tasks.mws.product_adv',
        'tasks.mws.inventory',
        'tasks.mws.reports',
        'tasks.parsed.products',
        'tasks.parsed.product_adv',
        'tasks.parsed.inventory',
        'tasks.parsed.reports',
        'tasks.ops.products',
        'tasks.ops.spiders',
        'tasks.ops.reports',
        'tasks.jobs.jobs'
    ]
    CELERYBEAT_MAX_LOOP_INTERVAL = 30
    REDBEAT_LOCK_TIMEOUT = 150
    CELERYD_PREFETCH_MULTIPLIER = 1
