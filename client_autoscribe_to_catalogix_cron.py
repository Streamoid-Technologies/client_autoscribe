from pymongo import MongoClient

mongo_host = "127.0.0.1"
db_client = MongoClient(mongo_host)

from custom_rules_v2 import get_db_name
import requests
import pandas as pd
from uuid import uuid4
import csv
import io
import json
import logging
import argparse
import os
import ast

logger = logging.getLogger(__name__)
logging.basicConfig(
    format="%(asctime)s - %(process)d - %(name)s - %(filename)s:%(lineno)d - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    level=logging.INFO,
)
# add all api in a config or api list

store_settings_api = (
    "https://kepler-backend.staging.streamoid.com/v1/store/{store_uuid}"
)
csv_upload_to_feed = "https://service.feed-upload.streamoid.com/v1/upload?file_id={req_id}&file_type=csv&authentication=%7B%7D"

feed_ingest_api = "https://service.feed-upload.streamoid.com/v1/feed/{store_uuid}/upload?history=true&validation=false&mapping_key={mapping_key}&file_url={file_url}&feed_format=csv&catalogix_user_id={catalogix_user_id}"


def real_time_upload(db, vendor_name, brand_name, style_codes):
    data = {}
    data[brand_name] = {}
    for style_code_data in style_codes:
        data[brand_name][style_code_data['StyleCode']] = db.get_product(vendor_name, brand_name, style_code_data['StyleCode'])

    return get_data(vendor_name, db, data)

def get_brands_sc(db, vendor_name, brands = None):
    if(brands == None):
        brands = db.list_brands(vendor_name)
    data = get_style_codes(vendor, brands, db)
    if data == {}:
        return -1,"No data found"

    return get_data(vendor_name, db,data)


def get_data(vendor_name, db, data):
    errors = {}
    for brand in data.keys():
        store_uuid = get_store_uuid(vendor_name, brand)
        if store_uuid == -1:
            logger.info(f"{brand} brand not found in mapping")
            if 'brand not found' in  errors.keys():
                errors['brand not found'].append(brand)
            continue
        store_settings = get_params(vendor_name, brand)
        logger.info(store_settings)
        csv_data = dict_to_csv(data[brand], store_settings["product_mappings"])
        csv_file = io.StringIO(csv_data)
        csv_url = upload_csv(csv_file)
        if csv_url == -1:
            logger.info(f"Unable to upload csv to feed for brand {brand}")
            if 'csv upload failed' in  errors.keys():
                errors['csv upload failed'].append(brand)
            continue
        logger.info(csv_url)
        store_settings["file_url"] = csv_url

        status = upload_csv_to_feed(store_settings)
        db.set_pushed_to_catalogix(vendor_name, brand, list(data[brand].keys()))
    logger.info(status)
    status['csv_url'] = csv_url
    return status, errors

# get new style codes of brands (the ones that are not processed) and product data of these style codes
def get_style_codes(vendor, brands, db):
    style_codes = {}
    for brand in brands:
        logger.info(brand)
        logger.info(db.get_new_products_not_pushed_catalogix(vendor, brand))
        if db.get_new_products_not_pushed_catalogix(vendor, brand) == []:
            continue
        style_codes[brand] = db.get_new_products_not_pushed_catalogix(vendor, brand)
    logger.info(style_codes)
    data = {}
    for brand, style_code in style_codes.items():
        data[brand] = {}
        for sc in style_code:
            data[brand][sc] = db.get_product(vendor, brand, sc)
    return data

# get store uuid of the brand from stores_map csv
def get_store_uuid(vendor_name, brand):
    logger.info(brand)
    try:
        if vendor_name == "abfrl_test":
            store_map_fpath = os.path.join(os.path.dirname(__file__), 'stores_test_map.csv')
        else:
            store_map_fpath = os.path.join(os.path.dirname(__file__), 'stores_map.csv')
        logger.info(store_map_fpath)
        df = pd.read_csv(store_map_fpath)
        filtered_data = df.loc[df['brands'] == brand, 'store_uuid']
        store_uuid = filtered_data.tolist()[0]
        return store_uuid
    except Exception as e:
        logger.error(f"brand not found in store mapping Error: {e}")
        return -1

