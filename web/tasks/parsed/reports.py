import io
import csv
from datetime import datetime

from .common import *
import tasks.mws.reports as mws


########################################################################################################################


@celery_app.task(bind=True)
def RequestReport(self, report_type, start=None, end=None, **kwargs):
    """Request a report."""
    kwargs.pop('priority', None)
    iso_8601 = '%Y-%m-%dT%H:%M:%SZ'
    start = start or kwargs.pop('StartDate', None)
    end = end or kwargs.pop('EndDate', None)

    params = {k: v for k, v in {
        'ReportType': report_type or kwargs.pop('ReportType'),
        'StartDate': start.strftime(iso_8601) if start else None,
        'EndDate': end.strftime(iso_8601) if end else None,
        **kwargs
    }.items() if v is not None}

    response = AmzXmlResponse(
        mws.RequestReport(**params, priority=self.get_priority())
    )

    if response.error_code:
        return format_parsed_response('RequestReport', params, errors=response.error_as_json())

    iso_8601 = '%Y-%m-%dT%H:%M:%S'

    result = {
        'type': response.xpath_get('//ReportType'),
        'status': response.xpath_get('//ReportProcessingStatus'),
        'start_date': datetime.strptime(
            response.xpath_get('//StartDate').split('+')[0],
            iso_8601
        ),
        'end_date': datetime.strptime(
            response.xpath_get('//EndDate').split('+')[0],
            iso_8601
        ),
        'request_id': response.xpath_get('//ReportRequestId'),
    }

    return format_parsed_response('RequestReport', params, result)


@celery_app.task(bind=True)
def GetReportRequestList(self, request_ids=None, **kwargs):
    """Get a list of reports and their statuses."""
    kwargs.pop('priority', None)

    params = {
        k: v for k, v in {
            'ReportRequestIdList': request_ids or kwargs.pop('ReportRequestIdList', None),
            **kwargs
        }.items() if v is not None
    }

    response = AmzXmlResponse(
        mws.GetReportRequestList(**params)
    )

    if response.error_code:
        return format_parsed_response('GetReportRequestList', params, errors=response.error_as_json())

    iso_8601 = '%Y-%m-%dT%H:%M:%S'
    results = []
    while response is not None:
        for tag in response.tree.iterdescendants('ReportRequestInfo'):
            info = {
                'request_id':   response.xpath_get('.//ReportRequestId', tag),
                'type':         response.xpath_get('.//ReportType', tag),
                'status':       response.xpath_get('.//ReportProcessingStatus', tag),
                'report_id':    response.xpath_get('.//GeneratedReportId', tag),

                'start_date':   datetime.strptime(
                    response.xpath_get('.//StartDate', tag).split('+')[0],
                    iso_8601
                ),
                'end_date':     datetime.strptime(
                    response.xpath_get('.//EndDate', tag).split('+')[0],
                    iso_8601
                )
            }

            results.append(info)

            has_next, next_token = response.xpath_get('.//HasNext'), response.xpath_get('.//NextToken')
            if has_next == 'true':
                response = AmzXmlResponse(
                    mws.GetReportRequestListByNextToken(NextToken=next_token)
                )
            else:
                response = None

    return format_parsed_response('GetReportRequestList', params, results)


@celery_app.task(bind=True)
def GetReport(self, report_id, report_type, **kwargs):
    """Get and parse a report."""
    kwargs.pop('priority', None)

    params = {
        k: v for k, v in {
            'ReportId': report_id or kwargs.pop('ReportId'),
            **kwargs
        }.items() if v is not None
    }

    report = mws.GetReport(**params)

    def type_cast(key, value):
        if 'price' in key:
            return float(value) if value else None
        elif 'quantity' in key:
            return int(value) if value else None
        elif 'exists' in key:
            if value.lower() == 'yes':
                return True
            elif value.lower() == 'no':
                return False
            else:
                return bool(value) if value else None
        elif 'volume' in key:
            return float(value) if value else None
        else:
            return value

    if report_type == '_GET_FBA_MYI_UNSUPPRESSED_INVENTORY_DATA_':
        buffer = io.StringIO(report)
        tsv = csv.DictReader(buffer, delimiter='\t')
        results = [
            {
                k.replace('-', '_'): type_cast(k, v)
                for k, v in d.items()
            } for d in tsv
        ]
    else:
        raise ValueError(f'Unsupported report type: {report_type}')

    return format_parsed_response('GetReport', params, results)