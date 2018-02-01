import re
import decimal
import json
import datetime
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import UserMixin
from app import app, db, login, celery_app
from sqlalchemy import UniqueConstraint, orm
from sqlalchemy.ext.associationproxy import association_proxy
from sqlalchemy.ext.hybrid import hybrid_property
from fuzzywuzzy import fuzz
from redbeat import RedBeatScheduler, RedBeatSchedulerEntry
import celery.schedules as schedules


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

        return f'{i:,}'

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

    return dict(
        as_money=as_money,
        as_percent=as_percent,
        as_quantity=as_quantity,
        as_yesno=as_yesno,
        set_page_number=set_page_number,
        Vendor=Vendor
    )


########################################################################################################################


@login.user_loader
def load_user(id):
    return User.query.get(int(id))


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

    products = db.relationship('Product', backref='vendor', lazy='dynamic', passive_deletes=True)

    def __repr__(self):
        return f'<{type(self).__name__} {self.name}>'

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
        return f'<{type(self).__name__} {self.text}={self.quantity}>'


########################################################################################################################


class Product(db.Model):
    __table_args__ = (UniqueConstraint('vendor_id', 'sku'),)

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(256), index=True)
    vendor_id = db.Column(db.Integer, db.ForeignKey('vendor.id', ondelete='CASCADE'), nullable=False)
    sku = db.Column(db.String(64), index=True, nullable=False)
    detail_url = db.Column(db.String(128))
    image_url = db.Column(db.String(128))
    price = db.Column(CURRENCY, index=True)
    quantity = db.Column(db.Integer, default=1)
    market_fees = db.Column(CURRENCY, index=True)
    rank = db.Column(db.Integer, index=True)

    brand = db.Column(db.String(64), index=True)
    model = db.Column(db.String(64), index=True)
    upc = db.Column(db.String(12))
    description = db.Column(db.Text)

    quantity_desc = db.Column(db.String(64))
    data = db.Column(db.JSON)
    tags = db.Column(db.JSON, default=[])

    supply_listings = association_proxy('supply_relations', 'supply', creator=lambda l: Opportunity(supply=l))
    market_listings = association_proxy('market_relations', 'market', creator=lambda l: Opportunity(market=l))

    @hybrid_property
    def unit_price(self):
        return quantize(self.price / self.quantity)

    @unit_price.expression
    def unit_price(cls):
        return db.cast(Product.price / Product.quantity, CURRENCY)

    @hybrid_property
    def cost(self):
        return quantize(self.price * (1 + self.vendor.ship_rate))

    @cost.expression
    def cost(cls):
        return db.select([
            db.cast(cls.price * (1 + Vendor.ship_rate), CURRENCY)
        ]).where(Vendor.id == cls.vendor_id).label('cost')

    @hybrid_property
    def unit_cost(self):
        return quantize(self.cost / self.quantity)

    @unit_cost.expression
    def unit_cost(cls):
        return db.cast(Product.cost / Product.quantity, CURRENCY)

    def __repr__(self):
        return f'<{type(self).__name__} {self.sku}>'

    @classmethod
    def build_query(cls, query=None, tags=None, vendor_id=None):
        q = cls.query

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

        return q

    def update(self, data):
        for key in list(data):
            if hasattr(self, key):
                setattr(self, key, data.pop(key))

        if self.data is not None:
            self.data.update(data)
        else:
            self.data = data

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


########################################################################################################################


class Opportunity(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    supply_id = db.Column(db.Integer, db.ForeignKey('product.id', ondelete='CASCADE'), nullable=False)
    market_id = db.Column(db.Integer, db.ForeignKey('product.id', ondelete='CASCADE'), nullable=False)
    similarity = db.Column(db.Float)

    supply = db.relationship(
        Product,
        primaryjoin=(supply_id == Product.id),
        backref=db.backref('market_relations', passive_deletes=True),
        single_parent=True
    )
    market = db.relationship(
        Product,
        primaryjoin=(market_id == Product.id),
        backref=db.backref('supply_relations', passive_deletes=True),
        single_parent=True
    )

    _m_alias = db.aliased(Product)
    _s_alias = db.aliased(Product)

    @classmethod
    def _cogs_expr(cls):
        return cls._s_alias.cost / cls._s_alias.quantity * cls._m_alias.quantity

    @classmethod
    def _revenue_expr(cls):
        return cls._m_alias.price - cls._m_alias.market_fees

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
        try:
            return quantize(self.market.price - self.market.market_fees)
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
        return quantize(self.supply.unit_cost * self.market.quantity)

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
                    min_rank=None, max_rank=None, sort_by=None, sort_order=None):

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

        if sort_field is not None:
            q = q.order_by(sort_field.asc() if sort_order == 'asc' else sort_field.desc())

        return q


########################################################################################################################


class Job(db.Model):
    """Stores recurring job information."""
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(64), index=True, unique=True)
    schedule = db.Column(db.JSON)
    enabled = db.Column(db.Boolean, default=False)
    task = db.Column(db.JSON)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.init_on_load()
        self.entry = None

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
        sched_type = list(self.schedule.keys())[0]
        sched_kwargs = self.schedule[sched_type]
        schedule = getattr(schedules, sched_type)(**sched_kwargs)

        task = list(self.task.keys())[0]
        task_kwargs = self.task[task]

        self.entry = RedBeatSchedulerEntry(
            name=self.name,
            task=task,
            schedule=schedule,
            kwargs=task_kwargs,
            enabled=self.enabled,
            app=celery_app
        )

    @staticmethod
    def create_entry(mapper, connection, target):
        print(f'Create entry')
        target.create_scheduler_entry()
        target.entry.save()

    @staticmethod
    def update_entry(mapper, connection, target):
        print('Update entry')
        if target.entry and target.entry.name != target.name:
            target.entry.delete()

        target.create_scheduler_entry()
        target.entry.save()

    @staticmethod
    def delete_entry(mapper, connection, target):
        print('Delete entry')
        if target.entry:
            target.entry.delete()
            target.entry = None

    @classmethod
    def __declare_last__(cls):
        db.event.listen(cls, 'after_insert', cls.create_entry)
        db.event.listen(cls, 'after_update', cls.update_entry)
        db.event.listen(cls, 'after_delete', cls.delete_entry)


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
            self.timestamp = datetime.datetime.now()
            self.price = product.price
            self.market_fees = product.market_fees
            self.rank = product.rank
