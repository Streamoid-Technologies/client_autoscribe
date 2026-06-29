import logging
import os
import sys

logger = logging.getLogger(__name__)

import json
import requests
from uuid import uuid4
from pymongo import MongoClient, DESCENDING
from copy import copy
from datetime import datetime

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from client_autoscribe.ontology_db import OntologyDB, escape, unescape
from client_autoscribe.custom_rules import CustomRulesDB
from client_autoscribe.slack_config import get_slack_token


def get_db_name(vendor_name):
    return 'v_' + vendor_name.lower().strip().replace(' ', '_').replace('-', '_') + '_autoscribe'


def _filter_data(row):
    # logger.info(row.keys())
    output = unescape(row['output'])
    if output['RequestID'] is not None:
        output['RequestID'] = str(output['RequestID'])
    for target, details in output.items():
        if isinstance(details, dict):
            details.pop('Sheet Name', None)
            details.pop('Sub-sheet Name', None)
    return output


def tags_to_dict(tags):
    output1 = {}
    for tag in tags:
        words = tag.split(':')
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
    response = requests.post(url, data=json.dumps(data), headers=headers)
    if response.ok:
        logger.info(response.text)
    else:
        logger.info(response.status_code)


class ClientAutoscribeDB(object):
    def __init__(self, mongo_host):
        self.client = MongoClient(mongo_host)
        self.products_coll = 'products'
        self.live_coll = 'live'
        self.cataloging_coll = 'cataloging'
        self.rejects_coll = 'rejects'
        self.prefs_coll = 'preferences'

        self.onto_db = OntologyDB(mongo_host, 'common')
        self.custom_rules_db = CustomRulesDB(mongo_host)

        self.source = 'Streamoid-MP'
        # target field to marketplace ontology mapping
        self.targets = {"ajio_attributes": "Ajio-MP",
                        "tatacliq_attributes": "Tatacliq-MP",
                        "myntra_attributes": "Myntra-MP",
                        "amazon_attributes": "Amazon-MP",
                        "limeroad_attributes": "Limeroad-MP",
                        "flipkart_attributes": "Flipkart-MP",
                        "paytm_attributes": "Paytm-MP",
                        "nykaa_attributes": "Nykaa-MP"}

        self.common_db = 'client_autoscribe_common'
        self.tokens_coll = 'tokens'

        # brand field to brand-specific ontology mapping
        self.brand_target = "brand_attributes"
        self.brand_ontologies = {"allen_solly": "Allen Solly-MP",
                                 "louis_philippe": "Louis Philippe-MP",
                                 "vanheusen": "Van Heusen-MP",
                                 "peter_england": "Peter England-MP",
                                 "peter_england_red": "Peter England Red-MP",
                                 "american_eagle": "American Eagle-MP",
                                 "simon_carter": "Simon Carter-MP",
                                 "people": "People-MP"}
        self.default_brand_ontology = "Brand-MP"

    def get_vendor_tokens(self):
        coll = self.client[self.common_db][self.tokens_coll]
        return {row['_id']: row['token'] for row in coll.find()}

    def upsert_vendor(self, vendor_name, token):
        coll = self.client[self.common_db][self.tokens_coll]
        coll.update({'_id': vendor_name}, {'$set': {'token': token}}, upsert=True)

    def get_products(self, vendor_name, style_codes):
        vendor_db = get_db_name(vendor_name)
        products = self.client[vendor_db][self.products_coll]
        out = []
        for style_code in style_codes:
            for row in products.find({'_id': style_code}):
                out.append(unescape(row))
        return out

    def get_new_products(self, vendor_name):
        vendor_db = get_db_name(vendor_name)
        products = self.client[vendor_db][self.products_coll]
        return [unescape(x) for x in products.find({'batch_number': {'$exists': False}})]

    def get_new_products_precomputed(self, vendor_name):
        style_codes = self.get_new_products(vendor_name)
        products = {}
        for row in style_codes:
            logger.info(row)
            style_code = row['StyleCode']
            output = self.get_live(vendor_name, style_code)
            if output is None:
                continue
            products[style_code] = output
        return products

    def get_products_for_review(self, vendor_name):
        vendor_db = get_db_name(vendor_name)
        coll = self.client[vendor_db][self.products_coll]
        coll2 = self.client[vendor_db][self.cataloging_coll]
        data = []
        for row in coll2.find({'reviewed': False}):
            style_code = row['_id']
            row['info'] = {}
            for row1 in coll.find({'_id': style_code}):
                row['info'] = {k: v for k, v in unescape(row1).items() if isinstance(v, str) and ':' not in v}
            if 'output' in row:
                row['output'] = unescape(row['output'])
                data.append(row)
        return data

    def get_products_to_post(self, vendor_name):
        vendor_db = get_db_name(vendor_name)
        coll = self.client[vendor_db][self.cataloging_coll]
        data = []
        for row in coll.find({'reviewed': True, 'pushed': False}):
            output = _filter_data(row)
            data.append(output)
        return data

    def get_data(self, vendor_name, style_code):
        vendor_db = get_db_name(vendor_name)
        coll = self.client[vendor_db][self.cataloging_coll]
        for row in coll.find({'_id': style_code}):
            output = _filter_data(row)
            return output

    def get_rejected_products(self, vendor_name):
        vendor_db = get_db_name(vendor_name)
        rejects = self.client[vendor_db][self.rejects_coll]
        style_codes = {}
        for x in rejects.find({'resolved': False}, {'_id': 0, 'StyleCode': 1, 'submit_time': 1}):
            if x['StyleCode'] in style_codes:
                if x['submit_time'] > style_codes[x['StyleCode']]:
                    style_codes[x['StyleCode']] = x['submit_time']
            else:
                style_codes[x['StyleCode']] = x['submit_time']

        products = self.client[vendor_db][self.products_coll]
        out = {}
        for style_code, timestamp in style_codes.items():
            for _ in products.find({'_id': style_code}):
                out[style_code] = timestamp
        return out

    def get_translations(self, vendor_name):
        vendor_db = get_db_name(vendor_name)
        coll = self.client[vendor_db][self.prefs_coll]
        out = {}
        for row in coll.find({'_id': 'translations'}):
            out = row['data']
        targets = list(self.targets.values()) + list(self.brand_ontologies.values()) + [self.default_brand_ontology]
        for x in targets:
            if x not in out:
                out[x] = True
        return out

    def save_products(self, vendor_db, style_codes):
        details = {}
        coll = self.client[vendor_db][self.products_coll]
        for data in style_codes:
            data = escape(data)
            style_code = data['StyleCode']
            try:
                found = False
                for row in coll.find({'_id': style_code}):
                    data['product_uuid'] = row['product_uuid']
                    coll.update({'_id': style_code}, {'$set': data})
                    details[style_code] = {'code': 0, 'message': 'already exists'}
                    found = True
                if not found:
                    data['_id'] = style_code
                    data['last_modified'] = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
                    data['product_uuid'] = uuid4().hex
                    coll.insert(data)
                    details[style_code] = {'code': 0, 'message': 'new created'}
            except Exception as e:
                details[style_code] = {'code': 1, 'message': str(e)}
        return details

    def save_rejects(self, vendor_db, style_codes):
        """

        :param vendor_db:
        :param style_codes: dict
        :return:
        """
        # create unique request for each reject
        ddata = copy(style_codes)
        coll = self.client[vendor_db][self.rejects_coll]
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
            coll.insert(style_codes)
            logger.info('Inserting new reject: %s %s', style_codes['_id'], style_code)
        else:
            logger.info('Reject already exists: %s %s', style_codes['_id'], style_code)

        # find and send back processing status
        coll = self.client[vendor_db][self.products_coll]
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

    def set_translations(self, vendor_name, data):
        vendor_db = get_db_name(vendor_name)
        coll = self.client[vendor_db][self.prefs_coll]
        coll.replace_one({'_id': 'translations'}, {'data': data}, upsert=True)

    def translate_image_attrs(self, target, source_tags):
        # image-based attributes
        logger.info('%s : %s', self.source, source_tags)
        target_tags = self.onto_db.translate(target, self.source, source_tags)
        logger.info('%s : %s', target, target_tags)
        target_tags = self.onto_db.restrict_tags(target, target_tags)
        logger.info('%s : %s', target, target_tags)
        return target_tags

    def translate_non_image_attrs(self, target, source_tags, vendor_tags):
        logger.info(source_tags)
        logger.info(vendor_tags)

        # non-image attributes
        non_image_tags = self.custom_rules_db.apply_rules(target, source_tags, vendor_tags)
        logger.info('non-image tags : %s : %s', target, non_image_tags)
        target_tags = non_image_tags

        # fabric
        if len(vendor_tags) > 0:
            fabric_tags = self.custom_rules_db.apply_fabric_rules(target, source_tags, vendor_tags)
            logger.info('fabric tags : %s : %s', target, fabric_tags)
            target_tags.extend(fabric_tags)

        return target_tags

    def translate_split(self, vendor_name, translations, curated_data, product, targets):
        source_tags = [k + ':' + v for k, v in curated_data.items() if v is not None and len(v) > 0]
        vendor_tags = [k + ':' + str(v) for k, v in unescape(product).items() if ':' not in str(v)]
        output = {}

        for field, target in targets:
            if target is None or not translations[target]:
                logger.info('Translation %s is disabled for vendor %s', target, vendor_name)
                output[field] = {}
                continue

            image_tags = self.translate_image_attrs(target, source_tags)
            non_image_tags = self.translate_non_image_attrs(target, source_tags, vendor_tags)

            # tags => attr-vals
            output[field] = {'image_tags': tags_to_dict(image_tags),
                             'non_image_tags': tags_to_dict(non_image_tags)}

        return output

    def translate(self, vendor_name, translations, curated_data, product, targets,
                  enable_non_image=True):
        # TODO: fix this to not rely on vendor_name
        source_tags = [k + ':' + v for k, v in curated_data.items() if v is not None and len(v) > 0]
        vendor_tags = [k + ':' + str(v) for k, v in unescape(product).items() if ':' not in str(v)]
        output = {}

        for field, target in targets:
            if target is None or not translations[target]:
                logger.info('Translation %s is disabled for vendor %s', target, vendor_name)
                output[field] = {}
                continue

            target_tags = self.translate_image_attrs(target, source_tags)
            if enable_non_image:
                target_tags.extend(self.translate_non_image_attrs(target, source_tags, vendor_tags))

            logger.info(target_tags)
            # tags => attr-vals
            output1 = tags_to_dict(target_tags)
            logger.info(output1)
            output[field] = output1

        return output

    def translate_and_save(self, vendor_name, style_codes):
        vendor_db = get_db_name(vendor_name)
        coll1 = self.client[vendor_db][self.products_coll]
        coll2 = self.client[vendor_db][self.cataloging_coll]
        translations = self.get_translations(vendor_name)
        targets = [(self.brand_target, self.brand_ontologies.get(vendor_name, self.default_brand_ontology))] + \
                  list(self.targets.items())
        for style_code in style_codes:
            logger.info(style_code)
            product = {}
            curated = {}
            for row in coll1.find({'_id': style_code}):
                product = row
            for row in coll2.find({'_id': style_code}):
                curated = row

            output = self.translate(vendor_name, translations, curated.get('data', {}), product, targets)
            output.update({'StyleCode': style_code, 'RequestID': product.get('RequestID', None)})
            coll2.update({'_id': style_code}, {'$set': {'output': escape(output)}}, upsert=True)

    def on_push(self, vendor_name, style_code, request_id):
        # only update for first time
        vendor_db = get_db_name(vendor_name)
        now = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
        coll = self.client[vendor_db][self.cataloging_coll]
        coll.update({'_id': style_code, 'pushed': False}, {'$set': {'pushed': True, 'push_time': now}})

        # mark existing issues as resolved while pushing
        coll2 = self.client[vendor_db][self.rejects_coll]
        result = coll2.update_many({'StyleCode': style_code, 'resolved': False},
                                   {'$set': {'resolved': True, 'resolution_time': now}})
        logger.info('marked resolved : %s %s matched=%d modified=%d', vendor_name, style_code, result.matched_count,
                    result.modified_count)

        # send Slack message
        view_url = 'http://cataloging.streamoid.com/api/autoscribe/abfrl/view/%s/%s' % (vendor_name, style_code)
        post_to_slack(vendor_db, request_id, view_url)

    def _post_to_client(self, vendor_name, output):
        # https://db.tools.streamoid.com/feed/insecure/get_data/?path=/autoscribe/v_peter_england_autoscribe_2020_06_01_08_22_16.json
        # Trigger unknown!
        if vendor_name in ['people']:
            url = "https://jnrmmg2h5m.execute-api.ap-southeast-1.amazonaws.com/testing/scribe"
        else:
            url = "https://jnrmmg2h5m.execute-api.ap-southeast-1.amazonaws.com/production/scribe"

        # reviewed in cataloging
        path = datetime.utcnow().strftime('%Y/%m/%d/%H/%M/%S')
        headers = {'x-api-key': 'U6BX0yOhtP6JBXOaPwkRm5LqmVR1soqW4k9rJmv0'}
        style_code = output['StyleCode']
        data = json.dumps([output])
        requestid = output['RequestID']
        if requestid is None:
            logger.warning('StyleCode %s has no RequestID', style_code)
            return
        params = {'requestid': requestid, 'type': 'update', 'path': path}
        logger.info(url)
        logger.info(params)
        logger.info(data)
        r = requests.post(url, params=params, data=data, headers=headers, timeout=60)

        if r.ok:
            res = r.text
            logger.info('%s %s', style_code, res)
            self.on_push(vendor_name, style_code, requestid)
        else:
            logger.error('%s %s', r.status_code, r.content)

    def post_to_client(self, vendor_name):
        for output in self.get_products_to_post(vendor_name):
            try:
                self._post_to_client(vendor_name, output)
            except Exception as e:
                logger.exception('%s %s', output, str(e))

    def export_rejected_products(self, vendor_name):
        style_codes = self.get_rejected_products(vendor_name)
        logger.info(style_codes)
        vendor_db = get_db_name(vendor_name)
        products = self.client[vendor_db][self.products_coll]
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

    def mark_reviewed(self, vendor_name, style_code):
        vendor_db = get_db_name(vendor_name)
        coll = self.client[vendor_db][self.cataloging_coll]
        coll.update({'_id': style_code}, {'$set': {'reviewed': True}})

    def import_data(self, vendor_name, data):
        # find max batch size in products
        vendor_db = get_db_name(vendor_name)
        coll1 = self.client[vendor_db][self.products_coll]
        batch_number = 1
        for row in coll1.find({'batch_number': {'$exists': True}}) \
                .sort([('batch_number', DESCENDING)]).limit(1):
            batch_number = row['batch_number']

        logger.info('batch_number : %d', batch_number)

        coll2 = self.client[vendor_db][self.cataloging_coll]
        style_codes = []
        for idx, row in data.iterrows():
            row1 = {}
            data = dict(row)
            for field in ['product_id', 'image_id', 'image_url', 'product_code']:
                row1[field] = data.pop(field)
            style_code = row1.pop('product_code')
            row1['data'] = data
            row1['_id'] = style_code
            row1['reviewed'] = False
            row1['pushed'] = False
            coll2.update({'_id': style_code}, row1, upsert=True)

            style_codes.append(style_code)
            # update product batch number, if not exists
            coll1.update({'_id': style_code, 'batch_number': {'$exists': False}},
                         {'$set': {'batch_number': batch_number}})

        logger.info('translating...')
        logger.info(style_codes)
        self.translate_and_save(vendor_name, style_codes)

        return batch_number, style_codes

    def save_live(self, vendor_name, style_code, output):
        vendor_db = get_db_name(vendor_name)
        coll = self.client[vendor_db][self.live_coll]
        coll.update({'_id': style_code}, {'$set': {'data': output}}, upsert=True)

    def get_live(self, vendor_name, style_code):
        vendor_db = get_db_name(vendor_name)
        coll = self.client[vendor_db][self.live_coll]
        for row in coll.find({'_id': style_code}):
            return row.get('data', {})
