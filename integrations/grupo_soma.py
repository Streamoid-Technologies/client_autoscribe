import os
import sys
import logging

logger = logging.getLogger(__name__)

import os
import gzip
import requests
import pandas as pd

from glob import glob
from io import BytesIO
from datetime import datetime

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))
from client_autoscribe.integrations.base_adapter import BaseAdapter
from client_autoscribe.google_bucket_writer import GoogleBucketWriter


def get_ontology_tags(ontology):
    params = {'all': '1'}
    r = requests.get('https://cataloging.streamoid.com/calm/ontology/%s' % ontology,
                     params=params, timeout=60)
    if r.ok:
        return r.json().get('data', [])


def write_parquet_gzip(out):
    df = pd.DataFrame(out)
    print(df.head())
    f1 = BytesIO()
    df.to_parquet(f1, compression='gzip', index=False)
    return f1.getvalue()


def read_parquet_gzip(data):
    return pd.read_parquet(BytesIO(data))


def generate_table(ontology):
    tags = get_ontology_tags(ontology)
    out = []
    for idx, tag in enumerate(tags):
        w = tag.split(':')
        out.append({'id_fashion_attributes': str(idx + 1),
                    'category_attributes': w[0],
                    'desc_fashion_attributes': w[1]})
    return write_parquet_gzip(out)


class GrupoSomaEncoder(object):
    def __init__(self, fname=None):
        # if fname is None:
        #     fnames = [fname for fname in glob('fashion_attributes_table_*.parquet.gzip')]
        #     fnames.sort(reverse=True)
        #     fname = fnames[0] if len(fnames) > 0 else None

        self.fpath = None
        self.mapper = {}
        if fname is not None:
            self.fpath = os.path.join(os.path.dirname(__file__), fname)
            self.mapper = self.load_mapper(self.fpath)

    def load_mapper(self, fpath):
        with open(fpath, 'rb') as f:
            data = f.read()
        df = read_parquet_gzip(data)
        mapper = {}
        for _, row in df.iterrows():
            idx = row['id_fashion_attributes']
            key = row['category_attributes']
            val = row['desc_fashion_attributes']
            mapper[(key, val)] = idx
        return mapper

    def encode(self, data, brand, ts, idx):
        out = []
        for tag_tuple in data['brand_attributes'].items():
            tag_idx = None
            if tag_tuple in self.mapper:
                tag_idx = self.mapper[tag_tuple]
            if tag_idx is None:
                continue
            idx += 1
            out.append({'id_categorization_results': '%06d' % idx,
                        'request_id': data['RequestID'],
                        'product_code': data['StyleCode'],
                        'id_fashion_attributes': tag_idx,
                        'fashion_attributes_score': 1.0,
                        'brand_id': brand,
                        'timestamp': ts})
        return out


class VendorAdapter(BaseAdapter):
    def __init__(self, vendor_config, brand_config, load_mapping=True):
        super(VendorAdapter, self).__init__(vendor_config, brand_config)
        this_dir = os.path.dirname(__file__)
        local_cred_path = os.path.join(this_dir, 'apt-bonbon-179602-14457dbec204.json')
        cred_path = os.environ.get('GOOGLE_APPLICATION_CREDENTIALS')
        if cred_path is None and os.path.exists(local_cred_path):
            cred_path = local_cred_path
        bucket = 'streamoid-bucket'
        self.ontology = 'Grupo_Soma-MP'
        mapping_path = None
        if load_mapping:
            # mapping_path = os.path.join(this_dir, 'fashion_attributes_table_2021_08_05__12_31_25_UTC.parquet.gzip')
            mapping_path = os.path.join(this_dir, 'fashion_attributes_table_2021_08_11__11_39_38_UTC.parquet.gzip')
        logger.info(mapping_path)
        self.encoder = GrupoSomaEncoder(mapping_path)
        self.writer = GoogleBucketWriter(bucket, cred_path)

    def post_request_to_vendor(self, request_id, outputs, **kwargs):
        out = {}
        brand_name = self.brand_config['_id']
        key = 'tagging-results/%s_%s.parquet.gzip' % (brand_name, request_id)
        ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')
        fpath = os.path.join('/tmp', key.split('/')[-1])
        idx = 0
        data = []
        for style_code, output in outputs.items():
            data.extend(self.encoder.encode(output, self.brand_config['_id'], ts, idx))
            idx = len(data)
            out[style_code] = True
        data_file = write_parquet_gzip(data)
        with open(fpath, 'wb') as f:
            f.write(data_file)
        logger.info('Saved to: %s', fpath)
        self.writer.save_file(key, data_file, content_type='application/gzip')
        return out

    def write_fashion_attributes_file(self):
        ts = datetime.now().strftime('%Y_%m_%d__%H_%M_%S_UTC')
        data = generate_table(self.ontology)
        key = 'fashion-attributes/fashion_attributes_table_%s.parquet.gzip' % ts
        fpath = key.split('/')[-1]
        with open(fpath, 'wb') as f:
            f.write(data)
        self.writer.save_file(key, data, content_type='application/gzip')

    def list_files(self, prefix):
        for blob in self.writer.list_files(prefix):
            print(blob)

    def remove_file(self, fname):
        self.writer.remove_file(fname)

    def fetch_file(self, fpath):
        fname = fpath.split('/')[-1]
        contents = self.writer.get_file(fpath)
        with open(fname, 'wb') as f:
            f.write(contents)


if __name__ == '__main__':
    adapter = VendorAdapter(None, None)
    # adapter.write_fashion_attributes_file()
    # adapter.remove_file('tagging-results/2.021071416e+19.parquet.gzip')
    # adapter.list_files('tagging-results')
    # adapter.list_files('fashion-attributes')
    # adapter.fetch_file('tagging-results/01.05.2781-2497.parquet.gzip')
