from app import app, db, celery_app, moment
from app.models import User, Vendor, Product, QuantityMap, Opportunity, Job, ProductHistory, VendorOrder,\
    VendorOrderItem, Delivery
from redbeat import RedBeatScheduler


########################################################################################################################


@app.shell_context_processor
def make_shell_context():
    models = {m.__name__: m for m in db.Model.__subclasses__()}
    return {
        'app': app,
        'celery_app': celery_app,
        'scheduler': RedBeatScheduler(app=celery_app),
        'db': db,
        'moment': moment,
        **models
    }

