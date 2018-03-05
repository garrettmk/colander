# -*- coding: utf-8 -*-

# Define your item pipelines here
#
# Don't forget to add your pipeline to the ITEM_PIPELINES setting
# See: http://doc.scrapy.org/en/latest/topics/item-pipeline.html
import os
from celery import Celery


class ColanderPipeline:

    def __init__(self):
        self.celery = None

    def open_spider(self, spider):
        self.celery = Celery(
            'main',
            broker=os.environ['CELERY_REDIS_URL'],
            backend=os.environ['CELERY_REDIS_URL']
        )

    def close_spider(self, spider):
        self.celery = None

    def process_item(self, item, spider):
        item_data = {k: v for k, v in dict(item).items() if v is not None}

        self.celery.send_task(
            'tasks.ops.products.clean_and_import',
            kwargs={'data': item_data}
        )

        return item


