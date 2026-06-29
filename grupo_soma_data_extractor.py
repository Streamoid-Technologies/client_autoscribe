import csv
from pymongo import MongoClient

def export_unique_to_csv(db_name, collection_name, csv_filename, category_field):
    client = MongoClient()
    db = client[db_name]
    collection = db[collection_name]

    # Find the first document in the collection to get field names dynamically
    first_document = collection.find_one()

    if not first_document:
        print(f"No documents found in {collection_name}")
        return

    # Get the fields from the document
    fields = list(first_document.keys())

    # Query the collection for unique entries based on the category field
    cursor = collection.aggregate([
        {"$group": {"_id": f"${category_field}", "document": {"$first": "$$ROOT"}}},
        {"$replaceRoot": {"newRoot": "$document"}}
    ])

    # Write to CSV file
    with open(csv_filename, 'w', newline='', encoding='utf-8') as csv_file:
        csv_writer = csv.DictWriter(csv_file, fieldnames=fields)
        csv_writer.writeheader()

        for document in cursor:
            csv_writer.writerow(document)

    print(f"Data exported to {csv_filename}")

# Specify your MongoDB database name
db_name = 'v_grupo_soma_autoscribe'

# Specify the collections and corresponding CSV filenames
collections = {
    'products:animal': 'animal.csv',
    'products:farm_us': 'farm_us.csv',
    'farm_rio':'products:farm_rio',
    # Add more collections as needed
}

# Specify the category field to check for uniqueness
category_field = 'category'  # Replace with the actual category field name

# Export data for each collection
for collection_name, csv_filename in collections.items():
    export_unique_to_csv(db_name, collection_name, csv_filename, category_field)

