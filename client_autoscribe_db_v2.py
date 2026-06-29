import logging
import os
import random
import sys

import pandas as pd

logger = logging.getLogger(__name__)

import json
import re
import requests

from uuid import uuid4
from pymongo import MongoClient, DESCENDING
from copy import copy
from datetime import datetime
from importlib import import_module

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
# from client_autoscribe.ontology_db import OntologyDB, escape, unescape
from client_autoscribe.client_autoscribe_config import rpa_dir
from client_autoscribe.custom_rules_v2 import CustomRulesDB, get_db_name
from client_autoscribe.teams_integration import generate_curated_card, post_to_teams, generate_curated_card_catalogix
from client_autoscribe.rpa_database import RPADatabase
from client_autoscribe.client_autoscribe_to_catalogix import get_store_uuid, get_brands_sc, get_style_code_data
from client_autoscribe.slack_config import get_slack_token
import pytz

def _escape(data, mapping):
    if isinstance(data, str):
        for k, v in mapping.items():
            data = data.replace(k, v)
        return data
    elif isinstance(data, dict):
        return {_escape(k, mapping): _escape(v, mapping) for k, v in data.items()}
    elif isinstance(data, list):
        return [_escape(x, mapping) for x in data]
    elif isinstance(data, bool):
        return data
    elif isinstance(data, float):
        return data
    elif isinstance(data, int):
        return data
    elif data is None:
        return data

    logger.warning('Unknown type in escape: %s [%s]', data, type(data))
    return data


def escape(data):
    return _escape(data, mapping={'.': '<dot>'})


def unescape(data):
    return _escape(data, mapping={'<dot>': '.'})


def tags_to_dict(tags):
    output1 = {}
    for tag in tags:
        words = tag.split(':', 1)
        if words[0] not in output1:
            output1[words[0]] = words[1]
    return output1


def post_to_slack(vendor_db, request_id, view_url):
    slack_api_token = get_slack_token('api_token', 'SLACK_API_TOKEN')
    if not slack_api_token:
        logger.warning('SLACK_API_TOKEN is not configured; skipping Slack post')
        return

    url = 'https://slack.com/api/chat.postMessage'
    headers = {'Authorization': 'Bearer %s' % slack_api_token,
               'Content-type': 'application/json; charset=UTF-8'}
    text = "*Data posted to ABFRL :*\n" + str(view_url) + "\n*request_id*:`" + str(
        request_id) + "`\n*Number of products*:`1`"
    data = {'username': vendor_db, 'channel': 'CN001732L', 'text': text}
    logger.info(json.dumps(data))
    response = requests.post(url, data=json.dumps(data), headers=headers, timeout=60)
    if response.ok:
        logger.info(response.text)
    else:
        logger.info(response.status_code)


