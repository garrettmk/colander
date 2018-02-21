import re
import collections
import sqlalchemy
import pymysql
from datetime import datetime

from .common import *

from celery.utils.log import get_task_logger
from celery import group, chain, chord

from app import db
from app.models import Vendor, Product, QuantityMap, Opportunity, ProductHistory

from sqlalchemy import func

from tasks.parsed.products import ListMatchingProducts, GetCompetitivePricingForASIN, GetMyFeesEstimate
from tasks.parsed.product_adv import ItemLookup
from tasks.parsed.inventory import ListInventorySupply

logger = get_task_logger(__name__)


########################################################################################################################


@celery_app.task(bind=True, base=OpsTask, **OpsTask.options)
def dummy(self, *args, **kwargs):
    print(f'Delivery options: {self.request.delivery_info}')
    print(f'get_priority: {self.get_priority()}')


########################################################################################################################


@celery_app.task(bind=True, base=OpsTask)
def clean_and_import(self, data):
    """Cleans, validates, and imports product data."""

    # Do some basic cleaning
    for field, value in data.items():
        if isinstance(value, str):
            data[field] = value.strip()

    sku = data.pop('sku')

    # Try to locate the product in the database
    vendor_name = data.pop('vendor', None)
    vendor_id = data.pop('vendor_id', None)

    # If vendor_name was provided, find or create the matching Vendor
    if vendor_name:
        vendor = Vendor.query.filter_by(name=vendor_name).first()
        if vendor is None:
            vendor = Vendor(name=vendor_name)
            db.session.add(vendor)
    elif vendor_id:
        vendor = Vendor.query.filter_by(id=vendor_id).first()
        if vendor is None:
            raise ValueError(f'Invalid vendor id: {vendor_id}')
    else:
        raise ValueError('Either \'vendor\' or \'vendor_id\' fields are required.')

    # Find a matching product in the db or create a new one
    product = Product.query.filter_by(vendor=vendor, sku=sku).first()
    if product is None:
        product = Product(vendor=vendor, sku=sku)
        db.session.add(product)

    # Update the product fields
    for key in list(data.keys()):
        if hasattr(product, key):
            setattr(product, key, data.pop(key))

    # Leftovers go in the data field
    product.data = data

    db.session.commit()

    # Try to determine listing quantity
    find_amazon_matches.apply_async(args=(product.id,), priority=self.get_priority())


########################################################################################################################


@celery_app.task(bind=True, base=OpsTask)
def guess_quantity(self, product_id):
    """Guess listing quantity based on QuantityMap data."""
    product = Product.query.filter_by(id=product_id).first()
    if product is None:
        raise ValueError(f'Invalid product id: {product_id}')

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
        qmap = QuantityMap.query.filter(QuantityMap.text.ilike(product.quantity_desc)).first()
        if qmap and qmap.quantity:
            product.quantity = qmap.quantity
        elif qmap is None:
            qmap = QuantityMap(text=product.quantity_desc)
            db.session.add(qmap)

    else:
        all_qmaps = QuantityMap.query\
            .order_by(func.char_length(QuantityMap.text).desc())\
            .all()

        for qmap in all_qmaps:
            if re.search(f'(\W|\A){qmap.text}(\W|\Z)', product.title, re.IGNORECASE):
                product.quantity_desc = qmap.text
                product.quantity = qmap.quantity
                break
        else:
            data = product.data if product.data is not None else {}
            quantity = max(data.get('PackageQuantity', 0), data.get('NumberOfItems', 0))
            if quantity:
                product.quantity = quantity

    db.session.commit()
    return product_id


########################################################################################################################


@celery_app.task(bind=True, base=OpsTask)
def quantity_map_updated(self, qmap_id):
    """Updates all related products with new quantity map data."""
    qmap = QuantityMap.query.filter_by(id=qmap_id).first()
    if qmap is None:
        raise ValueError(f'Invalid QuantityMap id: {qmap_id}')

    Product.query.filter(
        db.or_(
            Product.quantity_desc.ilike(qmap.text),
            Product.title.op('regexp')(f'[[:<:]]{qmap.text}[[:>:]]')
        )
    ).update(
        {
            'quantity': qmap.quantity,
            'last_modified': datetime.utcnow()
        },
        synchronize_session=False
    )

    db.session.commit()


########################################################################################################################


