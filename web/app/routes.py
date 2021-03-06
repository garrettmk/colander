import ast
import datetime
import redis

from flask import request, flash, render_template, redirect, url_for, jsonify
from flask_login import current_user, login_user, logout_user, login_required

from app import app, db, celery_app
from app.forms import LoginForm, EditVendorForm, EditQuantityMapForm, EditProductForm, SearchProductsForm, EditJobForm,\
    SearchOpportunitiesForm, AddOpportunityForm, EditVendorOrderForm, EditVendorOrderItemForm, ReportForm
from app.models import User, Vendor, QuantityMap, Product, Opportunity, Job, ProductHistory, VendorOrder,\
    VendorOrderItem, Delivery, AmzReport, AmzReportLineMixin, FBAManageInventoryReportLine

from celery import chain, chord
from celery.result import AsyncResult
from redbeat import RedBeatScheduler

from tasks.ops.products import clean_and_import, update_amazon_listing, update_fba_fees, find_amazon_matches,\
    quantity_map_updated
from tasks.parsed.products import GetCompetitivePricingForASIN, GetMyFeesEstimate
from tasks.parsed.product_adv import ItemLookup
from tasks.jobs import dummy


@app.route('/test')
def test():
    return render_template('test.html')


########################################################################################################################
# Index & user login

@app.route('/')
@app.route('/index')
@login_required
def index():
    return render_template('index.html', title=f'Welcome, {current_user.username}')


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
# API endpoints


@app.route('/api/<path:task>')
def api_call(task):
    task = task.replace('tasks/', '') if task.startswith('tasks/') else task
    task_module, task_file, task_name = task.split('/')

    args = [arg for arg, val in request.args.items() if not len(val)]
    kwargs = {arg: val for arg, val in request.args.items() if arg not in args}

    job = celery_app.send_task(
        '.'.join(['tasks', task_module, task_file, task_name]),
        args=args,
        kwargs=kwargs,
        expires=30 if task_module in ['mws', 'parsed'] else None,
        priority=2
    )

    if task_module in ['mws', 'parsed']:
        try:
            return jsonify(job.get(timeout=30))
        except Exception as e:
            return repr(e)
    else:
        return jsonify(id=job.id, status=job.status)


@app.route('/api/results/<string:task_id>')
def api_results(task_id):
    task = AsyncResult(task_id, app=celery_app)
    return jsonify(
        id=task.id,
        status=task.status,
        result=repr(task.result) if isinstance(task.result, Exception) else task.result
    )


@app.route('/api/redis/info/<section>')
def redis_info(section):
    """Return info from the redis server."""
    r = redis.from_url(app.config['BROKER_URL'])
    return jsonify(
        r.info(section)
    )


@app.route('/api/<obj_type>/<int:obj_id>', methods=['POST'])
@app.route('/api/<obj_type>/<int:obj_id>/<attr>', methods=['GET'])
@login_required
def obj_attrs(obj_type, obj_id, attr=None):
    """Set or get attributes on an arbitrary object."""
    type_ = {
        t.__name__.lower(): t for t in db.Model.__subclasses__()
    }[obj_type.lower()]

    def failure(e):
        return jsonify(
            status='failure',
            message=str(e)
        )

    def success(**kwargs):
        return jsonify(
            status='success',
            results=kwargs
        )

    try:
        obj = type_.query.filter_by(id=obj_id).first_or_404()
    except KeyError:
        return failure(f'Invalid object type: {obj_type}')
    except Exception as e:
        return failure(e)

    if request.method == 'POST':
        data = request.get_json()
        for key, value in data.items():
            setattr(obj, key, value)
        db.session.commit()
        return success(**data)

    else:
        try:
            value = getattr(obj, attr)
        except (AttributeError,) as e:
            return failure(e)

        return success(**{attr: value})


@app.route('/api/<string:obj_type>/<int:obj_id>/delete', methods=['POST'])
@login_required
def obj_delete(obj_type, obj_id):
    """Delete an object from the database."""
    type_ = {
        t.__name__.lower(): t for t in db.Model.__subclasses__()
    }[obj_type.lower()]

    obj = type_.query.filter_by(id=obj_id).first_or_404()
    db.session.delete(obj)
    db.session.commit()
    return jsonify(status='ok')


########################################################################################################################
# Vendors


