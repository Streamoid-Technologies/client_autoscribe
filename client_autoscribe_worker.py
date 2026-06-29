import os
import sys
import logging

logger = logging.getLogger(__name__)

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import requests
from time import time

from client_autoscribe.client_autoscribe_config import app
from client_autoscribe.client_autoscribe_db import ClientAutoscribeDB


@app.task
def post_to_client(vendor_name):
    db = ClientAutoscribeDB('localhost')
    db.post_to_client(vendor_name)


def precompute_live(vendor, image_url, style_code):
    url = 'https://cataloging.streamoid.com/calm/cataloging/vendors/%s/images' % vendor
    data = {'url': image_url}
    logger.info(image_url)
    t1 = time()
    out = []
    r = requests.post(url, data=data)
    if r.ok:
        out = r.json().get('data', [])
        for item in out:
            item['product_id'] = style_code
            item['product_code'] = style_code
            item['selected'] = 0
            item['url'] = image_url
    else:
        logger.error('%s %s %s', r.status_code, r.text, image_url, style_code)
    logger.info('Time taken: %f sec', time() - t1)

    return out


@app.task
def precompute_and_save(vendor_name, style_code, image_urls):
    output = {}
    for image_url in image_urls:
        try:
            # TODO: get cataloging vendor name based on client autoscribe vendor
            cataloging_vendor = 'abfrl_lbrd_prod'
            output[image_url] = precompute_live(cataloging_vendor, image_url, style_code)
        except Exception as e:
            logger.exception(str(e))

    if len(output) > 0:
        db = ClientAutoscribeDB('localhost')
        db.save_live(vendor_name, style_code, output)
