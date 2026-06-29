import os
import sys
from pymongo import MongoClient

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from client_autoscribe.client_autoscribe_db_v2 import ClientAutoscribeDB as DB2, get_db_name


if __name__ == '__main__':
    from_vendor = 'abfrl_lbrd_prod'
    to_vendor = 'abfrl_test'

    db2 = DB2('localhost')
    db2.set_vendor_config(to_vendor, {'curation_ontology': 'Streamoid-MP'})

    client = MongoClient()
    coll1 = client[get_db_name(from_vendor)]['brands']
    coll2 = client[get_db_name(to_vendor)]['brands']
    for row in coll1.find():
        coll2.replace_one({'_id': row['_id']}, row, upsert=True)
