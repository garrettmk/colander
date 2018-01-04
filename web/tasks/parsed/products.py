import itertools
from .common import *
import mws.products as products
from lib.amazonmws.amazonmws import MARKETID


########################################################################################################################


@app.task
def GetServiceStatus(**kwargs):
    response = AmzXmlResponse(
        products.GetServiceStatus(**kwargs)
    )

    return response.xpath_get('.//Status')


@app.task
def ListMatchingProducts(query=None, **kwargs):
    """Perform a ListMatchingProducts request."""
    # Allow two-letter abbreviations for MarketplaceId
    market_id = kwargs.pop('MarketplaceId', 'US')
    market_id = market_id if len(market_id) > 2 else MARKETID.get(market_id)
    params = {
        k: v for k, v in {
            'Query': kwargs.pop('Query', query),
            'MarketplaceId': market_id,
            **kwargs
        }.items() if v is not None
    }

    response = AmzXmlResponse(
        products.ListMatchingProducts(**params, **kwargs)
    )

    if response.error_code:
        return format_parsed_response('ListMatchingProducts', params, errors=response.error_as_json())

    results = []
    for tag in response.tree.iterdescendants('Product'):
        product = dict()
        product['sku'] = response.xpath_get('./Identifiers/MarketplaceASIN/ASIN', tag)
        product['brand'] = response.xpath_get('.//Brand', tag) \
                           or response.xpath_get('.//Manufacturer', tag) \
                           or response.xpath_get('.//Label', tag) \
                           or response.xpath_get('.//Publisher', tag) \
                           or response.xpath_get('.//Studio', tag)
        product['model'] = response.xpath_get('.//Model', tag) \
                           or response.xpath_get('.//PartNumber', tag)
        product['price'] = response.xpath_get('.//ListPrice/Amount', tag, _type=float)
        product['NumberOfItems'] = response.xpath_get('.//NumberOfItems', tag, _type=int)
        product['PackageQuantity'] = response.xpath_get('.//PackageQuantity', tag, _type=int)
        product['image_url'] = response.xpath_get('.//SmallImage/URL', tag)
        product['title'] = response.xpath_get('.//Title', tag)

        for rank_tag in tag.iterdescendants('SalesRank'):
            if not rank_tag.xpath('./ProductCategoryId')[0].text.isdigit():
                product['category'] = response.xpath_get('./ProductCategoryId', rank_tag)
                product['rank'] = response.xpath_get('./Rank', rank_tag, _type=int)
                break

        product['description'] = '\n'.join([t.text for t in tag.iterdescendants('Feature')]) or None

        results.append({k: v for k, v in product.items() if v is not None})

    return format_parsed_response('ListMatchingProducts', params, results)


@app.task
def GetMyFeesEstimate(asin=None, price=None, **kwargs):
    """Return the total fees estimate for a given ASIN and price."""
    # Allow two-letter marketplace abbreviations
    # Allow two-letter abbreviations for MarketplaceId
    market_id = kwargs.pop('MarketplaceId', 'US')
    market_id = market_id if len(market_id) > 2 else MARKETID.get(market_id)

    params = {
        'FeesEstimateRequestList': kwargs.pop('FeesEstimateRequestList', None) or [
            {
                'MarketplaceId': market_id,
                'IdType': 'ASIN',
                'IdValue': asin,
                'IsAmazonFulfilled': 'true',
                'Identifier': 'request1',
                'PriceToEstimateFees.ListingPrice.CurrencyCode': 'USD',
                'PriceToEstimateFees.ListingPrice.Amount': price
            }
        ],
        **kwargs
    }

    response = AmzXmlResponse(
        products.GetMyFeesEstimate(**params)
    )

    if response.error_code:
        return format_parsed_response('GetMyFeesEstimate', params, errors=response.error_as_json())

    results, errors = {}, {}
    for result_tag in response.tree.iterdescendants('FeesEstimateResult'):
        sku = response.xpath_get('.//FeesEstimateIdentifier/IdValue', result_tag)

        if response.xpath_get('.//Status', result_tag) == 'Success':
            results[sku] = {'total_fees_estimate': response.xpath_get('.//TotalFeesEstimate/Amount', _type=float)}
        else:
            errors[sku] = response.xpath_get('.//Error/Message')

    return format_parsed_response('GetMyFeesEstimate', params, results, errors)


@app.task
def GetCompetitivePricingForASIN(asin=None, **kwargs):
    """Perform a GetCompetivePricingForASIN call and return the results as a simplified JSON dictionary."""
    market_id = kwargs.pop('MarketplaceId', 'US')
    market_id = market_id if len(market_id) > 2 else MARKETID.get(market_id)

    params = {
        'MarketplaceId': market_id,
        'ASINList': kwargs.pop('ASINList', [asin]),
        **kwargs
    }

    response = AmzXmlResponse(
        products.GetCompetitivePricingForASIN(**params)
    )

    results, errors = {}, {}
    for result_tag in response.tree.iterdescendants('GetCompetitivePricingForASINResult'):
        price = {}
        sku = result_tag.attrib.get('ASIN')

        # Check that the request succeeded.
        if result_tag.attrib.get('status') != 'Success':
            code = response.xpath_get('.//Error/Code', result_tag)
            message = response.xpath_get('.//Error/Message', result_tag)
            errors[sku] = f'{code}: {message}'
            continue

        for price_tag in result_tag.iterdescendants('CompetitivePrice'):
            if price_tag.attrib.get('condition') != 'New':
                continue

            price['listing_price'] = response.xpath_get('.//ListingPrice/Amount', price_tag, _type=float)
            price['shipping'] = response.xpath_get('.//Shipping/Amount', price_tag, _type=float)
            price['landed_price'] = response.xpath_get('.//LandedPrice/Amount', price_tag, _type=float)

        for count_tag in result_tag.iterdescendants('OfferListingCount'):
            if count_tag.attrib.get('condition') == 'New':
                price['offers'] = count_tag.text
        else:
            if 'offers' not in price:
                price['offers'] = 0

        results[sku] = price

    return format_parsed_response('GetCompetitivePricingForASIN', params, results, errors)


