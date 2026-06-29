import requests

# First request to get product_uuid and product_code
import csv
from pymongo import MongoClient
from datetime import datetime, timedelta
from client_autoscribe_db_v2 import ClientAutoscribeDB
import logging
import os
import pandas as pd

logger = logging.getLogger(__name__)
logging.basicConfig(
    format="%(asctime)s - %(process)d - %(name)s - %(filename)s:%(lineno)d - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    level=logging.INFO,
)
# Set your MongoDB connection details
mongo_uri = "127.0.0.1"
client = MongoClient(mongo_uri)


ca = ClientAutoscribeDB(mongo_uri)

brands = ca.list_brands("abfrl_lbrd_prod")
db = client.v_abfrl_lbrd_prod_autoscribe


def get_store_uuid(brand):
    logger.info(brand)
    try:
        store_map_fpath = os.path.join(os.path.dirname(__file__), 'stores_map.csv')
        df = pd.read_csv(store_map_fpath)
        filtered_data = df.loc[df['brands'] == brand, 'store_uuid']
        store_uuid = filtered_data.tolist()[0]
        return store_uuid
    except Exception as e:
        logger.error(f"brand not found in store mapping Error: {e}")
        return -1


def get_category_from_catalogix(store_uuid, style_code):
    try:
        logger.info(f"Getting category using caspr API")
        url_filters = 'https://staging.caspr.products.streamoid.com/v1/products/filters/get'
        headers_filters = {
            'accept': 'application/json',
            'Content-Type': 'application/json'
        }
        data_filters = {
            'store_uuid': store_uuid,
            'filters': {'Style Code': style_code},
            'doc_type': 'metadata',
            'ontology_name': 'SMP'
        }

        response_filters = requests.post(url_filters, headers=headers_filters, json=data_filters)
        result_filters = response_filters.json()
        product_uuid = result_filters['data'][0]['product_uuid']
        product_code = result_filters['data'][0]['product_code']
        ov = result_filters['data'][0]["ov"]
        # Second request using product_uuid and product_code
        url_products = 'https://staging.caspr.products.streamoid.com/v1/products/get'
        headers_products = {
            'accept': 'application/json',
            'Content-Type': 'application/json'
        }
        data_products = {
            'store_uuid': store_uuid,
            'products_list': [
                {'product_uuid': product_uuid},
                {'product_code': product_code}
            ],
            'doc_type_list': [
                {'doc_type': 'metadata', 'ontology_list': [{'ontology_name': 'SMP', 'ov': ov}]}
            ]
        }

        response_products = requests.post(url_products, headers=headers_products, json=data_products)
        result_products = response_products.json()
        category = result_products['data'][product_uuid]['SMP'][0]['Category']

        print(f'Category: {category}')
    except Exception as e:
        logger.error(f"Error with Caspr api {e}")
        category = None
    return category

all_brand_data = []
category_counts = {}
for brand in brands:
    print(brand)
    store_uuid = get_store_uuid(brand)

    # Calculate the date range for the last 3 months
    end_date = datetime.now()
    start_date = end_date - timedelta(days=90)

    # Step 1: Find stylecodes within the last 3 months from the 'products' collection
    
    products_collection = db[ca.products_coll_pattern % brand]
    # stylecodes_cursor = ca.get_keys("abfrl_lbrd_prod", brand, pattern, condition2, only_count=False, fields=['_id', 'push_time'])
    stylecodes_cursor = products_collection.find(
        {
            'last_modified': {'$gte': start_date.strftime('%Y-%m-%d %H:%M:%S'), '$lte': end_date.strftime('%Y-%m-%d %H:%M:%S')}
        }
    )
    # Step 2: Iterate through each stylecode and check category in 'catalogix' and 'cataloging' collections
    not_found_categories = []
    try:
        for document in stylecodes_cursor:
            stylecode = document['_id']  # Assuming stylecode is in the _id field
            catalogix_category = db[f'catalogix:{brand}'].find_one({'_id': stylecode})
            cataloging_category = db[f'cataloging:{brand}'].find_one({'_id': stylecode})

            category = None

            if catalogix_category and catalogix_category.get('SMP') and catalogix_category['SMP'].get('Category'):
                category = catalogix_category['SMP']['Category']
            elif cataloging_category and cataloging_category.get('data') and cataloging_category['data'].get('Category'):
                category = cataloging_category['data']['Category']
            else:
                category = get_category_from_catalogix(store_uuid, stylecode)
            if category == None:
                not_found_categories.append({'stylecode': stylecode})
            else:
                all_brand_data.append({'brand': brand, 'stylecode': stylecode, 'category': category})
                category_counts[category] = category_counts.get(category, 0) + 1

            # Step 3: Save category in a CSV or perform any other necessary actions
            if category:
                # Replace this with your actual code to save the category to a CSV or perform other actions
                print(f"Brand: {brand}, Stylecode: {stylecode}, Category: {category}")
    except Exception as e:
        print(e)
        logger.warning(f"Cursor not found for brand {brand}. Skipping.")


    # Step 4: Save not found stylecodes in 'notfound_category.csv'
    if not_found_categories:
        csv_file_path = f'notfound_category_{brand}.csv'
        with open(csv_file_path, 'w', newline='') as csvfile:
            fieldnames = ['stylecode']
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            for item in not_found_categories:
                writer.writerow(item)
        print(f"Not found stylecodes for brand {brand} saved in: {csv_file_path}")

csv_file_path_all_brand = 'all_brand_data.csv'
with open(csv_file_path_all_brand, 'w', newline='') as csvfile:
    fieldnames_all_brand = ['brand', 'stylecode', 'category']
    writer_all_brand = csv.DictWriter(csvfile, fieldnames=fieldnames_all_brand)
    writer_all_brand.writeheader()
    for item in all_brand_data:
        writer_all_brand.writerow(item)
print(f"All brand data saved in: {csv_file_path_all_brand}")

csv_file_path = 'total_category_counts.csv'
with open(csv_file_path, 'w', newline='') as csvfile:
    fieldnames = ['category', 'count']
    writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
    writer.writeheader()
    for category, count in category_counts.items():
        writer.writerow({'category': category, 'count': count})

print(f"Total category counts saved in: {csv_file_path}")