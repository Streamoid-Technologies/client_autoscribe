import requests
import logging
import os
import pandas as pd
import argparse
from client_autoscribe_db_v2 import get_db_name, ClientAutoscribeDB
logger = logging.getLogger(__name__)
logging.basicConfig(
    format="%(asctime)s - %(process)d - %(name)s - %(filename)s:%(lineno)d - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    level=logging.INFO,
)



def get_all_store_uuid_mapping():
    try:
        store_map_fpath = os.path.join(os.path.dirname(__file__), 'stores_map.csv')
        logger.info(store_map_fpath)
        df = pd.read_csv(store_map_fpath)
        return dict(zip(df['brands'], df['store_uuid']))
    except Exception as e:
        logger.error(f"{e}")
        return -1
    
def get_problem_style_codes(store_uuid):
    first_url = 'https://staging.caspr.products.streamoid.com/v1/products/filters/get'
    first_payload = {
        "store_uuid": store_uuid,
        "filters": {"status": "problem"},
        "doc_type": "product"
    }
    first_headers = {
        "accept": "application/json",
        "Content-Type": "application/json"
    }

    response = requests.post(first_url, json=first_payload, headers=first_headers)
    data = response.json()

    # Extract product_uuids from the response
    product_uuids = [item["product_uuid"] for item in data["data"]]
    # product_uuids = product_uuids[0:2]
    # Second API request using extracted product_uuids
    second_url = 'https://staging.caspr.products.streamoid.com/v1/products/get'
    second_payload = {
        "store_uuid": store_uuid,
        "products_list": [{"product_uuid": uuid} for uuid in product_uuids],
        "doc_type_list": [
            {
                "doc_type": "metadata",
                "ontology_list": [
                    {
                        "ontology_name": "SMP",
                        "ov": 31
                    }
                ]
            }
        ]
    }
    second_headers = {
        "accept": "application/json",
        "Content-Type": "application/json"
    }

    # Make the second API request
    second_response = requests.post(second_url, json=second_payload, headers=second_headers)



    second_data = second_response.json()
    style_codes = []

    # Iterate through the dictionary and extract Style Codes
    style_codes = [products['SMP'][0]['Style Code']
                for product_data in second_data.values()
                for category, products in product_data.items()
                if isinstance(products, dict) and 'SMP' in products and isinstance(products['SMP'], list) and products['SMP']
                if 'Style Code' in products['SMP'][0]]

    logger.info(f"Style codes with problem in catalogix: {style_codes}")
    return style_codes

def update_products_coll(db, vendor_name, brand_name, style_codes):
    catalogix_coll = db.catalogix_coll_pattern % brand_name
    vendor_db = get_db_name(vendor_name)
    coll = db.client[vendor_db][catalogix_coll]
    for style_code in style_codes:
        coll.update_one({'_id': style_code},
                    {'$set': {'problem': True}},
                    upsert=True)



if __name__ == '__main__':
    store_uuids = get_all_store_uuid_mapping()
    logger.info(store_uuids)
    parser = argparse.ArgumentParser()
    parser = argparse.ArgumentParser(description="Vendor Name")
    parser.add_argument("vendor_name",default=None, help="Vendor to process",  nargs='*')
    args = parser.parse_args()
    vendor_name = args.vendor_name[0]
    mongo_host = "127.0.0.1"
    db = ClientAutoscribeDB(mongo_host)
    for brand_name, store_uuid in store_uuids.items():
        style_codes = get_problem_style_codes(store_uuid)
        update_products_coll(db,vendor_name, brand_name,style_codes)
