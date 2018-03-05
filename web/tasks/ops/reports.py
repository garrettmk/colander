from app import db
from app.models import AmzReport, AmzReportLineMixin

from tasks.parsed.reports import RequestReport, GetReportRequestList, GetReport

from .common import *


########################################################################################################################


@celery_app.task(bind=True, base=OpsTask, **OpsTask.options)
def update_reports(self, **args):
    """Submits new reports, updates information on pending reports, and downloads completed reports."""
    if args:
        reports = AmzReport.query.filter(AmzReport.id.in_(args)).all()
        if len(reports) != len(args):
            ids = (r.id for r in reports)
            raise ValueError(f"Invalid report id(s): {', '.join(str(i) for i in args if i not in ids)}")
    else:
        reports = AmzReport.query.filter_by(complete=False).all()

    submit = [r for r in reports if r.status is None]
    pending = [r for r in reports if r.status in ('_SUBMITTED_', '_IN_PROGRESS_')]

    # Submit new reports
    for report in submit:
        response = RequestReport(
            report.type,
            start=report.start_date,
            end=report.end_date,
            **report.options
        )

        if response['succeeded']:
            report.update(response['results'])
        else:
            report.status = '_CANCELLED_'

        db.session.commit()

    # Update pending reports
    if pending:
        response = GetReportRequestList([r.request_id for r in pending])
        if response['succeeded']:
            for result in response['results']:
                report = [r for r in pending if r.request_id == result['request_id']][0]
                report.update(result)

        db.session.commit()

    # Download completed reports
    download = [r for r in reports if r.status == '_DONE_']
    for report in download:
        report_data = GetReport(report.report_id, report.type)['results']
        try:
            line_type = [sc for sc in AmzReportLineMixin.__subclasses__() if sc.report_type == report.type][0]
        except IndexError:
            raise ValueError(f'No line item class exists for report type {report.type}')

        for line_data in report_data:
            line_item = line_type(report_id=report.id)
            line_item.update(**line_data)
            db.session.add(line_item)

        db.session.commit()

    # Mark reports as complete
    complete = [r for r in reports if r.status in ('_CANCELLED_', '_DONE_', '_DONE_NO_DATA_')]
    for report in complete:
        report.complete = True

    db.session.commit()


@celery_app.task(bind=True, base=OpsTask, **OpsTask.options)
def await_reports(self, *args, interval=60):
    """Call update_reports() and get_reports() at periodic intervals, retrying until all reports have been
    downloaded."""
    reports = AmzReport.query.filter(AmzReport.id.in_(args)).all()
    if len(reports) != len(args):
        ids = [r.id for r in reports]
        raise ValueError(f"Invalid report ids: {', '.join(str(i) for i in args if i not in ids)}")

    failed = [r for r in reports if r.status == '_CANCELLED_']
    if failed:
        raise ValueError(f'Reports failed: {failed}')

    pending = [r for r in reports if not r.complete]
    if pending:
        update_reports.delay()
        self.retry(countdown=interval)