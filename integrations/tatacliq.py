import json
import os
import sys
import logging

logger = logging.getLogger(__name__)

import pandas as pd
import requests

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))
from client_autoscribe.integrations.base_adapter import BaseAdapter


class VendorAdapter(BaseAdapter):
    def __init__(self, vendor_config, brand_config):
        super(VendorAdapter, self).__init__(vendor_config, brand_config)

    def convert_input(self, data, **kwargs):
        brand_name = self.brand_config['_id']
        if brand_name == 'neerus':
            return self._convert_input_neerus(data, **kwargs)
        elif brand_name == 'zola':
            return self._convert_input_zola(data, **kwargs)
        elif brand_name in ['Marca Disati', 'The Mom Store', 'High Star', 'Dollar Missy', 'Trend Arrest']:
            return self._convert_input_brands(data, **kwargs)
        else:
            return self._convert_input_default(data, **kwargs)

    def _convert_input_default(self, data, **kwargs):
        filename = kwargs.get('filename', None)
        if filename is not None and filename.lower().endswith('.csv'):
            data = pd.read_csv(data, keep_default_na=False, dtype=str)
            data.fillna('', inplace=True)
            data = [dict(row) for _, row in data.iterrows()]

        style_codes = []
        for row in data:
            # logger.info(dict(row))
            out = {key.strip(): val.strip() for key, val in row.items()}
            image_urls = []
            for i in range(3):
                field = 'Image URL%d' % (i + 1)
                if field in out:
                    image_urls.append(out[field])
            if len(image_urls) < 1:
                logger.warning('Skipping: No image URLs: %s', out)
                continue
            out['ImageURLs'] = ','.join([x for x in image_urls if len(x) > 0])
            out['StyleCode'] = str(out['Seller Article SKU'])
            out['RequestID'] = str(out['Seller Article SKU'])
            # logger.info(out)
            style_codes.append(out)
        return style_codes

    def _convert_input_neerus(self, data, **kwargs):
        filename = kwargs.get('filename', None)
        logger.info(filename)
        if filename is not None and filename.lower().endswith('.xlsx'):
            data1 = pd.read_excel(data, sheet_name=None, header=1, keep_default_na=False)
            data = []
            for sheet_name, df in data1.items():
                logger.info(sheet_name)
                for idx, row in df.iterrows():
                    if idx == 0:
                        continue
                    data.append(row.to_dict())

        logger.info(len(data))
        style_codes = []
        for row in data:
            out = {key.strip(): str(val).strip() for key, val in row.items()}
            # detect image URL field
            image_key = None
            for k, v in out.items():
                if v.startswith('http'):
                    image_key = k
                    break
            image_url = out.get(image_key, '')
            logger.info('Detected image key: %s %s', image_key, image_url)
            if image_key is None:
                logger.warning('Skipping: No image key found: %s', out)
                continue
            image_urls = [image_url]
            image_urls = [x for x in image_urls if len(x) > 0]
            if len(image_urls) < 1:
                logger.warning('Skipping: No image URLs: %s', out)
                continue
            out['ImageURLs'] = ','.join(image_urls)
            out['StyleCode'] = str(out['Seller Article SKU'])
            out['RequestID'] = str(out['Seller Article SKU'])
            # logger.info(out)
            style_codes.append(out)
        return style_codes

    def _convert_input_zola(self, data, **kwargs):
        filename = kwargs.get('filename', None)
        logger.info(filename)
        if filename is not None and filename.lower().endswith('.xlsx'):
            data1 = pd.read_excel(data, sheet_name=None, header=1, keep_default_na=False)
            data = []
            for sheet_name, df in data1.items():
                if sheet_name in ['Sheet1']:
                    logger.info('Skipping sheet : %s', sheet_name)
                    continue

                logger.info(sheet_name)
                for idx, row in df.iterrows():
                    if idx == 0:
                        continue
                    data.append(row.to_dict())

        logger.info(len(data))
        style_codes = []
        for row in data:
            # logger.info(dict(row))
            out = {key.strip(): str(val).strip() for key, val in row.items()}
            image_urls = [out['*MODEL']]
            for i in range(2, 6):
                field = 'MODEL%d' % i
                if field in out:
                    image_urls.append(out[field])
            if len(image_urls) < 1:
                logger.warning('Skipping: No image URLs: %s', out)
                continue
            out['ImageURLs'] = ','.join([x for x in image_urls if len(x) > 0])
            out['StyleCode'] = str(out['Seller Article SKU'])
            out['RequestID'] = str(out['Seller Article SKU'])
            # logger.info(out)
            style_codes.append(out)
        return style_codes

    def _convert_input_brands(self, data, **kwargs):
        filename = kwargs.get('filename', None)
        logger.info(filename)
        if filename is not None and filename.lower().endswith('.xlsx'):
            data1 = pd.read_excel(data, sheet_name=None, header=3, keep_default_na=False)
            data = []
            for sheet_name, df in data1.items():
                if sheet_name not in ['Template']:
                    logger.info('Skipping sheet : %s', sheet_name)
                    continue

                logger.info(sheet_name)
                for idx, row in df.iterrows():
                    if idx == 0:
                        continue
                    data.append(row.to_dict())

        logger.info(len(data))
        style_codes = []
        for row in data:
            out = {key.strip(): str(val).strip() for key, val in row.items()}
            # detect image URL field

            image_urls = [x for x in out.values() if x.startswith('http')]
            if len(image_urls) < 1:
                logger.warning('Skipping: No image URLs: %s', out)
                continue
            print(out.keys())
            out['ImageURLs'] = ','.join(image_urls)
            out['StyleCode'] = str(out['Seller Article SKU'])
            out['RequestID'] = str(out['Seller Article SKU'])
            # logger.info(out)
            style_codes.append(out)
        return style_codes

    def reverse_translate(self, tags, **kwargs):
        brand_name = self.brand_config['_id']
        mapping = {}
        if brand_name == 'neerus':
            mapping = load_mappings('neerus_mapping.csv')
        elif brand_name == 'zola':
            mapping = load_mappings('zola_mapping.csv')
        out = apply_mappings(mapping, tags)
        return out


