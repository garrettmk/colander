from .common import *


########################################################################################################################


@app.task(base=OpsTask, bind=True)
def clean_and_import(self, data):
    """Cleans, validates, and imports product data from a spider."""

    # Verify that required fields are present and valid
    required_fields = ['vendor', 'sku']
    for field in required_fields:
        assert field in data

    # Do some basic cleaning
    for field, value in data.items():
        if isinstance(value, str):
            data[field] = value.strip()

    # Get or create vendor
    vendor_id = self.get_or_create_vendor(data.pop('vendor'))

    # Upsert into products collection
    products = self.db['products']
    products.find_one_and_update(
        filter={'vendor': vendor_id, 'sku': data['sku']},
        update={
            '$set': {
                'vendor': vendor_id,
                **data
            }
        },
        upsert=True
    )

    # Trigger follow-up actions:
    #   Find matching Amazon products