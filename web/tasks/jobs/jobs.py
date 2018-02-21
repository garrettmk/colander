from celery import chain, chord
from celery.utils.log import get_task_logger
from app import celery_app
from app.models import Product, Vendor
from tasks.ops.products import store_product_history, update_amazon_listing, update_fba_fees
from tasks.parsed.products import GetCompetitivePricingForASIN
from tasks.parsed.product_adv import ItemLookup


logger = get_task_logger(__name__)
DEFAULT_PRIORITY = 1


########################################################################################################################


@celery_app.task(bind=True)
def dummy(self, **kwargs):
    products = Product.build_query(**kwargs).all()
    for product in products:
        print(f'{product.vendor.name} {product.sku} {product.title}')


@celery_app.task(bind=True)
def track_amazon_products(self, **kwargs):
    products = Product.build_query(**kwargs).all()
    for product in products:
        if product.vendor_id != Vendor.get_amazon().id:
            logger.warning(f'Cannot track product: {product.vendor.name} {product.sku}')
            continue

        self.pchain(
            self.pchord(
                (
                    GetCompetitivePricingForASIN.s(product.sku),
                    ItemLookup.s(product.sku)
                ),
                update_amazon_listing.s(product.id),
                priority=DEFAULT_PRIORITY
            ),
            update_fba_fees.s(),
            store_product_history.s(),
            priority=DEFAULT_PRIORITY
        ).apply_async()
