from .common import *
from app import db
from app.models import Product, Vendor, QuantityMap


########################################################################################################################


@celery_app.task(bind=True)
def clean_and_import(self, data):
    """Cleans, validates, and imports product data."""

    # Verify that required fields are present and valid
    required_fields = ['vendor', 'sku']
    for field in required_fields:
        assert field in data

    # Do some basic cleaning
    for field, value in data.items():
        if isinstance(value, str):
            data[field] = value.strip()

    vendor_name = data.pop('vendor')
    sku = data.pop('sku')

    # Try to locate the product in the database
    vendor = Vendor.query.filter_by(name=vendor_name).first()
    if vendor is None:
        vendor = Vendor(name=vendor_name)
        db.session.add(vendor)

    product = Product.query.filter_by(vendor=vendor, sku=sku).first()
    if product is None:
        product = Product(vendor=vendor, sku=sku)
        db.session.add(product)

    # Update the product fields
    for key, value in dict(data).items():
        try:
            setattr(product, key, value)
            data.pop(key)
        except AttributeError:
            pass

    # Leftovers go in the data field
    product.data = data

    # If quantity and quantity_desc are present, update the QuantityMap table
    if product.quantity and product.quantity_desc:
        qmap = QuantityMap.query.filter_by(text=product.quantity_desc).first()
        if qmap is None:
            qmap = QuantityMap(text=product.quantity_desc, quantity=product.quantity)
            db.session.add(qmap)
        elif qmap.quantity is None:
            qmap.quantity = product.quantity

    # If only quantity_desc, look up quantity in QuantityMap
    elif product.quantity_desc:
        qmap = QuantityMap.query.filter_by(text=product.quantity_desc).first()
        if qmap and qmap.quantity:
            product.quantity = qmap.quantity

    # If no quantity_desc, try to guess based on title, description, etc
    # TODO: createa a GuessQuantity task

    db.session.commit()

    # Trigger follow-up actions:
    #   Find matching Amazon products