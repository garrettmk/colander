from .common import *


########################################################################################################################


@celery.task(base=MWSTask, bind=True, cache_ttl=60*5)
class ItemLookup(MWSTask):
    pass


@celery.task(base=MWSTask, bind=True, cache_ttl=60*60*24)
class ItemSearch(MWSTask):
    pass


@celery.task(base=MWSTask, bind=True, cache_ttl=60*60*24)
class BrowseNodeLookup(MWSTask):
    pass


@celery.task(base=MWSTask, bind=True, cache_ttl=60*60*24)
class SimilarityLookup(MWSTask):
    pass

