from flask import request, flash, render_template, redirect, url_for, jsonify
from flask_login import current_user, login_user, logout_user, login_required

from app import app, db, celery
from app.forms import LoginForm, EditVendorForm, EditQuantityMapForm, EditProductForm
from app.models import User, Vendor, QuantityMap, Product, Opportunity

from celery import chain, chord
from tasks.ops.products import clean_and_import, update_amazon_listing, update_fba_fees, find_amazon_matches,\
    quantity_map_updated
from tasks.parsed.products import GetCompetitivePricingForASIN, GetMyFeesEstimate
from tasks.parsed.product_adv import ItemLookup


########################################################################################################################
# Index & user login

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


########################################################################################################################
# Task API


@app.route('/api/<path:task>')
def api_call(task):
    task_name = task.replace('/', '.')
    args = [arg for arg, val in request.args.items() if not len(val)]
    kwargs = {arg: val for arg, val in request.args.items() if arg not in args}

    job = celery.send_task(
        task_name,
        args=args,
        kwargs=kwargs,
        expires=30
    )

    try:
        return jsonify(job.get(timeout=30))
    except Exception as e:
        return repr(e)


########################################################################################################################
# Vendors


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


########################################################################################################################
# Quantity mappings


@app.route('/quantitymap', methods=['GET', 'POST'])
@login_required
def quantity_map():
    form = EditQuantityMapForm()
    if form.validate_on_submit():
        qmap = QuantityMap(
            text=form.text.data,
            quantity=form.quantity.data
        )

        db.session.add(qmap)
        db.session.commit()
        quantity_map_updated.delay(qmap.id)
        flash(f'New quantity map created: {qmap.text} = {qmap.quantity}')
        return redirect(url_for('quantity_map'))

    qmaps = QuantityMap.query.order_by(QuantityMap.text.asc()).all()
    return render_template('quantity_map.html', title='Quantity Maps', form=form, qmaps=qmaps)


@app.route('/quantitymap/<qmap_id>', methods=['GET', 'POST'])
@login_required
def edit_quantity_map(qmap_id):
    qmap = QuantityMap.query.filter_by(id=qmap_id).first()
    if qmap is None:
        flash(f'Invalid quantity map id: {qmap_id}')
        return redirect(url_for('quantity_map'))

    form = EditQuantityMapForm(obj=qmap)
    if form.validate_on_submit() and form.submit.data:
        form.populate_obj(qmap)
        db.session.commit()
        quantity_map_updated.delay(qmap.id)
        flash(f'Changes saved: {qmap.text} = {qmap.quantity}')
        return redirect(url_for('quantity_map'))
    elif form.delete.data:
        db.session.delete(qmap)
        db.session.commit()
        flash(f'Deleted quantity map \"{qmap.text}\"')
        return redirect(url_for('quantity_map'))

    return render_template('edit_quantity_map.html', title='Edit Quantity Map', form=form)


########################################################################################################################
# Products


@app.route('/products', methods=['GET', 'POST'])
@login_required
def products():
    form = EditProductForm()
    if form.validate_on_submit():
        product = Product()
        form.populate_obj(product)
        db.session.add(product)
        db.session.commit()

        if db.session.query(Vendor.name).filter(Vendor.id == form.vendor_id.data).first()[0] == 'Amazon':
            chain(
                chord(
                    (
                        GetCompetitivePricingForASIN.s(product.sku),
                        ItemLookup.s(product.sku),
                    ),
                    update_amazon_listing.s(product.id)
                ),
                update_fba_fees.s()
            ).apply_async()
        else:
            find_amazon_matches.delay(product.id)

        flash(f'Product created: {product.vendor.name} {product.sku}')
        return redirect(url_for('products'))

    products = Product.query.order_by(Product.vendor_id.asc()).all()
    return render_template('products.html', title='Products', form=form, products=products)


@app.route('/products/<product_id>', methods=['GET', 'POST'])
@login_required
def edit_product(product_id):
    product = Product.query.filter_by(id=product_id).first()
    if product is None:
        flash(f'Invalid product id: {product_id}')
        return redirect(url_for('products'))

    form = EditProductForm(obj=product)
    if form.validate_on_submit() and form.submit.data:
        data = form.data
        data.pop('submit', None)
        data.pop('delete', None)
        data.pop('csrf_token', None)
        clean_and_import(data)
        flash(f'Changes saved')
        return redirect(url_for('products'))
    elif form.delete.data:
        db.session.delete(product)
        db.session.commit()
        flash(f'Product deleted')
        return redirect(url_for('products'))

    return render_template('edit_product.html', title='Edit Product', form=form)


########################################################################################################################
# Opportunities


@app.route('/opportunities')
@login_required
def opportunities():
    opps = Opportunity.query.all()
    return render_template('opportunities.html', title='Opportunities', opportunities=opps)