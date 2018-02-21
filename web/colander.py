from app import app, db, celery_app, moment
from app.models import User, Vendor, Product, QuantityMap, Opportunity, Job, ProductHistory
from redbeat import RedBeatScheduler


########################################################################################################################


@app.shell_context_processor
def make_shell_context():
    return {
        'app': app,
        'celery_app': celery_app,
        'scheduler': RedBeatScheduler(app=celery_app),
        'db': db,
        'moment': moment,
        'User': User,
        'Vendor': Vendor,
        'Product': Product,
        'QuantityMap': QuantityMap,
        'Opportunity': Opportunity,
        'Job': Job,
        'ProductHistory': ProductHistory
    }

