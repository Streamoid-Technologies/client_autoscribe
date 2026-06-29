import os
import shutil

from pymongo import MongoClient
from glob import glob
from rpa_interface import get_md5sum

if __name__ == '__main__':
    client = MongoClient()
    coll = client['rpa_db']['requests']
    substr = 'Unable to construct record instance'

    collected = []
    for row in coll.find({'StreamoidRequests.RequestException': {'$ne': ''}}):
        reqs = row['StreamoidRequests']
        if len(reqs) > 0 and substr in reqs[0].get('RequestException', ''):
            collected.append(row['_id'])

    print(collected, len(collected))

    base_dir = os.path.join(os.getenv('HOME'), 'rpa_data')
    unique_ids = {}
    for row in collected:
        req_id = row.split(':')[0]
        for f1 in glob(os.path.join(base_dir, req_id, 'template_*')):
            with open(f1, 'rb') as f:
                id1 = get_md5sum(f.read())
            shutil.copyfile(f1, os.path.join('/tmp', id1 + '.xls'))
            if id1 not in unique_ids:
                unique_ids[id1] = []
            unique_ids[id1].append(f1)

    print(unique_ids, len(unique_ids))