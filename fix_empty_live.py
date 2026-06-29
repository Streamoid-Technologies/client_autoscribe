from pymongo import MongoClient

if __name__ == '__main__':
    vendor = 'v_streamoid_autoscribe'
    #coll_name = 'live:v_pantaloons'
    #coll_name = 'live:v_peter_england'
    coll_name = 'live:v_allen_solly'
    client = MongoClient()
    coll = client[vendor][coll_name]
    ids = set()
    for row in coll.find():
        for url, dets in row.get('data', {}).items():
            if len(dets) < 1:
                ids.add(row['_id'])
                break

    print('Removing %d docs', len(ids))
    for id1 in ids:
        coll.delete_one({'_id': id1})
