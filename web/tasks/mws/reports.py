from .common import *


########################################################################################################################


@celery_app.task(base=MWSTask, bind=True, **MWSTask.options)
class RequestReport(MWSTask):
    pass


@celery_app.task(base=MWSTask, bind=True, **MWSTask.options)
class GetReportRequestList(MWSTask):
    pass


@celery_app.task(base=MWSTask, bind=True, **MWSTask.options)
class GetReportRequestListByNextToken(MWSTask):
    pass


@celery_app.task(base=MWSTask, bind=True, **MWSTask.options)
class GetReportRequestCount(MWSTask):
    pass


@celery_app.task(base=MWSTask, bind=True, **MWSTask.options)
class CancelReportRequests(MWSTask):
    pass


@celery_app.task(base=MWSTask, bind=True, **MWSTask.options)
class GetReportList(MWSTask):
    pass


@celery_app.task(base=MWSTask, bind=True, **MWSTask.options)
class GetReportListByNextToken(MWSTask):
    pass


@celery_app.task(base=MWSTask, bind=True, **MWSTask.options)
class GetReportCount(MWSTask):
    pass


@celery_app.task(base=MWSTask, bind=True, **MWSTask.options, cache_ttl=60*30)
class GetReport(MWSTask):
    pass


@celery_app.task(base=MWSTask, bind=True, **MWSTask.options)
class ManageReportSchedule(MWSTask):
    pass


@celery_app.task(base=MWSTask, bind=True, **MWSTask.options)
class GetReportScheduleList(MWSTask):
    pass


@celery_app.task(base=MWSTask, bind=True, **MWSTask.options)
class GetReportScheduleListByNextToken(MWSTask):
    pass


@celery_app.task(base=MWSTask, bind=True, **MWSTask.options)
class GetReportScheduleCount(MWSTask):
    pass


@celery_app.task(base=MWSTask, bind=True, **MWSTask.options)
class UpdateReportAcknowledgements(MWSTask):
    pass

