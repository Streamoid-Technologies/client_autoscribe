from pymongo import MongoClient


def live(db_name, brand):
    coll_name = 'live:' + brand
    coll = client[db_name][coll_name]
    print(brand)
    x = coll.update_many({'info.gender': 'Women'}, {'$set': {'info.gender': 'women'}})
    print(x)
    x = coll.update_many({'info.gender': 'Men'}, {'$set': {'info.gender': 'men'}})
    print(x)


def products(db_name, brand):
    coll_name = 'products:' + brand
    coll = client[db_name][coll_name]
    print(brand)
    x = coll.update_many({'gender': 'Women'}, {'$set': {'gender': 'women'}})
    print(x)
    x = coll.update_many({'gender': 'Men'}, {'$set': {'gender': 'men'}})
    print(x)


if __name__ == '__main__':
    client = MongoClient()
    db_name = 'v_streamoid_autoscribe'
    coll = client[db_name]['brands']
    brands = [row['_id'] for row in coll.find()]

    for brand in brands:
        products(db_name, brand)
