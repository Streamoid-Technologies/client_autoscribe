from pymongo import MongoClient, DESCENDING
from client_autoscribe_db_v2 import ClientAutoscribeDB

db = ClientAutoscribeDB('localhost')

brands = db.list_brands('abfrl_lbrd_prod')
vendor_db = 'v_abfrl_lbrd_prod_autoscribe'
print(brands)

for brand in brands:
    catalogix_collection = db.client[vendor_db][db.catalogix_coll_pattern % brand]
    products_collection = db.client[vendor_db][db.products_coll_pattern % brand]

# Find documents in catalogix where pushed is true
    catalogix_documents = catalogix_collection.find({'pushed': True})

# Update products collection for each matching document
    for doc in catalogix_documents:
        products_collection.update_one(
            {'_id': doc['_id']},  # Assuming _id is the unique identifier
            {'$set': {'batch_number': 1}}
        )

print('Update completed successfully')
