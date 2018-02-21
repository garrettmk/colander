from .common import *


########################################################################################################################


@celery_app.task(base=MWSTask, bind=True, cache_ttl=60 * 5, **MWSTask.options)
class GetServiceStatus(MWSTask):
    pass


@celery_app.task(base=MWSTask, bind=True, cache_ttl=60 * 60 * 24, **MWSTask.options)
class ListMatchingProducts(MWSTask):
    pass


@celery_app.task(base=MWSTask, bind=True, cache_ttl=60 * 30, **MWSTask.options)
class GetMyFeesEstimate(MWSTask):
    pass


@celery_app.task(base=MWSTask, bind=True, cache_ttl=60 * 5, **MWSTask.options)
class GetCompetitivePricingForASIN(MWSTask):
    pass
