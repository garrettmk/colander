from celery import chain, chord
from celery.utils.log import get_task_logger
from app import celery_app
from app.models import Product
from tasks.ops.products import store_product_history, update_amazon_listing, update_fba_fees
from tasks.parsed.products import GetCompetitivePricingForASIN
from tasks.parsed.product_adv import ItemLookup


logger = get_task_logger(__name__)


########################################################################################################################


@celery_app.task()
def dummy(**kwargs):
    products = Product.build_query(**kwargs).all()
    for product in products:
        print(f'{product.vendor.name} {product.sku} {product.title}')


@celery_app.task()
def track_amazon_products(**kwargs):
    print(kwargs)
    products = Product.build_query(**kwargs).all()
    for product in products:
        if product.vendor_id != 1:
            logger.warning(f'Cannot track product: {product.vendor.name} {product.sku}')
            continue

        chain(
            chord(
                (
                    GetCompetitivePricingForASIN.s(product.sku),
                    ItemLookup.s(product.sku)
                ),
                update_amazon_listing.s(product.id)
            ),
            update_fba_fees.s(),
            store_product_history.s()
        ).apply_async()