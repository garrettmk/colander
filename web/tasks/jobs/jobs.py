from datetime import datetime, timedelta
from celery.utils.log import get_task_logger
from app import celery_app, db
from app.models import Product, Vendor, AmzReport, FBAManageInventoryReportLine, Spider
from tasks.ops.products import store_product_history, update_amazon_listing, update_fba_fees, get_inventory
from tasks.ops.reports import await_reports
from tasks.parsed.products import GetCompetitivePricingForASIN
from tasks.parsed.product_adv import ItemLookup

from urllib.parse import urlparse


logger = get_task_logger(__name__)
DEFAULT_PRIORITY = 1


########################################################################################################################


@celery_app.task(bind=True, ignore_result=True)
def dummy(self, *args, **kwargs):
    products = Product.build_query(*args, **kwargs).all()
    for product in products:
        print(f'{product.vendor.name} {product.sku} {product.title}')


@celery_app.task(bind=True)
def track_amazon_products(self, *args, **kwargs):
    products = Product.build_query(*args, **kwargs).all()
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


@celery_app.task(bind=True, ignore_result=True)
def update_inventory(self):
    """Download an inventory report from Amazon and import the products."""
    half_hour_ago = datetime.utcnow() - timedelta(hours=0.5)

    report = AmzReport.query.filter(
        AmzReport.type == FBAManageInventoryReportLine.report_type,
        AmzReport.start_date >= half_hour_ago,
        AmzReport.status != '_CANCELLED_'
    ).order_by(
        AmzReport.start_date.desc()
    ).first()

    if report is None:
        report = AmzReport(type=FBAManageInventoryReportLine.report_type)
        db.session.add(report)
        db.session.commit()

    self.pchain(
        await_reports.s(report.id),
        get_inventory.si(),
        priority=DEFAULT_PRIORITY
    ).delay()


@celery_app.task(bind=True, ignore_result=True)
def crawl_url(self, url, spider_id=None):
    """Crawl a given URL."""
    if spider_id:
        spider = Spider.query.filter_by(id=spider_id).one()
    else:
        spider = db.session.query(
            Spider
        ).filter(
            Vendor.website.ilike(f'%{urlparse(url)[1]}%'),
            Spider.vendor_id == Vendor.id
        ).one()

    spider.crawl_url(url)


@celery_app.task(bind=True, ignore_result=True)
def update_vendor_rates(self):
    """Update each vendor's average shipping and market fees estimates."""
    vendors = Vendor.query.all()

    for vendor in vendors:
        vendor.calculate_fee_rate()

    db.session.commit()
