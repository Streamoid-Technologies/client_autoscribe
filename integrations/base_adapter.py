import json
import os
import sys
import logging

logger = logging.getLogger(__name__)

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))


class BaseAdapter(object):
    def __init__(self, vendor_config=None, brand_config=None):
        self.vendor_config = vendor_config
        self.brand_config = brand_config

    # products input mechanisms
    def convert_input(self, data, **kwargs):
        """
        vendor data => vendor storage
        :param data:
        :return:
        """
        return data

    def pull_from_vendor(self, **kwargs):
        """
        endpoint => vendor data => vendor storage
        :return:
        """
        return

    # products output mechanisms
    def convert_output(self, output: dict, **kwargs):
        """
        translated response => vendor output format
        :return:
        """
        if output is None:
            return
        for target, details in output.items():
            if isinstance(details, dict):
                details.pop('Sheet Name', None)
                details.pop('Sub-sheet Name', None)
        return output

    def post_request_to_vendor(self, request_id, outputs, **kwargs):
        """
        Accepts data in vendor's output format
        vendor output format => endpoint
        Returns dict with {style_code: true/false} for success/failures
        Post data back to client based on some configured API
        :param request_id:
        :param output:
        :return:
        """
        out = {}
        for style_code, output in outputs.items():
            try:
                out[style_code] = self.post_to_vendor(output)
            except Exception as e:
                logger.exception('%s %s %s %s %s', self.vendor_config['_id'],
                                 self.brand_config['_id'], request_id, style_code, str(e))
                out[style_code] = False
        return out

    def post_to_vendor(self, output, **kwargs):
        """
        Accepts data in vendor's output format
        vendor output format => endpoint
        Returns true/false for success/failures
        Post data back to client based on some configured API
        :param output:
        :return:
        """
        return False

    def get_output_file(self, data, **kwargs):
        """
        Accepts data in vendor's output format
        Returns output file in vendor-specific format
        :param data:
        :param kwargs:
        :return:
        """
        return json.dumps(data), 'application/json'

    def translate(self, target_ontology, product, curated, translated, **kwargs):
        """
        Custom translation for vendor brand
        :param translated:
        :param product:
        :param curated:
        :param kwargs:
        :return:
        """
        return {}

    def reverse_translate(self, product, **kwargs):
        """
        Custom translation from brand_ontology to Streamoid-MP
        """
        return {}