# get store settings
def get_store_settings(store_uuid):
    r = requests.get(store_settings_api.format(store_uuid=store_uuid))
    if r.ok:
        return r.json()["data"]
    else:
        logger.info(r)
        # logger.error(r.json())
        return -1

# upload csv to feed
def upload_csv(csv_file):
    req_id = "client_autoscribe" + uuid4().hex
    files = {"file_data": ("data.csv", csv_file, "text/csv")}

    csv_file.seek(0)
    r = requests.post(csv_upload_to_feed.format(req_id = req_id), files=files)
    if r.ok:
        logger.info(r.json())
        return r.json()["data"]["download_url"]
    else:
        logger.error(r.json)
        return -1

def fix_http_image_url(image_urls):
    if isinstance(image_urls, list):
        output = ["https://"+x.strip() if not x.strip().startswith("http") else x.strip() for x in image_urls ]

    # elif isinstance(ast.literal_eval(image_urls), list):
    #     image_urls = ast.literal_eval(image_urls)
    #     output = ["https://"+x.strip() if not x.strip().startswith("http") else x.strip() for x in image_urls ]

    elif isinstance(image_urls, str):
        if (image_urls.startswith("[") and image_urls.endswith("]")):
            image_urls = ast.literal_eval(image_urls)
            output = ["https://"+x.strip() if not x.strip().startswith("http") else x.strip() for x in image_urls ]
            return str(output)

        temp_list = image_urls.split(",")
        temp_output = ["https://"+x.strip() if not x.strip().startswith("http") else x.strip() for x in temp_list ]
        output = (",").join(temp_output)
    return output
    

# map the new products keys from autoscribe to store_settings and return csv data
def dict_to_csv(data, product_mappings):

    data_list = []
    #logger.info(data)
    for key,values in data.items():
        if(values == None):
            logger.info(key)
            continue
        json_data = values
        for i in product_mappings.keys():
            if i not in json_data:
                if(i!="ImageURLs" and i.startswith("ImageURLs")):
                    continue
                json_data[i] = ""
        try:
            if json_data["ImageURLs"].endswith('jp'):
                json_data["ImageURLs"] = json_data["ImageURLs"]+ 'g'
        except:
            logger.error(f"Image URLS not found in data {json_data.keys()}")
        json_data = {key: value for key, value in json_data.items() if key}
        try:
            json_data["ImageURLs"] = fix_http_image_url(json_data["ImageURLs"])
        except:
            pass
        logger.info(json_data)
        data_list.append(json_data)

    csv_data = io.StringIO()

    fieldnames = set().union(*(d.keys() for d in data_list))

    csv_writer = csv.DictWriter(csv_data, fieldnames=fieldnames)

    csv_writer.writeheader()

    csv_writer.writerows(data_list)

    csv_data.seek(0)

    csv_contents = csv_data.read()

    csv_data.close()

    return csv_contents

# upload the csv to catalogix store using feed ingest api
def upload_csv_to_feed(store_settings):
    store_uuid = store_settings["store_uuid"]
    mapping_key = store_settings["mapping_key"]
    file_url = store_settings["file_url"]
    catalogix_user_id = store_settings["catalogix_user_id"]
    r = requests.get(
        feed_ingest_api.format(
            store_uuid=store_uuid,
            mapping_key=mapping_key,
            file_url=file_url,
            catalogix_user_id=catalogix_user_id,
        )
    )
    if r.ok:
        return r.json()
    else:
        logger.error(r.json())
        return -1

