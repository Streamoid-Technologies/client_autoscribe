import os
import sys
import logging

logger = logging.getLogger(__name__)

import json
import requests
import pandas as pd
from time import time
from io import StringIO

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))
from client_autoscribe.integrations.base_adapter import BaseAdapter


def update_curation_status(vendor, productId, curation_status):
    url = 'https://rulesengine.service.streamoid.com/rules_v2/setCurationStatus/%s' % vendor
    data = {'productIds': productId, 'ov': 4, 'curation_status': curation_status}
    r = requests.post(url, data=data)
    if r.ok:
        res = r.json()
        logger.info('%s %s', r.status_code, res)
        status = res.get('status', {})
        message = status.get('message', '')
        msg_ok = 'Unable to find all ids in product_meta' in message
        code = status.get('code', -1)
        # 21/03/2023: fix for missing products in child store while
        # curation happens in parent store
        code = 0 if code == 1 and msg_ok else code
        if code != 0:
            return False
        return True
    logger.error('%s %s', r.status_code, r.text)
    return False


class VendorAdapter(BaseAdapter):
    def __init__(self, vendor_config, brand_config):
        super(VendorAdapter, self).__init__(vendor_config, brand_config)

    def convert_input(self, data, **kwargs):
        filename = kwargs.get('filename', None)
        style_codes = []
        if filename is None:
            for x in data:
                if 'gender' in x:
                    x['gender'] = x['gender'].lower()
                style_codes.append(x)
            return style_codes

        if filename.lower().endswith('.csv'):
            data = pd.read_csv(data, keep_default_na=False, dtype=str)
            data.fillna('', inplace=True)
            data = [dict(row) for _, row in data.iterrows()]

            for row in data:
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

        return style_codes

    def post_to_vendor(self, output, **kwargs):
        # https://db.tools.streamoid.com/feed/insecure/get_data/?path=/autoscribe/v_peter_england_autoscribe_2020_06_01_08_22_16.json
        # Trigger unknown!
        brand_name = self.brand_config['_id']
        url = 'http://rulesengine.service.streamoid.com/rules_v2/setProductMetadata/%s' % brand_name

        logger.info(output)
        output1 = output['brand_attributes']
        # XXX: convert to upper case as expected
        gender_map = {'men': 'Men', 'women': 'Women'}
        # reviewed in cataloging
        style_code = output['StyleCode']
        gender = output1.get('gender', 'unknown')
        gender = gender_map.get(gender, gender)
        category = output1.get('category', 'unknown')
        metadata = {}
        for k, v in output1.items():
            if k in ['gender', 'category']:
                continue
            metadata[k] = [v]

        data = {'productIds': style_code, 'channel': 'calm', 'ov': 4, 'writer': 'calm',
                'gender': gender, 'category_name': category,
                'metadata': json.dumps(metadata)}

        logger.info(url)
        logger.info(data)
        t1 = time()
        r = requests.post(url, data=data, timeout=600)
        logger.info('Got response in %f sec', time() - t1)
        if r.ok:
            res = r.json()
            logger.info('%s %s', style_code, res)
            status = res.get('status', {})
            message = status.get('message', '')
            msg_ok = 'Unable to find all ids in product_meta' in message
            ok = status.get('code', -1)
            # 21/03/2023: fix for missing products in child store while
            # curation happens in parent store
            ok = 0 if ok == 1 and msg_ok else ok
            if ok != 0:
                return False
            ok = update_curation_status(brand_name, style_code, 'similar_ready')
            logger.info('Pushed: %s %s', brand_name, style_code)
            return ok
            # self.on_push(vendor_name, brand_name, style_code, requestid)
        else:
            logger.error('%s %s', r.status_code, r.content)
            return False

    def get_output_file(self, data, **kwargs):
        output = []
        for val in data.values():
            val1 = val['brand_attributes']
            val1['product_code'] = val['StyleCode']
            output.append(val1)

        f = StringIO()
        pd.DataFrame(output).to_csv(f, index=False)
        return f.getvalue(), 'text/csv'
