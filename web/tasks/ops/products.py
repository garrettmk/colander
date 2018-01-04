import collections
from celery import chain, group, chord
from celery.utils.log import get_task_logger
from bson import ObjectId
from .common import *
import parsed.products as products
import parsed.product_adv as product_adv


logger = get_task_logger(__name__)


########################################################################################################################


@app.task(base=OpsTask, bind=True)
def find_amazon_matches(self, product_id, brand=None, model=None):
    """Find matching products in Amazon's catalog, import them, and create corresponding opportunities."""

    if None not in [brand, model]:
        query_string = f'{brand} {model}'
    else:
        raise NotImplementedError

    matches = products.ListMatchingProducts(query=query_string)

    amz_id = self.get_or_create_vendor('Amazon')
    collection = self.db.products
    for match in matches:
        # Insert/update the Amazon product
        match['vendor'] = amz_id
        match = collection.find_one_and_update(
            filter={
                'vendor': amz_id,
                'sku': match['sku']
            },
            update={
                '$set': {**match}
            },
            upsert=True,
            return_document=pymongo.ReturnDocument.AFTER
        )

        # Insert/update the opportunity relationship
        match_id = match['_id']
        opportunity = {
            'market_listing': match_id,
            'supplier_listing': product_id
        }
        opportunity = collection.find_one_and_update(
            filter={**opportunity},
            update={
                '$set': {**opportunity}
            },
            upsert=True,
            return_document=pymongo.ReturnDocument.AFTER
        )

        # Follow-up tasks:
        asin = match['sku']
        chain(
            chord(
                products.GetCompetitivePricingForASIN.s(asin),
                product_adv.ItemLookup.s(asin)
            )(update_amazon_listing.s(str(match_id))),
            update_fba_fees.s(),
            update_opportunity.si(opportunity['_id'])
        ).apply_async()



@app.task(base=OpsTask, bind=True)
def update_amazon_listing(self, data, product_id):
    """Updates a product using various sources of data."""
    collection = self.db.products

    product_id = ObjectId(product_id)
    product = collection.find_one({'_id': product_id}, projection={'sku': 1})

    try: product_asin = product['sku']
    except AttributeError: raise ValueError(f'Invalid product id: {product_id}')

    # Separate the data sources into API call results and raw updates
    if not isinstance(data, collections.Sequence):
        data = [data]

    api_calls = [source for source in data if 'action' in source and 'params' in source]
    raw_updates = [source for source in data if source not in api_calls]

    # Process API calls first
    for api_call in api_calls:
        call_type = api_call['action']

        if call_type == 'ItemLookup':
            try:
                product.update(api_call['results'][product_asin])
            except KeyError:
                logger.debug(f"API call {call_type} does not contain results for {product_asin}, ignoring...")

        elif call_type == 'GetCompetitivePricingForASIN':
            try:
                landed_price = api_call['results'][product_asin].get('landed_price', None)
                listing_price = api_call['results'][product_asin].get('listing_price', None)
                shipping = api_call['results'][product_asin].get('shipping', None)

                product['price'] = landed_price if landed_price is not None else listing_price + shipping
                product['offers'] = api_call['results'][product_asin].get('offers', None)
            except KeyError:
                logger.debug(f"API call {call_type} does not contain results for {product_asin}, ignoring...")

        elif call_type == 'GetMyFeesEstimate':
            try:
                product['price'] = api_call['results'][product_asin]['price']
                product['market_fees'] = api_call['results'][product_asin]['total_fees']
            except KeyError:
                logger.debug(f"API call {call_type} does not contain results for {product_asin}, ignoring...")
                continue

        else:
            raise ValueError(f"Unrecognized API call: {call_type}")

    # Process raw updates
    for raw_data in raw_updates:
        product.update(raw_data)

    # Write to the DB
    collection.find_one_and_update(
        filter={'_id': product_id},
        update={'$set': {**product}},
    )

    return str(product_id)


@app.task(base=OpsTask, bind=True)
def update_fba_fees(self, product_id):
    """Updates the market_fees field with the total fee amount for the current price."""
    collection = self.db.products
    product = collection.find_one({'_id': ObjectId(product_id)})
    if product is None:
        raise ValueError(f"Invalid product id: {product_id}")

    asin = product['sku']
    price = product['price']

    return update_amazon_listing(
        products.GetMyFeesEstimate(asin, price),
        product_id
    )


@app.task(base=OpsTask, bind=True)
def update_opportunity(self, opp_id):
    """Recalculate opportunity metrics."""
    opp_collection = self.db.opportunities
    opp = opp_collection.find_one({'_id': ObjectId(opp_id)})
    if opp is None:
        raise ValueError(f'Invalid opportunity ID: {opp_id}')

    vendors = self.db.vendors
    products = self.db.products

    market_listing = products.find_one({'_id': opp['market_listing']})
    supplier_listing = products.find_one({'_id': opp['supplier_listing']})
    if None in (market_listing, supplier_listing):
        raise ValueError(f'Invalid product ID: {opp["market_listing"] if market_listing is None else opp["supplier_listing"]}')

    market_vendor = vendors.find_one({'_id': market_listing['vendor']})
    supplier_vendor = vendors.find_one({'_id': supplier_listing['vendor']})
    if None in (market_vendor, supplier_vendor):
        raise ValueError(f'Invalid vendor ID: {market_listing["vendor"] if market_vendor is None else supplier_listing["vendor"]}')

    try:
        supplier_price = supplier_listing['price']
        supplier_quantity = supplier_listing['quantity']['numeric']
        ship_rate = supplier_listing.get('ship_rate', supplier_vendor.get('ship_rate', 0))
        market_price = market_listing['price']
        market_quantity = market_listing['quantity']['numeric']
        market_fees = market_listing['market_fees']
    except (KeyError, TypeError):
        raise ValueError(f'market_listing or supplier_listing do not contain required data, or it is not in the correct'
                         f'format.')

    revenue = market_price - market_fees
    subtotal = (supplier_price / supplier_quantity) * market_quantity
    shipping = ship_rate * subtotal

    profit = revenue - subtotal - shipping
    margin = profit / market_price
    roi = profit / (subtotal + shipping)

    opp['profit'] = profit
    opp['margin'] = margin
    opp['roi'] = roi

    opp_collection.find_one_and_update(
        filter={'_id': ObjectId(opp_id)},
        update={'$set': opp}
    )

    return opp_id