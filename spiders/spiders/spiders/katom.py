# -*- coding: utf-8 -*-
import re
import scrapy
import html2text

from scrapy.linkextractors import LinkExtractor
from scrapy.loader import ItemLoader
from scrapy.loader.processors import TakeFirst, Join

from ..items import ProductItem


class KatomProductLoader(ItemLoader):
    default_output_processor = TakeFirst()

    re_sku = re.compile(r'KaTom #: ([^\s]+)', re.IGNORECASE)
    re_model = re.compile(r'MPN: ([^\s]+)', re.IGNORECASE)
    re_price = re.compile(r'(\d+[\d,]*\.\d+)')
    re_quantity = re.compile(r'/ (.*)')
    re_desc = re.compile(r'\t+')

    title_out = Join()

    def sku_in(self, input):
        match = self.re_sku.search(input[0])
        return match.group(1)

    def model_in(self, input):
        match = self.re_model.search(input[0])
        return match.group(1)

    def price_in(self, input):
        match = self.re_price.search(input[0])
        return match.group(1) if match is not None else None

    def quantity_string_in(self, input):
        match = self.re_quantity.search(input[0])
        return match.group(1)

    def description_in(self, input):
        return html2text.html2text(input[0])


class KatomSpider(scrapy.Spider):
    name = "katom"
    human_name = "KaTom Restaurant Supply"
    vendor_url = 'www.katom.com'

    allowed_domains = [vendor_url]
    start_urls = ['https://' + vendor_url]

    def parse(self, response):
        return scrapy.FormRequest(url='https://www.katom.com/account/login',
                                  formdata={'email': 'prwlrspider@gmail.com',
                                            'password': 'foofoo17'},
                                  callback=self.after_login)

    def after_login(self, response):
        if b'Error' in response.body:
            self.logger.error('Login failed.')
        else:
            self.logger.info('Login successful.')

        return scrapy.Request('https://www.katom.com', callback=self.parse_category)

    def parse_category(self, response):
        """Drills down into subcategories. The deepest subcategories (the ones that show actual products) are
        parsed by parse_product_category()."""

        # If this is a bottom-level category, extract product listings
        if response.css('div.row.prodDesc'):
            category = response.meta.get('category', response.css('.breadcrumb .active::text').extract_first())
            base_rank = response.meta.get('rank', 1)

            # Extract and parse product listings
            le = LinkExtractor(allow=r'\d+-\w+\.html')
            for rank, link in enumerate(le.extract_links(response), base_rank):
                yield scrapy.Request(link.url,
                                     callback=self.parse_product,
                                     meta={'category': category, 'rank': rank})

            # Get the next page of results
            for a in response.css('a.next::attr(href)'):
                yield scrapy.Request(response.urljoin(a.extract()),
                                     callback=self.parse_category,
                                     meta={'category': category, 'rank': rank + 1})

        # Not a bottom-level category; drill down
        else:
            le = LinkExtractor(allow=r'/cat/', deny=(r'tel:', r'\?'))
            for link in le.extract_links(response):
                yield scrapy.Request(link.url, callback=self.parse_category)

    def parse_product(self, response):
        """Extract data from a product listing."""
        loader = KatomProductLoader(item=ProductItem(), response=response)

        loader.add_css('description', 'section#overview')
        loader.add_css('image_url', '.product-image a::attr(href)')

        loader.nested_css('div.product-info')
        loader.add_css('title', 'h1[itemprop="name"]::text')
        loader.add_css('sku', 'span.code::text')
        loader.add_css('model', 'span.code::text')
        loader.add_css('price', 'strong.price::text')
        loader.add_css('quantity_string', 'strong.price span::text')

        loader.nested_css('section#overview')
        loader.add_css('brand', 'span[itemprop="brand"]::text')

        loader.add_value('detail_page_url', response.url)
        loader.add_value('category', response.meta.get('category', None))
        loader.add_value('rank', response.meta.get('rank', 0))

        return loader.load_item()

