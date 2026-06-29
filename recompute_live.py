import os
import sys
import logging

logger = logging.getLogger(__name__)

from argparse import ArgumentParser

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from client_autoscribe.client_autoscribe_config import app
from client_autoscribe.custom_rules_v2 import get_db_name
from client_autoscribe.client_autoscribe_db_v2 import ClientAutoscribeDB

if __name__ == '__main__':
    logging.basicConfig(
        format=
        '%(asctime)s - %(process)d - %(name)s - %(filename)s:%(lineno)d - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        level=logging.INFO)

    parser = ArgumentParser()
    parser.add_argument('vendor')
    parser.add_argument('brand')
    args = parser.parse_args()

    vendor_name = args.vendor
    brand_name = args.brand

    db = ClientAutoscribeDB('localhost')
    vendor_db = get_db_name(vendor_name)
    coll_name = db.products_coll_pattern % brand_name
    coll = db.client[vendor_db][coll_name]
    for row1 in coll.find({}, {'_id': True}):
        style_code = row1['_id']
        row = db.get_product(vendor_name, brand_name, style_code)
        logger.info(row)
        if 'ImageURLs' not in row:
            logger.warning('Missing image URLs: %s', style_code)
            continue

        image_urls = row['ImageURLs'].split(',')
        task = app.send_task('client_autoscribe_worker_v2.precompute_and_save',
                             (vendor_name, brand_name, style_code, image_urls))
        logger.info('Triggered pre-compute: %s', task.task_id)
