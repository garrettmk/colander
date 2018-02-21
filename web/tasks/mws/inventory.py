from .common import *


########################################################################################################################


@celery_app.task(base=MWSTask, bind=True, **MWSTask.options)
class ListInventorySupply(MWSTask):
    pass


@celery_app.task(base=MWSTask, bind=True, **MWSTask.options)
class ListInventorySupplyByNextToken(MWSTask):
    pass
