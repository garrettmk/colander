import re
import decimal
import json
import urllib
import collections
import redis
import os

from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import UserMixin
from sqlalchemy import UniqueConstraint, orm
from sqlalchemy.ext.associationproxy import association_proxy
from sqlalchemy.ext.hybrid import hybrid_property
from fuzzywuzzy import fuzz
from redbeat import RedBeatScheduler, RedBeatSchedulerEntry
import celery.schedules as schedules
from app import app, db, login, celery_app


DEFAULT_PRIORITY = 1


########################################################################################################################


decimal.getcontext().prec = 23
CURRENCY = db.Numeric(19, 4)


def quantize(d, places=4):
    depth = '.' + '0' * (places - 1) + '1'
    return d.quantize(decimal.Decimal(depth))


@app.context_processor
def jinja_context_funcs():

    def as_money(d, p=2):
        if d is None:
            return 'N/A'

        depth = '.' + '0' * (p - 1) + '1'
        return '$' + f'{(d.quantize(decimal.Decimal(depth))):,}'

    def as_percent(d, p=1):
        if d is None:
            return 'N/A'
        if isinstance(d, float):
            return f'{d * 100:.{p}f}%'

        depth = '.' + '0' * (p - 1) + '1'
        return str((d * 100).quantize(decimal.Decimal(depth))) + '%'

    def as_quantity(i):
        if i is None:
            return 'N/A'

        return f'{int(i):,}'

    def as_yesno(b):
        if b is None:
            return 'N/A'

        return 'Yes' if b else 'No'

    def set_page_number(url, page):
        if page is None:
            url = re.sub(f'&?page=\d+', '', url)
            return url[:-1] if url.endswith('?') else url

        if 'page=' in url:
            return re.sub(f'page=+\d','page=%d' % page, url)
        elif url.endswith('?'):
            return url + 'page=%d' % page
        elif '?' in url:
            return url + '&page=%d' % page
        else:
            return url + '?page=%d' % page

    def urlencode(s):
        return urllib.parse.quote_plus(s)

    def google(*args):
        q = ' '.join((str(a) for a in args))
        return 'http://www.google.com/search?as_q=' + urllib.parse.quote_plus(q)

    return dict(
        as_money=as_money,
        as_percent=as_percent,
        as_quantity=as_quantity,
        as_yesno=as_yesno,
        set_page_number=set_page_number,
        urlencode=urlencode,
        google=google,
        Vendor=Vendor,
        Product=Product
    )


########################################################################################################################


@login.user_loader
def load_user(id):
    return User.query.get(int(id))


########################################################################################################################


class UpdateMixin:
    extra = db.Column(db.JSON, default={})

    def update(self, *args, **kwargs):
        """Update attributes on the object. Unknown attributes are stored in the model's 'extra' attribute."""
        data = {}

        if len(args) == 1 and isinstance(args[0], collections.Mapping):
            data.update(args[0])
        elif len(args) > 1:
            raise ValueError('update() only accepts a single key-value mapping as a positional parameter.')

        data.update(kwargs)
        extra = {}

        for key, value in data.items():
            if hasattr(self, key):
                setattr(self, key, value)
            else:
                extra[key] = value

        if self.extra:
            self.extra.update(extra)
        else:
            self.extra = extra


########################################################################################################################


class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), index=True, unique=True)
    email = db.Column(db.String(128), index=True, unique=True)
    password_hash = db.Column(db.String(128))

    def __init__(self, *args, **kwargs):
        password = kwargs.pop('password', None)
        super().__init__(*args, **kwargs)

        if password:
            self.set_password(password)

    def __repr__(self):
        return f'<{type(self).__name__} {self.username}>'

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


########################################################################################################################


class Vendor(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(64), index=True, unique=True)
    website = db.Column(db.String(64), index=True, unique=True)
    image_url = db.Column(db.String(256))
    ship_rate = db.Column(CURRENCY, default=0)
    avg_market_fees = db.Column(CURRENCY, default=0)

    products = db.relationship('Product', backref='vendor', lazy='dynamic', passive_deletes=True)
    orders = db.relationship('VendorOrder', backref='vendor', lazy='dynamic', passive_deletes=True)

    def __repr__(self):
        return f'<{type(self).__name__} {self.name}>'

    def calculate_fee_rate(self):
        self.avg_market_fees = db.session.query(
            db.func.avg(Product.market_fees / Product.price)
        ).filter(
            Product.vendor_id == self.id,
            Product.price > 0,
            Product.market_fees.isnot(None)
        ).scalar()

        return self.avg_market_fees

    @staticmethod
    def get_amazon():
        amz = Vendor.query.filter_by(name='Amazon').first()
        if amz is None:
            amz = Vendor(name='Amazon', website='http://www.amazon.com')
            db.session.add(amz)
            db.session.commit()

        return amz


########################################################################################################################


