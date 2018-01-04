import os
from app import app
import web.app.models


########################################################################################################################


class OpsTask(app.Task):
    """Provides common behaviours and resources for the ops group of tasks."""

    def __init__(self):
        """Initialize the task object."""
        self._vendor_id_cache = {}

    @property
    def db(self):
        """The MongoDB database."""
        if self._db is None:
            uri = os.environ['MONGODB_URI']
            db_name = uri.split('/')[-1]
            self._db = pymongo.MongoClient(uri)[db_name]

        return self._db

    def after_return(self, status, retval, task_id, args, kwargs, einfo):
        """Close the database connection when the task finishes."""
        if self._db is not None:
            self._db.client.close()
            self._db = None

    def get_or_create_vendor(self, name):
        """Return the ID of the vendor with a given name. If none exists, one will be created."""
        if name not in self._vendor_id_cache:
            vendors = self.db['vendors']
            self._vendor_id_cache[name] = vendors.find_one_and_update(
                filter={'name': name},
                update={'$set': {'name': name}},
                projection={'_id': 1},
                upsert=True,
                return_document=pymongo.ReturnDocument.AFTER
            )['_id']

        return self._vendor_id_cache[name]

