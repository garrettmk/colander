from .common import *


########################################################################################################################


@app.task(base=MWSTask, bind=True, cache_ttl=60*5)
class GetServiceStatus(MWSTask):
    pass


@app.task(base=MWSTask, bind=True, cache_ttl=60*60*24)
class ListMatchingProducts(MWSTask):
    pass


@app.task(base=MWSTask, bind=True, cache_ttl=60*30)
class GetMyFeesEstimate(MWSTask):
    pass


@app.task(base=MWSTask, bind=True, cache_ttl=60*5)
class GetCompetitivePricingForASIN(MWSTask):
    pass
