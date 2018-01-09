from app import app, db
from app.models import User, Vendor, Product, QuantityMap, Opportunity


########################################################################################################################


@app.shell_context_processor
def make_shell_context():
    return {
        'db': db,
        'User': User,
        'Vendor': Vendor,
        'Product': Product,
        'QuantityMap': QuantityMap,
        'Opportunity': Opportunity
    }