@celery_app.task(bind=True, base=OpsTask, **OpsTask.options)
def find_amazon_matches(self, product_id):
    """Find matching products in Amazon's catalog, import them, and create corresponding opportunities."""
    product = Product.query.filter_by(id=product_id).first()
    if product is None:
        raise ValueError(f'Invalid product id: {product_id}')

    if product.brand and product.model:
        query_string = f'{product.brand} {product.model}'
    elif product.title:
        query_string = product.title
    else:
        raise ValueError(f'Data required: brand + model OR title')

    amazon = Vendor.get_amazon()
    matches = ListMatchingProducts(query=query_string, priority=self.get_priority())

    for match_data in matches['results']:
        match_product = Product.query.filter_by(vendor_id=amazon.id, sku=match_data['sku']).first()
        if match_product is None:
            match_product = Product(vendor=amazon, sku=match_data['sku'])
            db.session.add(match_product)

        match_product.update(match_data)
        opp = match_product.add_supplier(product)
        db.session.commit()

        # Follow-up tasks:
        self.pchain(
            self.pchord(
                (
                    GetCompetitivePricingForASIN.s(match_product.sku),
                    ItemLookup.s(match_product.sku),
                ),
                update_amazon_listing.s(match_product.id)
            ),
            update_fba_fees.s()
        ).apply_async()


@celery_app.task(bind=True, base=OpsTask)
def update_amazon_listing(self, data, product_id):
    """Updates a product using various sources of data."""

    product = Product.query.filter_by(id=product_id).first()
    if product is None:
        raise ValueError(f'Invalid product id: {product_id}')

    # Separate the data sources into API call results and raw updates
    if not isinstance(data, collections.Sequence):
        data = [data]

    api_calls = [source for source in data if 'action' in source and 'params' in source]
    raw_updates = [source for source in data if source not in api_calls]

    # Process API calls first
    for api_call in api_calls:
        call_type = api_call['action']

        try:
            api_results = api_call['results'][product.sku]
        except KeyError:
            logger.debug(f'API call {call_type} does not contain results for {product.sku}, ignoring...')
            continue

        if call_type == 'ItemLookup':
            product.update(api_results)

        elif call_type == 'GetCompetitivePricingForASIN':
            landed_price = api_results.get('landed_price', None)
            listing_price = api_results.get('listing_price', None)
            shipping = api_results.get('shipping', None)

            try:
                product.price = landed_price if landed_price is not None else listing_price + shipping
            except TypeError:
                pass

            if product.data is not None:
                product.data['offers'] = api_results.get('offers', None)
            else:
                product.data = {'offers': api_results.get('offers', None)}

        elif call_type == 'GetMyFeesEstimate':
            product.price = api_results['price']
            product.market_fees = api_results['total_fees_estimate']

        else:
            raise ValueError(f'Unrecognized API call: {call_type}')

    # Process raw updates
    for raw_data in raw_updates:
        product.update(raw_data)

    db.session.commit()

    return product.id


@celery_app.task(bind=True, base=OpsTask)
def update_fba_fees(self, product_id, **kwargs):
    """Updates the market_fees field with the total fee amount for the current price."""
    kwargs.pop('priority', None)

    product = Product.query.filter_by(id=product_id).first()
    if product is None:
        raise ValueError(f'Invalid product id: {product_id}')

    update_amazon_listing(
        GetMyFeesEstimate(product.sku, str(product.price), priority=self.get_priority(), **kwargs),
        product_id
    )

    return product_id


@celery_app.task(bind=True, base=OpsTask)
def store_product_history(self, product_id):
    """Stores a product's current state in the ProductHistory table."""
    product = Product.query.filter_by(id=product_id).first()
    if product is None:
        raise ValueError(f'Invalid product id: {product_id}')

    history = ProductHistory(product)
    db.session.add(history)
    db.session.commit()


@celery_app.task(bind=True, base=OpsTask)
def get_inventory(self, tags=['inventory']):
    """Retrieve inventory from Amazon."""
    results = ListInventorySupply(priority=self.get_priority())['results']
    amazon = Vendor.get_amazon()

    for result in results:
        product = Product.query.filter_by(vendor_id=amazon.id, sku=result['sku']).first()
        if product is None:
            product = Product(vendor_id=amazon.id, sku=result['sku'])
            db.session.add(product)

        product.update(result)
        product.add_tags(*tags)
        db.session.commit()


@celery_app.task(bind=True, base=OpsTask)
def update_from_vendor(self, product_id):
    """Retrieves up-to-date product data from the product's vendor."""
    product = Product.query.filter_by(id=product_id).one()

    if product.vendor_id == Vendor.get_amazon().id:
        self.pchain(
            self.pchord(
                (
                    GetCompetitivePricingForASIN.s(product.sku),
                    ItemLookup.s(product.sku),
                ),
                update_amazon_listing.s(product.id)
            ),
            update_fba_fees.s()
        ).apply_async()
    else:
        raise ValueError(f'Can\'t update from vendor: {product.vendor.name}')
