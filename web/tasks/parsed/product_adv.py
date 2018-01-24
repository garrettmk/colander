from .common import *
import tasks.mws.product_adv as product_adv
from amazonmws import MARKETID


########################################################################################################################


@celery_app.task
def ItemSearch(SearchIndex, **kwargs):
    raise NotImplementedError


@celery_app.task
def BrowseNodeLookup(BrowseNodeId, ResponseGroup=None):
    raise NotImplementedError


@celery_app.task
def ItemLookup(asin=None, **kwargs):
    params = {
        'ResponseGroup': 'Images,ItemAttributes,OfferFull,SalesRank,EditorialReview',
        'ItemId': kwargs.pop('ItemId', asin),
        **kwargs
    }

    response = AmzXmlResponse(
        product_adv.ItemLookup(**params)
    )

    errors = {}
    for error_tag in response.tree.iterdescendants('Error'):
        code = response.xpath_get('.//Code', error_tag)
        message = code + ': ' + response.xpath_get('.//Message', error_tag)
        asin = [sku.strip() for sku in params['ItemId'].split(',') if sku.strip().upper() in message]
        if asin:
            errors[asin[0]] = message
        else:
            errors['other'] = errors.get('other', []).append(message)

    results = {}
    for item_tag in response.tree.iterdescendants('Item'):
        product = {}
        product['sku'] = response.xpath_get('.//ASIN', item_tag)
        product['detail_url'] = f'http://www.amazon.com/dp/{product["sku"]}'
        product['rank'] = response.xpath_get('.//SalesRank', item_tag, _type=int)
        product['image_url'] = response.xpath_get('.//LargeImage/URL', item_tag)
        product['brand'] = response.xpath_get('.//Brand', item_tag)\
            or response.xpath_get('.//Manufacturer', item_tag)\
            or response.xpath_get('.//Label', item_tag)\
            or response.xpath_get('.//Publisher', item_tag)\
            or response.xpath_get('.//Studio', item_tag)\
            or response.xpath_get('.//Model', item_tag)
        product['model'] = response.xpath_get('.//Model', item_tag)\
            or response.xpath_get('.//MPN', item_tag)\
            or response.xpath_get('.//PartNumber', item_tag)
        product['NumberOfItems'] = response.xpath_get('.//NumberOfItems', item_tag, _type=int)
        product['PackageQuantity'] = response.xpath_get('.//PackageQuantity', item_tag, _type=int)
        product['title'] = response.xpath_get('.//Title', item_tag)
        product['upc'] = response.xpath_get('.//UPC', item_tag)
        product['merchant'] = response.xpath_get('.//Merchant', item_tag)
        product['prime'] = response.xpath_get('.//IsEligibleForPrime', item_tag)
        product['features'] = '\n'.join((t.text for t in item_tag.iterdescendants('Feature'))) or None
        product['description'] = response.xpath_get('.//EditorialReview/Content', item_tag)

        price = response.xpath_get('.//LowestNewPrice/Amount', _type=float)
        product['price'] = price / 100 if price is not None else None

        product = {k: v for k, v in product.items() if v is not None}
        results[product['sku']] = product

    return format_parsed_response('ItemLookup', params, results, errors)


@celery_app.task
def SimilarityLookup(ItemId, **kwargs):
    raise NotImplementedError
