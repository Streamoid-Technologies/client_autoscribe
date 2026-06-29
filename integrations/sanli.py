import os
import sys
import logging

logger = logging.getLogger(__name__)

import json
import pandas as pd
import requests
# from woocommerce import API
from requests.auth import HTTPBasicAuth

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))
from client_autoscribe.integrations.base_adapter import BaseAdapter
from client_autoscribe.client_autoscribe_db_v2 import ClientAutoscribeDB

class VendorAdapter(BaseAdapter):
    def __init__(self, vendor_config, brand_config):
        super(VendorAdapter, self).__init__(vendor_config, brand_config)

        self.consumer_key = "ck_14f3312c24b9af0940b1d1ebf76d45e68701e9bd"
        self.consumer_secret = "cs_dba7cac54b4858eab29294cb06ae6b1fb9197022"

        self.attributes = None
        self.categories = None

        self.db = ClientAutoscribeDB('localhost')

    def convert_input(self, data, **kwargs):
        filename = kwargs.get('filename', None)
        style_codes = []
        if filename is not None and filename.lower().endswith('.csv'):
            data = pd.read_csv(data, keep_default_na=False, dtype=str)
            for _, row in data.iterrows():
                out = dict(row)
                # logger.info(dict(row))
                out['ImageURLs'] = out['image_url']
                out['StyleCode'] = str(out['product_id'])
                out['RequestID'] = str(out['mpn'])
                # logger.info(out)
                style_codes.append(out)
        return style_codes

    def find_pid_for_sku(self, sku):
        auth = HTTPBasicAuth(self.consumer_key, self.consumer_secret)
        headers = {'Content-Type': 'application/json; charset=utf-8'}
        params = {'sku': sku}
        url = "https://brandinn.com/wp-json/wc/v3/products"
        r = requests.get(url, headers=headers, params=params, auth=auth, timeout=60)
        if r.ok:
            products = r.json()
            if len(products) > 0:
                return products[0]['id']

    def push_data(self, pid, data):
        auth = HTTPBasicAuth(self.consumer_key, self.consumer_secret)
        headers = {'Content-Type': 'application/json; charset=utf-8'}
        url = "https://brandinn.com/wp-json/wc/v3/products/%s" % pid
        logger.info(data)
        category = data.pop('Kategori', None)
        category_arr = []
        if category is not None:
            category_arr = [{'id': self.categories[category]}]
        attribute_arr = [{'id': self.attributes[k], 'options': v.replace(' ', '_')} for k, v in data.items()]
        data1 = json.dumps({'attributes': attribute_arr, 'categories': category_arr})
        logger.info(data1)
        r = requests.put(url, headers=headers, data=data1, auth=auth, timeout=60)
        if r.ok:
            logger.info(r.status_code)
            return r.json()
        else:
            logger.error('%s %s', r.status_code, r.text)

    def post_to_vendor(self, output, **kwargs):
        if self.attributes is None:
            self.attributes = self.get_attributes()
        if self.categories is None:
            self.categories = self.get_categories()

        vendor_name = self.vendor_config['_id']
        brand_name = self.brand_config['_id']
        request_id = output['RequestID']

        # https://woocommerce.github.io/woocommerce-rest-api-docs/wp-api-v2.html#update-a-product
        try:
            auth = HTTPBasicAuth(self.consumer_key, self.consumer_secret)
            headers = {'Content-Type': 'application/json; charset=utf-8'}
            data = output['brand_attributes']
            product_id = self.find_pid_for_sku(output['RequestID'])
            logger.info('%s => %s', output['RequestID'], product_id)
            if product_id is None:
                return False
            url = "https://brandinn.com/wp-json/wc/v3/products/%s" % product_id
            logger.info(data)
            r = requests.post(url, headers=headers, data=json.dumps(data), auth=auth, timeout=60)
            if r.ok:
                logging.info('%s %s', output, r.json())
                self.db.save_pushed_details(vendor_name, brand_name, style_code, request_id, r.text)
                return True
            else:
                res = f'{r.status_code} {r.text}'
                self.db.save_pushed_details(vendor_name, brand_name, style_code, requestid, res)

            logger.error('%s %s %s', output, r.status_code, r.text)
        except Exception as e:
            logger.exception(str(e))
            return False

    def get_attributes(self):
        auth = HTTPBasicAuth(self.consumer_key, self.consumer_secret)
        headers = {'Content-Type': 'application/json; charset=utf-8'}
        url = "https://brandinn.com/wp-json/wc/v3/products/attributes"
        r = requests.get(url, headers=headers, auth=auth, timeout=60)
        if r.ok:
            return {x['name']: x['id'] for x in r.json()}

    def get_categories(self):
        auth = HTTPBasicAuth(self.consumer_key, self.consumer_secret)
        headers = {'Content-Type': 'application/json; charset=utf-8'}
        url = "https://brandinn.com/wp-json/wc/v3/products/categories"
        r = requests.get(url, headers=headers, auth=auth, timeout=60)
        if r.ok:
            return {x['name']: x['id'] for x in r.json()}
