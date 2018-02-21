from .common import *


########################################################################################################################


@celery_app.task(base=MWSTask, bind=True, cache_ttl=60 * 5, **MWSTask.options)
class ItemLookup(MWSTask):
    pass


@celery_app.task(base=MWSTask, bind=True, cache_ttl=60 * 60 * 24, **MWSTask.options)
class ItemSearch(MWSTask):
    pass


@celery_app.task(base=MWSTask, bind=True, cache_ttl=60 * 60 * 24, **MWSTask.options)
class BrowseNodeLookup(MWSTask):
    pass


@celery_app.task(base=MWSTask, bind=True, cache_ttl=60 * 60 * 24, **MWSTask.options)
class SimilarityLookup(MWSTask):
    pass

