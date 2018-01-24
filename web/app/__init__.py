from flask import Flask, has_app_context
from config import Config
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_login import LoginManager
from celery import Celery


########################################################################################################################


class FlaskCelery(Celery):

    def __init__(self, app=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.app = None
        TaskBase = self.Task
        _celery = self

        class ContextTask(TaskBase):

            def __call__(self, *args, **kwargs):
                if has_app_context():
                    return TaskBase.__call__(self, *args, **kwargs)
                else:
                    with _celery.app.app_context():
                        return TaskBase.__call__(self, *args, **kwargs)

        self.Task = ContextTask

        if app is not None:
            self.init_app(app)

    def init_app(self, app):
        self.app = app
        self.config_from_object(app.config)


########################################################################################################################


app = Flask(__name__)
app.config.from_object(Config)
app.jinja_env.add_extension('jinja2.ext.do')

db = SQLAlchemy(app)
migrate = Migrate(app, db)

login = LoginManager(app)
login.login_view = 'login'

celery_app = FlaskCelery(app)


from app import routes
