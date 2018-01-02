from flask import request, flash, render_template, redirect, url_for
from flask_login import current_user, login_user, logout_user, login_required

from app import app, db
from app.forms import LoginForm, EditVendorForm
from app.models import User, Vendor


########################################################################################################################


@app.route('/')
@app.route('/index')
@login_required
def index():
    return render_template('index.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))

    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=form.username.data).first()
        if user is None or not user.check_password(form.password.data):
            flash('Invalid username or password.')
            return redirect(url_for('login'))

        login_user(user, remember=form.remember_me.data)
        next_page = request.args.get('next')
        if not next_page or not next_page.startswith('/'):
            return redirect(url_for('index'))
        else:
            return redirect(next_page)

    return render_template('login.html', title='Sign In', form=form)


@app.route('/logout')
def logout():
    logout_user()
    return redirect(url_for('index'))


@app.route('/vendors', methods=['GET', 'POST'])
@login_required
def vendors():
    form = EditVendorForm()
    if form.validate_on_submit():
        vendor = Vendor(
            name=form.name.data,
            website=form.website.data,
            image_url=form.image_url.data
        )

        db.session.add(vendor)
        db.session.commit()

        flash(f'Vendor \"{vendor.name}\" created successfully.')
        return redirect(url_for('vendors'))

    vendors = Vendor.query.order_by(Vendor.name.asc()).all()
    return render_template('vendors.html', title='Vendors', form=form, vendors=vendors)


@app.route('/vendors/<vendor_id>', methods=['GET', 'POST'])
@login_required
def edit_vendor(vendor_id):
    vendor = Vendor.query.filter_by(id=vendor_id).first()
    if vendor is None:
        flash(f'Invalid vendor id: {vendor_id}')
        return redirect(url_for('vendors'))

    form = EditVendorForm(obj=vendor)
    if form.validate_on_submit() and form.submit.data:
        form.populate_obj(vendor)
        db.session.commit()
        flash(f'Changes saved')
        return redirect(url_for('vendors'))
    elif form.delete.data:
        db.session.delete(vendor)
        db.session.commit()
        flash(f'Vendor \"{vendor.name}\" deleted.')
        return redirect(url_for('vendors'))

    return render_template('edit_vendor.html', title='Edit Vendor', form=form)