from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import UserMixin
from app import db, login
from sqlalchemy import UniqueConstraint


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
        return f'<User {self.username}>'

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


########################################################################################################################


class QuantityMap(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    text = db.Column(db.String(128), index=True, unique=True)
    quantity = db.Column(db.Integer)


########################################################################################################################


class Product(db.Model):
    __table_args__ = (UniqueConstraint('vendor', 'sku'),)

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(256), index=True)
    vendor = db.Column(db.Integer, db.ForeignKey('vendor.id'), nullable=False)
    sku = db.Column(db.String(64), index=True, nullable=False)
    detail_url = db.Column(db.String(128))
    image_url = db.Column(db.String(128))
    price = db.Column(db.Integer, index=True)
    quantity = db.Column(db.Integer)

    brand = db.Column(db.String(64), index=True)
    model = db.Column(db.String(64), index=True)
    upc = db.Column(db.String(12))

    quantity_desc = db.Column(db.String(64))
    data = db.Column(db.JSON)
