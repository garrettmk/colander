import ast
from itertools import zip_longest
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, BooleanField, SubmitField, IntegerField, DecimalField, SelectField,\
    TextAreaField, SelectMultipleField, RadioField, DateField, DateTimeField
from wtforms.fields.html5 import URLField
from wtforms.ext.sqlalchemy.fields import QuerySelectField
from wtforms.validators import DataRequired, URL, ValidationError, Optional, Length, NumberRange

from app.models import Vendor, QuantityMap, Product, VendorOrder, AmzReportLineMixin


########################################################################################################################


class TagsField(SelectMultipleField):

    def pre_validate(self, form):
        pass


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
    submit = SubmitField('OK')

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
    detail_url = URLField('Detail page URL', validators=[Optional()])
    image_url = URLField('Image URL', validators=[Optional()])
    price = DecimalField('Price', places=2, validators=[Optional()])
    quantity = IntegerField('Quantity', validators=[Optional()])
    quantity_desc = StringField('Quantity description', validators=[Optional()])
    brand = StringField('Brand', validators=[Optional()])
    model = StringField('Model', validators=[Optional()])
    upc = StringField('UPC', validators=[Optional()])
    description = TextAreaField('Description', validators=[Optional()])
    tags = TagsField('Tags', validators=[Optional()])

    submit = SubmitField('Submit')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.edit_product = kwargs.get('obj', None)
        self.vendor_id.choices = [(v.id, v.name) for v in Vendor.query.order_by(Vendor.name.asc()).all()]

        if self.edit_product and self.edit_product.tags:
            self.tags.choices = [(tag, tag) for tag in self.edit_product.tags]
        else:
            self.tags.choices = []

    def validate_tags(self, tags):
        pass

    def validate_sku(self, sku):
        vendor_id = int(self.vendor_id.data)
        product = Product.query.filter_by(vendor_id=vendor_id, sku=sku.data).first()
        if product and product != self.edit_product:
            raise ValidationError('Please choose a different vendor or SKU.')


class SearchProductsForm(FlaskForm):
    vendor_id = SelectField('Vendor', coerce=int, validators=[Optional()])
    query = StringField('Text', validators=[Optional()])
    tags = TagsField('Tags', validators=[Optional()])

    submit = SubmitField('Search')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.vendor_id.choices = [(0, 'Any')] + [(v.id, v.name) for v in Vendor.query.order_by(Vendor.name.asc()).all()]


class EditJobForm(FlaskForm):
    name = StringField('Name', validators=[DataRequired(), Length(max=64)])
    schedule_type = SelectField('Schedule', choices=[('schedule', 'Interval'), ('crontab', 'Crontab')])
    schedule_kwargs = StringField('Arguments', validators=[DataRequired()])
    task = SelectField('Task', choices=[
        ('tasks.jobs.jobs.dummy', 'Dummy'),
        ('tasks.jobs.jobs.track_amazon_products', 'Track Amazon Products'),
        ('tasks.jobs.jobs.update_inventory', 'Update FBA Inventory'),
        ('tasks.jobs.jobs.crawl_url', 'Crawl URL'),
        ('tasks.jobs.jobs.update_vendor_rates', 'Update Vendor Rates')
    ])
    task_params = StringField('Parameters', validators=[Optional()])
    enabled = BooleanField('Enabled', default=False)

    submit = SubmitField('Ok')

    def validate_schedule_kwargs(self, field):
        try:
            ast.literal_eval(field.data)
        except Exception as e:
            raise ValidationError(repr(e))

    def validate_task_params(self, field):
        try:
            ast.literal_eval(field.data)
        except Exception as e:
            raise ValidationError(repr(e))


class SearchOpportunitiesForm(FlaskForm):
    query = StringField('Keywords', validators=[Optional()])
    tags = TagsField('Tags', validators=[Optional()])
    max_cogs = DecimalField('Max. COGS', validators=[Optional()])
    min_profit = DecimalField('Min. profit', validators=[Optional()])
    min_roi = DecimalField('Min. ROI', validators=[Optional()])
    min_similarity = DecimalField('Min. similarity', validators=[Optional(), NumberRange(min=0, max=100)])
    min_rank = IntegerField('Min. rank', validators=[Optional(), NumberRange(min=0)])
    max_rank = IntegerField('Max. rank', validators=[Optional(), NumberRange(min=0)])
    sort_by = SelectField('Sort by', choices=[
        (None, 'None'),
        ('rank', 'Rank'),
        ('cogs', 'COGS'),
        ('profit', 'Profit'),
        ('roi', 'ROI'),
        ('similarity', 'Similarity'),
        ('updated', 'Last Update')
    ])
    sort_order = SelectField('Order', choices=[('asc', 'Ascending'), ('desc', 'Descending')])
    submit = SubmitField('Search')


class AddOpportunityForm(FlaskForm):
    vendor_id = SelectField('Vendor', coerce=int, validators=[DataRequired()])
    sku = StringField('SKU', validators=[DataRequired()])
    supply_or_market = RadioField('Type', coerce=int, choices=[(0, 'Supplier'), (1, 'Market')], validators=[DataRequired()])

    submit = SubmitField('Add')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.vendor_id.choices = [(v.id, v.name) for v in Vendor.query.order_by(Vendor.name.asc()).all()]

    def validate_sku(self, sku):
        vendor_id = int(self.vendor_id.data)
        product = Product.query.filter_by(vendor_id=vendor_id, sku=sku.data).first()
        if product is None:
            raise ValidationError('Not a valid Vendor/SKU combination.')


class EditVendorOrderForm(FlaskForm):
    vendor_id = SelectField('Vendor', coerce=int, validators=[DataRequired()])
    order_number = StringField('Order number', validators=[DataRequired()])
    order_date = DateField('Order date', format='%m/%d/%Y', validators=[Optional()])
    sales_tax = DecimalField('Sales tax', validators=[Optional()])
    shipping = DecimalField('Shipping', validators=[Optional()])

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.vendor_id.choices = [(v.id, v.name) for v in Vendor.query.order_by(Vendor.name.asc()).all()]
        self.edit_order = kwargs.get('obj', None)

    def validate_order_number(self, order_num):
        order = VendorOrder.query.filter_by(vendor_id=self.vendor_id.data, order_number=order_num.data).first()
        if order and order is not self.edit_order:
            raise ValidationError('Order number already in use for this vendor.')


class EditVendorOrderItemForm(FlaskForm):
    vendor_id = SelectField('Vendor', coerce=int, validators=[Optional()])  # Just used for display purposes
    sku = StringField('SKU', validators=[DataRequired()])
    price_each = DecimalField('Price', validators=[DataRequired()])
    quantity = IntegerField('Quantity', validators=[DataRequired()])

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.vendor_id.choices = [(v.id, v.name) for v in Vendor.query.order_by(Vendor.name.asc()).all()]
        self.order = kwargs.get('order', None)

    def validate_sku(self, sku):
        vendor_id = self.order.vendor_id if self.order is not None else self.vendor_id.data
        product = Product.query.filter_by(vendor_id=vendor_id, sku=sku.data).first()
        if product is None:
            raise ValidationError('Not a valid SKU for this vendor.')


class ReportForm(FlaskForm):
    type = SelectField('Report type', validators=[DataRequired()])
    start_date = DateTimeField('Start date', validators=[Optional()])
    end_date = DateTimeField('End date', validators=[Optional()])
    # options = StringField('Options', validators=[Optional()])

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.type.choices = [(t.report_type, t.report_type) for t in AmzReportLineMixin.__subclasses__()]