@app.route('/vendors')
@login_required
def vendors():
    """The top-level Vendor index."""
    product_count = Product.query.count()
    vendors = Vendor.query.order_by(Vendor.name.asc())
    return render_template(
        'vendors.html',
        title='Vendors',
        vendors=vendors.paginate(
            request.args.get('page', 1, type=int),
            app.config['MAX_PAGE_ITEMS'],
            False
        ),
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

    return render_template(
        'vendor_details.html',
        title=vendor.name,
        vendor=vendor,
        products=vendor.products.paginate(
            request.args.get('page', 1, type=int),
            app.config['MAX_PAGE_ITEMS'],
            False
        )
    )


########################################################################################################################
# Quantity mappings


@app.route('/quantitymaps', methods=['GET', 'POST'])
@login_required
def quantity_maps():
    """Render the top-level Quantity Maps index page."""
    qmaps = QuantityMap.query.order_by(QuantityMap.quantity.asc())

    return render_template(
        'quantity_maps.html',
        title='Quantity Maps',
        qmaps=qmaps.paginate(
            request.args.get('page', 1, type=int),
            app.config['MAX_PAGE_ITEMS'],
            False
        ),
        total_qmaps=QuantityMap.query.count()
    )


@app.route('/quantitymaps/create', methods=['GET', 'POST'])
@login_required
def new_quantity_map_form():
    """Renders the New Quantity Map form."""
    form = EditQuantityMapForm()
    if form.validate_on_submit():
        qmap = QuantityMap()
        form.populate_obj(qmap)
        db.session.add(qmap)
        db.session.commit()
        flash(f'Quantity map created (id={qmap.id})')
        return jsonify(status='ok')

    return render_template('forms/quantity_map.html', title='New Quantity Map', form=form)


@app.route('/quantitymaps/edit/<qmap_id>', methods=['GET', 'POST'])
@login_required
def edit_qmap_form(qmap_id):
    """Renders the Edit Quantity Map form."""
    qmap = QuantityMap.query.filter_by(id=qmap_id).first_or_404()
    form = EditQuantityMapForm(obj=qmap)
    if form.validate_on_submit():
        form.populate_obj(qmap)
        db.session.commit()
        flash(f'Changes saved')
        return jsonify(status='ok')

    return render_template('forms/quantity_map.html', title='Edit Quantity Map', form=form)


@app.route('/quantitymaps/delete', methods=['POST'])
def delete_quantity_map():
    """Deletes a quantity map."""
    qmap = QuantityMap.query.filter_by(id=request.form['qmap_id']).first_or_404()
    db.session.delete(qmap)
    db.session.commit()
    flash(f'Quantity map {qmap.text}={qmap.quantity} deleted.')
    return jsonify(status='ok')


########################################################################################################################
# Products


@app.route('/products')
@login_required
def products():
    """Render the top-level Products index page."""
    print(f'{request.args}')
    search_form = SearchProductsForm(request.args)
    search_form.tags.choices = [(tag, tag) for tag in request.args.getlist('tags')]

    products = Product.build_query(
        *[arg for arg, value in request.args.items() if value == ''],
        query=request.args.get('query'),
        tags=request.args.getlist('tags'),
        vendor_id=request.args.get('vendor_id', type=int)
    ).order_by(
        Product.title.asc()
    )

    return render_template(
        'products.html',
        title='Products',
        search_form=search_form,
        products=products.paginate(
            request.args.get('page', 1, type=int),
            app.config['MAX_PAGE_ITEMS'],
            False
        ),
        result_count=products.count(),
        total_products=Product.query.count()
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

        if product.vendor_id == Vendor.get_amazon().id:
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


@app.route('/products/<int:product_id>')
@login_required
def product_details(product_id):
    """Display a product's detail page."""
    page_num = request.args.get('page', 1, type=int)

    product = Product.query.filter_by(id=product_id).first_or_404()
    history = ProductHistory.query.filter_by(product_id=product_id).order_by(ProductHistory.timestamp).all()
    opps = Opportunity.query.filter(
        db.or_(
            Opportunity.market_id == product_id,
            Opportunity.supply_id == product_id
        ),
        Opportunity.hidden.is_(None)
    ).paginate(page_num, app.config['MAX_PAGE_ITEMS'], False)

    return render_template(
        'product_details.html',
        title=product.title,
        product=product,
        opps_page=opps,
        history=history
    )


@app.route('/products/<product_id>/edit', methods=['GET', 'POST'])
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


@app.route('/products/<product_id>/set', methods=['POST'])
def set_product_attr(product_id):
    """A generic endpoint for setting product properties."""
    product = Product.query.filter_by(id=product_id).first_or_404()

    if 'tags[]' in request.form:
        product.tags = request.form.getlist('tags[]')

    db.session.commit()
    return jsonify(status='ok')


@app.route('/products/delete', methods=['POST'])
def delete_product():
    """Delete a product."""
    product = Product.query.filter_by(id=request.form['product_id']).first_or_404()
    db.session.delete(product)
    db.session.commit()
    flash(f'Product {product.vendor.name} {product.sku} deleted.')
    return jsonify(status='ok')


@app.route('/products/tag', methods=['POST'])
def tag_products():
    action = request.form.get('action')
    tags = request.form.getlist('tags')
    ids = [int(i) for i in request.form.getlist('ids')]
    products = Product.query.filter(Product.id.in_(ids)).all()

    for product in products:
        product_tags = product.tags if product.tags is not None else []
        if action == 'add':
            product.tags = list(set(product_tags + tags))
        elif action == 'remove':
            product.tags = [tag for tag in product_tags if tag not in tags]
        else:
            db.session.rollback()
            return jsonify(status='error', message=f'Invalid action: {action}')

    db.session.commit()
    return jsonify(status='ok')


@app.route('/products/<int:product_id>/addopp', methods=['GET', 'POST'])
def add_opportunity_form(product_id):
    """Adds a supplier or market relationship to a product."""
    product = Product.query.filter_by(id=product_id).first_or_404()
    form = AddOpportunityForm()

    if form.validate_on_submit():
        add_product = Product.query.filter_by(vendor_id=form.vendor_id.data, sku=form.sku.data).first()
        as_supplier = form.supply_or_market.data

        if as_supplier:
            product.add_supplier(add_product)
        else:
            product.add_market(add_product)

        return jsonify(status='ok')

    return render_template('forms/add_opportunity.html', form=form)


@app.route('/products/<int:product_id>/history')
def history(product_id):
    """Return history data for a given product."""
    frame = request.args.get('frame', 'day')
    start = datetime.datetime.utcnow()

    if frame == 'day':
        start -= datetime.timedelta(days=1)
    elif frame == 'week':
        start -= datetime.timedelta(weeks=1)
    elif frame == 'month':
        start -= datetime.timedelta(days=31)
    else:
        raise ValueError(f'Invalid value for frame: {frame}')

    history = ProductHistory.query.filter(
        ProductHistory.product_id == product_id,
        ProductHistory.timestamp >= start
    ).all()

    return jsonify({
        'labels': [h.timestamp for h in history],
        'rank': [h.rank for h in history],
        'price': [float(h.price) for h in history]
    })


@app.route('/products/<int:product_id>/inventory')
def inventory(product_id):
    """Return inventory history data for a given product."""
    product = Product.query.filter_by(id=product_id).first_or_404()
    frame = request.args.get('frame', 'day')
    start = datetime.datetime.utcnow()

    if frame == 'day':
        start -= datetime.timedelta(days=1)
    elif frame == 'week':
        start -= datetime.timedelta(weeks=1)
    elif frame == 'month':
        start -= datetime.timedelta(days=31)
    else:
        raise ValueError(f'Invalid value for frame: {frame}')

    history = db.session.query(
        AmzReport.end_date,
        FBAManageInventoryReportLine.afn_fulfillable_quantity,
        FBAManageInventoryReportLine.afn_reserved_quantity,
        FBAManageInventoryReportLine.afn_unsellable_quantity,
        FBAManageInventoryReportLine.afn_inbound_shipped_quantity,
        FBAManageInventoryReportLine.afn_inbound_receiving_quantity,
        FBAManageInventoryReportLine.afn_inbound_working_quantity,
        FBAManageInventoryReportLine.your_price
    ).filter(
        FBAManageInventoryReportLine.asin == product.sku,
        AmzReport.id == FBAManageInventoryReportLine.report_id,
        AmzReport.end_date >= start
    ).order_by(
        AmzReport.end_date.asc()
    ).all()

    return jsonify({
        'labels': [h[0] for h in history],
        'fulfillable': [h[1] for h in history],
        'unsellable': [h[2] for h in history],
        'reserved': [h[3] for h in history],
        'inbound': [h[4] for h in history],
        'receiving': [h[5] for h in history],
        'working': [h[6] for h in history],
        'price': [float(h[7]) for h in history]
    })


########################################################################################################################
# Opportunities


@app.route('/opportunities')
@login_required
def opportunities():
    scale = lambda f: f/100 if f else f
    form = SearchOpportunitiesForm(request.args)
    form.tags.choices = [(tag, tag) for tag in request.args.getlist('tags')]
    opps = Opportunity.build_query(
        query=request.args.get('query'),
        tags=request.args.getlist('tags'),
        max_cogs=request.args.get('max_cogs', type=float),
        min_profit=request.args.get('min_profit', type=float),
        min_roi=scale(request.args.get('min_roi', type=float)),
        min_similarity=scale(request.args.get('min_similarity', type=float)),
        min_rank=request.args.get('min_rank', type=int),
        max_rank=request.args.get('max_rank', type=int),
        sort_by=request.args.get('sort_by'),
        sort_order=request.args.get('sort_order'),
    ).paginate(per_page=app.config['MAX_PAGE_ITEMS'])

    return render_template(
        'opportunities.html',
        title='Opportunities',
        opps=opps,
        form=form,
        total_opps=Opportunity.query.count()
    )


@app.route('/opportunities/delete', methods=['POST'])
@login_required
def delete_opps():
    ids = request.form.getlist('ids[]')
    Opportunity.query.filter(Opportunity.id.in_(ids)).delete(synchronize_session=False)
    db.session.commit()
    return jsonify(status='ok')


########################################################################################################################
# Jobs


@app.route('/jobs')
@login_required
def jobs():
    """The top-level Jobs index."""
    jobs = Job.query.paginate(per_page=app.config['MAX_PAGE_ITEMS'])
    return render_template(
        'jobs.html',
        title='Jobs',
        jobs=jobs,
        now=datetime.datetime.utcnow()
    )


@app.route('/jobs/create', methods=['GET', 'POST'])
@login_required
def new_job_form():
    """Render the New Job form."""
    form = EditJobForm()
    if form.validate_on_submit():
        job = Job(
            name=form.name.data,
            schedule_type=form.schedule_type.data,
            schedule_kwargs=ast.literal_eval(form.schedule_kwargs.data),
            enabled=form.enabled.data,
            task_type=form.task.data,
            task_params=ast.literal_eval(form.task_params.data)
        )

        db.session.add(job)
        db.session.commit()
        flash(f'New job \'{job.name}\' created.')
        return jsonify(status='ok')

    return render_template('forms/job.html', title='New Job', form=form)


@app.route('/jobs/<job_id>')
@login_required
def job_details(job_id):
    job = Job.query.filter_by(id=job_id).first_or_404()

    return render_template(
        'job_details.html',
        job=job
    )


@app.route('/jobs/edit/<job_id>', methods=['GET', 'POST'])
@login_required
def edit_job_form(job_id):
    job = Job.query.filter_by(id=job_id).first_or_404()
    form = EditJobForm()

    if request.method == 'POST' and form.validate_on_submit():
        job.name = form.name.data
        job.schedule_type = form.schedule_type.data
        job.schedule_kwargs = ast.literal_eval(form.schedule_kwargs.data)
        job.task_type = form.task.data
        job.task_params = ast.literal_eval(form.task_params.data)
        job.enabled = form.enabled.data
        db.session.commit()
        flash(f'Changes saved')
        return jsonify(status='ok')
    elif request.method == 'GET':
        form.name.data = job.name
        form.schedule_type.data = job.schedule_type
        form.schedule_kwargs.data = job.schedule_kwargs
        form.task.data = job.task_type
        form.task_params.data = job.task_params
        form.enabled.data = job.enabled

    return render_template('forms/job.html', title='Edit Job', form=form)


@app.route('/jobs/delete', methods=['POST'])
@login_required
def delete_job():
    job = Job.query.filter_by(id=request.form['job_id']).first_or_404()
    db.session.delete(job)
    db.session.commit()
    flash(f'Job deleted.')
    return jsonify(status='ok')


@app.route('/jobs/control', methods=['POST'])
@login_required
def control_jobs():
    action = request.form['action']

    if action == 'start':
        jobs = Job.query.all()
        for job in jobs:
            job.create_scheduler_entry()
            job.entry.save()

    elif action == 'stop':
        jobs = Job.query.all()
        for job in jobs:
            try:
                job.entry.delete()
            except AttributeError:
                pass

    return jsonify(status='ok')


@app.route('/jobs/schedule')
@login_required
def jobs_schedule():
    scheduler = RedBeatScheduler(app=celery_app)
    return render_template(
        'jobs_schedule.html',
        schedule=scheduler.schedule.values(),
        now=datetime.datetime.utcnow()
    )


########################################################################################################################
# Vendor orders


@app.route('/vendororders')
@login_required
def vendor_orders():
    orders = VendorOrder.query.paginate(per_page=app.config['MAX_PAGE_ITEMS'])
    return render_template(
        'vendor_orders.html',
        title='Vendor Orders',
        orders=orders
    )


@app.route('/vendororders/<int:order_id>')
@login_required
def vendor_order_details(order_id):
    order = VendorOrder.query.filter_by(id=order_id).first_or_404()

    return render_template(
        'vendor_order_details.html',
        title='Order Details',
        order=order
    )


@app.route('/vendororders/create', methods=['GET', 'POST'])
@login_required
def new_vendor_order_form():
    form = EditVendorOrderForm()
    if form.validate_on_submit():
        order = VendorOrder()
        form.populate_obj(order)
        db.session.add(order)
        db.session.commit()
        flash(f'Vendor order {order.order_number} created.')
        return jsonify(status='ok')

    return render_template('forms/vendororder.html', form=form)


@app.route('/vendororders/<int:order_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_vendor_order_form(order_id):
    order = VendorOrder.query.filter_by(id=order_id).first_or_404()
    form = EditVendorOrderForm(obj=order)

    if form.validate_on_submit():
        form.populate_obj(order)
        db.session.commit()
        return jsonify(status='ok')

    return render_template(
        'forms/vendororder.html',
        form=form
    )


@app.route('/vendororders/<int:order_id>/additem', methods=['GET', 'POST'])
@login_required
def new_vendor_order_item_form(order_id):
    order = VendorOrder.query.filter_by(id=order_id).first_or_404()
    form = EditVendorOrderItemForm(order=order)

    if form.validate_on_submit():
        product_id = Product.query.filter_by(vendor_id=order.vendor_id, sku=form.sku.data).first().id
        item = VendorOrderItem(
            order_id=order.id,
            product_id=product_id,
            quantity=form.quantity.data,
            price_each=form.price_each.data,
        )
        db.session.add(item)
        db.session.commit()
        return jsonify(status='ok')

    form.vendor_id.data = order.vendor_id
    return render_template(
        'forms/vendor_order_item_form.html',
        form=form
    )


@app.route('/vendororders/items/<int:item_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_vendor_order_item_form(item_id):
    item = VendorOrderItem.query.filter_by(id=item_id).first_or_404()
    form = EditVendorOrderItemForm(obj=item)

    if form.validate_on_submit():
        form.populate_obj(item)
        db.session.commit()
        return jsonify(status='ok')

    form.vendor_id.data = item.order.vendor_id
    form.sku.data = item.product.sku

    return render_template(
        'forms/vendor_order_item_form.html',
        form=form
    )


########################################################################################################################
# Reports


@app.route('/reports')
@login_required
def reports():
    page_num = request.args.get('page', 1, type=int)

    reports = AmzReport.query.order_by(
        AmzReport.id.desc()
    ).paginate(
        page_num,
        app.config['MAX_PAGE_ITEMS'],
        False
    )

    return render_template(
        'reports.html',
        title='Reports',
        reports=reports
    )


@app.route('/reports/<int:report_id>')
@login_required
def report_details(report_id):
    report = AmzReport.query.filter_by(id=report_id).first_or_404()

    return render_template(
        'report_details.html',
        title='Report Details',
        report=report
    )


@app.route('/reports/create', methods=['GET', 'POST'])
@login_required
def new_report_form():
    form = ReportForm()
    if form.validate_on_submit():
        report = AmzReport()
        form.populate_obj(report)
        db.session.add(report)
        db.session.commit()
        return jsonify(status='ok')

    return render_template(
        'forms/report.html',
        form=form
    )
