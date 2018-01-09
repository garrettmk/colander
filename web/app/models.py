import re
import decimal
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import UserMixin
from app import app, db, login
from sqlalchemy import UniqueConstraint
from sqlalchemy.ext.associationproxy import association_proxy
from sqlalchemy.ext.hybrid import hybrid_property
from fuzzywuzzy import fuzz

decimal.getcontext().prec = 23
CURRENCY = db.Numeric(19, 4)


def quantize(d, places=4):
    depth = '.' + '0' * (places - 1) + '1'
    return d.quantize(decimal.Decimal(depth))


@app.context_processor
def decimal_processor():

    def as_money(d, p=2):
        if d is None:
            return 'N/A'

        depth = '.' + '0' * (p - 1) + '1'
        return '$' + str(d.quantize(decimal.Decimal(depth)))

    def as_percent(d, p=1):
        if d is None:
            return 'N/A'

        depth = '.' + '0' * (p - 1) + '1'
        return str((d * 100).quantize(decimal.Decimal(depth))) + '%'

    return dict(as_money=as_money, as_percent=as_percent)


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

    @hybrid_property
    def revenue(self):
        try:
            return quantize(self.market.price - self.market.market_fees)
        except TypeError:
            return None

    @revenue.expression
    def revenue(cls):
        return db.select([
            db.cast(Product.price - Product.market_fees, CURRENCY)
        ]).where(Product.id == cls.market_id).label('revenue')

    @hybrid_property
    def cogs(self):
        return quantize(self.supply.unit_cost * self.market.quantity)

    @cogs.expression
    def cogs(cls):
        m_alias = db.aliased(Product)
        s_alias = db.aliased(Product)
        return db.select([
            db.cast(s_alias.cost / s_alias.quantity * m_alias.quantity, CURRENCY)
        ]).where(db.and_(s_alias.id == cls.supply_id, m_alias.id == cls.market_id)).\
            label('cogs')

    @hybrid_property
    def profit(self):
        try:
            return quantize(self.revenue - self.cogs)
        except TypeError:
            return None

    @profit.expression
    def profit(cls):
        return db.select([
            db.cast(cls.revenue - cls.cogs, CURRENCY)
        ]).label('profit')

    @hybrid_property
    def margin(self):
        try:
            return quantize(self.profit / self.market.price)
        except TypeError:
            return None

    @margin.expression
    def margin(cls):
        return db.select([
            db.cast(cls.profit / Product.price, CURRENCY)
        ]).where(Product.id == cls.market_id).label('margin')

    @hybrid_property
    def roi(self):
        try:
            return quantize(self.profit / self.cogs)
        except TypeError:
            return None

    @roi.expression
    def roi(cls):
        return db.select([
            db.cast(cls.profit / cls.cogs, db.Numeric(19, 4))
        ]).label('roi')