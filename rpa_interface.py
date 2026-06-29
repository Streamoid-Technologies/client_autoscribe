import json
import os
import logging
import sys
from typing import Union
import pandas as pd

logger = logging.getLogger(__name__)
logging.basicConfig(
    format=
    '%(asctime)s - %(process)d - %(name)s - %(filename)s:%(lineno)d - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    level=logging.INFO)

import requests
import hashlib

from glob import glob
from uuid import uuid4
from datetime import datetime
from pymongo import MongoClient
from tqdm import tqdm

from fastapi import FastAPI, Request, File, UploadFile, Form
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.templating import Jinja2Templates
from rpa_integration import post_to_rpa
from rpa_database import RPADatabase

from pathlib import Path

rpa_db = 'rpa_db'
rpa_coll = 'requests'
db = RPADatabase()
client = MongoClient()
coll = client[rpa_db][rpa_coll]

rpa_dir = os.path.join(str(Path.home()), 'rpa_data')  # os.path.join(os.environ.get('HOME'), 'rpa_data')
if not os.path.exists(rpa_dir):
    os.makedirs(rpa_dir, exist_ok=False)

app = FastAPI()
templates = Jinja2Templates(directory="templates")


def get_md5sum(contents):
    digestor = hashlib.md5()
    digestor.update(contents)
    return digestor.hexdigest()


@app.get('/rpa/upload', response_class=HTMLResponse)
def rpa_upload_get(request: Request):
    return templates.TemplateResponse("rpa_upload.html", {"request": request})


def get_params(template_filename, data_filename, sub_sheet_name, header_row, data_row, duplicate):
    return {
        # "Marketplace": marketplace,
        "InputFileName": data_filename,
        "InputTemplateFileName": template_filename,
        # "DestinationTemplate": rec['template_file'],
        "SheetName": sub_sheet_name,
        "HeaderRowNumber": str(header_row),
        "DataRowNumber": str(data_row),
        "AllowDuplicateHeaderEntry": duplicate,
    }


def get_file(template_filename, contents):
    ext_to_mimetype = {'csv': 'text/csv',
                       'xls': 'application/vnd.ms-excel',
                       'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                       'xlsb': 'application/vnd.ms-excel.sheet.binary.macroEnabled.12',
                       'xlsm': 'application/vnd.ms-excel.sheet.macroEnabled.12'}
    ext = template_filename.split('.')[-1].lower()
    return {'name': template_filename, 'contents': contents, 'mimetype': ext_to_mimetype[ext]}


@app.post('/rpa/upload')
async def rpa_upload_post(template: UploadFile = File(...),
                          data: UploadFile = File(...),
                          sub_sheet_name: str = Form(...),
                          header_row: Union[str, int] = Form(...),
                          data_row: Union[str, int] = Form(...),
                          callback_url: str = Form(None),
                          duplicate: bool = Form(False),
                          use_dev: bool = Form(False),
                          use_autofill: bool = Form(False)):
    # return {'data_row': data_row, 'header_row': header_row, 'sub_sheet_name': sub_sheet_name,
    #         'data': data.filename, 'template': template.filename}
    files = {'data': {'filename': data.filename, 'contents': await data.read()},
             'template': {'filename': template.filename, 'contents': await template.read()}}

    # save files
    rpa_request_id = uuid4().hex
    rpa_request_dir = os.path.join(rpa_dir, rpa_request_id)
    os.makedirs(rpa_request_dir, exist_ok=True)
    for vals in files.values():
        fpath = os.path.join(rpa_request_dir, vals['filename'])
        logger.info(fpath)
        with open(fpath, 'wb') as f:
            f.write(vals['contents'])
    # save callback URL if present
    # if callback_url is not None:
    fpath = os.path.join(rpa_request_dir, 'config.json')
    with open(fpath, 'w') as f:
        json.dump({'url': callback_url}, f)

    # post to 10xDS
    responses = {}

    file_list = [{'name': files['data']['filename'], 'contents': files['data']['contents'], 'mimetype': 'text/csv'},
                 get_file(files['template']['filename'], files['template']['contents'])]
    request_list = [
        get_params(files['template']['filename'], files['data']['filename'],
                   sub_sheet_name, header_row, data_row, duplicate)]

    # save responses to DB
    now = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    fname = files['data']['filename']
    callback_url = 'https://cataloging.streamoid.com/rpa/callback'
    response = post_to_rpa(request_list, file_list, callback_url, use_dev, use_autofill)
    response['ts'] = now
    responses[fname] = response

    db.set_rpa_details(coll, rpa_request_id, responses)

    # since filling task already completed, results available through callback url
    # batch_request_id = response['BatchRequestId']
    # update_status(batch_request_ids=[batch_request_id])

    return {'request_id': rpa_request_id}


@app.get('/rpa/status', response_class=HTMLResponse)
def rpa_status_get(request: Request,
                   error: str = None,
                   request_ids: str = None,
                   offset: int = 0, limit: int = 50):
    # get status from DB
    condition = {}
    if request_ids is not None:
        condition['_id'] = {'$in': request_ids.split(',')}
        offset = None
        limit = None
    if error is not None:
        condition['BatchRequestStatus'] = {'$ne': 'Completed'}
    logger.info(condition)
    rows = db.get_rpa_status(coll, condition=condition, offset=offset, limit=limit)
    for row in rows:
        logger.info(row)
        logger.info('%s %s', row['rpa_request_id'], row['filename'])
        input_filepath = os.path.join(rpa_dir, row['rpa_request_id'], row['filename'])
        template_filepath = find_template_file(row['rpa_request_id'], row['filename'])
        try:
            template_filename = template_filepath.split('/')[-1]
            logger.info(template_filepath)
            row['template_filename'] = template_filename
            row['time'] = get_timediff(row['rpa_request_id'])
            if os.path.exists(input_filepath):
                row['shape'] = pd.read_csv(input_filepath).shape
            if os.path.exists(template_filepath):
                with open(template_filepath, 'rb') as f:
                    row['md5'] = get_md5sum(f.read())
        except Exception as e:
            logger.exception(str(e))
    #     logger.info(row)
    return templates.TemplateResponse("rpa_status.html", {"request": request, 'vendor': 'unknown',
                                                          'brand': 'unknown', 'rows': rows})


def find_template_file(rpa_request_id, filename):
    fname = filename.replace('input_', 'template_').split('.')[0]
    req_pattern = os.path.join(rpa_dir, rpa_request_id, '%s.*' % fname)
    matches = []

    logger.info(req_pattern)
    for fpath in glob(req_pattern):
        matches.append(fpath)

    logger.info(matches)
    if len(matches) > 0:
        return matches[0]


def fetch_file_and_store(rpa_request_id, responses):
    urls = []
    for fname, response in responses.items():
        for sr in response.get('StreamoidRequests', []):
            output_url = sr['GeneratedMarketplaceFile']
            if len(output_url) < 1:
                continue
            ext = output_url.split('.')[-1]
            r2 = requests.get(output_url, timeout=60)
            if r2.ok:
                fname_out = fname.replace('.csv', '.' + ext)
                fpath = os.path.join(rpa_dir, rpa_request_id, fname_out)
                with open(fpath, 'wb') as f:
                    f.write(r2.content)
                urls.append(output_url)
    return urls


def update_db_and_callback(data):
    condition = {'BatchRequestId': data['BatchRequestId']}
    rows = db.get_rpa_status(coll, condition=condition)
    trigger = {}
    for row in rows:
        logger.info(row)
        rpa_request_id = row['rpa_request_id']
        fname = row['filename']
        responses = {fname: data}
        db.set_rpa_details(coll, rpa_request_id, responses)
        urls = fetch_file_and_store(rpa_request_id, responses)
        logger.info('%s %s', rpa_request_id, urls)
        if rpa_request_id not in trigger:
            trigger[rpa_request_id] = urls
        else:
            trigger[rpa_request_id].extend(urls)

    # Do not send callback if status is not final
    if data['BatchRequestStatus'] not in ['Completed', 'Errored']:
        return

    # TODO: optimize this process
    # get callback URL and post
    for rpa_request_id, urls in trigger.items():
        fpath = os.path.join(rpa_dir, rpa_request_id, 'config.json')
        if not os.path.exists(fpath):
            continue
        with open(fpath, 'r') as f:
            config = json.load(f)
        callback_url = config['url']
        if callback_url:
            data1 = {'data': json.dumps(urls)}
            r = requests.post(callback_url, data=data1, timeout=30)
            if r.ok:
                logger.info('%s %s %s', rpa_request_id, callback_url, urls)
            else:
                logger.error('%s %s %s : %s', rpa_request_id, callback_url,
                             urls, r.status_code)


@app.post('/rpa/callback')
async def callback_get(request: Request):
    try:
        data = await request.json()
        logger.info(data)
    except Exception as e:
        logger.exception(str(e))
        return {'code': -1, 'status': str(e)}

    update_db_and_callback(data)
    return {'code': 0, 'status': 'OK'}


@app.get('/rpa/requests/{rpa_request_id}/{filename}', response_class=FileResponse)
def get_request_file(request: Request, rpa_request_id: str, filename: str):
    return FileResponse(os.path.join(rpa_dir, rpa_request_id, filename),
                        filename='%s_%s' % (rpa_request_id, filename))


def get_timediff(rpa_request_id):
    # XXX: config.json not available for files without a callback
    min_time = os.stat(os.path.join(rpa_dir, rpa_request_id, 'config.json')).st_mtime
    max_time = -1
    # for d in os.scandir(os.path.join(rpa_dir, rpa_request_id)):
    # if d.name.startswith('input_'):
    #    max_time = max(d.stat().st_mtime, max_time)
    current_dir = os.path.join(rpa_dir, rpa_request_id)
    times = [max(d.stat().st_mtime, max_time) for d in os.scandir(current_dir)]
    max_time = max(times) if len(times) > 3 else min_time

    print(min_time, max_time)
    if max_time > min_time:
        return max_time - min_time


def update_status(limit=None, batch_request_ids=[]):
    condition = {'BatchRequestStatus': {'$nin': ['Completed', 'Errored']}}
    if batch_request_ids:
        condition.update({'BatchRequestId': {'$in': batch_request_ids}})
    rows = db.get_rpa_status(coll, condition, limit=limit)
    logger.info('Found %d records', len(rows))
    for row in tqdm(rows):
        try:
            logger.info(row['rpa_request_id'])
            callback_url = row['BatchRequestCallbackUrl']
            r = requests.get(callback_url, timeout=60)
            if r.ok:
                data = r.json()
                update_db_and_callback(data)
            else:
                logger.error('%s %s %s', callback_url, r.status_code, r.text)
        except Exception as e:
            logger.exception(str(e))


if __name__ == '__main__':
    update_status(100)
