import logging
import os
import sys

logger = logging.getLogger(__name__)
logging.basicConfig(
    format=
    '%(asctime)s - %(process)d - %(name)s - %(filename)s:%(lineno)d - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    level=logging.INFO)

import json
import pandas as pd
import zipfile
from math import log10
from datetime import datetime
from importlib import import_module

import werkzeug
import werkzeug.exceptions
from werkzeug.routing import Map, Rule
from werkzeug.wrappers import Request, Response
from jinja2 import Environment, FileSystemLoader

from time import time
from uuid import uuid4
from io import StringIO, BytesIO
from glob import glob

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from client_autoscribe.client_autoscribe_db_v2 import get_db_name, unescape
from client_autoscribe.reporting_v2 import ClientAutoscribeReporting
from client_autoscribe.client_autoscribe_config import app, rpa_dir
from client_autoscribe.teams_integration import generate_product_card, generate_reject_card, post_to_teams


def get_priority(length):
    return max(0, min(int(round(9 - log10(length + 1))), 9))


def create_csv(out):
    df = pd.DataFrame(out, dtype=str)
    f = StringIO()
    df.to_csv(f, index=False)
    return f.getvalue()


def create_csv_or_zip(out, product_id):
    bs = 500
    if len(out) < bs:
        data = create_csv(out)
        return Response(data, mimetype='text/csv')

    products = {}
    for row in out:
        pid = row[product_id]
        if pid not in products:
            products[pid] = []
        products[pid].append(row)
    pids = list(products.keys())

    # split into archive
    f = BytesIO()
    with zipfile.ZipFile(f, 'w') as zf:
        for i in range(0, len(pids), bs):
            pids1 = pids[i:i + bs]
            out1 = [row for pid in pids1 for row in products[pid]]
            data = create_csv(out1)
            zf.writestr('products_%s.csv' % i, data)

    return Response(f.getvalue(), mimetype='application/zip')


def has_spl_chars(desc):
    for c1 in desc:
        if not (0 <= ord(c1) <= 127):
            return True
    return False


def check_descriptions(fpath):
    df = pd.read_excel(fpath, keep_default_na=False)
    warnings = []
    for field in ['OpeningLine', 'StyleTip']:
        for idx, line in enumerate(df[field].tolist()):
            if has_spl_chars(line):
                warnings.append({'field': field, 'line': idx + 1, 'text': line})
    return warnings


class ClientAutoscribeAPI(ClientAutoscribeReporting):
    def __init__(self, mongo_host):
        super().__init__(mongo_host)
        prefix = '/api/autoscribe/'
        self.url_map = Map(
            [
                # vendors
                Rule('%s/vendors' % prefix, endpoint='vendors_get', methods=['GET']),
                Rule('%s/vendors/<string:vendor_name>/config' % prefix,
                     endpoint='vendor_config_get', methods=['GET']),
                Rule('%s/vendors/<string:vendor_name>/config' % prefix,
                     endpoint='vendor_config_post', methods=['POST']),

                Rule('%s/vendors/<string:vendor_name>/dashboard' % prefix,
                     endpoint='dashboard_get', methods=['GET']),

                # brands
                Rule('%s/vendors/<string:vendor_name>/brands' % prefix,
                     endpoint='brands_get', methods=['GET']),
                Rule('%s/vendors/<string:vendor_name>/brands/<string:brand_name>/config' % prefix,
                     endpoint='brand_config_get', methods=['GET']),
                Rule('%s/vendors/<string:vendor_name>/brands/<string:brand_name>/config' % prefix,
                     endpoint='brand_config_post', methods=['POST']),

                # Rule('%s/vendors/<string:vendor_name>/brands/<string:brand_name>/config.html' % prefix,
                #      endpoint='brand_config_html_get', methods=['GET']),
                # Rule('%s/vendors/<string:vendor_name>/brands/<string:brand_name>/config.html' % prefix,
                #      endpoint='brand_config_html_post', methods=['POST']),

                # To be deprecated: only required for autoscribe-listener
                Rule('%s/vendors/<string:vendor_name>/tokens' % prefix,
                     endpoint='vendor_tokens_get', methods=['GET']),

                # custom rules: at brand level
                Rule('%s/vendors/<string:vendor_name>/rules' % prefix,
                     endpoint='rules_get', methods=['GET']),
                Rule('%s/vendors/<string:vendor_name>/rules' % prefix,
                     endpoint='rules_post', methods=['POST']),
                Rule('%s/vendors/<string:vendor_name>/brands/<string:brand_name>/rules/<string:rules_type>' % prefix,
                     endpoint='rules_type_get', methods=['GET']),

                # product push: vendor specific formats
                Rule('%s/vendors/<string:vendor_name>/brands/<string:brand_name>/post' % prefix,
                     endpoint='product_post', methods=['POST']),
                Rule(
                    '%s/vendors/<string:vendor_name>/brands/<string:brand_name>/products/<string:style_code>/data' % prefix,
                    endpoint='product_data_get', methods=['GET']),
                Rule(
                    '%s/vendors/<string:vendor_name>/brands/<string:brand_name>/products/<string:style_codes>/multiple-data' % prefix,
                    endpoint='multiple_product_data_get', methods=['GET']),
                Rule(
                    '%s/vendors/<string:vendor_name>/brands/<string:brand_name>/products' % prefix,
                    endpoint='brand_products_get', methods=['GET']),

                # products bulk push
                Rule('%s/vendors/<string:vendor_name>/brands/<string:brand_name>/upload' % prefix,
                     endpoint='products_upload_get', methods=['GET']),
                Rule('%s/vendors/<string:vendor_name>/brands/<string:brand_name>/upload' % prefix,
                     endpoint='products_upload_post', methods=['POST']),

                # export by style_codes
                # Rule('%s/vendors/<string:vendor_name>/brands/<string:brand_name>/export' % prefix,
                #      endpoint='export_get', methods=['GET']),

                # export new
                Rule('%s/vendors/<string:vendor_name>/brands/<string:brand_name>/export' % prefix,
                     endpoint='export_new_get', methods=['GET']),
                Rule('%s/vendors/<string:vendor_name>/brands/<string:brand_name>/export-product-csv' % prefix,
                     endpoint='export_product_csv', methods=['GET']),
                Rule('%s/vendors/<string:vendor_name>/brands/<string:brand_name>/export-pushed' % prefix,
                     endpoint='export_pushed_get', methods=['GET']),
                Rule('%s/vendors/<string:vendor_name>/brands/<string:brand_name>/export-precomputed' % prefix,
                     endpoint='export_new_precomp_get', methods=['GET']),
                
                Rule('%s/vendors/<string:vendor_name>/brands/<string:brand_name>/export-product-status' % prefix,
                     endpoint='export_product_status', methods=['GET']),
                Rule('%s/vendors/<string:vendor_name>/brands/<string:brand_name>/export-product-status-catalogix' % prefix,
                     endpoint='export_product_status_catalogix', methods=['GET']),

                # retry live
                Rule('%s/vendors/<string:vendor_name>/brands/<string:brand_name>/retry-live' % prefix,
                     endpoint='retry_live_get', methods=['GET']),

                # import from cataloging
                Rule('%s/vendors/<string:vendor_name>/brands/<string:brand_name>/import' % prefix,
                     endpoint='import_data_post', methods=['POST']),
                Rule(
                    '%s/vendors/<string:vendor_name>/brands/<string:brand_name>/products/<string:style_code>/curated' % prefix,
                    endpoint='product_curated_get', methods=['GET']),
                

                # review
                Rule('%s/vendors/<string:vendor_name>/brands/<string:brand_name>/review' % prefix,
                     endpoint='review_products_get', methods=['GET']),
                Rule('%s/vendors/<string:vendor_name>/brands/<string:brand_name>/review-all' % prefix,
                     endpoint='review_all_products_get', methods=['GET']),
                Rule(
                    '%s/vendors/<string:vendor_name>/brands/<string:brand_name>'
                    '/products/<string:style_code>/mark-reviewed' % prefix,
                    endpoint='mark_reviewed_post', methods=['POST']),
                Rule(
                    '%s/vendors/<string:vendor_name>/brands/<string:brand_name>'
                    '/mark-reviewed-bulk' % prefix,
                    endpoint='mark_reviewed_bulk_get', methods=['GET']),

                # push to client
                Rule('%s/vendors/<string:vendor_name>/brands/<string:brand_name>/export-reviewed' % prefix,
                     endpoint='export_reviewed_get', methods=['GET']),
                Rule('%s/vendors/<string:vendor_name>/brands/<string:brand_name>/export-reviewed-catalogix' % prefix,
                     endpoint='export_reviewed_catalogix_get', methods=['GET']),
                Rule('%s/vendors/<string:vendor_name>/brands/<string:brand_name>/export-rpa' % prefix,
                     endpoint='export_rpa_get', methods=['GET']),
                Rule('%s/vendors/<string:vendor_name>/brands/<string:brand_name>/post-to-client' % prefix,
                     endpoint='post_to_client_get', methods=['GET']),
                Rule('%s/vendors/<string:vendor_name>/brands/<string:brand_name>/mark-pushed' % prefix,
                     endpoint='mark_pushed_get', methods=['GET']),
                Rule('%s/vendors/<string:vendor_name>/brands/<string:brand_name>/reset-pushed' % prefix,
                     endpoint='reset_push_status', methods=['GET']),

                # rejections
                # Rule('%s/vendors/<string:vendor_name>/brands/<string:brand_name>/reject' % prefix,
                #      endpoint='reject_get', methods=['GET']),
                Rule('%s/vendors/<string:vendor_name>/brands/<string:brand_name>/reject' % prefix,
                     endpoint='reject_post', methods=['POST']),
                Rule(
                    '%s/vendors/<string:vendor_name>/brands/<string:brand_name>/products/<string:style_code>/rejects' % prefix,
                    endpoint='rejects_data_get', methods=['GET']),
                Rule('%s/vendors/<string:vendor_name>/brands/<string:brand_name>/export-rejected' % prefix,
                     endpoint='export_rejected_get', methods=['GET']),

                # view final data
                Rule(
                    '%s/vendors/<string:vendor_name>/brands/<string:brand_name>/products/<string:style_code>/view' % prefix,
                    endpoint='view_get', methods=['GET']),
                
                Rule('%s/<string:service>/vendors/<string:vendor_name>/tracking' % prefix,
                     endpoint='track_vendor', methods=['GET']),

                Rule('%s/<string:service>/vendors/<string:vendor_name>/<string:brand_name>/csv_data' % prefix,
                     endpoint='get_brand_data', methods=['GET']),

                Rule('%s/vendors/<string:vendor_name>/csv_data' % prefix,
                     endpoint='get_product_data', methods=['GET']),

                #Catalogix -----------------------------------------------------

                #dashboard
                Rule('%s/vendors/<string:vendor_name>/catalogix-dashboard' % prefix,
                     endpoint='catalogix_dashboard_get', methods=['GET']),

                Rule('%s/vendors/<string:vendor_name>/brands/<string:brand_name>/export-catalogix' % prefix,
                     endpoint='export_catalogix_get', methods=['GET']),

                Rule('%s/vendors/<string:vendor_name>/brands/<string:brand_name>/export-only-new-catalogix' % prefix,
                     endpoint='export_only_new_catalogix_get', methods=['GET']),
                
                Rule('%s/vendors/<string:vendor_name>/brands/<string:brand_name>/export-recurated' % prefix,
                     endpoint='export_recurated_get', methods=['GET']),

                # import from catalogix
                Rule('%s/vendors/<string:vendor_name>/brands/<string:brand_name>/import-catalogix' % prefix,
                     endpoint='import_catalogix_data_post', methods=['POST']),

                # view final data
                Rule(
                    '%s/vendors/<string:vendor_name>/brands/<string:brand_name>/products/<string:style_code>/catalogix-view' % prefix,
                    endpoint='view_catalogix_get', methods=['GET']),

                #Catalogix-review
                Rule('%s/vendors/<string:vendor_name>/brands/<string:brand_name>/catalogix-review' % prefix,
                     endpoint='review_catalogix_products_get', methods=['GET']),
                Rule('%s/vendors/<string:vendor_name>/brands/<string:brand_name>/catalogix-review-all' % prefix,
                     endpoint='review_all_catalogix_products_get', methods=['GET']),
                Rule(
                    '%s/vendors/<string:vendor_name>/brands/<string:brand_name>'
                    '/products/<string:style_code>/catalogix-mark-reviewed' % prefix,
                    endpoint='catalogix_mark_reviewed_post', methods=['POST']),
                Rule(
                    '%s/vendors/<string:vendor_name>/brands/<string:brand_name>'
                    '/catalogix-mark-reviewed-bulk' % prefix,
                    endpoint='catalogix_mark_reviewed_bulk_get', methods=['GET']),
                # catalogix - hold
                Rule(
                    '%s/vendors/<string:vendor_name>/brands/<string:brand_name>'
                    '/products/<string:style_code>/catalogix-hold' % prefix,
                    endpoint='catalogix_hold_post', methods=['POST']),
                Rule(
                    '%s/vendors/<string:vendor_name>/brands/<string:brand_name>'
                    '/products/<string:style_code>/catalogix-unhold' % prefix,
                    endpoint='catalogix_unhold_post', methods=['POST']),
                
                #push to client
                Rule('%s/vendors/<string:vendor_name>/brands/<string:brand_name>/catalogix-post-to-client' % prefix,
                     endpoint='catalogix_post_to_client_get', methods=['GET']),

                #---------------------------------------------------------------------------------------

                # translate
                # TODO: minimize and simplify translation endpoints
                # old translation endpoint (JSON output)
                Rule('%s/vendors/<string:vendor_name>/brands/<string:brand_name>/translate' % prefix,
                     endpoint='translate_post', methods=['POST']),
                Rule('%s/vendors/<string:vendor_name>/brands/<string:brand_name>/translate-fabric' % prefix,
                     endpoint='translate_fabric_post', methods=['POST']),
                # new translation endpoint with image, non-image split (JSON output)
                Rule('%s/vendors/<string:vendor_name>/brands/<string:brand_name>/translate-split' % prefix,
                     endpoint='translate_split_post', methods=['POST']),

                # translate based on vendor+brand config
                Rule('%s/vendors/<string:vendor_name>/brands/<string:brand_name>/translate-csv' % prefix,
                     endpoint='translate_csv_get', methods=['GET']),
                Rule('%s/vendors/<string:vendor_name>/brands/<string:brand_name>/translate-csv' % prefix,
                     endpoint='translate_csv_post', methods=['POST']),

                # translate based on ontology only
                Rule('%s/translate-csv/ontology/<string:ontology>' % prefix,
                     endpoint='translate_csv_ontology_get', methods=['GET']),
                Rule('%s/translate-csv/ontology/<string:ontology>' % prefix,
                     endpoint='translate_csv_ontology_post', methods=['POST']),

                # report
                Rule('%s/vendors/<string:vendor_name>/report/<string:date>' % prefix,
                     endpoint='report_for_date_get', methods=['GET']),
                Rule('%s/vendors/<string:vendor_name>/report' % prefix,
                     endpoint='report_get', methods=['GET']),
                Rule('%s/vendors/<string:vendor_name>/report' % prefix,
                     endpoint='report_post', methods=['POST']),

                # monthly received summary
                Rule('%s/vendors/<string:vendor_name>/received-summary' % prefix,
                     endpoint='received_get', methods=['GET']),
                Rule('%s/vendors/<string:vendor_name>/received-summary' % prefix,
                     endpoint='received_post', methods=['POST']),

                # manage
                # Rule('%s/vendors/<string:vendor_name>/create-brand' % prefix,
                #      endpoint='create_brand_get', methods=['GET']),
                # Rule('%s/vendors/<string:vendor_name>/create-brand' % prefix,
                #      endpoint='create_brand_post', methods=['POST']),

                # RPA
                Rule('%s/vendors/<string:vendor_name>/brands/<string:brand_name>/rpa-trigger' % prefix,
                     endpoint='rpa_trigger_get', methods=['GET']),
                Rule('%s/vendors/<string:vendor_name>/brands/<string:brand_name>/rpa-status' % prefix,
                     endpoint='rpa_status_get', methods=['GET']),
                Rule('%s/vendors/<string:vendor_name>/brands/<string:brand_name>/callback' % prefix,
                     endpoint='rpa_callback'),
                Rule('%s/vendors/<string:vendor_name>/brands/<string:brand_name>'
                     '/rpa-status/<string:rpa_request_id>/update' % prefix,
                     endpoint='rpa_update_get', methods=['GET']),
                Rule('%s/vendors/<string:vendor_name>/brands/<string:brand_name>'
                     '/rpa-status/<string:rpa_request_id>/download' % prefix,
                     endpoint='rpa_download_get', methods=['GET']),

                # pantaloons description update
                Rule('%s/vendors/<string:vendor_name>/brands/pantaloons/files/descriptions' % prefix,
                     endpoint='descriptions_get', methods=['GET']),
                Rule('%s/vendors/<string:vendor_name>/brands/pantaloons/files/descriptions' % prefix,
                     endpoint='descriptions_post', methods=['POST']),
                Rule('%s/post-to-catalogix' % prefix,
                     endpoint='post_to_catalogix_html', methods=['GET']),
                
                Rule('%s/post-to-catalogix' % prefix,
                     endpoint='post_to_catalogix', methods=['POST']),
            ],
            redirect_defaults=False,
            strict_slashes=False)

        self.jinja_env = Environment(loader=FileSystemLoader(os.path.join(os.path.dirname(__file__), 'templates')),
                                     autoescape=True)
        self.archived_brands = ['v_planet_fashion', 'v_pantaloons']

    def dispatch_request(self, request):
        """Indirection layer which gives us automatic 404 for unknown paths.
        """
        adapter = self.url_map.bind_to_environ(request.environ)
        try:
            endpoint, values = adapter.match()
            return getattr(self, endpoint)(request, **values)
        except werkzeug.exceptions.HTTPException as e:
            return e

    def __call__(self, environ, start_response):
        """Implementation of the standard wsgi callable interface."""
        response = self.dispatch_request(Request(environ))
        return response(environ, start_response)

    # application specific code below
    def vendors_get(self, request):
        data = self.list_vendors()
        return Response(json.dumps(data), mimetype='application/json')

    def vendor_config_get(self, request, vendor_name):
        vendor_config = self.get_vendor_config(vendor_name)
        if vendor_config is None:
            response = {'code': -1, 'status': 'Invalid vendor!'}
            return Response(json.dumps(response), mimetype='application/json')

        return Response(json.dumps(vendor_config), mimetype='application/json')

    def vendor_config_post(self, request, vendor_name):
        try:
            data = json.loads(request.get_data())
        except Exception as e:
            output = {'code': -1, 'status': str(e)}
            return Response(json.dumps(output), mimetype='application/json')

        self.set_vendor_config(vendor_name, data)
        return Response(json.dumps(data), mimetype='application/json')

    def brands_get(self, request, vendor_name):
        vendor_config = self.get_vendor_config(vendor_name)
        if vendor_config is None:
            response = {'code': -1, 'status': 'Invalid vendor!'}
            return Response(json.dumps(response), mimetype='application/json')

        data = self.list_brands(vendor_name)
        return Response(json.dumps(data), mimetype='application/json')

    def brand_config_get(self, request, vendor_name, brand_name):
        vendor_config = self.get_vendor_config(vendor_name)
        if vendor_config is None:
            response = {'code': -1, 'status': 'Invalid vendor!'}
            return Response(json.dumps(response), mimetype='application/json')

        config = self.get_brand_config(vendor_name, brand_name)
        if config is None:
            response = {'code': -1, 'status': 'Invalid brand!'}
            return Response(json.dumps(response), mimetype='application/json')
        return Response(json.dumps(config), mimetype='application/json')

    def brand_config_post(self, request, vendor_name, brand_name):
        vendor_config = self.get_vendor_config(vendor_name)
        if vendor_config is None:
            response = {'code': -1, 'status': 'Invalid vendor!'}
            return Response(json.dumps(response), mimetype='application/json')

        try:
            data = json.loads(request.get_data())
        except Exception as e:
            output = {'code': -1, 'status': str(e)}
            return Response(json.dumps(output), mimetype='application/json')

        self.set_brand_config(vendor_name, brand_name, data)
        return Response(json.dumps(data), mimetype='application/json')

    def rules_get(self, request, vendor_name):
        vendor_config = self.get_vendor_config(vendor_name)
        if vendor_config is None:
            response = {'code': -1, 'status': 'Invalid vendor!'}
            return Response(json.dumps(response), mimetype='application/json')

        ontologies = self.get_ontologies()
        if ontologies is None:
            response = {'code': -1, 'status': 'Failed to fetch ontologies!'}
            return Response(json.dumps(response), mimetype='application/json')

        brands = self.list_brands(vendor_name)
        brands.insert(0, '')
        t = self.jinja_env.get_template('rules_v2.html')
        return Response(t.render(ontologies=ontologies, brands=brands), mimetype='text/html')

    def rules_type_get(self, request, vendor_name, brand_name, rules_type):
        vendor_config = self.get_vendor_config(vendor_name)
        if vendor_config is None:
            response = {'code': -1, 'status': 'Invalid vendor!'}
            return Response(json.dumps(response), mimetype='application/json')

        config = self.get_brand_config(vendor_name, brand_name)
        if config is None:
            response = {'code': -1, 'status': 'Invalid brand!'}
            return Response(json.dumps(response), mimetype='application/json')

        ontology = config['brand_ontology']
        rules = self.custom_rules_db.get_rules(vendor_name, ontology, rules_type, brand_name)
        response = {'status': {'code': 0, 'message': 'OK'}, 'data': rules}
        return Response(json.dumps(response), mimetype='application/json')

    def rules_post(self, request, vendor_name):
        vendor_config = self.get_vendor_config(vendor_name)
        if vendor_config is None:
            response = {'code': -1, 'status': 'Invalid vendor!'}
            return Response(json.dumps(response), mimetype='application/json')

        try:
            file = request.files['file']
            rules_type = request.form['type']
            ontology = request.form['ontology']
            brand_name = request.form.get('brand_name', None)
        except Exception as e:
            logger.exception(str(e))
            response = {'status': {'code': -1, 'message': str(e)}}
            return Response(json.dumps(response), mimetype='application/json')

        brands = self.get_brand_names(vendor_name)
        if brand_name is not None and brand_name not in brands:
            logger.error('Removing invalid brand: %s', brand_name)
            brand_name = None

        if brand_name is not None:
            config = self.get_brand_config(vendor_name, brand_name)
            brand_ontology = config['brand_ontology']
            if ontology != brand_ontology:
                logger.error('Removing non-brand: %s ; %s != %s', brand_name, ontology, brand_ontology)
                brand_name = None

        logger.info(brand_name)
        vendor_config = self.get_vendor_config(vendor_name)
        curation_ontology = vendor_config['curation_ontology']
        all_tags = self.get_all_tags(curation_ontology)
        if all_tags is None:
            response = {'status': {'code': -1, 'message': 'Failed to fetch ontology tags!'}}
            return Response(json.dumps(response), mimetype='application/json')

        if rules_type == 'fabric':
            errors = self.custom_rules_db.parse_fabric_sheets(file, vendor_name, ontology,
                                                              all_tags, brand_name)
        else:
            errors = self.custom_rules_db.parse_sheets(file, vendor_name, ontology,
                                                       all_tags, brand_name)
        if len(errors) > 0:
            response = {'status': {'code': -1, 'message': 'Errors'}, 'errors': errors}
        else:
            response = {'status': {'code': 0, 'message': 'OK'}}
        return Response(json.dumps(response), mimetype='application/json')

    def _check_vendor_token(self, request, vendor_name, brand_name):
        data = {}
        request_token = request.headers.get('vendor-token', None)
        logger.info('%s %s %s', vendor_name, brand_name, request_token)
        brand_tokens = self.get_brand_tokens(vendor_name)
        if brand_name not in brand_tokens:
            data['status'] = {'code': 3, 'message': 'vendor not found'}
            logger.error(data)
            return data

        token = brand_tokens.get(brand_name, None)
        if token is None:
            return data

        if request_token != token or request_token is None:
            data['status'] = {'code': 2, 'message': 'token invalid or not found'}
            logger.error(data)
            return data

        return data
    

    def product_get(self, request, vendor_name):
        t = self.jinja_env.get_template('product.html')
        return Response(t.render(), mimetype='text/html')

    def product_post(self, request, vendor_name, brand_name):
        vendor_config = self.get_vendor_config(vendor_name)
        if vendor_config is None:
            response = {'code': -1, 'status': 'Invalid vendor!'}
            return Response(json.dumps(response), mimetype='application/json')

        config = self.get_brand_config(vendor_name, brand_name)
        if config is None:
            response = {'code': -1, 'status': 'Invalid brand!'}
            return Response(json.dumps(response), mimetype='application/json')

        # 1. check vendor token
        data = self._check_vendor_token(request, vendor_name, brand_name)
        if len(data) > 0:
            logger.error(data)
            return Response(json.dumps(data), mimetype='application/json')

        # 2. convert response to internal format and save to DB
        response = {'status': {'code': 0, 'message': 'success'},
                    'data': {'details': {}, 'style_code': 0}}

        try:
            logger.info(request.form)
            data = json.loads(request.form['data'])
        except Exception as e:
            logger.exception(str(e))
            response['status']['code'] = -1
            response['status']['message'] = 'Could not parse `data` as json. Please send valid data.'
            return Response(json.dumps(response), mimetype='application/json')

        try:
            module = import_module('client_autoscribe.integrations.%s' % vendor_name)
            adapter = module.VendorAdapter(vendor_config, config)
            style_codes = adapter.convert_input(data)

            vendor_db = get_db_name(vendor_name)
            details = self.save_products(vendor_db, brand_name, style_codes)
            valid_style_codes = [k for k, v in details.items() if v.get('code', -1) > -1]
            response['data']['details'] = details
            response['data']['style_code'] = len(details)
        except Exception as e:
            logger.exception(str(e))
            response['status']['code'] = -1
            response['status']['message'] = 'No data with styleCode found!!'
            return Response(json.dumps(response), mimetype='application/json')

        # trigger pre-computation
        try:
            queue = 'client_autoscribe_v2_misc'
            self._trigger_precompute(vendor_name, brand_name, valid_style_codes, queue)
        except Exception as e:
            logger.exception(str(e))
        # real-time push to catalogix
        #Not Using it now due to mapping mails
        # try:
        #     # TODO: generic logic for queue selection
        #     task2 = app.send_task('client_autoscribe_worker_v2.trigger_push_catalogix',
        #                      (vendor_name, brand_name, style_codes), queue='client_autoscribe_v2_abfrl')
        #     logger.info('Triggered catalogix push: %s', task2.task_id)
        # except Exception as e:
        #     logger.exception(str(e))
        
        logger.info(response)
        return Response(json.dumps(response), mimetype='application/json')

    def _trigger_precompute(self, vendor_name, brand_name, style_codes, queue, post_msg=True):
        priority = get_priority(len(style_codes))
        task = app.send_task('client_autoscribe_worker_v2.trigger_precompute',
                             (vendor_name, brand_name, style_codes, queue, post_msg), queue=queue, priority=priority)
        logger.info('Triggered pre-compute: %s', task.task_id)
        
    def _trigger_precompute_old(self, vendor_name, brand_name, style_codes, queue, post_msg=True):
        logger.info('Triggering pre-processing for cataloging: %s', style_codes)
        for style_code in style_codes:
            row = self.get_product(vendor_name, brand_name, style_code)
            if row is None:
                logger.warning('Invalid style code: %s', style_code)
                continue
            logger.info(row)
            style_code = row['StyleCode']
            # post to Teams before trigger
            request_id = row.get('RequestID', None)
            last_modified = row.get('last_modified', None)
            if post_msg:
                try:
                    card_data = generate_product_card(vendor_name, brand_name, style_code, request_id, last_modified)
                    teams_url = self.teams_urls.get(vendor_name, {}).get('post', None)
                    if teams_url is not None:
                        post_to_teams(teams_url, card_data)
                except Exception as e:
                    logger.exception(str(e))
            if 'ImageURLs' not in row:
                logger.warning('Missing image URLs: %s', style_code)
                continue
            image_urls = row['ImageURLs']
            if isinstance(image_urls, str):
                image_urls = image_urls.split(',')
            task = app.send_task('client_autoscribe_worker_v2.precompute_and_save',
                                 (vendor_name, brand_name, style_code, image_urls),
                                 queue=queue)
            logger.info('Triggered pre-compute: %s', task.task_id)

    def product_data_get(self, request, vendor_name, brand_name, style_code):
        data = self.get_product(vendor_name, brand_name, style_code)
        if data is None:
            response = {'status': {'code': -1, 'message': 'Product not found!'}}
            return Response(json.dumps(response), mimetype='application/json')

        response = {'status': {'code': 0, 'message': 'OK'}, 'data': data}
        return Response(json.dumps(response), mimetype='application/json')

    def multiple_product_data_get(self, request, vendor_name, brand_name, style_codes):
        data = self.get_multiple_product(vendor_name, brand_name, style_codes)
        if data is None:
            response = {'status': {'code': -1, 'message': 'Product not found!'}}
            return Response(json.dumps(response), mimetype='application/json')
        response = {'status': {'code': 0, 'message': 'OK'}, 'Style codes':style_codes.split(','), 'data': data}
        return Response(json.dumps(response), mimetype='application/json')

    def reject_get(self, request, vendor_name, brand_name):
        t = self.jinja_env.get_template('reject.html')
        brands = self.get_brand_names(vendor_name)
        return Response(t.render(vendors=brands), mimetype='text/html')

    def reject_post(self, request, vendor_name, brand_name):
        """{"StyleCode": "ASSFWSPFS94011", "Rejects": [{"Remarks": "No issue", "Channel":
        "Allen Solly"}, {"Remarks": "Pack of should be 1, not 2", "Channel": "Flipkart"},
        {"Remarks": "No issue", "Channel": "Paytm"}, {"Remarks": "No issue", "Channel":
        "TataCliq"}, {"Remarks": "No issue", "Channel": "Amazon"}, {"Remarks": "No issue",
        "Channel": "Myntra"}, {"Remarks": "No issue", "Channel": "Limeroad"}],
        "batch_number": 39, "RequestID": "1196", "product_code": "ASSFWSPFS94011",
        "product_uuid": "decdb0809f3f4f61a07fac93ce5204be"}"""

        # 1. check vendor token
        data = self._check_vendor_token(request, vendor_name, brand_name)
        if len(data) > 0:
            logger.error(data)
            return Response(json.dumps(data), mimetype='application/json')

        # 2. convert response to internal format and save to DB
        vendor_db = get_db_name(vendor_name)
        response = {'data': {}, 'status': {'code': 0, 'message': 'success'}}

        try:
            data = json.loads(request.form['data'])
            logger.info(data)
            response['data'] = self.save_rejects(vendor_db, brand_name, data)
            # trigger Teams notification
            style_code = data['StyleCode']
            card_data = generate_reject_card(vendor_name, brand_name, style_code)
            teams_url = self.teams_urls.get(vendor_name, {}).get('reject', None)
            if teams_url is not None:
                post_to_teams(teams_url, card_data)
        except Exception as e:
            logger.exception(str(e))
            response['status']['code'] = -1
            response['status']['message'] = str(e)
        try:
            # TODO: generic logic for queue selection
            task2 = app.send_task('client_autoscribe_worker_v2.reject_catalogix',
                             (vendor_name, brand_name, data), queue='client_autoscribe_v2_abfrl')
            logger.info('Reject Catalogix Push: %s', task2.task_id)
        except Exception as e:
            logger.exception(f"Error while rejecting products in catalogix. Error: {e}")
        logger.info(response)
        
        return Response(json.dumps(response), mimetype='application/json')

    def rejects_data_get(self, request, vendor_name, brand_name, style_code):
        data = self.get_rejected_product(vendor_name, brand_name, style_code)
        if data is None:
            response = {'status': {'code': -1, 'message': 'Rejects not found!'}}
            return Response(json.dumps(response), mimetype='application/json')

        response = {'status': {'code': 0, 'message': 'OK'}, 'data': data}
        return Response(json.dumps(response), mimetype='application/json')

    # DEPRECATED!
    def export_get(self, request, vendor_name, brand_name):
        vendor_config = self.get_vendor_config(vendor_name)
        if vendor_config is None:
            response = {'code': -1, 'status': 'Invalid vendor!'}
            return Response(json.dumps(response), mimetype='application/json')

        config = self.get_brand_config(vendor_name, brand_name)
        if config is None:
            response = {'code': -1, 'status': 'Invalid brand!'}
            return Response(json.dumps(response), mimetype='application/json')

        style_codes = request.args.get('style_codes', '').split(',')
        out = []
        for style_code in style_codes:
            row = self.get_product(vendor_name, brand_name, style_code)
            if row is None:
                logger.warning('Invalid style code: %s', style_code)
                continue
            logger.info(row)
            if 'ImageURLs' not in row:
                logger.warning('Missing image URLs: %s', row)
                continue
            image_urls = row['ImageURLs']
            if isinstance(image_urls, str):
                image_urls = image_urls.split(',')
            for url in image_urls:
                out.append({'style_code': row['StyleCode'],
                            'image_url': url,
                            'timestamp': row['last_modified']})

        out = sorted(out, key=lambda x: x['timestamp'], reverse=True)
        # logger.info(out)
        df = pd.DataFrame(out)
        f = StringIO()
        df.to_csv(f, index=False)
        return Response(f.getvalue(), mimetype='text/csv')

    def export_product_csv(self, request, vendor_name, brand_name):
        vendor_config = self.get_vendor_config(vendor_name)
        if vendor_config is None:
            response = {'code': -1, 'status': 'Invalid vendor!'}
            return Response(json.dumps(response), mimetype='application/json')

        config = self.get_brand_config(vendor_name, brand_name)
        if config is None:
            response = {'code': -1, 'status': 'Invalid brand!'}
            return Response(json.dumps(response), mimetype='application/json')

        style_codes = [x for x in request.args.get('style_codes', '').split(',') if len(x) > 0]
        if len(style_codes) < 1:
            style_codes = self.get_new_products(vendor_name, brand_name)
        out = []
        for style_code in style_codes:
            row = self.get_product(vendor_name, brand_name, style_code)
            if row is None:
                logger.warning('Invalid style code: %s', style_code)
                continue
            out.append(row)

        # out = sorted(out, key=lambda x: x['timestamp'], reverse=True)
        return create_csv_or_zip(out, 'style_code')



    def export_new_get(self, request, vendor_name, brand_name):
        vendor_config = self.get_vendor_config(vendor_name)
        if vendor_config is None:
            response = {'code': -1, 'status': 'Invalid vendor!'}
            return Response(json.dumps(response), mimetype='application/json')

        config = self.get_brand_config(vendor_name, brand_name)
        if config is None:
            response = {'code': -1, 'status': 'Invalid brand!'}
            return Response(json.dumps(response), mimetype='application/json')

        style_codes = [x for x in request.args.get('style_codes', '').split(',') if len(x) > 0]
        if len(style_codes) < 1:
            style_codes = self.get_new_products(vendor_name, brand_name)
        out = []
        for style_code in style_codes:
            row = self.get_product(vendor_name, brand_name, style_code)
            if row is None:
                logger.warning('Invalid style code: %s', style_code)
                continue
            # logger.info(row)
            if row.get('ImageURLs', None) is None:
                logger.warning('Missing image URLs')
                continue
            image_urls = row['ImageURLs']
            if isinstance(image_urls, str):
                image_urls = image_urls.split(',')
            if isinstance(image_urls, list):
                for url in image_urls:
                    out.append({'style_code': row['StyleCode'],
                                'image_url': url,
                                'timestamp': row['last_modified']})
            else:
                out.append({'style_code': row['StyleCode'],
                                'image_url': image_urls,
                                'timestamp': row['last_modified']})

        out = sorted(out, key=lambda x: x['timestamp'], reverse=True)
        return create_csv_or_zip(out, 'style_code')

    def export_catalogix_get(self, request, vendor_name, brand_name):
        vendor_config = self.get_vendor_config(vendor_name)
        if vendor_config is None:
            response = {'code': -1, 'status': 'Invalid vendor!'}
            return Response(json.dumps(response), mimetype='application/json')

        config = self.get_brand_config(vendor_name, brand_name)
        if config is None:
            response = {'code': -1, 'status': 'Invalid brand!'}
            return Response(json.dumps(response), mimetype='application/json')

        style_codes = [x for x in request.args.get('style_codes', '').split(',') if len(x) > 0]
        if len(style_codes) < 1:
            style_codes = self.get_new_products(vendor_name, brand_name)
        out = []
        for style_code in style_codes:
            row = self.get_product(vendor_name, brand_name, style_code)
            if row is None:
                logger.warning('Invalid style code: %s', style_code)
                continue
            out.append(row)

        out = sorted(out, key=lambda x: x['last_modified'], reverse=True)
        return create_csv_or_zip(out, 'StyleCode')
    
    def export_only_new_catalogix_get(self, request, vendor_name, brand_name):
        vendor_config = self.get_vendor_config(vendor_name)
        if vendor_config is None:
            response = {'code': -1, 'status': 'Invalid vendor!'}
            return Response(json.dumps(response), mimetype='application/json')

        config = self.get_brand_config(vendor_name, brand_name)
        if config is None:
            response = {'code': -1, 'status': 'Invalid brand!'}
            return Response(json.dumps(response), mimetype='application/json')

        style_codes = [x for x in request.args.get('style_codes', '').split(',') if len(x) > 0]
        if len(style_codes) < 1:
            style_codes = self.get_only_new_products(vendor_name, brand_name)
        out = []
        for style_code in style_codes:
            row = self.get_product(vendor_name, brand_name, style_code)
            if row is None:
                logger.warning('Invalid style code: %s', style_code)
                continue
            out.append(row)

        out = sorted(out, key=lambda x: x['last_modified'], reverse=True)
        return create_csv_or_zip(out, 'StyleCode')
    def export_recurated_get(self, request, vendor_name, brand_name):
        vendor_config = self.get_vendor_config(vendor_name)
        if vendor_config is None:
            response = {'code': -1, 'status': 'Invalid vendor!'}
            return Response(json.dumps(response), mimetype='application/json')

        config = self.get_brand_config(vendor_name, brand_name)
        if config is None:
            response = {'code': -1, 'status': 'Invalid brand!'}
            return Response(json.dumps(response), mimetype='application/json')

        style_codes = [x for x in request.args.get('style_codes', '').split(',') if len(x) > 0]
        if len(style_codes) < 1:
            style_codes = self.get_recurated_products(vendor_name, brand_name)
        out = []
        for style_code in style_codes:
            row = self.get_product(vendor_name, brand_name, style_code)
            if row is None:
                logger.warning('Invalid style code: %s', style_code)
                continue
            out.append(row)

        out = sorted(out, key=lambda x: x['last_modified'], reverse=True)
        return create_csv_or_zip(out, 'StyleCode')

    def export_pushed_get(self, request, vendor_name, brand_name):  # NEW
        vendor_config = self.get_vendor_config(vendor_name)
        if vendor_config is None:
            response = {'code': -1, 'status': 'Invalid vendor!'}
            return Response(json.dumps(response), mimetype='application/json')

        config = self.get_brand_config(vendor_name, brand_name)
        if config is None:
            response = {'code': -1, 'status': 'Invalid brand!'}
            return Response(json.dumps(response), mimetype='application/json')

        products = self.get_products_by_status(vendor_name, brand_name, request_id=None, status='pushed')
        data = [self.get_data(vendor_name, brand_name, style_code) for style_code in products]

        required = ['_id', 'push_time', 'client_response']
        required_days = int(request.args.get('days', 365))
        logger.info(
            f'Requesting push status for vendor:{vendor_name}, brand_name:{brand_name} stylecodes for {required_days} days ...')
        push_details = []
        for idx, dat in enumerate(data):
            if dat is None:
                logger.warning(f'No data found for style-code: {products[idx]}')
                continue
            detail = {key: dat.get(key, None) for key in required}
            # detail['vendor'] = vendor_name
            # detail['brand'] = brand_name
            if detail.get('client_response', None):
                for pushes in detail['client_response']:
                    push_time = datetime.strptime(detail['push_time'], '%Y-%m-%d %H:%M:%S')
                    num_days = (datetime.utcnow() - push_time).days
                    if num_days <= required_days:
                        data = dict()
                        data['vendor'] = vendor_name
                        data['brand'] = brand_name
                        data['style_code'] = detail['_id']

                        pid, resp = tuple(*pushes.items())
                        rid, ts = pid.split(':', 1)
                        data['request_id'] = rid
                        data['time_stamp'] = ts
                        data['response'] = resp
                        push_details.append(data)

                    logger.info('push details: ', detail)

        out = sorted(push_details, key=lambda x: x['time_stamp'], reverse=True)
        logger.info(f'PUSH_STATUS: {out}')
        data = create_csv(out)
        return Response(data, mimetype='text/csv')

    def brand_products_get(self, request, vendor_name, brand_name):  # NEW
        # pagination
        offset = int(request.args.get('offset', '0'))
        limit = int(request.args.get('limit', '10'))

        vendor_config = self.get_vendor_config(vendor_name)
        if vendor_config is None:
            response = {'code': -1, 'status': 'Invalid vendor!'}
            return Response(json.dumps(response), mimetype='application/json')

        config = self.get_brand_config(vendor_name, brand_name)
        if config is None:
            response = {'code': -1, 'status': 'Invalid brand!'}
            return Response(json.dumps(response), mimetype='application/json')

        collection_name = 'cataloging' if request.args.get('curated', None) else 'products'
        style_codes = self.get_all_products(vendor_name, brand_name, collection_name)
        logger.info(f'{len(style_codes)} products found in collection {collection_name}')

        count = len(style_codes)
        style_codes = style_codes[offset:offset + limit]
        response = {'status': {'code': 0, 'message': 'OK'}, 'data': style_codes, 'count': count}
        return Response(json.dumps(response), mimetype='application/json')

    def reset_push_status(self, request, vendor_name, brand_name):
        vendor_config = self.get_vendor_config(vendor_name)
        if vendor_config is None:
            response = {'code': -1, 'status': 'Invalid vendor!'}
            return Response(json.dumps(response), mimetype='application/json')

        config = self.get_brand_config(vendor_name, brand_name)
        if config is None:
            response = {'code': -1, 'status': 'Invalid brand!'}
            return Response(json.dumps(response), mimetype='application/json')

        style_codes = [x for x in request.args.get('style_codes', '').split(',') if len(x) > 0]

        for style_code in style_codes:
            row = self.get_product(vendor_name, brand_name, style_code)
            if row is None:
                response = {'code': -1, 'status': 'Invalid style code: ' + str(style_code)}
                return Response(json.dumps(response), mimetype='application/json')
            self.mark_products_unpushed(vendor_name, brand_name, style_code, pushed=False)

        total = len(style_codes)
        logger.info(f"Reset pushed as False for style codes: {style_codes}")
        response = {'code': 0, 'status': f'Successfully reset status for all {total} style codes'}
        return Response(json.dumps(response), mimetype='application/json')

    def export_product_status(self, request, vendor_name, brand_name):
        vendor_config = self.get_vendor_config(vendor_name)
        if vendor_config is None:
            response = {'code': -1, 'status': 'Invalid vendor!'}
            return Response(json.dumps(response), mimetype='application/json')

        config = self.get_brand_config(vendor_name, brand_name)
        if config is None:
            response = {'code': -1, 'status': 'Invalid brand!'}
            return Response(json.dumps(response), mimetype='application/json')

        style_codes = [x for x in request.args.get('style_codes', '').split(',') if len(x) > 0]
        results = []

        for style_code in style_codes:
            info = {'style_code': style_code}
            data = self.get_product(vendor_name, brand_name, style_code)

            if not data:
                info['status'] = 'not received'
            else:
                curation_status = self.get_product_status(vendor_name, brand_name, style_code)
                info['status'] = curation_status
                info['received_time'] = data.get('last_modified', '')
                info['pushed_time'] = ''
                if curation_status == 'pushed':
                    pushed_data = self.get_data(vendor_name, brand_name, style_code)
                    info['pushed_time'] = pushed_data.get('push_time', '')
                info['time_taken'] = ''
                if info['pushed_time'] != '' and info['received_time'] != '':
                    info['time_taken'] = datetime.strptime(info['pushed_time'], '%Y-%m-%d %H:%M:%S') - datetime.strptime(info['received_time'], '%Y-%m-%d %H:%M:%S')

            results.append(info)

        out = sorted(results, key=lambda x: x.get('pushed_time', ''))
        logger.info('Exporting %d rows', len(out))
        data = create_csv(out)
        return Response(data, mimetype='text/csv')
        # return create_csv_or_zip(out, 'style_code')

    def export_new_precomp_get(self, request, vendor_name, brand_name):
        vendor_config = self.get_vendor_config(vendor_name)
        if vendor_config is None:
            response = {'code': -1, 'status': 'Invalid vendor!'}
            return Response(json.dumps(response), mimetype='application/json')

        config = self.get_brand_config(vendor_name, brand_name)
        if config is None:
            response = {'code': -1, 'status': 'Invalid brand!'}
            return Response(json.dumps(response), mimetype='application/json')

        style_codes = [x for x in request.args.get('style_codes', '').split(',') if len(x) > 0]
        if len(style_codes) < 1:
            style_codes = self.get_new_products_precomputed(vendor_name, brand_name)
        out = []
        logger.info('Exporting %d rows', len(style_codes))
        for style_code in style_codes:
            logger.info(style_code)
            output = self.get_live(vendor_name, brand_name, style_code)
            logger.info(output)
            info = output.get('info', {})
            timestamp = output.get('last_modified', None)
            if timestamp is None:
                product = self.get_product(vendor_name, brand_name, style_code)
                timestamp = product.get('last_modified', None)
            info['timestamp'] = timestamp
            out1 = output.get('data', {}).values()
            for v in out1:
                for vi in v:
                    category = vi['category']
                    gender = vi['gender']
                    vi.update(info)
                    if 'category' in info and info['category'] == 'unknown':
                        vi['category'] = category
                    if 'gender' in info and info['gender'] == 'unknown':
                        vi['gender'] = gender
                    vi['url'] = 'https://storage.googleapis.com/images-cataloging-streamoid-com/images/%s' % vi[
                        'image_id']
                    out.append(vi)

        out = sorted(out, key=lambda x: x['timestamp'], reverse=True)
        logger.info('Exporting %d rows', len(out))
        return create_csv_or_zip(out, 'product_code')

    def retry_live_get(self, request, vendor_name, brand_name):
        vendor_config = self.get_vendor_config(vendor_name)
        if vendor_config is None:
            response = {'code': -1, 'status': 'Invalid vendor!'}
            return Response(json.dumps(response), mimetype='application/json')

        config = self.get_brand_config(vendor_name, brand_name)
        if config is None:
            response = {'code': -1, 'status': 'Invalid brand!'}
            return Response(json.dumps(response), mimetype='application/json')

        style_codes1 = self.get_new_products(vendor_name, brand_name)
        style_codes2 = self.get_new_products_precomputed(vendor_name, brand_name)

        # trigger pre-computation
        valid_style_codes = list(set(style_codes1) - set(style_codes2))
        try:
            queue = 'client_autoscribe_v2_misc'
            self._trigger_precompute(vendor_name, brand_name, valid_style_codes, queue, post_msg=False)
            response = {'code': 0,
                        'status': 'Triggered live for %d products' % len(valid_style_codes)}
        except Exception as e:
            logger.exception(str(e))
            response = {'code': -1, 'status': str(e)}

        return Response(json.dumps(response), mimetype='application/json')

    def export_rejected_get(self, request, vendor_name, brand_name):
        vendor_config = self.get_vendor_config(vendor_name)
        if vendor_config is None:
            response = {'code': -1, 'status': 'Invalid vendor!'}
            return Response(json.dumps(response), mimetype='application/json')

        config = self.get_brand_config(vendor_name, brand_name)
        if config is None:
            response = {'code': -1, 'status': 'Invalid brand!'}
            return Response(json.dumps(response), mimetype='application/json')

        out = self.export_rejected_products(vendor_name, brand_name)
        # logger.info(out)
        df = pd.DataFrame(out)
        f = StringIO()
        df.to_csv(f, index=False)
        return Response(f.getvalue(), mimetype='text/csv')

    def import_data_post(self, request, vendor_name, brand_name):
        vendor_config = self.get_vendor_config(vendor_name)
        if vendor_config is None:
            response = {'code': -1, 'status': 'Invalid vendor!'}
            return Response(json.dumps(response), mimetype='application/json')

        config = self.get_brand_config(vendor_name, brand_name)
        if config is None:
            response = {'code': -1, 'status': 'Invalid brand!'}
            return Response(json.dumps(response), mimetype='application/json')

        if 'file' not in request.files:
            response = {'code': -1, 'message': 'Missing mandatory argument file!'}
            return Response(json.dumps(response), mimetype='application/json')

        optional = config.get('include_optional', False)
        style_codes = []
        missing_codes = []
        valid_columns = ['product_code', 'image_url', 'product_id', 'image_id']
        for file1 in request.files.getlist('file'):
            logger.info('Processing: %s', file1.filename)
            data1 = pd.read_csv(file1, keep_default_na=False, dtype=str)
            if not all(col in data1.columns for col in valid_columns):
                response = {'code': -1, 'message': 'Invalid data format. Please check the input file'}
                return Response(json.dumps(response), mimetype='application/json')
            # style_codes1 = self.import_data(vendor_name, brand_name, data1, optional)
            style_codes1, missing_codes1 = self.import_data(vendor_name, brand_name, data1, optional)
            style_codes.extend(style_codes1)
            missing_codes.extend(missing_codes1)

        # trigger translation
        logger.info('Translating %d style codes...', len(style_codes))
        logger.info(style_codes)
        # self.translate_and_save(vendor_name, brand_name, style_codes)
        priority = get_priority(len(style_codes))
        queue = 'client_autoscribe_v2'
        task = app.send_task('client_autoscribe_worker_v2.translate_and_save',
                             (vendor_name, brand_name, style_codes),
                             priority=priority, queue=queue)

        if len(missing_codes) == 0:
            response = {'code': 0, 'message': '%d products updated. Translation in progress.' % len(style_codes),
                        'task_id': task.task_id}
        else:
            response = {'code': 0,
                        'message': f"Invalid stylecodes found: {missing_codes}. Aborted translation of {len(missing_codes)} invalid styelcodes",
                        'task_id': task.task_id}
        return Response(json.dumps(response), mimetype='application/json')

    def export_reviewed_get(self, request, vendor_name, brand_name):
        vendor_config = self.get_vendor_config(vendor_name)
        if vendor_config is None:
            response = {'code': -1, 'status': 'Invalid vendor!'}
            return Response(json.dumps(response), mimetype='application/json')

        config = self.get_brand_config(vendor_name, brand_name)
        if config is None:
            response = {'code': -1, 'status': 'Invalid brand!'}
            return Response(json.dumps(response), mimetype='application/json')

        module = import_module('client_autoscribe.integrations.%s' % vendor_name)
        adapter = module.VendorAdapter(vendor_config, config)
        outputs = self.get_reviewed_data(vendor_name, brand_name)
        data, mimetype = adapter.get_output_file(outputs)
        return Response(data, mimetype=mimetype)

    def export_reviewed_catalogix_get(self, request, vendor_name, brand_name):
        vendor_config = self.get_vendor_config(vendor_name)
        if vendor_config is None:
            response = {'code': -1, 'status': 'Invalid vendor!'}
            return Response(json.dumps(response), mimetype='application/json')

        config = self.get_brand_config(vendor_name, brand_name)
        if config is None:
            response = {'code': -1, 'status': 'Invalid brand!'}
            return Response(json.dumps(response), mimetype='application/json')

        module = import_module('client_autoscribe.integrations.%s' % vendor_name)
        adapter = module.VendorAdapter(vendor_config, config)
        outputs = self.get_reviewed_catalogix_data(vendor_name, brand_name)
        data, mimetype = adapter.get_output_file(outputs)
        return Response(data, mimetype=mimetype)


    def export_rpa_get(self, request, vendor_name, brand_name):
        vendor_config = self.get_vendor_config(vendor_name)
        if vendor_config is None:
            response = {'code': -1, 'status': 'Invalid vendor!'}
            return Response(json.dumps(response), mimetype='application/json')

        config = self.get_brand_config(vendor_name, brand_name)
        if config is None:
            response = {'code': -1, 'status': 'Invalid brand!'}
            return Response(json.dumps(response), mimetype='application/json')

        data = self.get_rpa_inputs(vendor_name, brand_name, config)
        # convert to zip
        f = BytesIO()
        with zipfile.ZipFile(f, 'w') as zf:
            for filename, df in data.items():
                f1 = StringIO()
                df.to_csv(f1, index=False)
                zf.writestr(filename, f1.getvalue())
        return Response(f.getvalue(), mimetype='application/zip')

    def post_to_client_get(self, request, vendor_name, brand_name):
        # count = self.get_products_to_post(vendor_name, brand_name, only_count=True)
        if vendor_name in ['streamoid']:
            queue = 'client_autoscribe_v2_streamoid'
        elif vendor_name in ['abfrl_lbrd_prod', 'abfrl_test']:
            queue = 'client_autoscribe_v2_abfrl'
        else:
            queue = 'client_autoscribe_v2_misc'
        task = app.send_task('client_autoscribe_worker_v2.post_to_client',
                             (vendor_name, brand_name), queue=queue)
        response = {'status': {'code': 0, 'message': 'OK'}, 'task_id': task.task_id}
        return Response(json.dumps(response), mimetype='application/json')

    def mark_pushed_get(self, request, vendor_name, brand_name):
        self.mark_products_pushed(vendor_name, brand_name)
        response = {'status': {'code': 0, 'message': 'OK'}}
        return Response(json.dumps(response), mimetype='application/json')

    def vendor_tokens_get(self, request, vendor_name):
        vendor_tokens = self.get_brand_tokens(vendor_name)
        return Response(json.dumps(vendor_tokens), mimetype='application/json')

    def review_products_get(self, request, vendor_name, brand_name):
        vendor_config = self.get_vendor_config(vendor_name)
        if vendor_config is None:
            response = {'code': -1, 'status': 'Invalid vendor!'}
            return Response(json.dumps(response), mimetype='application/json')

        config = self.get_brand_config(vendor_name, brand_name)
        if config is None:
            response = {'code': -1, 'status': 'Invalid brand!'}
            return Response(json.dumps(response), mimetype='application/json')

        t = self.jinja_env.get_template('review_v2.html')
        style_codes = self.get_products_for_review_v2(vendor_name, brand_name)
        logger.info('%d products for review', len(style_codes))
        data = []
        for style_code in style_codes:
            product = self.get_product_for_review(vendor_name, brand_name, style_code)
            if product is not None:
                data.append(product)

        targets = list(config.get('targets', {}).keys())
        return Response(t.render(vendor=vendor_name, brand=brand_name,
                                 data=data, targets=targets),
                        mimetype='text/html')

    def review_all_products_get(self, request, vendor_name, brand_name):
        vendor_config = self.get_vendor_config(vendor_name)
        if vendor_config is None:
            response = {'code': -1, 'status': 'Invalid vendor!'}
            return Response(json.dumps(response), mimetype='application/json')

        config = self.get_brand_config(vendor_name, brand_name)
        if config is None:
            response = {'code': -1, 'status': 'Invalid brand!'}
            return Response(json.dumps(response), mimetype='application/json')

        t = self.jinja_env.get_template('review_v2.html')
        style_codes = self.get_random_products(vendor_name, brand_name)
        logger.info('%d products for review', len(style_codes))
        data = []
        for style_code in style_codes:
            product = self.get_product_for_review(vendor_name, brand_name, style_code)
            if product is not None:
                data.append(product)

        targets = list(config.get('targets', {}).keys())
        return Response(t.render(vendor=vendor_name, brand=brand_name,
                                 data=data, targets=targets),
                        mimetype='text/html')

    def dashboard_get(self, request, vendor_name):
        vendor_config = self.get_vendor_config(vendor_name)
        if vendor_config is None:
            response = {'code': -1, 'status': 'Invalid vendor!'}
            return Response(json.dumps(response), mimetype='application/json')

        # TODO: optimize this
        t1 = time()
        brands = sorted(self.list_brands(vendor_name))
        # new_products = {b: len(self.get_new_products(vendor_name, b)) for b in brands}
        new_products = {b: self.get_new_products(vendor_name, b, only_count=True)
                        for b in brands}
        new_products_precomp = {b: self.get_new_products_precomputed(vendor_name, b, only_count=True)
                                for b in brands}
        rejected_products = {b: len(self.get_rejected_products(vendor_name, b))
                             for b in brands}
        to_review_products = {b: self.get_products_for_review_v2(vendor_name, b, only_count=True)
                              for b in brands}
        # to_review_products = {b: self.products_processed(vendor_name, b, only_count=True)
        #                       for b in brands}
        to_push_products = {b: self.get_products_to_post(vendor_name, b, only_count=True)
                            for b in brands}
        t = self.jinja_env.get_template('dashboard_v2.html')
        logger.info('Time taken: %f sec', time() - t1)
        return Response(t.render(vendor=vendor_name, brands=brands,
                                 new_products=new_products,
                                 new_products_precomp=new_products_precomp,
                                 rejected_products=rejected_products,
                                 to_review_products=to_review_products,
                                 to_push_products=to_push_products), mimetype='text/html')

    def mark_reviewed_post(self, request, vendor_name, brand_name, style_code):
        vendor_config = self.get_vendor_config(vendor_name)
        if vendor_config is None:
            response = {'code': -1, 'status': 'Invalid vendor!'}
            return Response(json.dumps(response), mimetype='application/json')

        config = self.get_brand_config(vendor_name, brand_name)
        if config is None:
            response = {'code': -1, 'status': 'Invalid brand!'}
            return Response(json.dumps(response), mimetype='application/json')

        self.mark_reviewed(vendor_name, brand_name, style_code)
        response = {'code': 0, 'message': 'OK'}
        return Response(json.dumps(response), mimetype='application/json')

    def mark_reviewed_bulk_get(self, request, vendor_name, brand_name):
        vendor_config = self.get_vendor_config(vendor_name)
        if vendor_config is None:
            response = {'code': -1, 'status': 'Invalid vendor!'}
            return Response(json.dumps(response), mimetype='application/json')

        config = self.get_brand_config(vendor_name, brand_name)
        if config is None:
            response = {'code': -1, 'status': 'Invalid brand!'}
            return Response(json.dumps(response), mimetype='application/json')

        self.mark_reviewed_bulk(vendor_name, brand_name)
        response = {'code': 0, 'message': 'OK'}
        return Response(json.dumps(response), mimetype='application/json')

    def product_curated_get(self, request, vendor_name, brand_name, style_code):
        vendor_config = self.get_vendor_config(vendor_name)
        if vendor_config is None:
            response = {'code': -1, 'status': 'Invalid vendor!'}
            return Response(json.dumps(response), mimetype='application/json')

        config = self.get_brand_config(vendor_name, brand_name)
        if config is None:
            response = {'code': -1, 'status': 'Invalid brand!'}
            return Response(json.dumps(response), mimetype='application/json')

        data = self.get_data(vendor_name, brand_name, style_code)
        if config is None:
            response = {'code': -1, 'status': 'No data found!'}
            return Response(json.dumps(response), mimetype='application/json')

        output = data.get('data', {})
        if output is None:
            response = {'status': {'code': -1, 'message': 'Not found!'}}
        else:
            response = {'status': {'code': 0, 'message': 'OK'}, 'data': output}
        return Response(json.dumps(response), mimetype='application/json')

    def view_get(self, request, vendor_name, brand_name, style_code):
        vendor_config = self.get_vendor_config(vendor_name)
        if vendor_config is None:
            response = {'code': -1, 'status': 'Invalid vendor!'}
            return Response(json.dumps(response), mimetype='application/json')

        config = self.get_brand_config(vendor_name, brand_name)
        if config is None:
            response = {'code': -1, 'status': 'Invalid brand!'}
            return Response(json.dumps(response), mimetype='application/json')

        output = self.get_output(vendor_name, brand_name, style_code)
        if output is None:
            response = {'status': {'code': -1, 'message': 'Not found!'}}
        else:
            response = {'status': {'code': 0, 'message': 'OK'}, 'data': output}
        return Response(json.dumps(response), mimetype='application/json')

    def view_catalogix_get(self, request, vendor_name, brand_name, style_code):
        vendor_config = self.get_vendor_config(vendor_name)
        if vendor_config is None:
            response = {'code': -1, 'status': 'Invalid vendor!'}
            return Response(json.dumps(response), mimetype='application/json')

        config = self.get_brand_config(vendor_name, brand_name)
        if config is None:
            response = {'code': -1, 'status': 'Invalid brand!'}
            return Response(json.dumps(response), mimetype='application/json')

        output = self.get_catalogix_output(vendor_name, brand_name, style_code)
        if output is None:
            response = {'status': {'code': -1, 'message': 'Not found!'}}
        else:
            response = {'status': {'code': 0, 'message': 'OK'}, 'data': output}
        return Response(json.dumps(response), mimetype='application/json')

    def report_for_date_get(self, request, vendor_name, date):
        vendor_config = self.get_vendor_config(vendor_name)
        if vendor_config is None:
            response = {'code': -1, 'status': 'Invalid vendor!'}
            return Response(json.dumps(response), mimetype='application/json')

        f = StringIO()
        self.create_report(vendor_name, date, date, f)
        return Response(f.getvalue(), mimetype='text/csv')

    def translate_fabric_post(self, request, vendor_name, brand_name):
        vendor_config = self.get_vendor_config(vendor_name)
        if vendor_config is None:
            response = {'code': -1, 'status': 'Invalid vendor!'}
            return Response(json.dumps(response), mimetype='application/json')

        config = self.get_brand_config(vendor_name, brand_name)
        if config is None:
            response = {'code': -1, 'status': 'Invalid brand!'}
            return Response(json.dumps(response), mimetype='application/json')

        try:
            data = json.loads(request.form['data'])
        except Exception as e:
            logger.exception(str(e))
            response = {'status': {'code': -1, 'message': str(e)}}
            return Response(json.dumps(response), mimetype='application/json')

        # source = vendor_config.get('curation_ontology', 'Streamoid-MP')

        config = self.get_brand_config(vendor_name, brand_name)
        targets = config.get('targets', {})
        output = {}
        for style_code, info in data.items():
            # TODO: product info should come from DB
            product = info.get('product', {})
            curated_data = info.get('curated', {})
            source_tags = [k.strip() + ':' + v.strip() for k, v in curated_data.items() if v is not None and len(v) > 0]
            vendor_tags = [k.strip() + ':' + str(v).strip() for k, v in unescape(product).items() if
                           '://' not in str(v)]  # to avoid long urls

            out = {}
            for field, target in targets.items():
                out[field] = self.custom_rules_db.apply_fabric_rules(vendor_name, target,
                                                                     source_tags, vendor_tags,
                                                                     brand_name)
            output[style_code] = out
            # output[style_code] = self.translate(curated_data, product, targets, source,
            #                                     vendor_name, brand_name, custom_rules)

        response = {'status': {'code': 0, 'message': 'OK'}, 'data': output}
        return Response(json.dumps(response), mimetype='application/json')

    def translate_post(self, request, vendor_name, brand_name):
        vendor_config = self.get_vendor_config(vendor_name)
        if vendor_config is None:
            response = {'code': -1, 'status': 'Invalid vendor!'}
            return Response(json.dumps(response), mimetype='application/json')

        config = self.get_brand_config(vendor_name, brand_name)
        if config is None:
            response = {'code': -1, 'status': 'Invalid brand!'}
            return Response(json.dumps(response), mimetype='application/json')

        try:
            data = json.loads(request.form['data'])
        except Exception as e:
            logger.exception(str(e))
            response = {'status': {'code': -1, 'message': str(e)}}
            return Response(json.dumps(response), mimetype='application/json')

        source = vendor_config.get('curation_ontology', 'Streamoid-MP')

        config = self.get_brand_config(vendor_name, brand_name)
        targets = config.get('targets', {})
        custom_rules = config.get('custom_rules', False)
        output = {}
        for style_code, info in data.items():
            product = info.get('product', {})
            curated_data = info.get('curated', {})
            output[style_code] = self.translate(curated_data, product, targets, source,
                                                vendor_name, brand_name, custom_rules)
        response = {'status': {'code': 0, 'message': 'OK'}, 'data': output}
        return Response(json.dumps(response), mimetype='application/json')

    def translate_split_post(self, request, vendor_name, brand_name):
        vendor_config = self.get_vendor_config(vendor_name)
        if vendor_config is None:
            response = {'code': -1, 'status': 'Invalid vendor!'}
            return Response(json.dumps(response), mimetype='application/json')

        config = self.get_brand_config(vendor_name, brand_name)
        if config is None:
            response = {'code': -1, 'status': 'Invalid brand!'}
            return Response(json.dumps(response), mimetype='application/json')

        try:
            data = json.loads(request.form['data'])
        except Exception as e:
            logger.exception(str(e))
            response = {'status': {'code': -1, 'message': str(e)}}
            return Response(json.dumps(response), mimetype='application/json')

        source = vendor_config['curation_ontology']
        targets = config.get('targets', {})
        output = {}
        for style_code, info in data.items():
            product = info.get('product', {})
            curated_data = info.get('curated', {})
            output[style_code] = self.translate_split(vendor_name, brand_name,
                                                      curated_data, product, targets, source)
        response = {'status': {'code': 0, 'message': 'OK'}, 'data': output}
        return Response(json.dumps(response), mimetype='application/json')

    def translate_csv_get(self, request, vendor_name, brand_name):
        vendor_config = self.get_vendor_config(vendor_name)
        if vendor_config is None:
            response = {'code': -1, 'status': 'Invalid vendor!'}
            return Response(json.dumps(response), mimetype='application/json')

        config = self.get_brand_config(vendor_name, brand_name)
        if config is None:
            response = {'code': -1, 'status': 'Invalid brand!'}
            return Response(json.dumps(response), mimetype='application/json')

        t = self.jinja_env.get_template('translate_csv.html')
        return Response(t.render(vendor=vendor_name), mimetype='text/html')

    def translate_csv_post(self, request, vendor_name, brand_name):
        vendor_config = self.get_vendor_config(vendor_name)
        if vendor_config is None:
            response = {'code': -1, 'status': 'Invalid vendor!'}
            return Response(json.dumps(response), mimetype='application/json')

        config = self.get_brand_config(vendor_name, brand_name)
        if config is None:
            response = {'code': -1, 'status': 'Invalid brand!'}
            return Response(json.dumps(response), mimetype='application/json')

        try:
            data = pd.read_csv(request.files['file'], keep_default_na=False, dtype=str)
        except Exception as e:
            response = {'status': {'code': -1, 'message': 'Missing or invalid file: %s' % str(e)}}
            return Response(json.dumps(response), mimetype='application/json')

        source = vendor_config.get('curation_ontology', 'Streamoid-MP')
        targets = config.get('targets', {})
        custom_rules = config.get('custom_rules', False)

        output = {}
        for idx, row in data.iterrows():
            curated_data = dict(row)
            logger.info(curated_data)
            style_code = curated_data['product_id']
            output[style_code] = self.translate(curated_data, {}, targets, source,
                                                vendor_name, brand_name, custom_rules)
            output[style_code]['streamoid_attributes'] = curated_data

        # style_code <-> ontology
        output1 = {}
        for style_code, details in output.items():
            for ontology_field, info in details.items():
                if ontology_field not in output1:
                    output1[ontology_field] = {}
                output1[ontology_field][style_code] = info

        # convert to ZIP of CSVs
        filename = '/tmp/%s.xlsx' % uuid4().hex
        writer = pd.ExcelWriter(filename)
        for ontology_field, details in output1.items():
            out = []
            for style_code, info in details.items():
                info['style_code'] = style_code
                out.append(info)
            pd.DataFrame(out).to_excel(writer, sheet_name=ontology_field, index=False)
        writer.close()

        return Response(open(filename, 'rb'),
                        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

    def translate_csv_ontology_get(self, request, ontology):
        t = self.jinja_env.get_template('translate_csv.html')
        return Response(t.render(vendor=ontology), mimetype='text/html')

    def translate_csv_ontology_post(self, request, ontology):
        try:
            data = pd.read_csv(request.files['file'], keep_default_na=False, dtype=str)
        except Exception as e:
            response = {'status': {'code': -1, 'message': 'Missing or invalid file: %s' % str(e)}}
            return Response(json.dumps(response), mimetype='application/json')

        source = 'Streamoid-MP'
        targets = {'data': ontology}
        output = {}
        for idx, row in data.iterrows():
            curated_data = dict(row)
            logger.info(curated_data)
            style_code = curated_data['product_code']
            output[style_code] = self.translate(curated_data, {}, targets, source)
            output[style_code]['data']['image_url'] = curated_data['image_url']
            # output[style_code]['streamoid_attributes'] = curated_data

        # style_code <-> ontology
        output1 = {}
        for style_code, details in output.items():
            for ontology_field, info in details.items():
                if ontology_field not in output1:
                    output1[ontology_field] = {}
                output1[ontology_field][style_code] = info

        details = output1['data']
        out = []
        for style_code, info in details.items():
            info['style_code'] = style_code
            out.append(info)
        buf = StringIO()
        pd.DataFrame(out).to_csv(buf, index=False)

        return Response(buf.getvalue(), mimetype='text/csv')

    def report_get(self, request, vendor_name):
        vendor_config = self.get_vendor_config(vendor_name)
        if vendor_config is None:
            response = {'code': -1, 'status': 'Invalid vendor!'}
            return Response(json.dumps(response), mimetype='application/json')

        t = self.jinja_env.get_template('reporting.html')
        return Response(t.render(), mimetype='text/html')

    def report_post(self, request, vendor_name):
        vendor_config = self.get_vendor_config(vendor_name)
        if vendor_config is None:
            response = {'code': -1, 'status': 'Invalid vendor!'}
            return Response(json.dumps(response), mimetype='application/json')

        start_date = request.form['start_date']
        end_date = request.form['end_date']
        f = StringIO()
        self.create_report(vendor_name, start_date, end_date, f)
        return Response(f.getvalue(), mimetype='text/csv')

    def received_get(self, request, vendor_name):
        vendor_config = self.get_vendor_config(vendor_name)
        if vendor_config is None:
            response = {'code': -1, 'status': 'Invalid vendor!'}
            return Response(json.dumps(response), mimetype='application/json')

        t = self.jinja_env.get_template('reporting.html')
        return Response(t.render(), mimetype='text/html')

    def received_post(self, request, vendor_name):
        vendor_config = self.get_vendor_config(vendor_name)
        if vendor_config is None:
            response = {'code': -1, 'status': 'Invalid vendor!'}
            return Response(json.dumps(response), mimetype='application/json')

        start_date = request.form['start_date']
        end_date = request.form['end_date']
        f = BytesIO()
        self.get_monthly_received(vendor_name, start_date, end_date, f)
        return Response(f.getvalue(), mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

    def products_upload_get(self, request, vendor_name, brand_name):
        vendor_config = self.get_vendor_config(vendor_name)
        if vendor_config is None:
            response = {'code': -1, 'status': 'Invalid vendor!'}
            return Response(json.dumps(response), mimetype='application/json')

        t = self.jinja_env.get_template('upload_v2.html')
        brands = self.get_brand_names(vendor_name)
        return Response(t.render(brands=brands), mimetype='text/html')

    def products_upload_post(self, request, vendor_name, brand_name):
        vendor_config = self.get_vendor_config(vendor_name)
        if vendor_config is None:
            response = {'code': -1, 'status': 'Invalid vendor!'}
            return Response(json.dumps(response), mimetype='application/json')

        config = self.get_brand_config(vendor_name, brand_name)
        if config is None or config.get("_id", None) in self.archived_brands:
            response = {'code': -1, 'status': 'Invalid brand!'}
            return Response(json.dumps(response), mimetype='application/json')

        valid_style_codes = []
        try:
            file = request.files['file']
            module = import_module('client_autoscribe.integrations.%s' % vendor_name)
            adapter = module.VendorAdapter(vendor_config, config)
            style_codes = adapter.convert_input(file, filename=file.filename)

            vendor_db = get_db_name(vendor_name)
            details = self.save_products(vendor_db, brand_name, style_codes)
            failed = 0
            success = 0
            for key, val in details.items():
                if val['code'] == 0:
                    valid_style_codes.append(key)
                    success += 1
                else:
                    failed += 1
            response = {'success': success, 'failed': failed}
        except Exception as e:
            logger.exception(str(e))
            response = {'status': {
                'code': -1,
                'message': str(e)}}
            return Response(json.dumps(response), mimetype='application/json')

        # trigger pre-computation
        try:
            queue = 'client_autoscribe_v2_misc'
            self._trigger_precompute(vendor_name, brand_name, valid_style_codes, queue,
                                     post_msg=False)
        except Exception as e:
            logger.exception(str(e))

        return Response(json.dumps(response), mimetype='application/json')

    def rpa_status_get(self, request, vendor_name, brand_name):
        offset = int(request.args.get('offset', 0))
        limit = int(request.args.get('limit', 50))
        vendor_config = self.get_vendor_config(vendor_name)
        if vendor_config is None:
            response = {'code': -1, 'status': 'Invalid vendor!'}
            return Response(json.dumps(response), mimetype='application/json')

        config = self.get_brand_config(vendor_name, brand_name)
        if config is None:
            response = {'code': -1, 'status': 'Invalid brand!'}
            return Response(json.dumps(response), mimetype='application/json')

        # get status from DB
        rows = self.get_rpa_status(vendor_name, brand_name,
                                   offset=offset, limit=limit)
        for row in rows:
            logger.info(row)
        t = self.jinja_env.get_template('rpa_status.html')
        return Response(t.render(vendor=vendor_name, brand=brand_name, rows=rows),
                        mimetype='text/html')

    def rpa_trigger_get(self, request, vendor_name, brand_name):
        vendor_config = self.get_vendor_config(vendor_name)
        if vendor_config is None:
            response = {'code': -1, 'status': 'Invalid vendor!'}
            return Response(json.dumps(response), mimetype='application/json')

        config = self.get_brand_config(vendor_name, brand_name)
        if config is None:
            response = {'code': -1, 'status': 'Invalid brand!'}
            return Response(json.dumps(response), mimetype='application/json')

        data = self.get_rpa_inputs(vendor_name, brand_name, config)

        files = {}
        for filename, df in data.items():
            f1 = StringIO()
            df.to_csv(f1, index=False)
            files[filename] = f1.getvalue()

        # save files
        rpa_request_id = uuid4().hex
        for fname, contents in files.items():
            rpa_request_dir = os.path.join(rpa_dir, vendor_name, brand_name, rpa_request_id)
            os.makedirs(rpa_request_dir, exist_ok=True)
            fpath = os.path.join(rpa_request_dir, fname)
            with open(fpath, 'w') as f:
                f.write(contents)

        # trigger RPA on worker
        filenames = list(files.keys())
        task = app.send_task('client_autoscribe_worker_v2.post_rpa_files',
                             (vendor_name, brand_name, rpa_request_id, filenames))

        response = {'status': {'code': 0, 'message': 'OK'}, 'task_id': task.task_id}
        return Response(json.dumps(response), mimetype='application/json')

    def rpa_callback(self, request, vendor_name, brand_name):
        # 1. update status
        # 2. download output file and keep
        logger.info('%s %s', vendor_name, brand_name)
        try:
            data1 = request.get_data()
            logger.info(data1)
            data = json.loads(data1)
            logger.info(data)
        except Exception as e:
            logger.exception(str(e))
            response = {'code': -1, 'status': str(e)}
            return Response(json.dumps(response), mimetype='application/json')

        condition = {'BatchRequestId': data['BatchRequestId']}
        rows = self.get_rpa_status(vendor_name, brand_name, condition=condition)
        task_id = None
        for row in rows:
            logger.info(row)
            rpa_request_id = row['rpa_request_id']
            fname = row['filename']
            responses = {fname: data}
            self.set_rpa_details(vendor_name, brand_name, rpa_request_id, responses)
            task = app.send_task('client_autoscribe_worker_v2.fetch_files_and_store',
                                 (vendor_name, brand_name, rpa_request_id, responses))
            task_id = task.task_id

        response = {'code': 0, 'status': 'OK', 'task_id': task_id}
        return Response(json.dumps(response), mimetype='application/json')

    def rpa_update_get(self, request, vendor_name, brand_name, rpa_request_id):
        task = app.send_task('client_autoscribe_worker_v2.update_rpa_status',
                             (vendor_name, brand_name, rpa_request_id))

        response = {'status': {'code': 0, 'message': 'OK'}, 'task_id': task.task_id}
        return Response(json.dumps(response), mimetype='application/json')

    def rpa_download_get(self, request, vendor_name, brand_name, rpa_request_id):
        rpa_request_dir = os.path.join(rpa_dir, vendor_name, brand_name, rpa_request_id)
        files = []
        for fname in glob(rpa_request_dir + '/*'):
            if fname.endswith('.csv'):
                continue
            files.append(fname)

        # create zipfile
        f = BytesIO()
        with zipfile.ZipFile(f, 'w') as zf:
            for fpath in files:
                with open(fpath, 'rb') as f1:
                    data = f1.read()
                fname = fpath.split('/')[-1]
                zf.writestr(fname, data)

        return Response(f.getvalue(), mimetype='application/zip')

    def descriptions_get(self, request, vendor_name):
        t = self.jinja_env.get_template('upload_file.html')
        return Response(t.render(), mimetype='text/html')

    def descriptions_post(self, request, vendor_name):
        try:
            file = request.files['file']
            out_path = os.path.join(os.path.dirname(__file__), 'integrations/pantaloons_descriptions.xlsx')
            file.save(out_path)
            warnings = check_descriptions(file)
            response = {'code': 0, 'message': 'OK', 'warnings': warnings}
        except Exception as e:
            logger.exception(str(e))
            response = {'code': -1, 'message': str(e)}

        return Response(json.dumps(response), mimetype='application/json')
    

    def catalogix_dashboard_get(self, request, vendor_name):
        vendor_config = self.get_vendor_config(vendor_name)
        if vendor_config is None:
            response = {'code': -1, 'status': 'Invalid vendor!'}
            return Response(json.dumps(response), mimetype='application/json')

        # TODO: optimize this
        t1 = time()
        brands = sorted(self.list_brands(vendor_name))
        # new_products = {b: len(self.get_new_products(vendor_name, b)) for b in brands}
        recurated_products = {b: self.get_recurated_products(vendor_name, b, only_count=True)
                        for b in brands}
        new_products = {b: self.get_new_products(vendor_name, b, only_count=True) - recurated_products[b]
                        for b in brands}
        
        rejected_products = {b: len(self.get_rejected_products(vendor_name, b))
                             for b in brands}
        to_review_products_all = {b: self.get_catalogix_products_for_review_v2(vendor_name, b, only_count=True)
                              for b in brands}
        to_review_products = {b: self.get_catalogix_products_for_review_v2_count(vendor_name, b, only_count=True)
                              for b in brands}
        to_push_products = {b: self.get_catalogix_products_to_post(vendor_name, b, only_count=True)
                            for b in brands}
        
        t = self.jinja_env.get_template('catalogix_dashboard.html')
        logger.info('Time taken: %f sec', time() - t1)
        return Response(t.render(vendor=vendor_name, brands=brands,
                                 new_products=new_products,
                                 recurated_products = recurated_products,
                                 to_review_products_all=to_review_products_all,
                                 to_review_products=to_review_products,
                                 rejected_products=rejected_products,
                                 to_push_products=to_push_products,
                                 ), mimetype='text/html')

    def import_catalogix_data_post(self, request, vendor_name, brand_name):
        logger.info("Import Data from catalogix called")
        vendor_config = self.get_vendor_config(vendor_name)
        if vendor_config is None:
            response = {'code': -1, 'status': 'Invalid vendor!'}
            logger.info(response)
            return Response(json.dumps(response), mimetype='application/json')

        config = self.get_brand_config(vendor_name, brand_name)
        if config is None:
            response = {'code': -1, 'status': 'Invalid brand!'}
            logger.info(response)
            return Response(json.dumps(response), mimetype='application/json')

        try:
            data = request.get_json()
            logger.info(data)
            # {"status" : {"message":"success", "code":"0"},
            #  "data" :
            #         {"store_uuid": "<>",
            #          "marketplace": "<>",
            #           "data":
            #               [{"product_uuid": "<>",
            #                 "style_code":"<>",
            #                 "attribute":"<>"....},
            #                 {}
            #                ]
            #         }
            # }
        except Exception as e:
            response = {'code': -1, 'message': str(e)}
            logger.info(response)
            return Response(json.dumps(response), mimetype='application/json')


        if 'data' not in data:
            response = {'code': -1, 'message': 'Missing data!'}
            return Response(json.dumps(response), mimetype='application/json')
        if 'store_uuid' not in data['data']:
            response = {'code': -1, 'message': 'Missing store_uuid!'}
            return Response(json.dumps(response), mimetype='application/json')           
        if 'marketplace' not in data['data']:
            response = {'code': -1, 'message': 'Missing marketplace!'}
            return Response(json.dumps(response), mimetype='application/json') 
        if 'data' not in data['data']:
            response = {'code': -1, 'message': 'Missing style codes data!'}
            return Response(json.dumps(response), mimetype='application/json') 
        
        store_uuid = data['data']['store_uuid']
        marketplace = data['data']['marketplace']
        style_codes = data['data']['data']

        # trigger format and push to client
        logger.info('Saving %d style codes...', len(style_codes))
        logger.info(style_codes)
        self.save_catalogix_data(vendor_name, brand_name, style_codes, store_uuid, marketplace)

        # TODO: trigger client push
        # queue = 'client_autoscribe_v2'
        # task = app.send_task('client_autoscribe_worker_v2.translate_and_save',
        #                      (vendor_name, brand_name, style_codes),
        #                      queue=queue)

        response = {'code': 0, 'message': 'OK'}
        return Response(json.dumps(response), mimetype='application/json')
    
    def review_catalogix_products_get(self, request, vendor_name, brand_name):
        vendor_config = self.get_vendor_config(vendor_name)
        if vendor_config is None:
            response = {'code': -1, 'status': 'Invalid vendor!'}
            return Response(json.dumps(response), mimetype='application/json')

        config = self.get_brand_config(vendor_name, brand_name)
        if config is None:
            response = {'code': -1, 'status': 'Invalid brand!'}
            return Response(json.dumps(response), mimetype='application/json')

        t = self.jinja_env.get_template('catalogix_review_v2.html')
        style_codes = self.get_catalogix_products_for_review_v2(vendor_name, brand_name)
        logger.info('%d products for review', len(style_codes))
        data = []
        for style_code in style_codes:
            product = self.get_catalogix_product_for_review(vendor_name, brand_name, style_code)
            if product is not None:
                data.append(product)

        targets = list(config.get('targets', {}).keys())
        return Response(t.render(vendor=vendor_name, brand=brand_name,
                                 data=data, targets=targets),
                        mimetype='text/html')
    
    def review_all_catalogix_products_get(self, request, vendor_name, brand_name):
        vendor_config = self.get_vendor_config(vendor_name)
        if vendor_config is None:
            response = {'code': -1, 'status': 'Invalid vendor!'}
            return Response(json.dumps(response), mimetype='application/json')

        config = self.get_brand_config(vendor_name, brand_name)
        if config is None:
            response = {'code': -1, 'status': 'Invalid brand!'}
            return Response(json.dumps(response), mimetype='application/json')

        t = self.jinja_env.get_template('catalogix_review_v2.html')
        style_codes = self.get_random_catalogix_products(vendor_name, brand_name)
        logger.info('%d products for review', len(style_codes))
        data = []
        for style_code in style_codes:
            product = self.get_catalogix_product_for_review(vendor_name, brand_name, style_code)
            if product is not None:
                data.append(product)

        targets = list(config.get('targets', {}).keys())
        return Response(t.render(vendor=vendor_name, brand=brand_name,
                                 data=data, targets=targets),
                        mimetype='text/html')

    def catalogix_mark_reviewed_post(self, request, vendor_name, brand_name, style_code):
        vendor_config = self.get_vendor_config(vendor_name)
        if vendor_config is None:
            response = {'code': -1, 'status': 'Invalid vendor!'}
            return Response(json.dumps(response), mimetype='application/json')

        config = self.get_brand_config(vendor_name, brand_name)
        if config is None:
            response = {'code': -1, 'status': 'Invalid brand!'}
            return Response(json.dumps(response), mimetype='application/json')
        self.catalogix_mark_reviewed(vendor_name, brand_name, style_code)
        response = {'code': 0, 'message': 'OK'}
        return Response(json.dumps(response), mimetype='application/json')

    def catalogix_mark_reviewed_bulk_get(self, request, vendor_name, brand_name):
        vendor_config = self.get_vendor_config(vendor_name)
        if vendor_config is None:
            response = {'code': -1, 'status': 'Invalid vendor!'}
            return Response(json.dumps(response), mimetype='application/json')

        config = self.get_brand_config(vendor_name, brand_name)
        if config is None:
            response = {'code': -1, 'status': 'Invalid brand!'}
            return Response(json.dumps(response), mimetype='application/json')

        self.catalogix_mark_reviewed_bulk(vendor_name, brand_name)
        response = {'code': 0, 'message': 'OK'}
        return Response(json.dumps(response), mimetype='application/json')

    def catalogix_hold_post(self, request, vendor_name, brand_name, style_code):
        vendor_config = self.get_vendor_config(vendor_name)
        if vendor_config is None:
            response = {'code': -1, 'status': 'Invalid vendor!'}
            return Response(json.dumps(response), mimetype='application/json')

        config = self.get_brand_config(vendor_name, brand_name)
        if config is None:
            response = {'code': -1, 'status': 'Invalid brand!'}
            return Response(json.dumps(response), mimetype='application/json')

        self.catalogix_hold_products(vendor_name, brand_name, style_code)
        response = {'code': 0, 'message': 'OK'}
        return Response(json.dumps(response), mimetype='application/json')
    
    def catalogix_unhold_post(self, request, vendor_name, brand_name, style_code):
        vendor_config = self.get_vendor_config(vendor_name)
        if vendor_config is None:
            response = {'code': -1, 'status': 'Invalid vendor!'}
            return Response(json.dumps(response), mimetype='application/json')

        config = self.get_brand_config(vendor_name, brand_name)
        if config is None:
            response = {'code': -1, 'status': 'Invalid brand!'}
            return Response(json.dumps(response), mimetype='application/json')

        self.catalogix_unhold_products(vendor_name, brand_name, style_code)
        response = {'code': 0, 'message': 'OK'}
        return Response(json.dumps(response), mimetype='application/json')
    
    def catalogix_post_to_client_get(self, request, vendor_name, brand_name):
        # count = self.get_products_to_post(vendor_name, brand_name, only_count=True)
        # return "In Development"
        post = True
        if vendor_name in ['streamoid']:
            queue = 'client_autoscribe_v2_streamoid'
        elif vendor_name in ['abfrl_lbrd_prod', 'abfrl_test']:
            queue = 'client_autoscribe_v2_abfrl'
        else:
            queue = 'client_autoscribe_v2_misc'
        task = app.send_task('client_autoscribe_worker_v2.catalogix_post_to_client',
                             (vendor_name, brand_name, post), queue=queue)
        response = {'status': {'code': 0, 'message': 'OK'}, 'task_id': task.task_id}
        return Response(json.dumps(response), mimetype='application/json')

    def export_product_status_catalogix(self, request, vendor_name, brand_name):
        vendor_config = self.get_vendor_config(vendor_name)
        if vendor_config is None:
            response = {'code': -1, 'status': 'Invalid vendor!'}
            return Response(json.dumps(response), mimetype='application/json')

        config = self.get_brand_config(vendor_name, brand_name)
        if config is None:
            response = {'code': -1, 'status': 'Invalid brand!'}
            return Response(json.dumps(response), mimetype='application/json')

        style_codes = [x for x in request.args.get('style_codes', '').split(',') if len(x) > 0]
        results = []

        for style_code in style_codes:
            info = {'style_code': style_code}
            data = self.get_product(vendor_name, brand_name, style_code)

            if not data:
                info['status'] = 'not received'
            else:
                curation_status = self.get_product_status_catalogix(vendor_name, brand_name, style_code)
                info['status'] = curation_status
                info['received_time'] = data.get('last_modified', '')
                print(info['received_time'])
                info['received_time'] = self.convert_utc_to_ist(info['received_time'])
                if curation_status == 'pushed':
                    pushed_data = self.get_catalogix_data(vendor_name, brand_name, style_code)
                    info['pushed_time'] = pushed_data.get('push_time', '')
                    info['pushed_time'] = self.convert_utc_to_ist(info['pushed_time'])
                
                info['time_taken'] = ''
                if 'pushed_time' in info.keys() and info['pushed_time'] != '' and info['received_time'] != '':
                    info['time_taken'] = datetime.strptime(info['pushed_time'], '%Y-%m-%d %H:%M:%S') - datetime.strptime(info['received_time'], '%Y-%m-%d %H:%M:%S')


            results.append(info)

        out = sorted(results, key=lambda x: x.get('pushed_time', ''))
        logger.info('Exporting %d rows', len(out))
        data = create_csv(out)
        return Response(data, mimetype='text/csv')
        # return create_csv_or_zip(out, 'style_code')
    

    def track_vendor(self, request, service, vendor_name):
        since_ts = request.args.get('since_ts')
        till_ts = request.args.get('till_ts')
        csv_data = self.get_vendor_csv_data(service, vendor_name, since_ts, till_ts)
        data = create_csv(csv_data)
        return Response(data, mimetype='text/csv')
    
    def get_brand_data(self, request, service, vendor_name, brand_name):
        since_ts = request.args.get('since_ts')
        till_ts = request.args.get('till_ts')
        csv_data = self.get_brand_csv_data(service, vendor_name, brand_name, since_ts, till_ts)
        logger.info(csv_data)
        data = create_csv(csv_data)
        return Response(data, mimetype='text/csv')

    def get_product_data(self, request, vendor_name):
        since_ts = request.args.get('since_ts')
        till_ts = request.args.get('till_ts')
        csv_data = self.get_product_data_timestamp(vendor_name, since_ts, till_ts)
        return csv_data
        logger.info(csv_data)
        data = create_csv(csv_data)
        return Response(data, mimetype='text/csv')

    def post_to_catalogix_html(self, request):
        brands = {}
        for vendor in self.list_vendors():
            brands[vendor] = self.list_brands(vendor)
        t = self.jinja_env.get_template('post_to_catalogix.html')
        rendered_html = t.render(vendor=self.list_vendors(), brands=brands)
        return Response(rendered_html, mimetype='text/html')

    
    def post_to_catalogix(self, request):
    # Get form data submitted by the client
        vendor_name = request.form['vendor_name']
        brand_name = request.form['brand_name']
        all_new_products = request.form['all_new_products']
        style_codes = request.form['style_codes']

        status, error = self.manual_post_to_catalogix(vendor_name, brand_name, all_new_products, style_codes)
        response_body = "Received form data: vendor={}, brand={}, all_new_products={}, style_codes={}, status={}, error ={}".format(vendor_name, brand_name, all_new_products, style_codes, status, error)
        return Response(response_body, mimetype='text/plain')




application = ClientAutoscribeAPI('localhost')

if __name__ == '__main__':
    from werkzeug.serving import run_simple

    run_simple('0.0.0.0', 4009, application)
