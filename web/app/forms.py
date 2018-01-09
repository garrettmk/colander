from itertools import zip_longest
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, BooleanField, SubmitField, IntegerField, DecimalField, SelectField,\
    TextAreaField
from wtforms.fields.html5 import URLField
from wtforms.validators import DataRequired, URL, ValidationError, Optional, Length

from app.models import Vendor, QuantityMap, Product


########################################################################################################################


class LoginForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired()])
    password = PasswordField('Password', validators=[DataRequired()])
    remember_me = BooleanField('Remember me')
    submit = SubmitField('Sign In')


class EditVendorForm(FlaskForm):
    name = StringField('Name', validators=[DataRequired()])
    website = URLField('Website', validators=[DataRequired()])
    image_url = URLField('Image URL')
    submit = SubmitField('Create Vendor')
    delete = SubmitField('Delete')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.edit_vendor = kwargs.get('obj', None)

    def validate_name(self, name):
        vendor = Vendor.query.filter_by(name=name.data).first()
        if vendor and vendor != self.edit_vendor:
            raise ValidationError('Please use a different name.')

    def validate_website(self, website):
        vendor = Vendor.query.filter_by(website=website.data).first()
        if vendor and vendor != self.edit_vendor:
            raise ValidationError('Please use a different website.')


class EditQuantityMapForm(FlaskForm):
    text = StringField('Text', validators=[DataRequired()])
    quantity = IntegerField('Quantity', validators=[DataRequired()])
    submit = SubmitField('Submit')
    delete = SubmitField('Delete')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.edit_qmap = kwargs.get('obj', None)

    def validate_text(self, text):
        qmap = QuantityMap.query.filter_by(text=text.data).first()
        if qmap and qmap != self.edit_qmap:
            raise ValidationError('Please use different text.')


class EditProductForm(FlaskForm):
    vendor_id = SelectField('Vendor', coerce=int, validators=[DataRequired()])
    sku = StringField('SKU', validators=[DataRequired()])
    title = StringField('Title', validators=[Optional(), Length(max=256)])
    detail_url = URLField('Detail page URL')
    image_url = URLField('Image URL')
    price = DecimalField('Price', places=2)
    quantity = IntegerField('Quantity', validators=[Optional()])
    quantity_desc = StringField('Quantity description')
    brand = StringField('Brand')
    model = StringField('Model')
    upc = StringField('UPC')
    description = TextAreaField('Description', validators=[Optional()])

    submit = SubmitField('Submit')
    delete = SubmitField('Delete')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.edit_product = kwargs.get('obj', None)
        self.vendor_id.choices = [(v.id, v.name) for v in Vendor.query.order_by(Vendor.name.asc()).all()]

    def validate_sku(self, sku):
        vendor_id = int(self.vendor_id.data)
        product = Product.query.filter_by(vendor_id=vendor_id, sku=sku.data).first()
        if product and product != self.edit_product:
            raise ValidationError('Please choose a different vendor or SKU.')