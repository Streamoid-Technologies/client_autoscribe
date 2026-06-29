import os
import sys
import logging

logger = logging.getLogger(__name__)

import json

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))
from client_autoscribe.integrations.base_adapter import BaseAdapter


class VendorAdapter(BaseAdapter):
    def __init__(self, vendor_config, brand_config):
        super(VendorAdapter, self).__init__(vendor_config, brand_config)

    def convert_input(self, data, **kwargs):
        filename = kwargs.get('filename', None)
        if filename is not None and filename.lower().endswith('.json'):
            data = json.loads(data.read()).items()

        style_codes = []
        for k, v in data:
            # logger.info(dict(row))
            out = {'ImageURLs': v, 'StyleCode': str(k), 'RequestID': str(k)}
            # logger.info(out)
            style_codes.append(out)
        return style_codes

    def get_output_file(self, data, **kwargs):
        output = []
        for val in data.values():
            val1 = val['brand_attributes']
            val1['product_code'] = val['StyleCode']
            output.append(val1)

        return json.dumps(output), 'application/json'
