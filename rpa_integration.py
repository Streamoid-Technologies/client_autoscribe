import os
import json
import logging
import sys

import zipfile
import requests
import pandas as pd

logger = logging.getLogger(__name__)

from time import time
from datetime import datetime


def post_to_rpa(request_list, file_list, callback_url=None, use_dev=False, use_autofill=False):
    # {
    #     "Marketplace": "Paytm",
    #     "InputFileName": "Paytm.csv",
    #     "DestinationTemplate": "Men Western Wear.xlsx",
    #     "SheetName": "Men_Topwear",
    #     "HeaderRowNumber": 2,
    #     "DataRowNumber": 6
    # }
    if use_dev:
        params = {'code': 'eH1ImOnfxmaWUvwTJPgNZ0JMm9IAYq9EzUP1KXSLiLoUni3CCg9uFQ=='}
        url = "https://dev-api-functionclienthttptrigger.azurewebsites.net/api/FunctionClientHttpTrigger"
    else:
        params = {'code': 'ghDgISF7IcSoyJadOi5amSSB86J5XlRXVSt5lkyxcPzHjCwZE3vKdQ=='}
        url = "https://streamoid-api-FunctionClientHttpTrigger.azurewebsites.net/api/FunctionClientHttpTrigger"

    if use_autofill:
        url = 'https://autofill.exports.streamoid.com/template-autofill/win32/upload/templates'#'http://localhost:5003/template-autofill/win32/upload/templates'

    callback_url = '' if callback_url is None else callback_url
    parameters = {
        "StreamoidCallbackUrl": callback_url,
        "StreamoidRequests": request_list
    }
    data = {'Parameters': json.dumps(parameters)}
    files = []
    for f in file_list:
        files.append(('Files', (f['name'], f['contents'], f['mimetype'])))

    logger.info(url)
    logger.info(params)
    logger.info(data)
    # increased timeout due to read timeout issue
    r = requests.post(url, params=params, data=data, files=files, timeout=180)
    if r.ok:
        logging.info(f"response: {r.text}")
        return r.json()

    logging.error('%s %s', r.status_code, r.text)
    return {'error': r.text, 'status_code': r.status_code}


class RPAManager(object):
    def __init__(self):
        this_dir = os.path.dirname(__file__)
        templates_file = os.path.join(this_dir, 'templates.csv')

        self.base_dir = os.getenv('HOME')
        self.data = self.parse_templates_file(templates_file)
        self.ontology_to_marketplace = {'Amazon-MP': 'Amazon',
                                        'Flipkart-MP': 'Flipkart',
                                        'Limeroad-MP': 'Limeroad',
                                        'Myntra-MP': 'Myntra',
                                        'Nykaa-MP': 'Nykaa',
                                        'Paytm-MP': 'Paytm',
                                        'Tatacliq-MP': 'Tatacliq ABFRL',
                                        'Ajio-MP': 'Ajio'}

    def parse_templates_file(self, template_file):
        df = pd.read_csv(template_file, keep_default_na=False)

        df['sheet_name'] = df['template_file'].apply(lambda x: x.strip().split('/')[-1].split('.')[0])
        data = {}
        for _, row in df.iterrows():
            marketplace = row['marketplace']
            sheet_name = row['sheet_name']
            tab_name = row['tab name']
            key = '%s:%s:%s' % (marketplace, sheet_name, tab_name)
            if key in data:
                logger.warning('Already exists: %s : %s', key, data[key])
            else:
                data[key] = dict(row)

        logger.info(df['marketplace'].unique().tolist())
        return data

    def get_params(self, file_name, duplicate):
        fname = '.'.join(file_name.split('.')[:-1])
        words = fname.split('__')
        if len(words) != 3:
            return
        ontology, sheet_name, tab_name = words
        marketplace = self.ontology_to_marketplace[ontology]
        key = '%s:%s:%s' % (marketplace, sheet_name, tab_name)
        if key not in self.data:
            return
        rec = self.data[key]
        template_filename = rec['template_file'].split('/')[-1]
        params = {
            # "Marketplace": marketplace,
            "InputFileName": file_name,
            "InputTemplateFileName": template_filename,
            # "DestinationTemplate": rec['template_file'],
            "SheetName": tab_name,
            "HeaderRowNumber": rec['header_row'],
            "DataRowNumber": rec['data_row'],
            "AllowDuplicateHeaderEntry": duplicate,
        }
        template_fpath = os.path.join(self.base_dir, rec['template_file'])
        with open(template_fpath, 'rb') as f:
            contents = f.read()

        ext_to_mimetype = {'csv': 'text/csv',
                           'xls': 'application/vnd.ms-excel',
                           'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                           'xlsb': 'application/vnd.ms-excel.sheet.binary.macroEnabled.12',
                           'xlsm': 'application/vnd.ms-excel.sheet.macroEnabled.12'}
        ext = template_filename.split('.')[-1].lower()
        file_list = [{'name': template_filename, 'contents': contents, 'mimetype': ext_to_mimetype[ext]}]
        return params, file_list

    def post(self, files, callback_url=None, duplicate=False):
        responses = {}
        now = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
        for fname, contents in files.items():
            params, file_list = self.get_params(fname, duplicate)
            if params is None:
                logger.error('Unable to find template for file: %s', fname)
                responses[fname] = {'error': 'Unable to find template for file', 'ts': now}
                continue

            request_list = [params]
            file_list.append({'name': fname, 'contents': contents, 'mimetype': 'text/csv'})
            response = post_to_rpa(request_list, file_list, callback_url)
            response['ts'] = now
            responses[fname] = response

        return responses


if __name__ == '__main__':
    logging.basicConfig(
        format=
        '%(asctime)s - %(process)d - %(name)s - %(filename)s:%(lineno)d - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        level=logging.INFO)

    t1 = time()
    rpa = RPAManager()
    logger.info('Time to load templates file: %f sec', time() - t1)

    # read zip files and submit
    fname = sys.argv[1]
    files = {}
    with zipfile.ZipFile(fname, 'r') as zf:
        for name in zf.namelist():
            logger.info(name)
            files[name] = zf.read(name)

    responses = rpa.post(files)
    for fname, response in responses.items():
        print(fname, response)