class QuantityMap(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    text = db.Column(db.String(128), index=True, unique=True)
    quantity = db.Column(db.Integer)

    def __repr__(self):
        return f'<{type(self).__name__} {self.text} = {self.quantity}>'

    @classmethod
    def __declare_last__(cls):
        # Add a short delay when executing the task after an insert, to ensure that the qmap is committed to the DB
        db.event.listen(cls, 'after_insert', lambda m, c, t: cls._update_products(t, 3))
        db.event.listen(cls, 'after_update', lambda m, c, t: cls._update_products(t))

    @staticmethod
    def _update_products(target, delay=None):
        if target.text and target.quantity:
            # Using send_task to avoid a circular import
            celery_app.send_task(
                'tasks.ops.products.quantity_map_updated',
                kwargs={'qmap_id': target.id},
                priority=DEFAULT_PRIORITY,
                countdown=delay
            )


########################################################################################################################


class ProductHistory(db.Model):
    """Stores key product data for a given point in time."""
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id', ondelete='CASCADE'), nullable=False)
    timestamp = db.Column(db.DateTime, nullable=False)
    price = db.Column(CURRENCY)
    market_fees = db.Column(CURRENCY)
    rank = db.Column(db.Integer)
    flags = db.Column(db.JSON)

    def __init__(self, product=None):
        if product:
            self.product_id = product.id
            self.timestamp = datetime.now()
            self.price = product.price
            self.market_fees = product.market_fees
            self.rank = product.rank


########################################################################################################################


class Product(db.Model, UpdateMixin):
    __table_args__ = (UniqueConstraint('vendor_id', 'sku'),)

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(384), index=True)
    vendor_id = db.Column(db.Integer, db.ForeignKey('vendor.id', ondelete='CASCADE'), nullable=False)
    sku = db.Column(db.String(64), index=True, nullable=False)
    detail_url = db.Column(db.String(128))
    image_url = db.Column(db.String(128))
    price = db.Column(CURRENCY, index=True)
    quantity = db.Column(db.Integer, default=1)
    market_fees = db.Column(CURRENCY, index=True)
    rank = db.Column(db.Integer, index=True)
    category = db.Column(db.String(64), index=True)

    brand = db.Column(db.String(64), index=True)
    model = db.Column(db.String(64), index=True)
    upc = db.Column(db.String(12))
    description = db.Column(db.Text)

    quantity_desc = db.Column(db.String(64))
    tags = db.Column(db.JSON, default=[])

    last_modified = db.Column(db.DateTime, default=db.func.now(), onupdate=db.func.now())

    supply_listings = association_proxy('supply_opportunities', 'supply', creator=lambda l: Opportunity(supply=l))
    market_listings = association_proxy('market_opportunities', 'market', creator=lambda l: Opportunity(market=l))
    history = db.relationship('ProductHistory', backref='product', lazy='dynamic', passive_deletes=True)

    order_items = db.relationship('VendorOrderItem', backref='product', lazy='dynamic', passive_deletes=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.suppress_guessing = False

    @orm.reconstructor
    def __init_on_load__(self):
        self.suppress_guessing = False

    # Event handlers

    @classmethod
    def __declare_last__(cls):
        db.event.listen(cls, 'before_insert', cls._maybe_clear_fees)
        db.event.listen(cls, 'before_update', cls._maybe_clear_fees)
        db.event.listen(cls, 'after_insert', lambda m, c, t: cls._maybe_guess_quantity(t, 3))
        db.event.listen(cls, 'after_update', lambda m, c, t: cls._maybe_guess_quantity(t))

    @staticmethod
    def _maybe_guess_quantity(target, delay=None):
        if target.suppress_guessing:
            return

        insp = db.inspect(target)
        changed = insp.attrs['title'].history.has_changes() or \
                  insp.attrs['quantity_desc'].history.has_changes()

        if changed:
            # Use send_task to avoid circular import
            celery_app.send_task(
                'tasks.ops.products.guess_quantity',
                kwargs={'product_id': target.id},
                priority=DEFAULT_PRIORITY,
                countdown=delay
            )
            target.suppress_guessing = True

    @staticmethod
    def _maybe_clear_fees(mapper, conn, target):
        insp = db.inspect(target)
        price_changed = insp.attrs['price'].history.has_changes()
        fees_changed = insp.attrs['market_fees'].history.has_changes()

        if price_changed and not fees_changed:
            target.market_fees = None

    # Supplier order properties

    @hybrid_property
    def supplier_order_items(self):
        return VendorOrderItem.query.filter(
            VendorOrderItem.product_id.in_(l.id for l in self.supply_listings)
        )

    @hybrid_property
    def supplier_order_total_units(self):
        return sum(i.quantity for i in self.supplier_order_items.all())

    @hybrid_property
    def supplier_order_total_cost(self):
        return sum(i.total for i in self.supplier_order_items.all())

    @hybrid_property
    def supplier_order_avg_unit_cost(self):
        supplier_order_items = self.supplier_order_items.all()
        return sum(i.total for i in supplier_order_items) / sum(i.quantity for i in supplier_order_items)

    # Vendor order properties

    @hybrid_property
    def total_units_ordered(self):
        return sum(item.quantity * self.quantity for item in self.order_items.all())

    @total_units_ordered.expression
    def total_units_ordered(cls):
        return cls.quantity * db.select([
            db.func.sum(VendorOrderItem.quantity)
        ]).where(
            VendorOrderItem.product_id == cls.id
        ).label('total_units_ordered')

    @hybrid_property
    def total_ordered_cost(self):
        return sum(
            item.total for item in self.order_items.all()
        )

    @total_ordered_cost.expression
    def total_ordered_cost(cls):
        return db.select([
            db.func.sum(VendorOrderItem.total)
        ]).where(
            VendorOrderItem.product_id == cls.id
        ).label('total_ordered_cost')

    @hybrid_property
    def avg_unit_cost(self):
        return self.total_ordered_cost / self.total_units_ordered

    @avg_unit_cost.expression
    def avg_unit_cost(cls):
        return db.select([
            db.func.sum(VendorOrderItem.total) / db.func.sum(VendorOrderItem.total_units)
        ]).where(
            VendorOrderItem.product_id == cls.id
        ).label('avg_unit_cost')

    # Market & opportunity properties

    @hybrid_property
    def market_fees_estimate(self):
        return self.price * self.vendor.avg_market_fees

    @market_fees_estimate.expression
    def market_fees_estimate(cls):
        return db.select([
            db.cast(cls.price * Vendor.avg_market_fees, CURRENCY)
        ]).where(
            Vendor.id == cls.vendor_id
        ).label('market_fees_estimate')

    @hybrid_property
    def total_suppliers(self):
        return len(self.supply_listings)

    @total_suppliers.expression
    def total_suppliers(cls):
        return db.select([
            db.func.count(Opportunity.id)
        ]).where(
            Opportunity.market_id == cls.id
        ).label('total_suppliers')

    @hybrid_property
    def total_markets(self):
        return len(self.market_listings)

    @total_markets.expression
    def total_markets(cls):
        return db.select([
            db.func.count(Opportunity.id)
        ]).where(
            Opportunity.supply_id == cls.id
        ).label('total_markets')

    @hybrid_property
    def total_opportunities(self):
        return self.total_markets + self.total_suppliers

    @total_opportunities.expression
    def total_opportunities(cls):
        return db.select([
            db.func.count(Opportunity.id)
        ]).where(
            db.or_(
                Opportunity.supply_id == cls.id,
                Opportunity.market_id == cls.id
            )
        ).label('total_opportunities')

    # Derived properties

    @hybrid_property
    def unit_price(self):
        if self.price is not None and self.quantity:
            return quantize(self.price / self.quantity)
        elif self.price is not None:
            return self.price
        else:
            return None

    @unit_price.expression
    def unit_price(cls):
        return db.cast(Product.price / Product.quantity, CURRENCY)

    @hybrid_property
    def cost(self):
        if self.price is not None and self.vendor.ship_rate is not None:
            return quantize(self.price * (1 + self.vendor.ship_rate))
        elif self.price is not None:
            return self.price
        else:
            return None

    @cost.expression
    def cost(cls):
        return db.select([
            db.cast(cls.price * (1 + Vendor.ship_rate), CURRENCY)
        ]).where(Vendor.id == cls.vendor_id).label('cost')

    @hybrid_property
    def unit_cost(self):
        if self.cost is not None and self.quantity:
            return quantize(self.cost / self.quantity)
        elif self.cost is not None:
            return self.cost
        else:
            return None

    @unit_cost.expression
    def unit_cost(cls):
        return db.cast(Product.cost / Product.quantity, CURRENCY)

    def __repr__(self):
        return f'<{type(self).__name__} {self.sku}>'

    # Helper methods

    @classmethod
    def build_query(cls, *args, query=None, tags=None, vendor_id=None, total_suppliers=None, total_markets=None,
                    total_opps=None):

        if args and len(args) > 1:
            raise ValueError('build_query() only accepts 1 positional argument.')
        else:
            arg = args[0] if args else None

        q = cls.query

        if arg == 'inventory':
            inv_skus = db.select([
                FBAManageInventoryReportLine.asin
            ]).where(
                FBAManageInventoryReportLine.report_id == db.select([
                    AmzReport.id
                ]).where(
                    db.and_(
                        AmzReport.type == FBAManageInventoryReportLine.report_type,
                        AmzReport.status == '_DONE_',
                    )
                ).order_by(
                    AmzReport.start_date.desc()
                ).limit(1)
            )

            q = q.filter(
                Product.vendor_id == Vendor.get_amazon().id,
                Product.sku.in_(inv_skus)
            )
        elif arg is not None:
            raise ValueError(f'Unsupported argument: {arg}')

        if query:
            q_str = f'%{query}%'
            q = q.filter(
                db.or_(
                    cls.title.ilike(q_str),
                    cls.sku.ilike(q_str),
                    cls.quantity_desc.ilike(q_str)
                )
            )

        if tags:
            q = q.filter(
                db.func.json_contains(
                    cls.tags,
                    json.dumps(tags)
                )
            )

        if vendor_id:
            q = q.filter_by(
                vendor_id=vendor_id
            )

        if total_suppliers is not None:
            q = q.filter_by(total_suppliers=total_suppliers)

        if total_markets is not None:
            q = q.filter_by(total_markets=total_markets)

        if total_opps is not None:
            q = q.filter_by(total_opportunities=total_opps)

        return q

    def similarity_to(self, other):
        """Return the probability that this listing and other refer to the safe product."""

        def remove_symbols(s):
            return re.sub(r'[^a-zA-Z0-9 ]', '', s)

        def average_partial_ratio(s1, s2):
            sims = (
                fuzz.partial_ratio(s1, s2),
                fuzz.partial_ratio(s2, s1)
            )
            return sum(sims) / len(sims)

        scores = []

        brand_1 = remove_symbols(self.brand.lower().strip()) if self.brand else None
        brand_2 = remove_symbols(other.brand.lower().strip()) if other.brand else None

        model_1 = remove_symbols(self.model.lower().strip()) if self.model else None
        model_2 = remove_symbols(other.model.lower().strip()) if other.model else None

        title_1 = remove_symbols(self.title.lower().strip()) if self.title else None
        title_2 = remove_symbols(other.title.lower().strip()) if other.title else None

        brand_scores = []
        if brand_1 and brand_2:
            brand_scores.append(average_partial_ratio(brand_1, brand_2))
        elif brand_1 and title_2:
            brand_scores.append(fuzz.partial_ratio(brand_1, title_2))
        elif brand_2 and title_1:
            brand_scores.append(fuzz.partial_ratio(brand_2, title_1))

        if brand_scores:
            scores.append(max(brand_scores))

        model_scores = []
        if model_1 and model_2:
            model_scores.append(average_partial_ratio(model_1, model_2))
        elif model_1 and title_2:
            model_scores.append(fuzz.partial_ratio(model_1, title_2))
        elif model_2 and title_1:
            model_scores.append(fuzz.partial_ratio(model_2, title_1))

        if model_scores:
            scores.extend((max(model_scores), max(model_scores)))

        if title_1 and title_2:
            scores.append(fuzz.token_set_ratio(title_1, title_2))

        return sum(scores) / len(scores) / 100 if scores else None

    def add_supplier(self, other):
        similarity = self.similarity_to(other)
        opp = Opportunity.query.filter_by(market_id=self.id, supply_id=other.id).first()
        if opp:
            opp.similarity = similarity
        else:
            opp = Opportunity(market=self, supply=other, similarity=similarity)
            db.session.add(opp)

        db.session.commit()
        return opp

    def add_market(self, other):
        similarity = self.similarity_to(other)
        opp = Opportunity.query.filter_by(market_id=other.id, supply_id=self.id).first()

        if opp:
            opp.similarity = similarity
        else:
            opp = Opportunity(market=other, supply=self, similarity=similarity)
            db.session.add(opp)

        db.session.commit()

    def add_tags(self, *args):
        tag_set = set(self.tags or [])
        tag_set.update(args)
        self.tags = list(tag_set)

    def remove_tags(self, *args):
        if self.tags:
            for tag in args:
                try:
                    self.tags.remove(tag)
                except ValueError:
                    pass


########################################################################################################################


class VendorOrder(db.Model):
    """Represents an inventory order."""
    __table_args__ = (UniqueConstraint('vendor_id', 'order_number'),)

    id = db.Column(db.Integer, primary_key=True)
    vendor_id = db.Column(db.Integer, db.ForeignKey('vendor.id', ondelete='RESTRICT'), nullable=False)
    order_number = db.Column(db.String(64), nullable=False)
    order_date = db.Column(db.Date, nullable=False, default=db.func.now())
    sales_tax = db.Column(CURRENCY, default=0)
    shipping = db.Column(CURRENCY, default=0)

    items = db.relationship('VendorOrderItem', backref='order', lazy='dynamic', passive_deletes=True)

    @hybrid_property
    def subtotal(self):
        return decimal.Decimal(sum((item.subtotal for item in self.items)))

    @subtotal.expression
    def subtotal(cls):
        return db.select([
            db.cast(
                db.func.sum(VendorOrderItem.price_each * VendorOrderItem.quantity),
                CURRENCY
            )
        ]).where(
            VendorOrderItem.order_id == cls.id
        ).label('subtotal')

    @hybrid_property
    def total(self):
        return self.subtotal + self.sales_tax + self.shipping

    @total.expression
    def total(cls):
        return db.select([
            db.cast(
                db.func.sum(VendorOrderItem.price_each * VendorOrderItem.quantity) + cls.sales_tax + cls.shipping,
                CURRENCY
            )
        ]).where(
            VendorOrderItem.order_id == cls.id
        ).label('total')

    @hybrid_property
    def total_units(self):
        return sum(
            item.quantity * item.product.quantity for item in self.items.all()
        )

    @total_units.expression
    def total_units(cls):
        return db.select([
            db.func.sum(
                VendorOrderItem.quantity * Product.quantity
            )
        ]).where(
            db.and_(
                VendorOrderItem.order_id == cls.id,
                Product.id == VendorOrderItem.product_id
            )
        ).label('total_units')

    @hybrid_property
    def shipping_per_unit(self):
        return self.shipping / self.total_units

    @shipping_per_unit.expression
    def shipping_per_unit(cls):
        return cls.shipping / cls.total_units

    @hybrid_property
    def sales_tax_per_unit(self):
        return self.sales_tax / self.total_units

    @sales_tax_per_unit.expression
    def sales_tax_per_unit(cls):
        return cls.sales_tax / cls.total_units


########################################################################################################################


class VendorOrderItem(db.Model):
    """A single SKU in a single shipment of an order."""
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('vendor_order.id', ondelete='CASCADE'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id', ondelete='RESTRICT'), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    price_each = db.Column(CURRENCY, nullable=False)
    delivery_id = db.Column(db.Integer, db.ForeignKey('delivery.id'))

    delivery = db.relationship('Delivery')

    @classmethod
    def _total_order_units(cls):
        item_alias = db.aliased(VendorOrderItem)
        product_alias = db.aliased(Product)

        return db.select([
            db.func.sum(
                item_alias.quantity * product_alias.quantity
            )
        ]).where(
            db.and_(
                item_alias.order_id == cls.order_id,
                product_alias.id == item_alias.product_id
            )
        ).label('total_order_units')

    @hybrid_property
    def subtotal(self):
        return self.price_each * self.quantity

    @subtotal.expression
    def subtotal(cls):
        return db.cast(cls.price_each * cls.quantity, CURRENCY)

    @hybrid_property
    def total_units(self):
        return self.quantity * self.product.quantity

    @total_units.expression
    def total_units(cls):
        return cls.quantity * db.select([
            Product.quantity
        ]).where(
            Product.id == cls.product_id
        ).label('product_units')

    @hybrid_property
    def shipping(self):
        return self.order.shipping_per_unit * self.total_units

    @shipping.expression
    def shipping(cls):
        return cls.total_units * db.select([
            VendorOrder.shipping_per_unit
        ]).where(
            VendorOrder.id == cls.order_id,
        ).label('shipping')

    @hybrid_property
    def sales_tax(self):
        return self.order.sales_tax / self.order.total_units * self.total_units

    @sales_tax.expression
    def sales_tax(cls):
        return cls.total_units * db.select([
            VendorOrder.sales_tax_per_unit
        ]).where(
            VendorOrder.id == cls.order_id
        ).label(
            'sales_tax'
        )

    @hybrid_property
    def total(self):
        return self.subtotal + self.shipping + self.sales_tax

    @total.expression
    def total(cls):
        return cls.subtotal + cls.total_units * db.select([
            VendorOrder.sales_tax_per_unit + VendorOrder.shipping_per_unit
        ]).where(
            VendorOrder.id == cls.order_id
        ).label('total')

    @hybrid_property
    def unit_cost(self):
        return self.total / self.total_units


########################################################################################################################


class Delivery(db.Model):
    """A shipment from a vendor."""
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('vendor_order.id', ondelete='CASCADE'), nullable=False)
    carrier = db.Column(db.Enum('ups', 'fedex', 'usps', 'dhl'))
    tracking_number = db.Column(db.String(128))
    delivered_on = db.Column(db.Date)


########################################################################################################################


class Opportunity(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    supply_id = db.Column(db.Integer, db.ForeignKey('product.id', ondelete='CASCADE'), nullable=False)
    market_id = db.Column(db.Integer, db.ForeignKey('product.id', ondelete='CASCADE'), nullable=False)
    similarity = db.Column(db.Float)
    hidden = db.Column(db.Enum('hidden', 'invalid', 'partial'))

    supply = db.relationship(
        Product,
        primaryjoin=(supply_id == Product.id),
        backref=db.backref('market_opportunities', passive_deletes=True, lazy='dynamic'),
        single_parent=True
    )
    market = db.relationship(
        Product,
        primaryjoin=(market_id == Product.id),
        backref=db.backref('supply_opportunities', passive_deletes=True, lazy='dynamic'),
        single_parent=True
    )

    _m_alias = db.aliased(Product)
    _s_alias = db.aliased(Product)

    @classmethod
    def _is_estimate_expr(cls):
        return db.func.isnull(cls._m_alias.market_fees)

    @hybrid_property
    def is_estimate(self):
        return self.market.market_fees is None

    @is_estimate.expression
    def is_estimate(cls):
        return cls._is_estimate_expr()

    @classmethod
    def _cogs_expr(cls):
        return cls._s_alias.cost / cls._s_alias.quantity * cls._m_alias.quantity

    @classmethod
    def _revenue_expr(cls):
        return cls._m_alias.price - db.func.ifnull(
            cls._m_alias.market_fees,
            cls._m_alias.market_fees_estimate
        )

    @classmethod
    def _profit_expr(cls):
        return cls._revenue_expr() - cls._cogs_expr()

    @classmethod
    def _margin_expr(cls):
        return cls._profit_expr() / cls._m_alias.price

    @classmethod
    def _roi_expr(cls):
        return cls._profit_expr() / cls._cogs_expr()

    @hybrid_property
    def revenue(self):
        fees = self.market.market_fees if self.market.market_fees is not None\
            else self.market.price * self.market.vendor.avg_market_fees

        try:
            return quantize(self.market.price - fees)
        except TypeError:
            return None

    @revenue.expression
    def revenue(cls):
        return db.select([
            db.cast(
                cls._revenue_expr(),
                CURRENCY
            )
        ]).where(
            cls._m_alias.id == cls.market_id
        ).label('revenue')

    @hybrid_property
    def cogs(self):
        try:
            return quantize(self.supply.unit_cost * self.market.quantity)
        except TypeError:
            return None

    @cogs.expression
    def cogs(cls):
        return db.select([
            db.cast(
                cls._cogs_expr(),
                CURRENCY
            )
        ]).where(
            db.and_(
                cls._s_alias.id == cls.supply_id,
                cls._m_alias.id == cls.market_id
            )
        ).label('cogs')

    @hybrid_property
    def profit(self):
        try:
            return quantize(self.revenue - self.cogs)
        except TypeError:
            return None

    @profit.expression
    def profit(cls):
        return db.select([
            db.cast(
                cls._profit_expr(),
                CURRENCY
            )
        ]).where(
            db.and_(
                cls._s_alias.id == cls.supply_id,
                cls._m_alias.id == cls.market_id
            )
        ).label('profit')

    @hybrid_property
    def margin(self):
        try:
            return quantize(self.profit / self.market.price)
        except TypeError:
            return None

    @margin.expression
    def margin(cls):
        return db.select([
            db.cast(
                cls._margin_expr(),
                CURRENCY
            )
        ]).where(
            db.and_(
                cls._s_alias.id == cls.supply_id,
                cls._m_alias.id == cls.market_id
            )
        ).label('margin')

    @hybrid_property
    def roi(self):
        try:
            return quantize(self.profit / self.cogs)
        except TypeError:
            return None

    @roi.expression
    def roi(cls):
        return db.select([
            db.cast(
                cls._profit_expr() / cls._cogs_expr(),
                CURRENCY
            )
        ]).where(
            db.and_(
                cls._s_alias.id == cls.supply_id,
                cls._m_alias.id == cls.market_id
            )
        ).label('roi')

    @classmethod
    def build_query(cls, query=None, tags=None, max_cogs=None, min_profit=None, min_roi=None, min_similarity=None,
                    min_rank=None, max_rank=None, sort_by=None, sort_order=None, show_hidden=None):

        q = cls.query.join(
            cls._m_alias,
            cls.market_id == cls._m_alias.id
        ).join(
            cls._s_alias,
            cls.supply_id == cls._s_alias.id
        )

        if query or tags:
            q_str = f'%{query}%'
            tags_json = json.dumps(tags)

            query_conditions = [
                cls._m_alias.title.ilike(q_str),
                cls._m_alias.sku.ilike(q_str),
                cls._m_alias.brand.ilike(q_str),
                cls._m_alias.model.ilike(q_str),
                cls._s_alias.title.ilike(q_str),
                cls._s_alias.sku.ilike(q_str),
                cls._s_alias.brand.ilike(q_str),
                cls._s_alias.model.ilike(q_str)
            ] if query else []

            tag_conditions = [
                db.func.json_contains(cls._m_alias.tags, tags_json),
                db.func.json_contains(cls._s_alias.tags, tags_json)
            ] if tags else []

            q = q.filter(
                db.or_(
                    *query_conditions,
                    *tag_conditions
                )
            )

        if tags:
            q = q.filter(
                db.or_(
                    db.func.json_contains(
                        cls._m_alias.tags,
                        json.dumps(tags)
                    ),
                    db.func.json_contains(
                        cls._s_alias.tags,
                        json.dumps(tags)
                    )
                )
            )

        if max_cogs is not None:
            q = q.filter(cls._cogs_expr() <= max_cogs)

        if min_profit is not None:
            q = q.filter(cls._profit_expr() >= min_profit)

        if min_roi is not None:
            q = q.filter(cls._roi_expr() >= min_roi)

        if min_similarity is not None:
            q = q.filter(cls.similarity >= min_similarity)

        if min_rank is not None:
            q = q.filter(cls._m_alias.rank >= min_rank)

        if max_rank is not None:
            q = q.filter(cls._m_alias.rank <= max_rank)

        if not show_hidden:
            q = q.filter(cls.hidden.is_(None))

        sort_field = None
        if sort_by == 'rank':
            sort_field = cls._m_alias.rank
        elif sort_by == 'cogs':
            sort_field = cls._cogs_expr()
        elif sort_by == 'profit':
            sort_field = cls._profit_expr()
        elif sort_by == 'roi':
            sort_field = cls._roi_expr()
        elif sort_by == 'similarity':
            sort_field = cls.similarity
        elif sort_by == 'updated':
            sort_field = db.func.greatest(cls._m_alias.last_modified, cls._s_alias.last_modified)

        if sort_field is not None:
            q = q.order_by(sort_field.asc() if sort_order == 'asc' else sort_field.desc())

        return q


########################################################################################################################


class Job(db.Model):
    """Stores recurring job information."""
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(64), index=True, unique=True)
    schedule_type = db.Column(db.String(64), nullable=False)
    schedule_kwargs = db.Column(db.JSON)
    task_type = db.Column(db.String(64), nullable=False)
    task_params = db.Column(db.JSON)
    enabled = db.Column(db.Boolean, default=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.entry = None
        self.init_on_load()

    @orm.reconstructor
    def init_on_load(self):
        try:
            self.entry = RedBeatSchedulerEntry.from_key(
                key=RedBeatSchedulerEntry(name=self.name, app=celery_app).key,
                app=celery_app
            )
        except KeyError:
            self.entry = None

    def create_scheduler_entry(self):
        args = self.task_params.get('args', None) if self.task_params else None
        kwargs = self.task_params.get('kwargs', None) if self.task_params else None

        self.entry = RedBeatSchedulerEntry(
            name=self.name,
            task=self.task_type,
            schedule=getattr(schedules, self.schedule_type)(**self.schedule_kwargs),
            args=args,
            kwargs=kwargs,
            enabled=self.enabled,
            app=celery_app
        )

    @staticmethod
    def create_entry(mapper, connection, target):
        target.create_scheduler_entry()
        target.entry.save()

    @staticmethod
    def update_entry(mapper, connection, target):
        if target.entry and target.entry.name != target.name:
            target.entry.delete()

        target.create_scheduler_entry()
        target.entry.save()

    @staticmethod
    def delete_entry(mapper, connection, target):
        if target.entry:
            target.entry.delete()
            target.entry = None

    @classmethod
    def __declare_last__(cls):
        db.event.listen(cls, 'after_insert', cls.create_entry)
        db.event.listen(cls, 'after_update', cls.update_entry)
        db.event.listen(cls, 'after_delete', cls.delete_entry)


########################################################################################################################


REPORT_TYPES = (
    '_GET_FLAT_FILE_OPEN_LISTINGS_DATA_',
    '_GET_MERCHANT_LISTINGS_ALL_DATA_',
    '_GET_MERCHANT_LISTINGS_DATA_',
    '_GET_MERCHANT_LISTINGS_INACTIVE_DATA_',
    '_GET_MERCHANT_LISTINGS_DATA_BACK_COMPAT_',
    '_GET_MERCHANT_LISTINGS_DATA_LITE_',
    '_GET_MERCHANT_LISTINGS_DATA_LITER_',
    '_GET_MERCHANT_CANCELLED_LISTINGS_DATA_',
    '_GET_CONVERGED_FLAT_FILE_SOLD_LISTINGS_DATA_',
    '_GET_MERCHANT_LISTINGS_DEFECT_DATA_',
    '_GET_PAN_EU_OFFER_STATUS_',
    '_GET_MFN_PAN_EU_OFFER_STATUS_',
    '_GET_FLAT_FILE_ACTIONABLE_ORDER_DATA_',
    '_GET_ORDERS_DATA_',
    '_GET_FLAT_FILE_ORDERS_DATA_',
    '_GET_CONVERGED_FLAT_FILE_ORDER_REPORT_DATA_',
    '_GET_FLAT_FILE_ALL_ORDERS_DATA_BY_LAST_UPDATE_',
    '_GET_FLAT_FILE_ALL_ORDERS_DATA_BY_ORDER_DATE_',
    '_GET_XML_ALL_ORDERS_DATA_BY_LAST_UPDATE_',
    '_GET_XML_ALL_ORDERS_DATA_BY_ORDER_DATE_',
    '_GET_FLAT_FILE_PENDING_ORDERS_DATA_',
    '_GET_PENDING_ORDERS_DATA_',
    '_GET_CONVERGED_FLAT_FILE_PENDING_ORDERS_DATA_',
    '_GET_SELLER_FEEDBACK_DATA_',
    '_GET_V1_SELLER_PERFORMANCE_REPORT_',
    '_GET_V2_SETTLEMENT_REPORT_DATA_FLAT_FILE_',
    '_GET_V2_SETTLEMENT_REPORT_DATA_XML_',
    '_GET_V2_SETTLEMENT_REPORT_DATA_FLAT_FILE_V2_',
    '_GET_AMAZON_FULFILLED_SHIPMENTS_DATA_',
    '_GET_FBA_FULFILLMENT_CUSTOMER_SHIPMENT_SALES_DATA_',
    '_GET_FBA_FULFILLMENT_CUSTOMER_SHIPMENT_PROMOTION_DATA_',
    '_GET_FBA_FULFILLMENT_CUSTOMER_TAXES_DATA_',
    '_GET_AFN_INVENTORY_DATA_',
    '_GET_AFN_INVENTORY_DATA_BY_COUNTRY_',
    '_GET_FBA_FULFILLMENT_CURRENT_INVENTORY_DATA_',
    '_GET_FBA_FULFILLMENT_MONTHLY_INVENTORY_DATA_',
    '_GET_FBA_FULFILLMENT_INVENTORY_RECEIPTS_DATA_',
    '_GET_RESERVED_INVENTORY_DATA_',
    '_GET_FBA_FULFILLMENT_INVENTORY_SUMMARY_DATA_',
    '_GET_FBA_FULFILLMENT_INVENTORY_ADJUSTMENTS_DATA_',
    '_GET_FBA_FULFILLMENT_INVENTORY_HEALTH_DATA_',
    '_GET_FBA_MYI_UNSUPPRESSED_INVENTORY_DATA_',
    '_GET_FBA_MYI_ALL_INVENTORY_DATA_',
    '_GET_FBA_FULFILLMENT_CROSS_BORDER_INVENTORY_MOVEMENT_DATA_',
    '_GET_RESTOCK_INVENTORY_RECOMMENDATIONS_REPORT_',
    '_GET_FBA_FULFILLMENT_INBOUND_NONCOMPLIANCE_DATA_',
    '_GET_STRANDED_INVENTORY_UI_DATA_',
    '_GET_STRANDED_INVENTORY_LOADER_DATA_',
    '_GET_FBA_INVENTORY_AGED_DATA_',
    '_GET_EXCESS_INVENTORY_DATA_',
    '_GET_FBA_ESTIMATED_FBA_FEES_TXT_DATA_',
    '_GET_FBA_REIMBURSEMENTS_DATA_',
    '_GET_FBA_FULFILLMENT_CUSTOMER_RETURNS_DATA_',
    '_GET_FBA_FULFILLMENT_CUSTOMER_SHIPMENT_REPLACEMENT_DATA_',
    '_GET_FBA_RECOMMENDED_REMOVAL_DATA_',
    '_GET_FBA_FULFILLMENT_REMOVAL_ORDER_DETAIL_DATA_',
    '_GET_FBA_FULFILLMENT_REMOVAL_SHIPMENT_DETAIL_DATA_',
    '_GET_FLAT_FILE_SALES_TAX_DATA_',
    '_SC_VAT_TAX_REPORT_',
    '_GET_VAT_TRANSACTION_DATA_',
    '_GET_XML_BROWSE_TREE_DATA_',
)

CONDITIONS = (
    'New', 'NewItem', 'NewWithWarranty', 'NewOEM', 'NewOpenBox',
    'Used', 'UsedLikeNew', 'UsedVeryGood', 'UsedGood', 'UsedAcceptable', 'UsedPoor', 'UsedRefurbished',
    'CollectibleLikeNew', 'CollectibleVeryGood', 'CollectibleGood', 'CollectibleAcceptable', 'CollectiblePoor',
    'Refurbished', 'RefurbishedWithWarranty',
    'Club',
    'Unknown'
)


########################################################################################################################


class AmzReportLineMixin:
    report_type = NotImplementedError

    @classmethod
    def field_names(cls):
        return db.inspect(cls).columns.keys()

    def iter_fields(self):
        names = self.field_names()
        values = map(lambda k: getattr(self, k), names)
        return zip(names, values)


class AmzReport(db.Model, UpdateMixin):
    """An Amazon report"""
    id = db.Column(db.Integer, primary_key=True)
    type = db.Column(db.Enum(*REPORT_TYPES), nullable=False)
    start_date = db.Column(db.DateTime)
    end_date = db.Column(db.DateTime)
    options = db.Column(db.JSON, default={})
    request_id = db.Column(db.String(64))
    status = db.Column(db.Enum('_SUBMITTED_', '_IN_PROGRESS_', '_CANCELLED_', '_DONE_', '_DONE_NO_DATA_'))
    report_id = db.Column(db.String(64))
    complete = db.Column(db.Boolean, default=False)

    @property
    def lines(self):
        if self.type is None:
            return None
        try:
            line_type = [t for t in AmzReportLineMixin.__subclasses__() if t.report_type == self.type][0]
        except IndexError:
            raise ValueError(f'Unsupported report type: {self.type}')

        return line_type.query.filter_by(report_id=self.id)


########################################################################################################################


class FBAManageInventoryReportLine(db.Model, UpdateMixin, AmzReportLineMixin):
    """A single line on the FBA Manage Inventory report."""
    report_type = '_GET_FBA_MYI_UNSUPPRESSED_INVENTORY_DATA_'

    id = db.Column(db.Integer, primary_key=True)
    report_id = db.Column(db.Integer, db.ForeignKey('amz_report.id', ondelete='CASCADE'), nullable=False)
    sku = db.Column(db.String(32))
    fnsku = db.Column(db.String(32))
    asin = db.Column(db.String(32))
    product_name = db.Column(db.String(384))
    condition = db.Column(db.Enum(*CONDITIONS))
    your_price = db.Column(CURRENCY)
    mfn_listing_exists = db.Column(db.Boolean)
    mfn_fulfillable_quantity = db.Column(db.Integer)
    afn_listing_exists = db.Column(db.Boolean)
    afn_warehouse_quantity = db.Column(db.Integer)
    afn_fulfillable_quantity = db.Column(db.Integer)
    afn_unsellable_quantity = db.Column(db.Integer)
    afn_reserved_quantity = db.Column(db.Integer)
    afn_total_quantity = db.Column(db.Integer)
    per_unit_volume = db.Column(db.Float)
    afn_inbound_working_quantity = db.Column(db.Integer)
    afn_inbound_shipped_quantity = db.Column(db.Integer)
    afn_inbound_receiving_quantity = db.Column(db.Integer)

    report = db.relationship('AmzReport')
    product = db.relationship(
        'Product',
        backref=db.backref('inventory_history', lazy='dynamic'),
        primaryjoin='Product.sku == FBAManageInventoryReportLine.asin',
        foreign_keys=asin,
    )


########################################################################################################################


class Spider(db.Model):
    """A spider, used for crawling URLs for a vendor."""
    id = db.Column(db.Integer, primary_key=True)
    vendor_id = db.Column(db.Integer, db.ForeignKey('vendor.id', ondelete='CASCADE'), unique=True)
    name = db.Column(db.String(64), nullable=False, unique=True)

    vendor = db.relationship('Vendor', backref=db.backref('spider', uselist=False))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._redis = None

    @orm.reconstructor
    def init_on_load(self):
        self._redis = None

    @property
    def redis(self):
        if self._redis is None:
            self._redis = redis.from_url(os.environ.get('SCRAPY_REDIS_URL'))

        return self._redis

    def crawl_url(self, url):
        """Adds :url: to the spiders crawl queue."""
        self.redis.lpush(self.name + ':start_urls', url)
