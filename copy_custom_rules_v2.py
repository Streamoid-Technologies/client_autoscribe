import logging
import os
import sys

logger = logging.getLogger(__name__)

from pymongo import MongoClient
from copy import copy
from tqdm import tqdm

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
# from client_autoscribe.client_autoscribe_db import ClientAutoscribeDB as DB1
from client_autoscribe.client_autoscribe_db_v2 import ClientAutoscribeDB as DB2, get_db_name


def copy_collection(in_vendor, in_coll,
                    out_vendor, out_coll):
    client = MongoClient()
    coll1 = client[in_vendor][in_coll]
    coll2 = client[out_vendor][out_coll]
    logger.info('%s %s => %s %s', in_vendor, in_coll, out_vendor, out_coll)
    for row in tqdm(coll1.find()):
        try:
            row.pop('', None)
            coll2.replace_one({'_id': row['_id']}, row, upsert=True)
        except Exception as _:
            logger.exception(row)


def remove_old(vendor_name):
    client = MongoClient()
    db2 = DB2('localhost')
    out_vendor = get_db_name(vendor_name)
    for brand_name in db2.list_brands(vendor_name):
        out_coll = 'custom_rules:%s' % brand_name
        client[out_vendor][out_coll].drop()


def copy_custom_rules(vendor1, vendor2):
    client = MongoClient()
    db1 = get_db_name(vendor1)
    db2 = get_db_name(vendor2)
    for coll_name in client[db1].list_collection_names():
        print(coll_name)
        if coll_name.startswith('custom_rules:'):
            copy_collection(db1, coll_name, db2, coll_name)


if __name__ == '__main__':
    logging.basicConfig(
        format=
        '%(asctime)s - %(process)d - %(name)s - %(filename)s:%(lineno)d - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        level=logging.INFO)

    #remove_old('abfrl_lbrd_prod')
    copy_custom_rules('abfrl_lbrd_prod', 'abfrl_test')
