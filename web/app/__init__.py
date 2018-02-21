import re
import celery

from flask import Flask, has_app_context
from config import Config
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_login import LoginManager
from flaskext.markdown import Markdown
from flask_moment import Moment


########################################################################################################################


class FlaskCelery(celery.Celery):

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

            def get_priority(self):
                try:
                    return self.request.delivery_info['priority'] or 0
                except TypeError:
                    try:
                        return self.request.kwargs.get('priority', 0)
                    except AttributeError:
                        return 0

            def pchain(self, *tasks, **options):
                """Creates a chain where each task is prioritized. Uses the current task's priority by default."""
                priority = options.get('priority', self.get_priority())
                sigs = tasks if len(tasks) > 1 else tasks[0]

                return celery.chain((s.set(priority=priority) for s in sigs), **options)

            def pchord(self, header, body, **kwargs):
                """Creates a chord where each task (including the body) is prioritized. Uses the current task's
                priority by default."""
                priority = kwargs.get('priority', self.get_priority())
                return celery.chord(
                    (s.set(priority=priority) for s in header),
                    body.set(priority=priority),
                    **kwargs
                )


        self.Task = ContextTask

        if app is not None:
            self.init_app(app)

    def init_app(self, app):
        self.app = app
        self.config_from_object(app.config)
        self.conf.task_routes = [
            self.priority_router,
            {
                'tasks.jobs.*': {
                    'queue': 'medium'
                }
            }
        ]

    @staticmethod
    def priority_router(name, args, kwargs, options, task=None, **kw):
        priority = options.get('priority') or 0

        if priority == 1:
            return {'queue': 'medium'}
        elif priority > 1:
            return {'queue': 'high'}


########################################################################################################################


class ColanderSQLAlchemy(SQLAlchemy):
    def apply_driver_hacks(self, app, info, options):
        if 'isolation_level' not in options:
            options['isolation_level'] = 'READ COMMITTED'
        return super().apply_driver_hacks(app, info, options)


########################################################################################################################


app = Flask(__name__)
app.config.from_object(Config)
app.jinja_env.add_extension('jinja2.ext.do')

db = ColanderSQLAlchemy(app)
migrate = Migrate(app, db)
login = LoginManager(app)
login.login_view = 'login'

celery_app = FlaskCelery(app)
markdown = Markdown(app)
moment = Moment(app)

from app import routes