class ClientAutoscribeDB(object):
    # products -> live -> cataloging -> [export] -> [import] -> rejects
    def __init__(self, mongo_host):
        
        self.marketplaces_url = 'https://marketplaces-staging.service.streamoid.com'
        
        self.client = MongoClient(mongo_host)
        # self.onto_db = OntologyDB(mongo_host)
        self.custom_rules_db = CustomRulesDB(self.client)

        # common db to store vendor-specific config
        self.common_db = 'client_autoscribe_common'
        self.vendors_coll = 'vendors'

        # vendor-specific databases
        self.brands_coll = 'brands'

        self.products_coll_pattern = 'products:%s'
        self.live_coll_pattern = 'live:%s'
        self.cataloging_coll_pattern = 'cataloging:%s'
        # already translated product data from Catalogix
        self.catalogix_coll_pattern = 'catalogix:%s'
        self.rejects_coll_pattern = 'rejects:%s'
        self.rpa_coll_pattern = 'rpa:%s'

        # load teams urls
        this_dir = os.path.dirname(os.path.join(__file__))
        teams_file = os.path.join(this_dir, 'teams_urls.json')
        if os.path.exists(teams_file):
            with open(teams_file) as f:
                self.teams_urls = json.load(f)
        else:
            logger.warning('teams_urls.json not found; Teams notifications are disabled')
            self.teams_urls = {}

        self.ontology_host = 'https://cataloging.streamoid.com'
        self.rpa_db = RPADatabase()

 
    # vendors
    def list_vendors(self):
        coll = self.client[self.common_db][self.vendors_coll]
        return [row['_id'] for row in coll.find()]

    def get_vendor_config(self, vendor_name):
        coll = self.client[self.common_db][self.vendors_coll]
        for row in coll.find({'_id': vendor_name}):
            return row

    def set_vendor_config(self, vendor_name, data):
        # {'curation_ontology': ... }
        coll = self.client[self.common_db][self.vendors_coll]
        coll.update_one({'_id': vendor_name}, {'$set': data}, upsert=True)

    # brands
    def list_brands(self, vendor_name):
        vendor_db = get_db_name(vendor_name)
        coll = self.client[vendor_db][self.brands_coll]
        return [row['_id'] for row in coll.find()]

    def get_brand_tokens(self, vendor_name):
        vendor_db = get_db_name(vendor_name)
        coll = self.client[vendor_db][self.brands_coll]
        return {row['_id']: row.get('token', None) for row in coll.find()}

    def get_brand_config(self, vendor_name, brand_name):
        vendor_db = get_db_name(vendor_name)
        coll = self.client[vendor_db][self.brands_coll]
        for row in coll.find({'_id': brand_name}):
            return row

    def set_brand_config(self, vendor_name, brand_name, config):
        # {'token': token,
        #  'brand_ontology': ontology,
        #  'targets': { ... },
        #  'custom_rules': True/False,
        # }
        # <custom rules storage>
        vendor_db = get_db_name(vendor_name)
        coll = self.client[vendor_db][self.brands_coll]
        coll.update_one({'_id': brand_name}, {'$set': config}, upsert=True)

    # products
    def get_keys(self, vendor_name, brand_name, coll_pattern,
             condition=None, only_count=False, fields=None):

        vendor_db = get_db_name(vendor_name)
        coll_name = coll_pattern % brand_name
        coll = self.client[vendor_db][coll_name]
        
        condition = {} if condition is None else condition

        projection = {'_id': True}
        if fields is not None:
            for field in fields:
                projection[field] = True

        if only_count:
            return coll.count_documents(condition)
        else:
            cursor = coll.find(condition, projection)
            if fields is not None:
                return list(cursor)
            else:
                return [x['_id'] for x in cursor]

    def get_product(self, vendor_name, brand_name, style_code):
        vendor_db = get_db_name(vendor_name)
        coll = self.products_coll_pattern % brand_name
        products = self.client[vendor_db][coll]
        # logger.info(escape(style_code))
        for row in products.find({'_id': escape(style_code)}):
            return unescape(row)
    
    def get_multiple_product(self, vendor_name, brand_name, style_codes):
        vendor_db = get_db_name(vendor_name)
        coll = self.products_coll_pattern % brand_name
        products = self.client[vendor_db][coll]
        # logger.info(escape(style_code))
        result_list = []
        for style_code in style_codes.split(','):
            for row in products.find({'_id': escape(style_code)}):
                result_list.append(unescape(row))
        return result_list

    def get_new_products(self, vendor_name, brand_name, request_id=None, only_count=False):
        condition = {'batch_number': {'$exists': False}}
        if request_id is not None:
            condition['RequestID'] = request_id
        return self.get_keys(vendor_name, brand_name, self.products_coll_pattern,
                             condition, only_count)

    def get_new_products_not_pushed_catalogix(self, vendor_name, brand_name, request_id=None, only_count=False):
        condition = {
                    "$and": [
                        {"batch_number": {"$exists": False}},
                        {"last_modified": {"$gte": datetime(2024, 8, 21, 11, 00, 0).strftime("%Y-%m-%d %H:%M:%S")}},
                        {
                            "$or": [
                                {"push_catalogix": {"$exists": False}},
                                {"push_catalogix": False}
                            ]
                        }
                    ]
                }

        logger.info(condition)

        if request_id is not None:
            condition['RequestID'] = request_id
        return self.get_keys(vendor_name, brand_name, self.products_coll_pattern,
                             condition, only_count)

    def set_pushed_to_catalogix(self, vendor_name, brand_name, style_codes):
        vendor_db = get_db_name(vendor_name)
        coll_name = self.products_coll_pattern % brand_name
        coll = self.client[vendor_db][coll_name]

        
        result = coll.update_many(
            {"_id": {"$in": style_codes}},
            {"$set": {"push_catalogix": True}}
        )
        
        return result.matched_count

        

    def get_only_new_products(self, vendor_name, brand_name, request_id=None, only_count=False):
        condition = {'re-curate': False, 'batch_number': {'$exists': False}}
        if request_id is not None:
            condition['RequestID'] = request_id
        return self.get_keys(vendor_name, brand_name, self.products_coll_pattern,
                             condition, only_count)

    def get_recurated_products(self, vendor_name, brand_name, request_id=None, only_count=False):
        condition = {'re-curate': True, 'batch_number': {'$exists': False}}
        if request_id is not None:
            condition['RequestID'] = request_id
        return self.get_keys(vendor_name, brand_name, self.products_coll_pattern,
                             condition, only_count)

    def get_all_products(self, vendor_name, brand_name, collection_name='products', condition=None,
                         only_count=False):  # NEW
        if collection_name == 'cataloging':
            return self.get_keys(vendor_name, brand_name, self.cataloging_coll_pattern,
                                 condition, only_count)
        return self.get_keys(vendor_name, brand_name, self.products_coll_pattern,
                             condition, only_count)

    def get_products_to_recurate(self, vendor_name, brand_name, request_id=None, only_count=False):
        condition = {'re-curate': {'$eq': True}}
        if request_id is not None:
            condition['RequestID'] = request_id
        return self.get_keys(vendor_name, brand_name, self.products_coll_pattern,
                             condition, only_count)

    def get_new_products_precomputed(self, vendor_name, brand_name, only_count=False):
        new_product_keys = self.get_new_products(vendor_name, brand_name)
        condition = {'_id': {'$in': new_product_keys}}
        return self.get_keys(vendor_name, brand_name,
                             self.live_coll_pattern, condition, only_count)

    def get_products_for_review_v2(self, vendor_name, brand_name,
                                   request_id=None, only_count=False):
        condition = {'reviewed': False, 'output': {'$exists': True}}
        if request_id is not None:
            condition['output.RequestID'] = request_id
            condition.pop('output', None)
        keys1 = self.get_keys(vendor_name, brand_name,
                              self.cataloging_coll_pattern, condition, only_count=False)
        keys2 = self.get_keys(vendor_name, brand_name,
                              self.products_coll_pattern, {}, only_count=False)
        keys3 = list(set(keys1) & set(keys2))
        return len(keys3) if only_count else keys3

        # condition = {'_id': {'$in': ready}}
        # return self.get_keys(vendor_name, brand_name,
        #                      self.live_coll_pattern, condition, only_count)

    def get_product_for_review(self, vendor_name, brand_name, style_code):
        product = self.get_product(vendor_name, brand_name, style_code)
        data = self.get_data(vendor_name, brand_name, style_code)
        if product is None or data is None:
            logger.warning('Product %s is null', style_code)
            return
        data['info'] = {k: v for k, v in product.items()
                        if isinstance(v, str) and '://' not in v}
        data['output'] = unescape(data['output']) if 'output' in data else {}
        return data

    def get_products_for_review(self, vendor_name, brand_name):
        vendor_db = get_db_name(vendor_name)
        products_coll = self.products_coll_pattern % brand_name
        cataloging_coll = self.cataloging_coll_pattern % brand_name
        coll = self.client[vendor_db][products_coll]
        coll2 = self.client[vendor_db][cataloging_coll]
        data = []
        for row in coll2.find({'reviewed': False}):
            style_code = row['_id']
            row['info'] = {}
            for row1 in coll.find({'_id': style_code}):
                row['info'] = {k: v for k, v in unescape(row1).items() if isinstance(v, str) and '://' not in v}
            if 'output' in row:
                row['output'] = unescape(row['output'])
                data.append(row)
        return data

    def get_requests(self, vendor_name, brand_name, search=None, from_ts=None, to_ts=None):
        vendor_db = get_db_name(vendor_name)
        coll_name = self.products_coll_pattern % brand_name
        coll = self.client[vendor_db][coll_name]
        condition = {}
        if search is not None:
            condition['RequestID'] = {'$regex': search}
        last_modified = {}
        if from_ts is not None:
            last_modified['$gte'] = from_ts
        if to_ts is not None:
            last_modified['$lte'] = to_ts
        if len(last_modified) > 0:
            condition['last_modified'] = last_modified
        request_dict = {}
        for row in coll.find(condition, {'RequestID': True, 'last_modified': True}):
            style_code = unescape(row['_id'])
            request_id = row['RequestID']
            last_modified = row['last_modified']
            if request_id not in request_dict:
                request_dict[request_id] = {'last_modified': last_modified, 'products': []}

            r = request_dict[request_id]
            if last_modified > r['last_modified']:
                r['last_modified'] = last_modified
            r['products'].append(style_code)

        # reverse sort
        request_list = []
        for k, v in request_dict.items():
            v['RequestID'] = k
            request_list.append(v)
        request_list.sort(key=lambda x: x['last_modified'], reverse=True)
        return request_list

    def get_distinct_requests(self, vendor_name, brand_name, only_count=False):
        vendor_db = get_db_name(vendor_name)
        coll_name = self.products_coll_pattern % brand_name
        coll = self.client[vendor_db][coll_name]
        rows = coll.distinct('RequestID')
        dates = [x for x in coll.find({}, {'last_modified': True}).sort('last_modified', DESCENDING).limit(1)]
        ts = dates[0]['last_modified'] if len(dates) > 0 else None
        # rows = coll.aggregate([{'$group': {'_id': '$RequestID', 'date': {'$max': '$last_modified'}}}])
        if only_count:
            return len(rows), ts
        return rows

    def get_product_status(self, vendor_name, brand_name, style_code):
        # states = ['ready', 'curated', 're-curated', 'reviewed', 'pushed', 'failed']
        data = self.get_data(vendor_name, brand_name, style_code)
        status = 'ready'
        if data is not None:
            if data['pushed'] and data.get('push_time', None):
                status = 'pushed'
            elif data['reviewed']:
                status = 'reviewed'
            else:
                product = self.get_product(vendor_name, brand_name, style_code)
                recurate = product.get('re-curate', False)
                status = 're-curated' if recurate else 'curated'
        return status

    def get_product_status_catalogix(self, vendor_name, brand_name, style_code):
        # states = ['ready', 'curated', 're-curated', 'reviewed', 'pushed', 'failed']
        data = self.get_catalogix_data(vendor_name, brand_name, style_code)
        status = 'received'
        if data is not None:
            if data.get('pushed', None) and data.get('push_time', None):
                status = 'pushed'
            elif data.get('reviewed', None):
                status = 'reviewed'
            elif data.get('hold', None):
                status = 'on hold'
            elif data.get('problem', None):
                status = 'problem'
            else:
                product = self.get_product(vendor_name, brand_name, style_code)
                recurate = product.get('re-curate', False)
                status = 're-curated' if recurate else 'curated'
        return status
    
    def convert_utc_to_ist(self, utc_string):
    # Convert string to datetime object
        utc_datetime = datetime.strptime(utc_string, '%Y-%m-%d %H:%M:%S')

        # Set timezone to UTC
        utc_datetime = pytz.utc.localize(utc_datetime)

        # Convert to IST
        ist_timezone = pytz.timezone('Asia/Kolkata')
        ist_datetime = utc_datetime.astimezone(ist_timezone)

        # Format IST datetime as string
        ist_string = ist_datetime.strftime('%Y-%m-%d %H:%M:%S')
        
        return ist_string
    
    def get_products_for_request(self, vendor_name, brand_name, request_id, only_count=False):
        condition = {'RequestID': request_id}
        return self.get_keys(vendor_name, brand_name, self.products_coll_pattern,
                             condition, only_count)

    def get_products_by_status(self, vendor_name, brand_name, request_id, status):
        products = []
        if status == 'ready':
            products = self.get_new_products(vendor_name, brand_name, request_id)
        elif status in ['curated', 're-curated']:
            products = self.get_products_for_review_v2(vendor_name, brand_name, request_id)
            if status == 're-curated':
                products1 = self.get_products_to_recurate(vendor_name, brand_name, request_id)
                products = list(set(products) & set(products1))
        elif status == 'reviewed':
            products = self.get_products_to_post(vendor_name, brand_name, request_id)
        elif status == 'pushed':
            products = self.products_pushed(vendor_name, brand_name, request_id)

        return unescape(products)

    def products_processed(self, vendor_name, brand_name, only_count=False):
        condition = {'reviewed': True}
        keys1 = self.get_keys(vendor_name, brand_name,
                              self.cataloging_coll_pattern, condition, only_count=False)
        keys2 = self.get_keys(vendor_name, brand_name,
                              self.products_coll_pattern, {}, only_count=False)
        keys3 = list(set(keys1) & set(keys2))
        return len(keys3) if only_count else keys3

    def products_pushed(self, vendor_name, brand_name, request_id=None, only_count=False):
        condition = {'pushed': True}
        if request_id is not None:
            condition['output.RequestID'] = request_id
        return self.get_keys(vendor_name, brand_name,
                             self.cataloging_coll_pattern, condition, only_count)

    def get_requests_to_post(self, vendor_name, brand_name):
        fields = ['output.RequestID']
        condition = {'reviewed': True, 'pushed': False}
        rows = self.get_keys(vendor_name, brand_name,
                             self.cataloging_coll_pattern, condition, only_count=False,
                             fields=fields)
        request_ids = []
        for row in rows:
            request_id = row.get('output', {}).get('RequestID', None)
            if request_id is not None:
                request_ids.append(request_id)
        return list(set(request_ids))

    def get_products_to_post(self, vendor_name, brand_name, request_id=None,
                             only_count=False, for_push=False):
        condition = {'reviewed': True, 'pushed': False}
        if request_id is not None:
            condition['output.RequestID'] = {'$eq': request_id}
            # push all products which are reviewed
            if for_push:
                condition.pop('pushed', None)
        return self.get_keys(vendor_name, brand_name,
                             self.cataloging_coll_pattern, condition, only_count)

    def mark_products_pushed(self, vendor_name, brand_name):
        vendor_db = get_db_name(vendor_name)
        cataloging_coll = self.cataloging_coll_pattern % brand_name
        logger.info('%s %s', vendor_db, cataloging_coll)
        coll = self.client[vendor_db][cataloging_coll]
        out = coll.update_many({'reviewed': True, 'pushed': False}, {'$set': {'pushed': True}})
        logger.info(out.raw_result)

    def mark_products_unpushed(self, vendor_name, brand_name, style_code, pushed=False):
        vendor_db = get_db_name(vendor_name)
        cataloging_coll = self.cataloging_coll_pattern % brand_name
        logger.info('%s %s', vendor_db, cataloging_coll)
        coll = self.client[vendor_db][cataloging_coll]
        out = coll.update_many({'_id': escape(style_code)}, {'$set': {'pushed': pushed}})
        logger.info(out.raw_result)

    def get_data(self, vendor_name, brand_name, style_code):
        vendor_db = get_db_name(vendor_name)
        cataloging_coll = self.cataloging_coll_pattern % brand_name
        coll = self.client[vendor_db][cataloging_coll]
        for row in coll.find({'_id': escape(style_code)}):
            return unescape(row)
        
    def get_catalogix_data(self, vendor_name, brand_name, style_code):
        vendor_db = get_db_name(vendor_name)
        catalogix_coll = self.catalogix_coll_pattern % brand_name
        coll = self.client[vendor_db][catalogix_coll]
        for row in coll.find({'_id': escape(style_code)}):
            return unescape(row)

    def get_output(self, vendor_name, brand_name, style_code):
        vendor_config = self.get_vendor_config(vendor_name)
        config = self.get_brand_config(vendor_name, brand_name)
        module = import_module('client_autoscribe.integrations.%s' % vendor_name)
        adapter = module.VendorAdapter(vendor_config, config)
        data = self.get_data(vendor_name, brand_name, style_code)
        product = self.get_product(vendor_name, brand_name, style_code)
        if data is None:
            return
        output = data.get('output', {})
        # return adapter.convert_output(output)
        data1 = data.get('data', {})
        return adapter.convert_output(output, product=product, data=data1)
    
    def get_catalogix_output(self, vendor_name, brand_name, style_code):
        vendor_config = self.get_vendor_config(vendor_name)
        config = self.get_brand_config(vendor_name, brand_name)
        module = import_module('client_autoscribe.integrations.%s' % vendor_name)
        adapter = module.VendorAdapter(vendor_config, config)
        data = self.get_catalogix_data(vendor_name, brand_name, style_code)
        product = self.get_product(vendor_name, brand_name, style_code)
        if data is None:
            return
        output = data.get('output', {})
        # return adapter.convert_output(output)
        data1 = data.get('data', {})
        return adapter.convert_output(output, product=product, data=data1)

    def get_rejected_products(self, vendor_name, brand_name):
        vendor_db = get_db_name(vendor_name)
        rejects_coll = self.rejects_coll_pattern % brand_name
        rejects = self.client[vendor_db][rejects_coll]
        style_codes = {}
        for x in rejects.find({'resolved': False}, {'_id': 0, 'StyleCode': 1, 'submit_time': 1}):
            if x['StyleCode'] in style_codes:
                if x['submit_time'] > style_codes[x['StyleCode']]:
                    style_codes[x['StyleCode']] = x['submit_time']
            else:
                style_codes[x['StyleCode']] = x['submit_time']

        products_coll = self.products_coll_pattern % brand_name
        products = self.client[vendor_db][products_coll]
        out = {}
        for style_code, timestamp in style_codes.items():
            for _ in products.find({'_id': style_code}):
                out[style_code] = timestamp
        return out

    def save_products(self, vendor_db, brand_name, style_codes):
        details = {}
        products_coll = self.products_coll_pattern % brand_name
        coll = self.client[vendor_db][products_coll]

        cataloging_coll = self.cataloging_coll_pattern % brand_name
        coll2 = self.client[vendor_db][cataloging_coll]
        
        catalogix_coll = self.catalogix_coll_pattern % brand_name
        coll3 = self.client[vendor_db][catalogix_coll]

        for data in style_codes:
            data = escape(data)
            logger.info(data)
            data = {k: v for k, v in data.items() if k.strip() and v is not None}
            style_code = data['StyleCode']
            requestId = data.get('RequestID', None)
            data['RequestID'] = str(requestId) if requestId is not None else None
            try:
                found = False
                for row in coll.find({'_id': style_code}):
                    data['product_uuid'] = row['product_uuid']
                    data['last_modified'] = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
                    data['re-curate'] = True
                    data['push_catalogix'] = False
                    try:
                        coll.update_one({'_id': escape(style_code)}, {'$unset': {'batch_number': ''}})
                    except Exception as e:
                        logger.error(f"{e} for stylecode {style_code}")
                    coll.update_one({'_id': escape(style_code)}, {'$set': data} )
                    details[style_code] = {'code': 0, 'message': 'already exists'}
                    found = True
                if not found:
                    data['_id'] = style_code
                    data['last_modified'] = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
                    data['product_uuid'] = uuid4().hex
                    data['re-curate'] = False
                    data['push_catalogix'] = False
                    coll.insert_one(data)
                    details[style_code] = {'code': 0, 'message': 'new created'}
            except Exception as e:
                details[style_code] = {'code': 1, 'message': str(e)}

            # update pushed because request id might have changed
            coll2.update_one({'_id': escape(style_code)}, {'$set': {'pushed': False, 'reviewed': False,
                                                        'output.RequestID': requestId}})
            
            coll3.update_one({'_id': escape(style_code)}, {'$set': {'pushed': False, 'reviewed': False,
                                                        'output.RequestID': requestId}})

        return details

    def save_rejects(self, vendor_db, brand_name, style_codes):
        """

        :param brand_name:
        :param vendor_db:
        :param style_code: dict
        :return:
        """
        # create unique request for each reject
        ddata = copy(style_codes)
        rejects_coll = self.rejects_coll_pattern % brand_name
        coll = self.client[vendor_db][rejects_coll]
        style_code = style_codes['StyleCode']

        # check for duplicates
        fields = ['_id', 'submit_time', 'resolved']
        is_unique = True
        for row in coll.find({'StyleCode': style_code}):
            row1 = copy(row)
            for field in fields:
                row1.pop(field, None)
            if row1 == style_codes:
                is_unique = False
                style_codes = row
                break

        if is_unique:
            style_codes['_id'] = uuid4().hex
            style_codes['submit_time'] = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
            style_codes['resolved'] = False
            coll.insert_one(style_codes)
            logger.info('Inserting new reject: %s %s', style_codes['_id'], style_code)
        else:
            logger.info('Reject already exists: %s %s', style_codes['_id'], style_code)

        # find and send back processing status
        products_coll = self.products_coll_pattern % brand_name
        coll = self.client[vendor_db][products_coll]
        for row in coll.find({'_id': style_code}):
            if 'product_uuid' in row:
                ddata['product_uuid'] = row['product_uuid']
            else:
                ddata['product_uuid'] = 'Not Present in Database'
            if 'batch_number' in row:
                ddata['batch_number'] = row['batch_number']
            else:
                ddata['batch_number'] = 'Product Not Processed'

        return ddata

    def get_ontologies(self):
        url = self.ontology_host + '/calm/ontology'
        r = requests.get(url, timeout=60)
        if r.ok:
            return r.json().get('data', [])

        logger.error('%s %s', r.status_code, r.content)

    def get_all_tags(self, ontology):
        url = self.ontology_host + '/calm/ontology/%s' % ontology
        r = requests.get(url, params={'all': '1'}, timeout=60)
        if r.ok:
            return r.json().get('data', [])

        logger.error('%s %s', r.status_code, r.content)

    def translate_image_attrs(self, source, target, source_tags, restrict=True):
        # image-based attributes
        url = self.ontology_host + '/calm/ontology/translate/%s/%s' % (target, source)
        logger.info('Translating %s : %s', source, source_tags)
        r = requests.post(url, data={'tags': ';'.join(source_tags),
                                     'restrict': 1 if restrict else 0}, timeout=60)
        target_tags = []
        if r.ok:
            target_tags = r.json().get('data', [])
        else:
            logger.error('%s %s', r.status_code, r.content)
        logger.info('%s : %s', target, target_tags)
        return target_tags

    def translate_non_image_attrs(self, vendor_name, ontology, source_tags,
                                  vendor_tags, brand_name=None):
        logger.info(source_tags)
        logger.info(vendor_tags)

        # non-image attributes
        non_image_tags = self.custom_rules_db.apply_rules(vendor_name, ontology,
                                                          source_tags, vendor_tags, brand_name)
        logger.info('non-image tags : %s : %s : %s', vendor_name, ontology, non_image_tags)
        target_tags = non_image_tags

        # fabric
        if len(vendor_tags) > 0:
            fabric_tags = self.custom_rules_db.apply_fabric_rules(vendor_name, ontology,
                                                                  source_tags, vendor_tags,
                                                                  brand_name)
            logger.info('fabric tags : %s : %s : %s', vendor_name, ontology, fabric_tags)
            target_tags.extend(fabric_tags)

        return target_tags

    # TODO: standardize translation APIs
    def translate_split(self, vendor_name, brand_name, curated_data, product, targets, source):
        source_tags = [k + ':' + v for k, v in curated_data.items() if v is not None and len(v) > 0]
        vendor_tags = [k + ':' + str(v) for k, v in unescape(product).items() if '://' not in str(v)]
        output = {}

        for field, target in targets.items():
            image_tags = self.translate_image_attrs(source, target, source_tags)
            non_image_tags = self.translate_non_image_attrs(vendor_name, target,
                                                            source_tags, vendor_tags,
                                                            brand_name=brand_name)

            # tags => attr-vals
            output[field] = {'image_tags': tags_to_dict(image_tags),
                             'non_image_tags': tags_to_dict(non_image_tags)}

        return output

    def translate(self, curated_data, product, targets, source,
                  vendor_name=None, brand_name=None, custom_rules=False, style_code=None):
        module = import_module('client_autoscribe.integrations.%s' % vendor_name)
        vendor_config = self.get_vendor_config(vendor_name)
        config = self.get_brand_config(vendor_name, brand_name)
        adapter = module.VendorAdapter(vendor_config, config)
        # brand_ontology = config['brand_ontology']

        source_tags = [k.strip() + ':' + v.strip() for k, v in curated_data.items() if v is not None and len(v) > 0]
        vendor_tags = [k.strip() + ':' + str(v).strip() for k, v in unescape(product).items() if
                       '://' not in str(v)]  # to avoid long urls
        output = {}

        # custom translation for brand ontology
        logger.info('%s => %s %s', style_code, source_tags, vendor_tags)
        for field, target in targets.items():
            target_tags = self.translate_image_attrs(source, target, source_tags)
            if custom_rules:
                target_tags.extend(self.translate_non_image_attrs(vendor_name, target,
                                                                  source_tags, vendor_tags,
                                                                  brand_name))

            # logger.info(target_tags)
            # tags => attr-vals
            output1 = tags_to_dict(target_tags)
            output2 = adapter.translate(target, product, curated_data, output1)
            logger.info('Updating: %s', output2)
            output1.update(output2)
            logger.info('%s => %s %s', style_code, field, output1)
            output[field] = output1

        return output

    def translate_and_save(self, vendor_name, brand_name, style_codes):
        vendor_db = get_db_name(vendor_name)
        products_coll = self.products_coll_pattern % brand_name
        cataloging_coll = self.cataloging_coll_pattern % brand_name
        coll1 = self.client[vendor_db][products_coll]
        coll2 = self.client[vendor_db][cataloging_coll]

        vendor_config = self.get_vendor_config(vendor_name)
        source = vendor_config['curation_ontology']

        config = self.get_brand_config(vendor_name, brand_name)
        targets = config.get('targets', {})
        custom_rules = config.get('custom_rules', False)
        for style_code in style_codes:
            try:
                logger.info(style_code)
                product = {}
                curated = {}
                for row in coll1.find({'_id': escape(style_code)}):
                    product = row
                for row in coll2.find({'_id': escape(style_code)}):
                    curated = row

                output = self.translate(curated.get('data', {}), product, targets, source,
                                        vendor_name, brand_name, custom_rules, style_code)
                output.update({'StyleCode': style_code, 'RequestID': product.get('RequestID', None)})
                coll2.update_one({'_id': escape(style_code)}, {'$set': {'output': escape(output)}}, upsert=True)
            except Exception as e:
                logger.exception('%s %s %s %s', vendor_name, brand_name, style_code, str(e))

    def on_push(self, vendor_name, brand_name, style_code, request_id):
        # only update for first time
        vendor_db = get_db_name(vendor_name)
        now = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
        cataloging_coll = self.cataloging_coll_pattern % brand_name
        coll = self.client[vendor_db][cataloging_coll]
        coll.update_one({'_id': style_code},
                    {'$set': {'pushed': True, 'push_time': now}})

        # mark existing issues as resolved while pushing
        rejects_coll = self.rejects_coll_pattern % brand_name
        coll2 = self.client[vendor_db][rejects_coll]
        result = coll2.update_many({'StyleCode': style_code, 'resolved': False},
                                   {'$set': {'resolved': True, 'resolution_time': now}})
        logger.info('marked resolved : %s %s matched=%d modified=%d', vendor_name, style_code, result.matched_count,
                    result.modified_count)

        # send Slack message
        # view_url = 'http://cataloging.streamoid.com/api/autoscribe/vendors' \
        #            '%s/brands/%s/products/%s' % (vendor_name, brand_name, style_code)
        # post_to_slack(vendor_db, request_id, view_url)

        # post to Teams
        card_data = generate_curated_card(vendor_name, brand_name, style_code, request_id)
        teams_url = self.teams_urls.get(vendor_name, {}).get('post', None)
        if teams_url is not None:
            post_to_teams(teams_url, card_data)

    def on_push_catalogix(self, vendor_name, brand_name, style_code, request_id):
    # only update for first time
        vendor_db = get_db_name(vendor_name)
        now = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
        catalogix_coll = self.catalogix_coll_pattern % brand_name
        coll = self.client[vendor_db][catalogix_coll]
        coll.update_one({'_id': style_code},
                    {'$set': {'pushed': True, 'push_time': now}})

        # mark existing issues as resolved while pushing
        rejects_coll = self.rejects_coll_pattern % brand_name
        coll2 = self.client[vendor_db][rejects_coll]
        result = coll2.update_many({'StyleCode': style_code, 'resolved': False},
                                {'$set': {'resolved': True, 'resolution_time': now}})
        logger.info('marked resolved : %s %s matched=%d modified=%d', vendor_name, style_code, result.matched_count,
                    result.modified_count)

        # send Slack message
        # view_url = 'http://cataloging.streamoid.com/api/autoscribe/vendors' \
        #            '%s/brands/%s/products/%s' % (vendor_name, brand_name, style_code)
        # post_to_slack(vendor_db, request_id, view_url)

        # post to Teams
        # card_data = generate_curated_card_catalogix(vendor_name, brand_name, style_code, request_id)
        # teams_url = self.teams_urls.get(vendor_name, {}).get('post', None)
        # if teams_url is not None:
        #     post_to_teams(teams_url, card_data)


    def save_pushed_details(self, vendor_name, brand_name, style_code, request_id, client_response):
        # only update for first time
        vendor_db = get_db_name(vendor_name)
        now = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
        cataloging_coll = self.cataloging_coll_pattern % brand_name
        coll = self.client[vendor_db][cataloging_coll]

        push_id = f'{request_id}:{now}'
        client_response = {push_id: client_response}
        result = coll.update_one({'_id': style_code},
                             {'$push': {'client_response': client_response}})

        logger.info(result)
        logger.info(
            f" saving client response for {vendor_name},{brand_name},{style_code},{request_id}: {client_response}")

    def get_reviewed_data(self, vendor_name, brand_name, request_id=None, for_push=False):
        module = import_module('client_autoscribe.integrations.%s' % vendor_name)
        vendor_config = self.get_vendor_config(vendor_name)
        config = self.get_brand_config(vendor_name, brand_name)
        adapter = module.VendorAdapter(vendor_config, config)
        style_codes = self.get_products_to_post(vendor_name, brand_name,
                                                request_id=request_id, for_push=for_push)
        outputs = {}
        for style_code in style_codes:
            try:
                output = self.get_output(vendor_name, brand_name, style_code)
                if output is None:
                    continue
                # request_id = output.get('RequestID', None)
                outputs[style_code] = output  # adapter.convert_output(output)
            except Exception as e:
                logger.exception('%s %s %s %s', vendor_name, brand_name, style_code, str(e))
        return outputs

    def get_rpa_inputs(self, vendor_name, brand_name, config):
        style_codes = self.get_products_to_post(vendor_name, brand_name)

        targets = config.get('targets', {})
        products = {x: [] for x in targets.values()}
        for style_code in style_codes:
            try:
                data = self.get_data(vendor_name, brand_name, style_code)
                if data is None:
                    continue
                output = data.get('output', {})
                for field, details in output.items():
                    details['StyleCode'] = style_code
                    ontology = targets[field]
                    products[ontology].append(details)
            except Exception as e:
                logger.exception('%s %s', style_code, str(e))

        output = {}
        for ontology, data1 in products.items():
            df = pd.DataFrame(data1)
            logger.info(df.shape)
            if 'Sheet Name' not in df.columns or 'Sub-sheet Name' not in df.columns:
                logger.info(df.columns)
                continue
            for key, df1 in df.groupby(['Sheet Name', 'Sub-sheet Name']):
                sheet_name, sub_sheet_name = key
                filename = '%s__%s__%s.csv' % (ontology, sheet_name, sub_sheet_name)
                logger.info('%s %s %s %s %s', ontology, sheet_name, sub_sheet_name, df1.shape,
                            filename)
                df1.drop(columns=['Sheet Name', 'Sub-sheet Name'], inplace=True)
                output[filename] = df1
        return output

    def post_to_client(self, vendor_name, brand_name, request_id):
        module = import_module('client_autoscribe.integrations.%s' % vendor_name)
        vendor_config = self.get_vendor_config(vendor_name)
        config = self.get_brand_config(vendor_name, brand_name)
        adapter = module.VendorAdapter(vendor_config, config)

        post_incomplete = vendor_config.get('post_incomplete', True)
        products = self.get_products_for_request(vendor_name, brand_name, request_id)
        outputs = self.get_reviewed_data(vendor_name, brand_name, request_id, for_push=True)
        remaining = set(products) - set(outputs.keys())
        logger.info('%s %s %s %s %d', vendor_name, brand_name, request_id,
                    post_incomplete, len(remaining))
        if not post_incomplete and len(remaining) > 0:
            logger.error('Could not post due to incomplete curation of %s : %d : %s',
                         request_id, len(remaining), remaining)
            return
        logger.info('Posting %d style codes for request %s', len(outputs), request_id)
        if post_incomplete:
            # post only one request, if post_incomplete is true;
            # randomize style codes to allow parallelization by
            # allowing multiple workers to upload different products
            style_codes = list(outputs.keys())
            random.shuffle(style_codes)
            for style_code in style_codes:
                values = outputs[style_code]
                data1 = self.get_data(vendor_name, brand_name, style_code)
                # Push only if not pushed
                # TODO: how to handle rejected products?
                if not data1.get('pushed', False):
                    logger.info('%s %s %s pushing', vendor_name, brand_name, style_code)
                    status = adapter.post_request_to_vendor(request_id, {style_code: values})
                    for style_code, ok in status.items():
                        if ok:
                            self.on_push(vendor_name, brand_name, style_code, request_id)
                            logger.info('%s %s %s pushed now', vendor_name, brand_name, style_code)
                        else:
                            logger.info('%s %s %s unsuccessful push', vendor_name, brand_name, style_code)
                else:
                    logger.info('%s %s %s already pushed', vendor_name, brand_name, style_code)
        else:
            status = adapter.post_request_to_vendor(request_id, outputs)
            for style_code, ok in status.items():
                if ok:
                    self.on_push(vendor_name, brand_name, style_code, request_id)

    def get_catalogix_requests_to_post(self, vendor_name, brand_name):
        fields = ['output.RequestID']
        condition = {'reviewed': True, 'pushed': False}
        rows = self.get_keys(vendor_name, brand_name,
                             self.catalogix_coll_pattern, condition, only_count=False,
                             fields=fields)
        request_ids = []
        for row in rows:
            request_id = row.get('output', {}).get('RequestID', None)
            if request_id is not None:
                request_ids.append(request_id)
        return list(set(request_ids))

    def catalogix_post_to_client(self, vendor_name, brand_name, request_id, post_flag):
        module = import_module('client_autoscribe.integrations.%s' % vendor_name)
        vendor_config = self.get_vendor_config(vendor_name)
        config = self.get_brand_config(vendor_name, brand_name)
        adapter = module.VendorAdapter(vendor_config, config)

        post_incomplete = vendor_config.get('post_incomplete', True)
        products = self.get_products_for_request(vendor_name, brand_name, request_id)
        outputs = self.get_reviewed_catalogix_data(vendor_name, brand_name, request_id, for_push=True)
        remaining = set(products) - set(outputs.keys())
        logger.info('%s %s %s %s %d', vendor_name, brand_name, request_id,
                    post_incomplete, len(remaining))
        if not post_incomplete and len(remaining) > 0:
            logger.error('Could not post due to incomplete curation of %s : %d : %s',
                         request_id, len(remaining), remaining)
            return
        logger.info('Posting %d style codes for request %s', len(outputs), request_id)
        
        if post_incomplete:
            style_codes = list(outputs.keys())
            if(post_flag == False):
                for style_code in style_codes:
                    logger.info('(Post == False). For %s stylecode the output is %s',
                                style_code, outputs[style_code])
                return True
            # post only one request, if post_incomplete is true;
            # randomize style codes to allow parallelization by
            # allowing multiple workers to upload different products
            
            random.shuffle(style_codes)
            for style_code in style_codes:
                values = outputs[style_code]
                data1 = self.get_catalogix_data(vendor_name, brand_name, style_code)
                # Push only if not pushed
                # TODO: how to handle rejected products?
                if not data1.get('pushed', False):
                    logger.info('%s %s %s pushing', vendor_name, brand_name, style_code)
                    status = adapter.post_request_to_vendor(request_id, {style_code: values})
                    for style_code, ok in status.items():
                        if ok:
                            self.on_push_catalogix(vendor_name, brand_name, style_code, request_id)
                            logger.info('%s %s %s pushed now', vendor_name, brand_name, style_code)
                        else:
                            logger.info('%s %s %s unsuccessful push', vendor_name, brand_name, style_code)
                else:
                    logger.info('%s %s %s already pushed', vendor_name, brand_name, style_code)
        else:
            if(post_flag == False):
                return True
            status = adapter.post_request_to_vendor(request_id, outputs)
            for style_code, ok in status.items():
                if ok:
                    self.on_push_catalogix(vendor_name, brand_name, style_code, request_id)

    def export_rejected_products(self, vendor_name, brand_name):
        style_codes = self.get_rejected_products(vendor_name, brand_name)
        logger.info(style_codes)
        vendor_db = get_db_name(vendor_name)
        products_coll = self.products_coll_pattern % brand_name
        products = self.client[vendor_db][products_coll]
        out = []
        for style_code, timestamp in style_codes.items():
            for row1 in products.find({'_id': style_code}):
                row = unescape(row1)
                for url in row['ImageURLs'].split(','):
                    out.append({'style_code': row['StyleCode'],
                                'image_url': url,
                                'timestamp': timestamp})

        out = sorted(out, key=lambda x: x['timestamp'], reverse=True)
        return out

    def mark_reviewed(self, vendor_name, brand_name, style_code):
        vendor_db = get_db_name(vendor_name)
        cataloging_coll = self.cataloging_coll_pattern % brand_name
        coll = self.client[vendor_db][cataloging_coll]
        coll.update_one({'_id': escape(style_code)}, {'$set': {'reviewed': True}})

    def mark_reviewed_bulk(self, vendor_name, brand_name):
        vendor_db = get_db_name(vendor_name)
        cataloging_coll = self.cataloging_coll_pattern % brand_name
        coll = self.client[vendor_db][cataloging_coll]
        coll.update_many({'reviewed': False}, {'$set': {'reviewed': True}})

    def import_data(self, vendor_name, brand_name, data, optional=False):
        # find max batch size in products
        vendor_db = get_db_name(vendor_name)
        products_coll = self.products_coll_pattern % brand_name
        coll1 = self.client[vendor_db][products_coll]
        # batch_number = 1
        # for row in coll1.find({'batch_number': {'$exists': True}}) \
        #         .sort([('batch_number', DESCENDING)]).limit(1):
        #     batch_number = row['batch_number']

        # logger.info('batch_number : %d', batch_number)

        cataloging_coll = self.cataloging_coll_pattern % brand_name
        coll2 = self.client[vendor_db][cataloging_coll]
        style_codes = []
        missing_codes = []
        for idx, row in data.iterrows():
            row1 = {}
            data = dict(row)
            style_code = data.pop('product_code', None)
            if style_code is None:
                logger.warning('Skipping data with no style code: %s', data)
                continue
            if not self.get_product(vendor_name, brand_name, style_code):
                logger.warning(f'Style code {style_code} not found in products collection')
                missing_codes.append(style_code)
                continue
            # remove ignore extra fields, if present
            if not optional:
                for field in ['title', 'description',
                              'seo:title', 'seo:description', 'seo:keyword']:
                    data.pop(field, None)
            # update these fields, if present
            for field in ['product_id', 'image_id', 'image_url']:
                if field in data:
                    row1[field] = data.pop(field)
            row1['data'] = data
            row1['_id'] = style_code
            row1['reviewed'] = False
            row1['pushed'] = False
            row1 = escape(row1)
            coll2.update_one({'_id': row1['_id']}, row1, upsert=True)

            style_codes.append(style_code)
            # update product batch number, if not exists
            coll1.update_one({'_id': row1['_id'], 'batch_number': {'$exists': False}},
                         {'$set': {'batch_number': 1}})

        # logger.info('translating...')
        # logger.info(style_codes)
        # self.translate_and_save(vendor_name, brand_name, style_codes)

        return style_codes, missing_codes

    def save_live(self, vendor_name, brand_name, style_code, data):
        """
        :param output: image_url => live info
        :param info: translated product_info
        :return:
        """
        vendor_db = get_db_name(vendor_name)
        live_coll = self.live_coll_pattern % brand_name
        coll = self.client[vendor_db][live_coll]
        coll.update_one({'_id': style_code}, {'$set': data}, upsert=True)

    def get_live(self, vendor_name, brand_name, style_code):
        vendor_db = get_db_name(vendor_name)
        live_coll = self.live_coll_pattern % brand_name
        coll = self.client[vendor_db][live_coll]
        for row in coll.find({'_id': style_code}):
            return row

    def get_random_products(self, vendor_name, brand_name, count=25):
        vendor_db = get_db_name(vendor_name)
        cataloging_coll = self.cataloging_coll_pattern % brand_name
        coll = self.client[vendor_db][cataloging_coll]
        return [row['_id'] for row in coll.aggregate([{'$sample': {'size': count}}])]

    def get_rpa_details(self, vendor_name, brand_name, rpa_request_id, filename):
        vendor_db = get_db_name(vendor_name)
        rpa_coll = self.rpa_coll_pattern % brand_name
        coll = self.client[vendor_db][rpa_coll]
        return self.rpa_db.get_rpa_details(coll, rpa_request_id, filename)

    def set_rpa_details(self, vendor_name, brand_name, rpa_request_id, responses):
        vendor_db = get_db_name(vendor_name)
        rpa_coll = self.rpa_coll_pattern % brand_name
        coll = self.client[vendor_db][rpa_coll]
        self.rpa_db.set_rpa_details(coll, rpa_request_id, responses)

    def get_rpa_status(self, vendor_name, brand_name, condition=None, offset=None, limit=None):
        vendor_db = get_db_name(vendor_name)
        rpa_coll = self.rpa_coll_pattern % brand_name
        coll = self.client[vendor_db][rpa_coll]
        return self.rpa_db.get_rpa_status(coll, condition, offset, limit)
    
    def extract_number(self, key):
        match = re.search(r'(\d+)$', key)
        return int(match.group(1)) if match else None

    def apply_template_mapping(self, data1, template_map):
        result = {}
        new_result = {}

        for old_key, value in data1.items():
            if old_key in template_map:
                new_key = template_map[old_key]['attribute']
                if new_key not in result:
                    result[new_key] = []
                result[new_key].append({old_key: value})
            else:
                new_result[old_key] = value

        for new_key, entries in result.items():
            if len(entries) == 1:
                only_item = list(entries[0].items())[0]
                new_result[new_key] = only_item[1]
            else:
                selected = None
                min_number = float('inf')
                fallback = None
                longest_key = ""

                for entry in entries:
                    old_key, value = list(entry.items())[0]
                    num = self.extract_number(old_key)
                    print(num)

                    if num is None:
                        fallback = value
                        selected = value
                        break  # Prefer key without number
                    else:
                        if num < min_number:
                            min_number = num
                            selected = value

                    if len(old_key) > len(longest_key):
                        longest_key = old_key
                        longest_value = value

                if selected is None:
                    selected = longest_value

                new_result[new_key] = selected

        return new_result

    
    def generate_flags_catalogix_data(self, vendor_name, brand_name, marketplace, data1, mapping_attr):
        data1 = self.apply_template_mapping(data1, mapping_attr)
        print(data1)
        output = {}
        logger.info(f'{vendor_name}, {brand_name}, {data1["StyleCode"]}')
        row = self.get_product(vendor_name, brand_name, data1['StyleCode'])
        output["RequestID"] = row.get('RequestID', None)
        output["StyleCode"] = data1['StyleCode']
        output["ProductID"] = data1.get("ProductID", None)
        data1 = {key: value for key, value in data1.items() if not isinstance(value, dict)}
        keys_to_remove = [
            "store_group",
            "product_uuid",
            "store_uuid",
            "product_code",
            "ov",
            "curation_status",
            "completion",
            "template",
            "images",
            "videos",
            "style_code",
            "StyleCode",
            "doc_type",
            "catalogix_availability",
            "optional_completion",
            "required_completion",
            "Seller Article SKU",
            "S_OR_D",
            "Seller Product Association Status (Refer LOV List)",
            "Description",
            "Product Description",
            "Short Product Description",
            "Brand Description",
            "PRODUCT DESCRIPTION",
            "Product Details",
            "ProductID",
        ]
        logger.info(vendor_name)
        logger.info(marketplace)
        if vendor_name == 'abfrl_lbrd_prod' and marketplace == 'myntra_attributes':
            keys_to_remove.extend(['styleId','vendorArticleName'])
        data1 = {key: value for key, value in data1.items() if key not in keys_to_remove}

        # attributes_to_empty = [
        #     "Seller Article SKU",
        #     "S_OR_D",
        #     "Seller Product Association Status (Refer LOV List)",
        #     "Description",
        #     "Product Description",
        #     "Short Product Description",
        #     "Brand Description",
        #     "PRODUCT DESCRIPTION",
        #     "Product Details"
        # ]
        # for key in data1:
        #     if key in attributes_to_empty:
        #         data1[key] = ""
        data1 = {key: value for key, value in data1.items() if not (isinstance(value, str) and (value.startswith('http://') or value.startswith('https://')))}
        data1 = {key.replace("_AccessoriesMen ApparelStitched", "").replace("_ApparelsMen ApparelStitched", "").replace("_Brooch", "").replace("_Bandhgala","").replace("_Churidar", "").replace("_Indo-Western", ""): value for key, value in data1.items() }
        output[marketplace] = data1
        return output
    
    def update_products(self, vendor_name, brand_name, style_code):
        products_coll = self.products_coll_pattern % brand_name
        vendor_db = get_db_name(vendor_name)
        coll  = self.client[vendor_db][products_coll]
        coll.update_one({'_id': style_code, 'batch_number': {'$exists': False}},
                         {'$set': {'batch_number': 1}})
    def get_marketplaces_versions(self):
        url = self.marketplaces_url + '/marketplaces/versions'
        response = requests.get(url)
        return response.json()
    
    
    def select_template(self, marketplace, versions, smp_tags):
        url = self.marketplaces_url + f'/marketplaces/{marketplace}/versions/{versions[marketplace]}/select-template'
        
        data = {
            "from_marketplace": "SMP",
            "from_version": versions['SMP'],
            "data": json.dumps(smp_tags) 
        }
        response = requests.post(url, data=data)
        return response.json()
    

    def get_mapping(self, marketplace, versions, template):
        url = self.marketplaces_url + f'/marketplaces/{marketplace}/versions/{versions[marketplace]}/attribute-mapping'
        params = {
            "template": template
        }
        headers = {
            "accept": "application/json"
        }
        response = requests.get(url, params=params, headers=headers)
        response.raise_for_status()  # Optional: raises error if request failed
        return response.json()
    
    def save_catalogix_data(self, vendor_name, brand_name, style_codes, store_uuid, marketplace):
        
        template_mapping = {}

        # Step 1: Get unique templates from style_codes
        unique_templates = {item["template"] for item in style_codes if "template" in item}

        # Step 2: Call API once per template and store result
        marketplace_versions = self.get_marketplaces_versions()

        for template in unique_templates:
            try:
                mapping = self.get_mapping(marketplace.replace("_attribute", ""), marketplace_versions, template)
                template_mapping[template] = mapping
            except Exception as e:
                logger.error(f"Error fetching mapping for template '{template}': {e}")
                
                
        vendor_db = get_db_name(vendor_name)
        catalogix_coll = self.catalogix_coll_pattern % brand_name
        coll1 = self.client[vendor_db][catalogix_coll]

        # config = self.get_brand_config(vendor_name, brand_name)
        # targets = config.get('targets', {})
        brands_dict = {
        "abfrl-ABOF": "abof",
        "abfrl-allen_solly": "allen_solly",
        "ABFRL-AS": "allen_solly",
        "abfrl-american_eagle": "american_eagle",
        "ABFRL-LP":"louis_philippe",
        "abfrl-louis_philippe": "louis_philippe",
        "abfrl-peter_england": "peter_england",
        "Abfrl-Peter_england": "peter_england",
        "ABFRL-PE": "peter_england",
        "abfrl-peterengland_red": "peter_england_red",
        "abfrl-simon_carter": "simon_carter",
        "ABFRL-SC": "simon_carter",
        "abfrl-van_heusen": "van_heusen",
        "ABFRL-VH": "van_heusen",
        "abfrl-forever_21": "forever21",
        "abfrl-the_collective": "the_collective",
        "tasva": "tasva",
        "grupo-soma": "grupo-soma",
        "likeme-spanish": "likeme",
        "reebok": "reebok",
        }
        logger.info(marketplace)
        if (marketplace == "ajiob2c"):
            marketplace = "ajio"
        if marketplace in brands_dict.keys():
            marketplace = 'brand_attributes'
        elif marketplace == "likeme-english":
            marketplace = 'attributes'
        elif '-vendor' in marketplace:
            marketplace = marketplace.replace('-vendor','') + '_attributes'
        else:
            marketplace = marketplace + '_attributes'
       
                
        for data1 in style_codes:
            style_code = None
            try:
                logger.info(data1)
                style_code = data1['style_code_for_autoscribe']
                self.update_products(vendor_name, brand_name, style_code)
                data1["StyleCode"] = style_code
                del data1['style_code_for_autoscribe']
                if(marketplace.lower()== "smp_attributes"):
                    coll1.update_one(
                    {"_id": escape(style_code)},
                    {
                        "$set": {
                            f"data.{marketplace}": escape(data1),
                        }
                    },
                    upsert=True,
                )
                else:
                    logger.info(data1)
                    output = self.generate_flags_catalogix_data(vendor_name, brand_name, marketplace , data1, template_mapping.get(data1.get('template', 'a'), {}))
                    if output["ProductID"] is None:
                        coll1.update_one(
                        {"_id": escape(style_code)},
                        {
                            "$set": {
                                f"output.{marketplace}": escape(output[marketplace]),
                                f"output.RequestID": output["RequestID"],
                                f"output.StyleCode": output["StyleCode"],
                                f"data.{marketplace}": escape(data1),
                                "reviewed": False,
                                "pushed": False,
                                "problem": False,
                            }
                        },
                        upsert=True,
                        )
                    else:
                        coll1.update_one(
                            {"_id": escape(style_code)},
                            {
                                "$set": {
                                    f"output.{marketplace}": escape(output[marketplace]),
                                    f"output.RequestID": output["RequestID"],
                                    f"output.StyleCode": output["StyleCode"],
                                    f"output.ProductID": output["ProductID"],
                                    f"data.{marketplace}": escape(data1),
                                    "reviewed": False,
                                    "pushed": False,
                                    "problem": False,
                                }
                            },
                            upsert=True,
                        )
            except Exception as e:
                logger.exception('%s %s %s %s', vendor_name, brand_name, style_code, str(e))
                
    
    
    def get_random_catalogix_products(self, vendor_name, brand_name, count=25):
        vendor_db = get_db_name(vendor_name)
        catalogix_coll = self.catalogix_coll_pattern % brand_name
        coll = self.client[vendor_db][catalogix_coll]
        return [row['_id'] for row in coll.aggregate([{'$sample': {'size': count}}])]
    
    def get_catalogix_products_for_review_v2_count(self, vendor_name, brand_name,
                                   request_id=None, only_count=False):
        
        condition = {'$and': [{'$or': [{'hold': False}, {'hold': {'$exists': False}}]}, {'reviewed': False}, {'output': {'$exists': True}}] }
        if request_id is not None:
            condition['output.RequestID'] = request_id
            condition.pop('output', None)
        keys1 = self.get_keys(vendor_name, brand_name,
                              self.catalogix_coll_pattern, condition, only_count=False)
        keys2 = self.get_keys(vendor_name, brand_name,
                              self.products_coll_pattern, {}, only_count=False)
        keys3 = list(set(keys1) & set(keys2))
        return len(keys3) if only_count else keys3

        # condition = {'_id': {'$in': ready}}
        # return self.get_keys(vendor_name, brand_name,
        #                      self.live_coll_pattern, condition, only_count)
    
    def get_catalogix_products_for_review_v2(self, vendor_name, brand_name,
                                   request_id=None, only_count=False):
        
        condition = {'pushed': False, 'output': {'$exists': True}}
        if request_id is not None:
            condition['output.RequestID'] = request_id
            condition.pop('output', None)
        keys1 = self.get_keys(vendor_name, brand_name,
                              self.catalogix_coll_pattern, condition, only_count=False)
        keys2 = self.get_keys(vendor_name, brand_name,
                              self.products_coll_pattern, {}, only_count=False)
        keys3 = list(set(keys1) & set(keys2))
        return len(keys3) if only_count else keys3

        # condition = {'_id': {'$in': ready}}
        # return self.get_keys(vendor_name, brand_name,
        #                      self.live_coll_pattern, condition, only_count)

    def get_catalogix_product_for_review(self, vendor_name, brand_name, style_code):
        product = self.get_product(vendor_name, brand_name, style_code)
        data = self.get_catalogix_data(vendor_name, brand_name, style_code)
        if product is None or data is None:
            logger.warning('Product %s is null', style_code)
            return
        if 'problem' in data:
            if data['problem']:
                return
        data['info'] = {k: v for k, v in product.items()
                        if isinstance(v, str) and '://' not in v}
        data['output'] = unescape(data['output']) if 'output' in data else {}
        return data
    
    def catalogix_mark_reviewed(self, vendor_name, brand_name, style_code):
        vendor_db = get_db_name(vendor_name)
        catalogix_coll = self.catalogix_coll_pattern % brand_name
        coll = self.client[vendor_db][catalogix_coll]
        query = {'$and': [{'$or': [{'hold': False}, {'hold': {'$exists': False}}]}, {'reviewed': False}, {'_id': escape(style_code)}]}
        update = {'$set': {'reviewed': True}}
        coll.update_many(query, update)

    def catalogix_mark_reviewed_bulk(self, vendor_name, brand_name):
        vendor_db = get_db_name(vendor_name)
        catalogix_coll = self.catalogix_coll_pattern % brand_name
        coll = self.client[vendor_db][catalogix_coll]
        query = {'$and': [{'$or': [{'hold': False}, {'hold': {'$exists': False}}]}, {'reviewed': False}]}
        update = {'$set': {'reviewed': True}}
        coll.update_many(query, update)
    

    def get_reviewed_catalogix_data(self, vendor_name, brand_name,  request_id=None, for_push = True):
        module = import_module('client_autoscribe.integrations.%s' % vendor_name)
        vendor_config = self.get_vendor_config(vendor_name)
        config = self.get_brand_config(vendor_name, brand_name)
        adapter = module.VendorAdapter(vendor_config, config)
        style_codes = self.get_catalogix_products_to_post(vendor_name, brand_name,
                                                request_id=request_id, for_push=for_push)
        outputs = {}
        for style_code in style_codes:
            try:
                output = self.get_catalogix_output(vendor_name, brand_name, style_code)
                if output is None:
                    continue
                # request_id = output.get('RequestID', None)
                outputs[style_code] = output  # adapter.convert_output(output)
            except Exception as e:
                logger.exception('%s %s %s %s', vendor_name, brand_name, style_code, str(e))
        return outputs
    
    def get_requestId_from_styleCode(self, vendor_name, brand_name, style_code):
        vendor_db = get_db_name(vendor_name)
        coll = self.products_coll_pattern % brand_name
        products = self.client[vendor_db][coll]
        codition = {'StyleCode': style_code}
        rows = products.find(codition)
        for row in rows:
            if row:
                request_id = row.get("RequestID")
                if(request_id):
                    return request_id
        
        return "Unable to Fetch RequestID"
    

    def get_catalogix_products_to_post(self, vendor_name, brand_name, request_id=None,
                             only_count=False, for_push=False):
        condition = {'reviewed': True, 'pushed': False}
        if request_id is not None:
            condition['output.RequestID'] = {'$eq': request_id}
            # push all products which are reviewed
            if for_push:
                condition.pop('pushed', None)
        return self.get_keys(vendor_name, brand_name,
                             self.catalogix_coll_pattern, condition, only_count)
    
    def catalogix_hold_products(self, vendor_name, brand_name, style_code):
        vendor_db = get_db_name(vendor_name)
        catalogix_coll = self.catalogix_coll_pattern % brand_name
        coll = self.client[vendor_db][catalogix_coll]
        coll.update_one({'_id': escape(style_code)}, {'$set': {'hold': True, 'reviewed': False}})

    def catalogix_unhold_products(self, vendor_name, brand_name, style_code):
        vendor_db = get_db_name(vendor_name)
        catalogix_coll = self.catalogix_coll_pattern % brand_name
        coll = self.client[vendor_db][catalogix_coll]
        coll.update_one({'_id': escape(style_code)}, {'$set': {'hold': False}})
    
    def get_vendor_csv_data(self, service, vendor_name, from_ts, to_ts):
        if(service == 'catalogix'):
            pattern = self.catalogix_coll_pattern
        else:
            pattern = self.cataloging_coll_pattern
        brands = self.list_brands(vendor_name)
        condition = {}
        last_modified = {}
        if from_ts is not None:
            last_modified['$gte'] = from_ts
        if to_ts is not None:
            last_modified['$lte'] = to_ts
        if len(last_modified) > 0:
            condition['last_modified'] = last_modified
        csv_data = []
        for brand in brands:
            result1 = self.get_keys(vendor_name, brand, self.products_coll_pattern, condition, only_count=False, fields=['_id', 'last_modified'])
            last_modified_dict = {}
            ids = []
            for doc in result1:
                csv_data.append({'brand':brand, 'style_code': doc['_id'], "Last Modified": doc['last_modified']})

            #     last_modified_dict[doc['_id']] = doc['last_modified']
            #     ids.append(doc['_id'])
            # condition2 = {'_id': {'$in': ids}}
            # result2 = self.get_keys(vendor_name, brand, pattern, condition2, only_count=False, fields=['_id', 'push_time'])
            # for doc in result2:
            #     csv_data.append({'brand': brand, 'style_code': doc['_id'],'input_received':last_modified_dict[doc['_id']], 'push_time': doc.get('push_time')})
        return csv_data
    
    def get_product_data_timestamp(self, vendor_name, from_ts, to_ts):
        brands = self.list_brands(vendor_name)
        condition = {}
        last_modified = {}
        if from_ts is not None:
            last_modified['$gte'] = from_ts
        if to_ts is not None:
            last_modified['$lte'] = to_ts
        if len(last_modified) > 0:
            condition['last_modified'] = last_modified
        logger.info(condition)
        product_data = {}
        for brand in brands:
            product_data[brand] = []
            result1 = self.get_keys(vendor_name, brand, self.products_coll_pattern, condition, only_count=False, fields=['_id', 'last_modified'])
            for doc in result1:
                product_data[brand].append(doc['_id'])
        return product_data

    def get_brand_csv_data(self, service, vendor_name, brand, from_ts, to_ts):
        if(service == 'catalogix'):
            pattern = self.catalogix_coll_pattern
        else:
            pattern = self.cataloging_coll_pattern
        condition = {}
        last_modified = {}
        if from_ts is not None:
            last_modified['$gte'] = from_ts
        if to_ts is not None:
            last_modified['$lte'] = to_ts
        if len(last_modified) > 0:
            condition['last_modified'] = last_modified
        data = []
        logger.info(condition)
        result1 = self.get_keys(vendor_name, brand, self.products_coll_pattern, condition, only_count=False, fields=['_id', 'last_modified'])
        ids = []
        for doc in result1:
            ids.append(doc['_id'])
            data1 = self.get_product(vendor_name, brand, doc['_id'])
            
            # logger.info(data1)
            if(data1 == None):
                data.append({"_id": doc['_id']})
                continue
            data1 = {key.replace('%20', ' '): value for key, value in data1.items()}
            data.append(data1)
            # for doc in result2:
        
        return data

    def manual_post_to_catalogix(self, vendor_name, brand_name, all_brands, style_codes):
        if(all_brands == 'true'):
            return get_brands_sc(self, vendor_name, [brand_name])
        else:
            return get_style_code_data(self, vendor_name, [brand_name], style_codes)


if __name__ == '__main__':
    db = ClientAutoscribeDB('localhost')
    vendor_name = 'grupo_soma_test'
    brand_name = 'animale'
    request_id = '2021_08_19_VER22_0'
    status = 'pushed'
    products = db.get_products_by_status(vendor_name, brand_name, request_id, status)
    # products = db.products_pushed(vendor_name, brand_name, request_id)
    info = db.get_requests(vendor_name, brand_name, search=request_id)
    print(len(products))
    print(len(info[0]['products']), len(set(info[0]['products'])))
    print(set(info[0]['products']) - set(products))
    print(set(products) - set(info[0]['products']))
