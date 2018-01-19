from flask import request, flash, render_template, redirect, url_for, jsonify
from flask_login import current_user, login_user, logout_user, login_required

from app import app, db, celery
from app.forms import LoginForm, EditVendorForm, EditQuantityMapForm, EditProductForm, SearchProductsForm
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
    return redirect(url_for('login'))


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


@app.route('/vendors')
@login_required
def vendors():
    """The top-level Vendor index."""
    product_count = Product.query.count()
    vendors = Vendor.query.order_by(Vendor.name.asc()).all()
    return render_template(
        'vendors.html',
        title='Vendors',
        vendors=vendors,
        product_count=product_count
    )


@app.route('/vendors/create', methods=['GET', 'POST'])
@login_required
def new_vendor_form():
    """Renders the vendor creation form."""
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
        return jsonify(status='ok')

    return render_template('forms/vendor.html', title="New Vendor", form=form)


@app.route('/vendors/edit/<vendor_id>', methods=['GET', 'POST'])
@login_required
def edit_vendor_form(vendor_id):
    """Renders the Edit Vendor form."""
    vendor = Vendor.query.filter_by(id=vendor_id).first_or_404()
    form = EditVendorForm(obj=vendor)
    if form.validate_on_submit():
        form.populate_obj(vendor)
        db.session.commit()
        flash(f'Changes saved')
        return jsonify(status='ok')

    return render_template('forms/vendor.html', title="Edit Vendor", form=form)


@app.route('/vendors/delete', methods=['POST'])
@login_required
def delete_vendor():
    """Delete a vendor."""
    print(request.form)
    vendor = Vendor.query.filter_by(id=request.form['vendor_id']).first_or_404()
    db.session.delete(vendor)
    db.session.commit()
    flash(f'Vendor \"{vendor.name}\" deleted.')
    return 'ok'


@app.route('/vendors/<vendor_id>')
@login_required
def vendor_details(vendor_id):
    """Display a vendor's detail page."""
    vendor = Vendor.query.filter_by(id=vendor_id).first_or_404()
    page_num = request.args.get('page', 1, type=int)
    product_page = vendor.products.paginate(page_num, app.config['MAX_PAGE_ITEMS'], False)
    return render_template(
        'vendor_details.html',
        vendor=vendor,
        product_page=product_page
    )


########################################################################################################################
# Quantity mappings


@app.route('/quantitymap', methods=['GET', 'POST'])
@login_required
def quantity_map():
    """Render the top-level Quantity Maps index page."""
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
    """Render the top-level Products index page."""
    page_num = request.args.get('page', 1, type=int)
    products = Product.query.order_by(Product.title.asc()).paginate(page_num, app.config['MAX_PAGE_ITEMS'], False)
    product_count = Product.query.count()
    return render_template(
        'products.html',
        title='Products',
        products=products,
        product_count=product_count
    )


@app.route('/products/create', methods=['GET', 'POST'])
@login_required
def new_product_form():
    """Renders the New Product form."""
    form = EditProductForm()
    if form.validate_on_submit():
        product = Product()
        form.populate_obj(product)
        db.session.add(product)
        db.session.commit()
        flash(f'Product {product.sku} created.')

        if product.vendor_id == 1:
            chain(
                chord(
                    (
                        GetCompetitivePricingForASIN.s(product.sku),
                        ItemLookup.s(product.sku)
                    ),
                    update_amazon_listing.s(product.id)
                ),
                update_fba_fees.s()
            ).apply_async()
        else:
            find_amazon_matches.delay(product.id)

        return jsonify(status='ok')
    
    return render_template('forms/product.html', title='New Product', form=form)


@app.route('/products/<product_id>')
@login_required
def product_details(product_id):
    """Display a product's detail page."""
    page_num = request.args.get('page', 1, type=int)

    product = Product.query.filter_by(id=product_id).first_or_404()
    opps = Opportunity.query.filter(
        db.or_(
            Opportunity.market_id == product_id,
            Opportunity.supply_id == product_id
        )
    ).paginate(page_num, app.config['MAX_PAGE_ITEMS'], False)
    return render_template(
        'product_details.html',
        product=product,
        opps_page=opps
    )


@app.route('/products/edit/<product_id>', methods=['GET', 'POST'])
@login_required
def edit_product_form(product_id):
    """Renders the Edit Product form."""
    product = Product.query.filter_by(id=product_id).first_or_404()
    form = EditProductForm(obj=product)
    if form.validate_on_submit():
        form.populate_obj(product)
        db.session.commit()
        flash('Changes saved')
        return jsonify(status='ok')

    return render_template('forms/product.html', title='Edit Product', form=form)


@app.route('/products/delete', methods=['POST'])
def delete_product():
    """Delete a product."""
    product = Product.query.filter_by(id=request.form['product_id']).first_or_404()
    db.session.delete(product)
    db.session.commit()
    flash(f'Product {product.vendor.name} {product.sku} deleted.')
    return jsonify(status='ok')


########################################################################################################################
# Opportunities


@app.route('/opportunities')
@login_required
def opportunities():
    opps = Opportunity.query.all()
    return render_template('opportunities.html', title='Opportunities', opportunities=opps)