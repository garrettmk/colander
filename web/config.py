import os
basedir = os.path.abspath(os.path.dirname(__file__))


class Config:
    SECRET_KEY = os.environ.get('FLASK_SECRET_KEY', 'not a good key')
    MAX_PAGE_ITEMS = 10

    SQLALCHEMY_TRACK_MODIFICATIONS = True
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URI', 'mysql://')

    BROKER_URL = os.environ.get('REDIS_URL', 'redis://')
    CELERY_RESULT_BACKEND = os.environ.get('REDIS_URL', 'redis://')
    CELERY_IMPORTS = ['tasks.mws.products']
    CELERYBEAT_MAX_LOOP_INTERVAL = 30
    REDBEAT_LOCK_TIMEOUT = 150
    CELERYD_PREFETCH_MULTIPLIER = 1
