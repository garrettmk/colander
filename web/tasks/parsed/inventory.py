import time
from .common import *
import tasks.mws.inventory as inventory
from amazonmws import MARKETID


########################################################################################################################


@celery_app.task(bind=True)
def ListInventorySupply(self, start=None, **kwargs):
    """Return a dictionary of ASINs in inventory, with some associated data (FNSKUs, etc.)"""
    kwargs.pop('priority', None)
    time_format = '%Y-%m-%dT00:00:00Z'
    start = start.strftime(time_format) if start else None


    params = {
        'QueryStartDateTime': start\
                              or kwargs.pop('QueryStartDateTime', None)\
                              or time.strftime(time_format, time.gmtime()),
        **kwargs
    }

    response = AmzXmlResponse(
        inventory.ListInventorySupply(**params, priority=self.get_priority())
    )

    results = []
    while response:
        if response.error_code:
            return format_parsed_response('ListInventorySupply', params, results, errors=response.error_as_json())

        for tag in response.tree.iterdescendants('member'):
            product = dict()
            product['sku'] = response.xpath_get('.//ASIN', tag)
            product['fnsku'] = response.xpath_get('.//FNSKU', tag)
            product['in_stock'] = response.xpath_get('.//InStockSupplyQuantity', tag, _type=int)
            product['msku'] = response.xpath_get('.//SellerSKU', tag)

            results.append({k: v for k, v in product.items() if v is not None})

        next_token = response.xpath_get('.//NextToken')
        if next_token:
            response = AmzXmlResponse(
                inventory.ListInventorySupplyByNextToken(next_token, priority=self.get_priority())
            )
        else:
            response = None

    return format_parsed_response('ListInventorySupply', params, results)
