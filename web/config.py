import os
basedir = os.path.abspath(os.path.dirname(__file__))


class Config:
    SECRET_KEY = os.environ['FLASK_SECRET_KEY']
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_DATABASE_URI = os.environ['DATABASE_URI']
    BROKER_URL = os.environ.get('REDIS_URL', 'redis://')
    CELERY_RESULT_BACKEND = os.environ.get('REDIS_URL', 'redis://')
    CELERY_IMPORTS = [
        'tasks.mws.products',
    ]
    MAX_PAGE_ITEMS = 3
