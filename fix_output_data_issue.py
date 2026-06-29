from pymongo import MongoClient

if __name__ == '__main__':
    client = MongoClient()
    db_name = 'v_abfrl_lbrd_prod_autoscribe'
    coll = client[db_name]['brands']
    brands = [row['_id'] for row in coll.find()]

    for brand in brands:
        coll_name = 'live:' + brand
        coll = client[db_name][coll_name]
        print(brand)
        x = coll.update_many({'output': {'$exists': True}},
                             {'$rename': {'output': 'data'}})
        print(x)
