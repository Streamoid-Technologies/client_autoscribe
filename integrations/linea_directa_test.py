import os
import sys
import logging

import pandas as pd
from io import BytesIO
import requests
import string

logger = logging.getLogger(__name__)

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))
from client_autoscribe.integrations.base_adapter import BaseAdapter


def export_as_excel(data):

    excel_file = BytesIO()
    with pd.ExcelWriter(excel_file) as writer:
        for sheet, df in data.items():
            df.to_excel(writer, sheet_name=sheet, index=False)
    return excel_file.getvalue()


class VendorAdapter(BaseAdapter):

    def __init__(self, vendor_config, brand_config):
        super(VendorAdapter, self).__init__(vendor_config, brand_config)

    def convert_input(self, data, **kwargs):
        filename = kwargs.get('filename', None)
        if filename is None:
            return data
        style_codes = []
        if filename.lower().endswith('.csv'):
            data = pd.read_csv(data, keep_default_na=False, dtype=str)
            data.fillna('', inplace=True)
            data = [dict(row) for _, row in data.iterrows()]
        for row in data:
            image_urls = []
            out = {key.strip(): val.strip() for key, val in row.items()}
            out['ImageURLs'] = out.pop('image_url')
            out['StyleCode'] = out.pop('style_code')
            logger.info(out)
            style_codes.append(out)
        return style_codes

    def convert_output(self, output, **kwargs):
        output = super().convert_output(output)
        if 'RequestID' not in output:
            return output
        if output['RequestID'] is not None:
            output['RequestID'] = str(output['RequestID'])

        vendor_name = self.vendor_config['_id']
        brand_name = self.brand_config['_id']
        style_code = output['StyleCode']
        product = kwargs.get('product', None)
        if product:
            url = product.get("ImageURLs", None)
            output.update({"URL": url})
        for key, value in output['attributes'].items():
            output['attributes'][key] = value.capitalize() if value not in ['N/A', 'N/a', 'n/a'] else value.upper()
        return output

    def get_output_file(self, data, **kwargs):

        english, spanish = [], []
        for idx, row in data.items():
            style_code = row['StyleCode']
            RequestID = row['RequestID']
            URL = row['URL']

            eng = row['attributes']
            spa = row['brand_attributes']

            eng = {k: string.capwords(v) if isinstance(v, str) else v for k, v in eng.items()}
            spa = {k: string.capwords(v) if isinstance(v, str) else v for k, v in spa.items()}

            eng.update({'StyleCode': style_code, 'RequestID': RequestID, 'URL': URL})
            spa.update({'StyleCode': style_code, 'RequestID': RequestID, 'URL': URL})

            english.append(eng)
            spanish.append(spa)

        excel = export_as_excel({'English': pd.DataFrame(english), 'Spanish': pd.DataFrame(spanish)})
        return excel, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
