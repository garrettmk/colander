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
            broker=os.environ['REDIS_URL'],
            backend=os.environ['REDIS_URL']
        )

    def close_spider(self, spider):
        self.celery = None

    def process_item(self, item, spider):
        vendor = spider.human_name
        item_data = dict(item)
        item_data.update(vendor=vendor)

        self.celery.send_task(
            'ops.products.clean_and_import',
            kwargs={'data': item_data}
        )

        return item


