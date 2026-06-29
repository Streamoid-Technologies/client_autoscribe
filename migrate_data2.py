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


def main_old():
    client = MongoClient()
    cdb1 = client['abfrl_custom_rules']
    for brand, token in brand_tokens.items():
        targets1 = copy(targets)

        trans = db1.get_translations(brand)
        targets2 = {k: v for k, v in targets1.items() if trans.get(v, True)}

        brand_ontology = brand_ontologies.get(brand, 'Brand-MP')
        targets2['brand_attributes'] = brand_ontology

        coll = cdb1[brand_ontology]
        custom_rules = True if coll.count() > 0 else False

        config = {'token': token,
                  'brand_ontology': brand_ontology,
                  'targets': targets2,
                  'custom_rules': custom_rules}
        db2.set_brand_config(vendor_name, brand, config)

        in_vendor = 'abfrl_custom_rules'
        in_coll = brand_ontology
        out_vendor = get_db_name(vendor_name)
        out_coll = 'custom_rules:%s' % brand
        if custom_rules:
            copy_collection(in_vendor, in_coll, out_vendor, out_coll)


if __name__ == '__main__':
    logging.basicConfig(
        format=
        '%(asctime)s - %(process)d - %(name)s - %(filename)s:%(lineno)d - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        level=logging.INFO)

    # client = MongoClient()

    # 1. set vendor and brand preferences
    # db1 = DB1('localhost')
    db2 = DB2('localhost')
    vendor_name = 'abfrl_lbrd_prod'

    brands = db2.list_brands(vendor_name)
    done = set()
    for brand_name in brands:
        config = db2.get_brand_config(vendor_name, brand_name)
        brand_ontology = config['brand_ontology']
        logger.info(brand_name)
        in_vendor = 'abfrl_custom_rules'
        out_vendor = get_db_name(vendor_name)
        out_coll = 'custom_rules:%s' % brand_ontology
        for ontology in config['targets'].values():
            logger.info(ontology)
            in_coll = ontology
            out_coll = 'custom_rules:%s' % ontology
            if ontology == brand_ontology:
                out_coll += ':' + brand_name
            if out_coll not in done:
                logger.info(out_coll)
                copy_collection(in_vendor, in_coll, out_vendor, out_coll)
                done.add(out_coll)
