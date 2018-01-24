import os
basedir = os.path.abspath(os.path.dirname(__file__))


class Config:
    SECRET_KEY = os.environ.get('FLASK_SECRET_KEY', 'not a good key')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URI', 'mysql://')
    BROKER_URL = os.environ.get('REDIS_URL', 'redis://')
    CELERY_RESULT_BACKEND = os.environ.get('REDIS_URL', 'redis://')
    CELERY_IMPORTS = [
        'tasks.mws.products',
    ]
    MAX_PAGE_ITEMS = 3