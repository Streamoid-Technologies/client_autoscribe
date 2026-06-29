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

from io import StringIO
from werkzeug.routing import Map, Rule
from werkzeug.wrappers import Request, Response
from jinja2 import Environment, FileSystemLoader

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from client_autoscribe.client_autoscribe_db_v2 import ClientAutoscribeDB


class TrackingAPI(ClientAutoscribeDB):
    def __init__(self, mongo_host):
        super().__init__(mongo_host)
        prefix = '/api/tracking/'
        self.url_map = Map(
            [
                # vendors -> brands -> requests -> products -> status

                # brands: vendor-level info
                Rule('%s/vendors/<string:vendor_name>/brands' % prefix,
                     endpoint='brands_get', methods=['GET']),

                # requests:
                Rule('%s/vendors/<string:vendor_name>/brands/<string:brand_name>/requests' % prefix,
                     endpoint='requests_get', methods=['GET']),

                # download CSVs
                Rule('%s/vendors/<string:vendor_name>/brands/<string:brand_name>'
                     '/requests/<string:request_id>/status/<string:status>/download' % prefix,
                     endpoint='download_get', methods=['GET']),
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

    # application specific code below
    def brands_get(self, request, vendor_name):
        brands = self.list_brands(vendor_name)
        data = {}
        for brand_name in brands:
            count, ts = self.get_distinct_requests(vendor_name, brand_name, only_count=True)
            data[brand_name] = {'requests': count, 'timestamp': ts}

        status = {'code': 0, 'message': 'OK'}
        response = {'status': status, 'data': data}
        return Response(json.dumps(response), mimetype='application/json')

    def requests_get(self, request, vendor_name, brand_name):
        # pagination
        offset = int(request.args.get('offset', 0))
        limit = int(request.args.get('limit', 10))
        # search
        search = request.args.get('search', None)
        from_ts = request.args.get('since', None)
        if from_ts is not None:
            from_ts = from_ts + ' 00:00:00'
        to_ts = request.args.get('upto', None)
        if to_ts is not None:
            to_ts = to_ts + ' 23:59:59'

        # compute summary
        # count = self.get_distinct_requests(vendor_name, brand_name, only_count=True)
        processed = self.products_processed(vendor_name, brand_name)
        recurated = self.get_products_to_recurate(vendor_name, brand_name)
        recurated = list(set(recurated) & set(processed))
        pushed = self.products_pushed(vendor_name, brand_name, only_count=True)
        summary = {'processed': len(processed), 're-processed': len(recurated), 'pushed': pushed}

        # get top request ids
        request_list = self.get_requests(vendor_name, brand_name, search, from_ts, to_ts)
        summary['requests'] = len(request_list)
        request_list = request_list[offset: offset + limit]

        # get status for requests
        data = []
        states = ['ready', 'curated', 're-curated', 'reviewed', 'pushed', 'failed']
        for request in request_list:
            data1 = {k: 0 for k in states}
            data1['request_id'] = request['RequestID']
            data1['last_modified'] = request['last_modified']
            data1['total'] = len(request['products'])
            for style_code in request['products']:
                status = self.get_product_status(vendor_name, brand_name, style_code)
                data1[status] += 1
            data.append(data1)

        # get statuses for those requests
        status = {'code': 0, 'message': 'OK'}
        response = {'summary': summary, 'data': data, 'status': status}
        return Response(json.dumps(response), mimetype='application/json')

    def download_get(self, request, vendor_name, brand_name, request_id, status):
        products = self.get_products_by_status(vendor_name, brand_name, request_id, status)
        df = pd.DataFrame({'StyleCode': products})
        f = StringIO()
        df.to_csv(f, index=False)
        return Response(f.getvalue(), mimetype='text/csv')


application = TrackingAPI('localhost')

if __name__ == '__main__':
    from werkzeug.serving import run_simple

    run_simple('0.0.0.0', 4010, application)
