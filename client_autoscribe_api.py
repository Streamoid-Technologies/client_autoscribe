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

import werkzeug
import werkzeug.exceptions
from werkzeug.routing import Map, Rule
from werkzeug.wrappers import Request, Response
from jinja2 import Environment, FileSystemLoader

from uuid import uuid4
from io import StringIO

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from client_autoscribe.client_autoscribe_db import get_db_name
from client_autoscribe.reporting import ClientAutoscribeReporting
from client_autoscribe.client_autoscribe_config import app


class ClientAutoscribeAPI(ClientAutoscribeReporting):
    def __init__(self, mongo_host):
        super().__init__(mongo_host)
        prefix = '/api/autoscribe/abfrl'
        self.url_map = Map(
            [
                # custom rules
                Rule('%s/rules' % prefix, endpoint='rules_get', methods=['GET']),
                Rule('%s/rules' % prefix, endpoint='rules_post', methods=['POST']),

                # product push
                Rule('%s/post' % prefix, endpoint='product_get', methods=['GET']),  # to be removed
                Rule('%s/post/<string:vendor_name>' % prefix, endpoint='product_post', methods=['POST']),
                Rule('%s/<string:vendor_name>/post' % prefix, endpoint='product_post', methods=['POST']),

                # products bulk push
                Rule('%s/upload' % prefix, endpoint='products_upload_get', methods=['GET']),
                Rule('%s/upload' % prefix, endpoint='products_upload_post', methods=['POST']),

                # export by style_codes
                Rule('%s/export/<string:vendor_name>' % prefix, endpoint='export_get',
                     methods=['GET']),

                # export new
                Rule('%s/export-new/<string:vendor_name>' % prefix, endpoint='export_new_get',
                     methods=['GET']),
                Rule('%s/export-new-precomputed/<string:vendor_name>' % prefix,
                     endpoint='export_new_precomp_get', methods=['GET']),

                # import from cataloging
                Rule('%s/import/<string:vendor_name>' % prefix, endpoint='import_data_post',
                     methods=['POST']),

                # review
                Rule('%s/review/<string:vendor_name>' % prefix, endpoint='review_products_get',
                     methods=['GET']),
                Rule('%s/mark-reviewed/<string:vendor_name>/<string:style_code>' % prefix,
                     endpoint='mark_reviewed_post', methods=['POST']),

                # push to client
                Rule('%s/post-to-client/<string:vendor_name>' % prefix, endpoint='post_to_client_get',
                     methods=['GET']),

                # rejections
                # Rule('%s/reject' % prefix, endpoint='reject_get', methods=['GET']),
                Rule('%s/reject/<string:vendor_name>' % prefix, endpoint='reject_post', methods=['POST']),
                Rule('%s/<string:vendor_name>/reject' % prefix, endpoint='reject_post', methods=['POST']),
                Rule('%s/export-rejected/<string:vendor_name>' % prefix, endpoint='export_rejected_get',
                     methods=['GET']),

                # utility functions
                Rule('%s/vendor-tokens' % prefix, endpoint='vendor_tokens_get', methods=['GET']),
                Rule('%s/dashboard' % prefix, endpoint='dashboard_get', methods=['GET']),

                # translation control
                Rule('%s/translations/<string:vendor_name>' % prefix,
                     endpoint='translations_get', methods=['GET']),
                Rule('%s/translations/<string:vendor_name>' % prefix,
                     endpoint='translations_post', methods=['POST']),

                # view final data
                Rule('%s/view/<string:vendor_name>/<string:style_code>' % prefix,
                     endpoint='view_get', methods=['GET']),

                # translate
                # TODO: minimize and simplify translation endpoints
                Rule('%s/translate/<string:vendor_name>' % prefix,
                     endpoint='translate_post', methods=['POST']),
                Rule('%s/translate-split/<string:vendor_name>' % prefix,
                     endpoint='translate_split_post', methods=['POST']),
                Rule('%s/translate-csv/<string:vendor_name>' % prefix,
                     endpoint='translate_csv_get', methods=['GET']),
                Rule('%s/translate-csv/<string:vendor_name>' % prefix,
                     endpoint='translate_csv_post', methods=['POST']),
                Rule('%s/translate-csv/ontology/<string:ontology>' % prefix,
                     endpoint='translate_csv_ontology_get', methods=['GET']),
                Rule('%s/translate-csv/ontology/<string:ontology>' % prefix,
                     endpoint='translate_csv_ontology_post', methods=['POST']),

                # report
                Rule('%s/report/<string:date>' % prefix,
                     endpoint='report_for_date_get', methods=['GET']),
                Rule('%s/report' % prefix, endpoint='report_get', methods=['GET']),
                Rule('%s/report' % prefix, endpoint='report_post', methods=['POST']),

                # manage
                Rule('%s/create-vendor' % prefix, endpoint='create_vendor_get', methods=['GET']),
                Rule('%s/create-vendor' % prefix, endpoint='create_vendor_post', methods=['POST']),
            ],
            redirect_defaults=False,
            strict_slashes=False)

        self.jinja_env = Environment(loader=FileSystemLoader(os.path.join(os.path.dirname(__file__), 'templates')),
                                     autoescape=True)

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

    def _check_vendor_token(self, request, vendor_name):
        data = {}
        request_token = request.headers.get('vendor-token', None)
        logger.info('%s %s', vendor_name, request_token)
        vendor_tokens = self.get_vendor_tokens()
        token = vendor_tokens.get(vendor_name, None)
        if token is None:
            data['status'] = {'code': 3, 'message': 'vendor not found'}
            logger.error(data)
            return data

        if request_token != token or request_token is None:
            data['status'] = {'code': 2, 'message': 'token not found'}
            logger.error(data)
            return data

        return data

    # application specific code below
    def rules_get(self, request):
        t = self.jinja_env.get_template('rules.html')
        options = list(self.targets.values()) + list(self.brand_ontologies.values()) + [self.default_brand_ontology]
        return Response(t.render(options=options), mimetype='text/html')

    def rules_post(self, request):
        try:
            file = request.files['file']
            ontology = request.form['ontology']
            rules_type = request.form['type']
        except Exception as e:
            logger.exception(str(e))
            response = {'status': {'code': -1, 'message': str(e)}}
            return Response(json.dumps(response), mimetype='application/json')

        all_tags = self.onto_db.get_ontology_conditionals(self.source, [], show_all=True)
        if rules_type == 'fabric':
            errors = self.custom_rules_db.parse_fabric_sheets(file, ontology, all_tags)
        else:
            errors = self.custom_rules_db.parse_sheets(file, ontology, all_tags)
        if len(errors) > 0:
            response = {'status': {'code': -1, 'message': 'Errors'}, 'errors': errors}
        else:
            response = {'status': {'code': 0, 'message': 'OK'}}
        return Response(json.dumps(response), mimetype='application/json')

    def product_get(self, request):
        t = self.jinja_env.get_template('product.html')
        return Response(t.render(), mimetype='text/html')

    def product_post(self, request, vendor_name):
        # https://db.tools.streamoid.com/feed/insecure/get_data/?path=ABFRL/autoscribe/vanheusen_e5d421d49b0049548d4cbfc90285715f.json

        # 1. check vendor token
        data = self._check_vendor_token(request, vendor_name)
        if len(data) > 0:
            return Response(json.dumps(data), mimetype='application/json')

        # 2. convert response to internal format
        vendor_db = get_db_name(vendor_name)
        data = {'data': {'vendor_name': vendor_db}, 'status': {}}
        try:
            logger.info(request.form)
            style_codes = request.form.get('data')
            logger.info(style_codes)
            style_codes = json.loads(style_codes)
            data['data']['StyleCodes'] = style_codes
        except Exception as e:
            logger.exception(str(e))
            data['status']['code'] = -1
            data['status']['message'] = 'No data with styleCode found!!'
            return Response(json.dumps(data), mimetype='application/json')

        # 3. save data to DB
        details = {}
        try:
            details = self.save_products(vendor_db, style_codes)
            # notify_slack(vendor_db, link)
            data['status']['code'] = 0
            data['status']['message'] = 'success'
            data['data']['details'] = details
            data['data']['style_code'] = len(data['data']['StyleCodes'])
            del data['data']['StyleCodes']
        except Exception as e:
            logger.exception(str(e))
            data['status']['code'] = -1
            data['status']['message'] = str(e)

        # 4. trigger pre-computation
        try:
            valid_style_codes = [k for k, v in details.items() if v.get('code', -1) > -1]
            self._trigger_precompute(vendor_name, valid_style_codes)
        except Exception as e:
            logger.exception(str(e))

        return Response(json.dumps(data), mimetype='application/json')

    def _trigger_precompute(self, vendor_name, style_codes):
        logger.info('Triggering pre-processing for cataloging: %s', style_codes)
        products = self.get_products(vendor_name, style_codes)
        for row in products:
            logger.info(row)
            style_code = row['StyleCode']
            if 'ImageURLs' not in row:
                logger.warning('Missing image URLs: %s', style_code)
                continue
            image_urls = row['ImageURLs'].split(',')
            task = app.send_task('client_autoscribe_worker.precompute_and_save',
                                 (vendor_name, style_code, image_urls))
            logger.info('Triggered pre-compute: %s', task.task_id)

    def reject_get(self, request):
        t = self.jinja_env.get_template('reject.html')
        vendor_tokens = self.get_vendor_tokens()
        vendors = sorted(list(vendor_tokens.keys()))
        return Response(t.render(vendors=vendors), mimetype='text/html')

    def reject_post(self, request, vendor_name):
        """{"StyleCode": "ASSFWSPFS94011", "Rejects": [{"Remarks": "No issue", "Channel":
        "Allen Solly"}, {"Remarks": "Pack of should be 1, not 2", "Channel": "Flipkart"},
        {"Remarks": "No issue", "Channel": "Paytm"}, {"Remarks": "No issue", "Channel":
        "TataCliq"}, {"Remarks": "No issue", "Channel": "Amazon"}, {"Remarks": "No issue",
        "Channel": "Myntra"}, {"Remarks": "No issue", "Channel": "Limeroad"}],
        "batch_number": 39, "RequestID": "1196", "product_code": "ASSFWSPFS94011",
        "product_uuid": "decdb0809f3f4f61a07fac93ce5204be"}"""

        # 1. check vendor token
        data = self._check_vendor_token(request, vendor_name)
        if len(data) > 0:
            return Response(json.dumps(data), mimetype='application/json')

        # 2. convert response to internal format
        vendor_db = get_db_name(vendor_name)
        data = {'data': {'vendor_name': vendor_db}, 'status': {}}

        try:
            style_codes = request.form.get('data')
            logger.info(style_codes)
            style_codes = json.loads(style_codes)
            data['data']['style_code'] = style_codes
            if isinstance(style_codes, list):
                style_codes = style_codes[0]
        except Exception as e:
            logger.exception(str(e))
            data['status']['code'] = -1
            data['status']['message'] = 'No data with styleCode found!!'
            return Response(json.dumps(data), mimetype='application/json')

        # 3. save data to DB
        try:
            data = self.save_rejects(vendor_db, style_codes)
        except Exception as e:
            logger.exception(str(e))
            data['status']['code'] = -1
            data['status']['message'] = str(e)

        return Response(json.dumps(data), mimetype='application/json')

    def export_get(self, request, vendor_name):
        style_codes1 = request.args.get('style_codes', '').split(',')
        style_codes = self.get_products(vendor_name, style_codes1)
        out = []
        for row in style_codes:
            logger.info(row)
            if 'ImageURLs' not in row:
                logger.warning('Missing image URLs')
                continue
            for url in row['ImageURLs'].split(','):
                out.append({'style_code': row['StyleCode'],
                            'image_url': url,
                            'timestamp': row['last_modified']})

        out = sorted(out, key=lambda x: x['timestamp'], reverse=True)
        # logger.info(out)
        df = pd.DataFrame(out)
        f = StringIO()
        df.to_csv(f, index=False)
        return Response(f.getvalue(), mimetype='text/csv')

    def export_new_get(self, request, vendor_name):
        style_codes = self.get_new_products(vendor_name)
        out = []
        for row in style_codes:
            logger.info(row)
            if row.get('ImageURLs', None) is None:
                logger.warning('Missing image URLs')
                continue
            for url in row['ImageURLs'].split(','):
                out.append({'style_code': row['StyleCode'],
                            'image_url': url,
                            'timestamp': row['last_modified']})

        out = sorted(out, key=lambda x: x['timestamp'], reverse=True)
        # logger.info(out)
        df = pd.DataFrame(out)
        f = StringIO()
        df.to_csv(f, index=False)
        return Response(f.getvalue(), mimetype='text/csv')

    def export_new_precomp_get(self, request, vendor_name):
        products = self.get_new_products_precomputed(vendor_name)
        out = []
        for output in products.values():
            out.extend([vi for v in output.values() for vi in v])
        df = pd.DataFrame(out)
        f = StringIO()
        df.to_csv(f, index=False)
        return Response(f.getvalue(), mimetype='text/csv')

    def export_rejected_get(self, request, vendor_name):
        out = self.export_rejected_products(vendor_name)
        # logger.info(out)
        df = pd.DataFrame(out)
        f = StringIO()
        df.to_csv(f, index=False)
        return Response(f.getvalue(), mimetype='text/csv')

    def import_data_post(self, request, vendor_name):
        if 'file' not in request.files:
            response = {'code': -1, 'message': 'Missing mandatory argument file!'}
            return Response(json.dumps(response), mimetype='application/json')

        file1 = request.files['file']
        data = pd.read_csv(file1, keep_default_na=False, dtype=str)
        batch_number, style_codes = self.import_data(vendor_name, data)

        response = {'code': 0, 'message': 'Products updated to batch number %d' % batch_number}
        return Response(json.dumps(response), mimetype='application/json')

    def post_to_client_get(self, request, vendor_name):
        task = app.send_task('client_autoscribe_worker.post_to_client', (vendor_name,))
        response = {'status': {'code': 0, 'message': 'OK'}, 'task_id': task.task_id}
        return Response(json.dumps(response), mimetype='application/json')

    def vendor_tokens_get(self, request):
        vendor_tokens = self.get_vendor_tokens()
        return Response(json.dumps(vendor_tokens), mimetype='application/json')

    def review_products_get(self, request, vendor_name):
        t = self.jinja_env.get_template('review.html')
        data = self.get_products_for_review(vendor_name)
        targets = [self.brand_target] + list(self.targets.keys())
        return Response(t.render(vendor=vendor_name, data=data, targets=targets),
                        mimetype='text/html')

    def dashboard_get(self, request):
        vendor_tokens = self.get_vendor_tokens()
        vendors = sorted(list(vendor_tokens.keys()))
        new_products = {vendor: len(self.get_new_products(vendor)) for vendor in vendors}
        new_products_precomp = {vendor: len(self.get_new_products_precomputed(vendor))
                                for vendor in vendors}
        rejected_products = {vendor: len(self.get_rejected_products(vendor)) for vendor in vendors}
        to_review_products = {vendor: len(self.get_products_for_review(vendor)) for vendor in vendors}
        to_push_products = {vendor: len(self.get_products_to_post(vendor)) for vendor in vendors}
        t = self.jinja_env.get_template('dashboard.html')
        return Response(t.render(vendors=vendors, new_products=new_products,
                                 new_products_precomp=new_products_precomp,
                                 rejected_products=rejected_products,
                                 to_review_products=to_review_products,
                                 to_push_products=to_push_products), mimetype='text/html')

    def mark_reviewed_post(self, request, vendor_name, style_code):
        self.mark_reviewed(vendor_name, style_code)
        response = {'code': 0, 'message': 'OK'}
        return Response(json.dumps(response), mimetype='application/json')

    def translations_get(self, request, vendor_name):
        t = self.jinja_env.get_template('translations.html')
        data = sorted(self.get_translations(vendor_name).items(), key=lambda x: x[0])
        return Response(t.render(vendor=vendor_name, data=data), mimetype='text/html')

    def translations_post(self, request, vendor_name):
        values = request.form.getlist('values')
        data = {}
        targets = list(self.targets.values()) + list(self.brand_ontologies.values())
        for x in targets:
            if x in values:
                data[x] = True
            else:
                data[x] = False
        logger.info(data)
        self.set_translations(vendor_name, data)
        response = {'status': {'code': 0, 'message': 'OK'}, 'data': data}
        return Response(json.dumps(response), mimetype='application/json')

    def view_get(self, request, vendor_name, style_code):
        data = self.get_data(vendor_name, style_code)
        if data is None:
            response = {'status': {'code': -1, 'message': 'Not found!'}}
        else:
            response = {'status': {'code': 0, 'message': 'OK'}, 'data': data}
        return Response(json.dumps(response), mimetype='application/json')

    def report_for_date_get(self, request, date):
        f = StringIO()
        self.create_report(date, date, f)
        return Response(f.getvalue(), mimetype='text/csv')

    def translate_post(self, request, vendor_name):
        try:
            data = json.loads(request.form['data'])
        except Exception as e:
            logger.exception(str(e))
            response = {'status': {'code': -1, 'message': str(e)}}
            return Response(json.dumps(response), mimetype='application/json')

        translations = self.get_translations(vendor_name)
        targets = [(self.brand_target, self.brand_ontologies.get(vendor_name, self.default_brand_ontology))] + list(
            self.targets.items())
        output = {}
        for style_code, info in data.items():
            product = info.get('product', {})
            curated_data = info.get('curated', {})
            output[style_code] = self.translate(vendor_name, translations,
                                                curated_data, product, targets,
                                                enable_non_image=False)
        response = {'status': {'code': 0, 'message': 'OK'}, 'data': output}
        return Response(json.dumps(response), mimetype='application/json')

    def translate_split_post(self, request, vendor_name):
        try:
            data = json.loads(request.form['data'])
        except Exception as e:
            logger.exception(str(e))
            response = {'status': {'code': -1, 'message': str(e)}}
            return Response(json.dumps(response), mimetype='application/json')

        translations = self.get_translations(vendor_name)
        targets = [(self.brand_target, self.brand_ontologies.get(vendor_name, self.default_brand_ontology))] + list(
            self.targets.items())
        output = {}
        for style_code, info in data.items():
            product = info.get('product', {})
            curated_data = info.get('curated', {})
            output[style_code] = self.translate_split(vendor_name, translations,
                                                      curated_data, product, targets)
        response = {'status': {'code': 0, 'message': 'OK'}, 'data': output}
        return Response(json.dumps(response), mimetype='application/json')

    def translate_csv_get(self, request, vendor_name):
        t = self.jinja_env.get_template('translate_csv.html')
        return Response(t.render(vendor=vendor_name), mimetype='text/html')

    def translate_csv_ontology_get(self, request, ontology):
        t = self.jinja_env.get_template('translate_csv.html')
        return Response(t.render(vendor=ontology), mimetype='text/html')

    def translate_csv_ontology_post(self, request, ontology):
        try:
            data = pd.read_csv(request.files['file'], keep_default_na=False, dtype=str)
        except Exception as e:
            response = {'status': {'code': -1, 'message': 'Missing or invalid file: %s' % str(e)}}
            return Response(json.dumps(response), mimetype='application/json')

        # translations = self.get_translations(vendor_name)
        # targets = [(self.brand_target, self.brand_ontologies.get(vendor_name, self.default_brand_ontology))] + list(
        #     self.targets.items())
        translations = {ontology: True}
        targets = [(ontology, ontology)]
        output = {}
        for idx, row in data.iterrows():
            curated_data = dict(row)
            logger.info(curated_data)
            style_code = curated_data['product_id']
            output[style_code] = self.translate(ontology, translations, curated_data, {}, targets)
            # output[style_code]['streamoid_attributes'] = curated_data

        # style_code <-> ontology
        output1 = {}
        for style_code, details in output.items():
            for ontology_field, info in details.items():
                if ontology_field not in output1:
                    output1[ontology_field] = {}
                output1[ontology_field][style_code] = info

        details = output1[ontology]
        out = []
        for style_code, info in details.items():
            info['style_code'] = style_code
            out.append(info)
        buf = StringIO()
        pd.DataFrame(out).to_csv(buf, index=False)

        return Response(buf.getvalue(), mimetype='text/csv')

    def translate_csv_post(self, request, vendor_name):
        try:
            data = pd.read_csv(request.files['file'], keep_default_na=False, dtype=str)
        except Exception as e:
            response = {'status': {'code': -1, 'message': 'Missing or invalid file: %s' % str(e)}}
            return Response(json.dumps(response), mimetype='application/json')

        translations = self.get_translations(vendor_name)
        targets = [(self.brand_target, self.brand_ontologies.get(vendor_name, self.default_brand_ontology))] + list(
            self.targets.items())
        output = {}
        for idx, row in data.iterrows():
            curated_data = dict(row)
            logger.info(curated_data)
            style_code = curated_data['product_id']
            output[style_code] = self.translate(vendor_name, translations, curated_data, {}, targets)
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

    def report_get(self, request):
        t = self.jinja_env.get_template('reporting.html')
        return Response(t.render(), mimetype='text/html')

    def report_post(self, request):
        start_date = request.form['start_date']
        end_date = request.form['end_date']
        f = StringIO()
        self.create_report(start_date, end_date, f)
        return Response(f.getvalue(), mimetype='text/csv')

    def products_upload_get(self, request):
        t = self.jinja_env.get_template('upload.html')
        vendor_tokens = self.get_vendor_tokens()
        vendors = list(vendor_tokens.keys())
        return Response(t.render(vendors=vendors), mimetype='text/html')

    def products_upload_post(self, request):
        try:
            vendor_name = request.form['vendor']
            data = pd.read_excel(request.files['file'], keep_default_na=False, dtype=str)
            data.fillna('', inplace=True)
        except Exception as e:
            response = {'status': {'code': -1, 'message': 'Missing or invalid file: %s' % str(e)}}
            return Response(json.dumps(response), mimetype='application/json')

        for column in ['Style code', 'PID']:
            if column not in data.columns:
                response = {'status': {'code': -1, 'message': 'Missing column: %s' % column}}
                return Response(json.dumps(response), mimetype='application/json')

        vendor_db = get_db_name(vendor_name)
        style_codes = []
        for idx, row in data.iterrows():
            image_urls = []
            for i in range(1, 11):
                field = 'id_image%d' % i
                if field not in row:
                    break
                val = str(row[field]).strip()
                if val.startswith('http'):
                    image_urls.append(val)
                row.pop(field)
            logger.info(dict(row))
            out = {key.strip(): val.strip() for key, val in row.items()}
            out['ImageURLs'] = ','.join(image_urls)
            out['StyleCode'] = out.pop('Style code')
            out['RequestID'] = 'A' + str(row['PID'])
            logger.info(out)
            style_codes.append(out)

        details = self.save_products(vendor_db, style_codes)
        return Response(json.dumps(details), mimetype='application/json')

    def create_vendor_get(self, request):
        t = self.jinja_env.get_template('create_vendor.html')
        return Response(t.render(), mimetype='text/html')

    def create_vendor_post(self, request):
        try:
            vendor_name = request.form['vendor']
            token = request.form['token']
        except Exception as e:
            output = {'code': -1, 'status': str(e)}
            return Response(json.dumps(output), mimetype='application/json')

        self.upsert_vendor(vendor_name, token)
        return Response(json.dumps({'code': 0, 'status': 'OK'}), mimetype='application/json')


application = ClientAutoscribeAPI('localhost')

if __name__ == '__main__':
    from werkzeug.serving import run_simple

    run_simple('0.0.0.0', 4005, application)
