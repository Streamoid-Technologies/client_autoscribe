import os
import sys
import logging

logger = logging.getLogger(__name__)

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import requests
from time import time
from importlib import import_module

from client_autoscribe.client_autoscribe_config import app, rpa_dir
from client_autoscribe.client_autoscribe_db_v2 import ClientAutoscribeDB
from client_autoscribe.teams_integration import post_to_teams, generate_product_card
from client_autoscribe.rpa_integration import RPAManager
from client_autoscribe.client_autoscribe_to_catalogix import real_time_upload, reject_catalogix_push

@app.task
def post_to_client(vendor_name, brand_name):
    db = ClientAutoscribeDB('localhost')
    request_ids = db.get_requests_to_post(vendor_name, brand_name)
    logger.info('Pushing %s requests', len(request_ids))
    logger.info(request_ids)
    for request_id in request_ids:
        try:
            db.post_to_client(vendor_name, brand_name, request_id)
        except Exception as _:
            logger.exception('Exception while pushing request: %s %s %s',
                             vendor_name, brand_name, request_id)

@app.task
def catalogix_post_to_client(vendor_name, brand_name, post):
    db = ClientAutoscribeDB('localhost')
    request_ids = db.get_catalogix_requests_to_post(vendor_name, brand_name)
    logger.info('Pushing %s requests (catalogix)', len(request_ids))
    logger.info(request_ids)
    for request_id in request_ids:
        try:
            db.catalogix_post_to_client(vendor_name, brand_name, request_id, post)
        except Exception as _:
            logger.exception('Exception while pushing request (catalogix): %s %s %s',
                             vendor_name, brand_name, request_id)


def precompute_live(vendor, image_url, style_code):
    url = 'https://cataloging.streamoid.com/calm/cataloging/vendors/%s/images' % vendor
    data = {'url': image_url}
    logger.info(image_url)
    t1 = time()
    out = []
    r = requests.post(url, data=data, timeout=60)
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


def precompute_and_save(db, vendor_name, brand_name, style_code, image_urls, adapter):
    vendor_config = db.get_vendor_config(vendor_name)
    config = db.get_brand_config(vendor_name, brand_name)
    live_out = db.get_live(vendor_name, brand_name, style_code)

    # update output, required
    output = live_out.get('data', {}) if live_out is not None else {}
    for image_url in image_urls:
        if image_url in output:
            logger.info('Already computed for URL: %s %s %s', vendor_name, brand_name, image_url)
            continue
        try:
            cataloging_vendor = vendor_config['cataloging_vendor']
            output1 = precompute_live(cataloging_vendor, image_url, style_code)
            if len(output1) > 0:
                output[image_url] = output1
        except Exception as e:
            logger.exception(str(e))

    # update translations anyways
    product = db.get_product(vendor_name, brand_name, style_code)
    source_tags = []
    for k, v in product.items():
        if k not in ['_id', 'product_uuid', 'last_modified', 'batch_number',
                     'ImageURLs', 'StyleCode', 'RequestID', 're-curate'] and v is not None:
            source_tags.append(k + ':' + v)
    logger.info(source_tags)
    target = vendor_config['curation_ontology']
    source = config['brand_ontology']
    logger.info('%s %s', target, source)
    if target == source:
        target_tags = source_tags
    else:
        target_tags = db.translate_image_attrs(source, target, source_tags, restrict=False)
    logger.info(target_tags)

    # custom reverse translation
    target_tags1 = adapter.reverse_translate(source_tags)
    logger.info(target_tags1)
    target_tags.extend(target_tags1)
    logger.info(target_tags)

    info = {}
    for x in target_tags:
        x1 = x.split(':')
        if len(x1) > 1:
            info[x1[0]] = x1[1]

    if len(output) > 0:
        data = {'data': output, 'info': info, 'last_modified': product['last_modified']}
        db.save_live(vendor_name, brand_name, style_code, data)


@app.task
def queue_post_to_teams(vendor_name, brand_name, style_code, request_id, last_modified, teams_url):
    card_data = generate_product_card(vendor_name, brand_name, style_code, request_id, last_modified)
    post_to_teams(teams_url, card_data)


@app.task
def trigger_precompute(vendor_name, brand_name, style_codes, queue, post_msg=True):
    db = ClientAutoscribeDB('localhost')
    teams_url = db.teams_urls.get(vendor_name, {}).get('post', None)
    logger.info('Pre-processing for cataloging: %s', style_codes)
    module = import_module('client_autoscribe.integrations.%s' % vendor_name)
    vendor_config = db.get_vendor_config(vendor_name)
    config = db.get_brand_config(vendor_name, brand_name)
    adapter = module.VendorAdapter(vendor_config, config)
    for style_code in style_codes:
        row = db.get_product(vendor_name, brand_name, style_code)
        if row is None:
            logger.warning('Invalid style code: %s', style_code)
            continue
        logger.info(row)
        style_code = row['StyleCode']
        # post to Teams before trigger
        request_id = row.get('RequestID', None)
        last_modified = row.get('last_modified', None)
        teams_queue = 'client_autoscribe_v2_large'
        post_msg = False
        if post_msg and teams_url is not None:
            app.send_task('client_autoscribe_worker_v2.queue_post_to_teams',
                          (vendor_name, brand_name, style_code, request_id,
                           last_modified, teams_url), queue=teams_queue)
        # precompute
        if 'ImageURLs' not in row:
            logger.warning('Missing image URLs: %s', style_code)
            continue
        image_urls = row['ImageURLs']
        if isinstance(image_urls, str):
            image_urls = image_urls.split(',')
        precompute_and_save(db, vendor_name, brand_name, style_code, image_urls, adapter)