def apply_mappings(mapping, tags):
    out2 = []
    for tag in tags:
        out1 = mapping.get(tag, None)
        if out1 is None:
            continue
        out2.append(out1)
    return out2


def load_mappings(fname):
    mapping_file = os.path.join(os.path.dirname(__file__), fname)
    mapping = pd.read_csv(mapping_file, keep_default_na=False)
    out = {}
    for _, row in mapping.iterrows():
        if len(row['output_key']) < 1 or len(row['output_value']) < 1:
            continue
        input1 = row['input_key'] + ':' + row['input_value']
        output1 = row['output_key'] + ':' + row['output_value']
        out[input1] = output1
    return out


def fetch_ontology(ontology_name):
    url = "https://cataloging.streamoid.com/calm/ontology/%s?all=1" % ontology_name
    r = requests.get(url)
    out = {}
    if r.ok:
        data = r.json().get('data', {})

        for val in data:
            w = val.split(':')
            if w[0] not in out:
                out[w[0]] = []
            out[w[0]] = w[1]

    return out


def generate_missing_mappings(rows, config, fname_out):
    ontology = fetch_ontology(config['brand_ontology'])

    out = {}
    for row in rows:
        for k, v in row.items():
            if k in ontology and v not in ontology[k]:
                if k not in out:
                    out[k] = set()
                out[k].add(v)

    out1 = [{'input_key': k, 'input_value': vi} for k, v in out.items() for vi in v]
    pd.DataFrame(out1).to_csv(fname_out, index=False)


if __name__ == '__main__':
    brand_name = sys.argv[1]
    fname_in = sys.argv[2]
    fname_out = sys.argv[3]

    vendor_config = {
        "_id": "tatacliq",
        "curation_ontology": "Streamoid-MP",
        "cataloging_vendor": "tatacliq"
    }
    config = {
        "_id": brand_name,
        "brand_ontology": "Tatacliq_Prod-MP",
        "custom_rules": False,
        "targets": {
            "brand_attributes": "Tatacliq_Prod-MP"
        },
        "token": None
    }
    adapter = VendorAdapter(vendor_config, config)
    with open(fname_in, 'rb') as f:
        data = f.read()
    rows = adapter.convert_input(data, filename=fname_in)
    rows1 = {row['Seller Article SKU']: row for row in rows}
    print(rows1['AWKX1794'])
    exit()
    print(len(rows))
    print(rows[0])

    out = {}
    for row in rows:
        sku = row['Seller Article SKU']
        style_code = row['Style Code']
        out[sku] = style_code
    with open(fname_out, 'w') as f:
        json.dump(out, f)
