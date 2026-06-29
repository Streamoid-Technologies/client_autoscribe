import logging
import os
import sys

logger = logging.getLogger(__name__)

from pymongo import MongoClient
from copy import copy
from tqdm import tqdm

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from client_autoscribe.client_autoscribe_db import ClientAutoscribeDB as DB1
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


def create_config(db1, db2, vendor_name):
    brand_tokens = db1.get_vendor_tokens()
    brand_ontologies = {"allen_solly": "Allen Solly-MP",
                        "louis_philippe": "Louis Philippe-MP",
                        "vanheusen": "Van Heusen-MP",
                        "peter_england": "Peter England-MP",
                        "peter_england_red": "Peter England Red-MP",
                        "american_eagle": "American Eagle-MP",
                        "simon_carter": "Simon Carter-MP",
                        "people": "People-MP"}
    targets = {"ajio_attributes": "Ajio-MP",
               "tatacliq_attributes": "Tatacliq-MP",
               "myntra_attributes": "Myntra-MP",
               "amazon_attributes": "Amazon-MP",
               "limeroad_attributes": "Limeroad-MP",
               "flipkart_attributes": "Flipkart-MP",
               "paytm_attributes": "Paytm-MP",
               "nykaa_attributes": "Nykaa-MP"}

    db2.set_vendor_config(vendor_name, {'curation_ontology': 'Streamoid-MP'})

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


def copy_data(db1, db2, vendor_name):
    brand_tokens = db1.get_vendor_tokens()
    vendor_db = get_db_name(vendor_name)
    client = MongoClient()
    for brand in brand_tokens:
        in_vendor = get_db_name(brand)
        for coll_name in ['products', 'live', 'cataloging', 'rejects']:
            out_coll = coll_name + ':' + brand
            if coll_name == 'rejects':
                client[vendor_db][out_coll].drop()
            copy_collection(in_vendor, coll_name, vendor_db, out_coll)


if __name__ == '__main__':
    logging.basicConfig(
        format=
        '%(asctime)s - %(process)d - %(name)s - %(filename)s:%(lineno)d - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        level=logging.INFO)

    # client = MongoClient()

    # 1. set vendor and brand preferences
    db1 = DB1('localhost')
    db2 = DB2('localhost')
    vendor_name = 'abfrl_lbrd_prod'
    create_config(db1, db2, vendor_name)

    # 2. copy data
    copy_data(db1, db2, vendor_name)