@app.task
def trigger_push_catalogix(vendor_name, brand_name, style_codes, post_msg=True):
    logger.info('Pre-processing for catalogix: %s', style_codes)
    db = ClientAutoscribeDB('localhost')
    status, error = real_time_upload(db, vendor_name, brand_name, style_codes)
    logger.info(f"StyleCodes: {style_codes} Status: {status}. Error: {error}")

@app.task
def reject_catalogix(vendor_name, brand_name, data):
    logger.info('Rejecting in catalogix: %s', data["StyleCode"])
    status, msg = reject_catalogix_push(vendor_name, brand_name, data)
    logger.info(f"Data: {data}. Status: {status}. Message: {msg}")


@app.task
def translate_and_save(vendor_name, brand_name, style_codes):
    db = ClientAutoscribeDB('localhost')
    db.translate_and_save(vendor_name, brand_name, style_codes)


@app.task
def post_rpa_files(vendor_name, brand_name, rpa_request_id, filenames, duplicate=False):
    # collect files back
    files = {}
    for fname in filenames:
        fpath = os.path.join(rpa_dir, vendor_name, brand_name, rpa_request_id, fname)
        with open(fpath, 'rb') as f:
            files[fname] = f.read()

    rpa = RPAManager()
    callback_url = 'https://cataloging.streamoid.com/api/autoscribe' \
                   '/vendors/%s/brands/%s/callback' % (vendor_name, brand_name)
    responses = rpa.post(files, callback_url, duplicate)

    # update DB
    db = ClientAutoscribeDB('localhost')
    db.set_rpa_details(vendor_name, brand_name, rpa_request_id, responses)

    # trigger update task after 1 hour?
    task = app.send_task('client_autoscribe_worker_v2.update_rpa_status',
                         (vendor_name, brand_name, rpa_request_id),
                         countdown=60 * 60)
    logger.info('Triggered task for status update: %s', task.task_id)


@app.task
def fetch_files_and_store(vendor_name, brand_name, rpa_request_id, responses):
    # get marketplace files and store
    for fname, response in responses.items():
        for sr in response.get('StreamoidRequests', []):
            output_url = sr['GeneratedMarketplaceFile']
            if len(output_url) < 1:
                continue
            ext = output_url.split('.')[-1]
            r2 = requests.get(output_url, timeout=60)
            if r2.ok:
                fname_out = fname.replace('.csv', '.' + ext)
                fpath = os.path.join(rpa_dir, vendor_name, brand_name, rpa_request_id, fname_out)
                with open(fpath, 'wb') as f:
                    f.write(r2.content)


@app.task
def update_rpa_status(vendor_name, brand_name, rpa_request_id):
    db = ClientAutoscribeDB('localhost')

    condition = {'rpa_request_id': rpa_request_id}
    rows = db.get_rpa_status(vendor_name, brand_name, condition=condition)
    for row in rows:
        if 'BatchRequestStatus' in row and row['BatchRequestStatus'] in ['Completed', 'Errored']:
            continue
        callback_url = row.get('BatchRequestCallbackUrl', None)
        if callback_url is None:
            continue
        logger.info(row)
        fname = row['filename']
        r = requests.get(callback_url, timeout=60)
        responses = {}
        if r.ok:
            response = r.json()
            responses[fname] = response
        else:
            logger.error('%s %s', r.status_code, r.text)
            continue

        db.set_rpa_details(vendor_name, brand_name, rpa_request_id, responses)
        fetch_files_and_store(vendor_name, brand_name, rpa_request_id, responses)


if __name__ == '__main__':
    logging.basicConfig(
        format=
        '%(asctime)s - %(process)d - %(name)s - %(filename)s:%(lineno)d - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        level=logging.INFO)

    # vendor = 'v_allen_solly'
    # url = 'https://d1q9qhpdr6ehzh.cloudfront.net/ae31fb5356ac6887c51b955f57e90555'
    # print(precompute_live(vendor, url, 'test'))

    # update_rpa_status('abfrl_lbrd_prod', 'allen_solly', 'fa57985dae58494aa456ddb322d38e75')

    post_to_client('abfrl_lbrd_prod', 'american_eagle')