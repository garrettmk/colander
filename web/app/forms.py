from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, BooleanField, SubmitField
from wtforms.fields.html5 import URLField
from wtforms.validators import DataRequired, URL, ValidationError, Optional

from app.models import Vendor


########################################################################################################################


class LoginForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired()])
    password = PasswordField('Password', validators=[DataRequired()])
    remember_me = BooleanField('Remember me')
    submit = SubmitField('Sign In')


class EditVendorForm(FlaskForm):
    name = StringField('Name', validators=[DataRequired()])
    website = StringField('Website', validators=[DataRequired(), URL()])
    image_url = StringField('Image URL', validators=[Optional(), URL()])
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

