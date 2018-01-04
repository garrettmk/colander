from .common import *


########################################################################################################################


@celery.task(base=MWSTask, bind=True, cache_ttl=60*5)
class GetServiceStatus(MWSTask):
    pass


@celery.task(base=MWSTask, bind=True, cache_ttl=60*60*24)
class ListMatchingProducts(MWSTask):
    pass


@celery.task(base=MWSTask, bind=True, cache_ttl=60*30)
class GetMyFeesEstimate(MWSTask):
    pass


@celery.task(base=MWSTask, bind=True, cache_ttl=60*5)
class GetCompetitivePricingForASIN(MWSTask):
    pass