# generate store settings
def get_params(vendor_name, brand):
    store_uuid = get_store_uuid(vendor_name, brand)
    data = get_store_settings(store_uuid)
    mappings_data = list(data["mappings"].values())[-1]
    product_mapping = {}
    for smp, mp in mappings_data["mp_mapping_column_names"].items():
        product_mapping[mp["fields"][0]["path"]] = smp

    mapping_key = list(data["mappings"].keys())[-1]
    store_settings = {}
    store_settings["mapping_key"] = mapping_key
    store_settings["product_mappings"] = product_mapping
    store_settings["store_uuid"] = data["_id"]
    store_settings["catalogix_user_id"] = mappings_data["catalogix_user_id"]
    return store_settings


def reject_catalogix_push(vendor_name, brand_name, data):
    # check if vendor is abfrl first

    # get store_uuid from mapping
    store_uuid = get_store_uuid(vendor_name, brand_name)

    uuid_data = {
        "store_uuid": store_uuid,
        "filters": {"Style Code": {"$in": [data["StyleCode"]]}},
        "doc_type": "metadata",
        "ontology_name": "SMP",
        "ov": 27,
    }
    

    # api to get product_uuid
    r = requests.post(
        "https://staging.caspr.products.streamoid.com/v1/products/filters/get",
        data=json.dumps(uuid_data),
        timeout=120,
    )
    try:
        product_uuid = r.json()["data"][0]["product_uuid"]
        product_code = r.json()["data"][0]["product_code"]
    except:
        msg = f"(reject_catalogix) product_uuid not found for store: {store_uuid} and style_codes: {data['StyleCode']}. API error msg: {r.json()}"
        return -1, msg

    # api to get store_group
    r = requests.get(
        f"https://kepler-backend.staging.streamoid.com/v1/store/{store_uuid}",
        timeout=120,
    )
    try:
        store_group = r.json()["data"]["linked_store_id"]
    except:
        msg = f"(Reject) Store Group not found for store_uuid: {store_uuid}. API error msg: {r.json()}"
        return -1, msg

    status_data = [
        {
            "product": {
                "store_uuid": store_uuid,
                "store_group": store_group,
                "product_code": product_code,
                "status": "problem",
            },
            "extras": {
                "store_uuid": store_uuid,
                "store_group": store_group,
                "product_code": product_code,
                "product_uuid": product_uuid,
                "channel": "user",
                "data": {i["Channel"]: str(i["Remarks"]) for i in data["Rejects"]},
            },
        }
    ]
    query_params = {"trigger_smp_image_upsert": True, "trigger_fp_processes": True}

    # api to post data i.e remarks and change status to problem in catalogix store
    r = requests.post(
        "https://staging.caspr.products.streamoid.com/v1/products/upsert",
        data=json.dumps(status_data),
        params=query_params,
        timeout=120,
    )
    try:
        msg = f"(reject_catalogix) Style codes: {data['StyleCode']} Status changed to problem in Catalogix"
        return 0, msg
    except:
        msg = f"(reject_catalogix) Unable to change status in catalogix. Error: {r.json()}"
        return -1, msg



def get_style_code_data(db ,vendor, brands, style_codes):
    style_codes = style_codes.split(",")
    data = {}
    not_found = []
    for brand in brands:
        data[brand] = {}
        for style_code in style_codes:
            style_code_data = db.get_product(vendor, brand, style_code)
            if(style_code_data == None):
                not_found.append(style_code)
            else:
                data[brand][style_code] = style_code_data
    csv_file_path = 'my_list.csv'

    # Open the file in write mode with newline='' to prevent extra newline characters
    with open(csv_file_path, 'w', newline='') as csv_file:
        # Create a CSV writer object
        csv_writer = csv.writer(csv_file)

        # Write the entire list as a single row in the CSV file
        csv_writer.writerow(not_found)

    logger.info(f'The list has been successfully written to {csv_file_path}.')
    return get_data(vendor, db, data)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    #vendor = "grupo_soma"
    vendor = "abfrl_lbrd_prod"

    from client_autoscribe_db_v2 import ClientAutoscribeDB
    db = ClientAutoscribeDB(mongo_host)
    status, errors = get_brands_sc(db, vendor)