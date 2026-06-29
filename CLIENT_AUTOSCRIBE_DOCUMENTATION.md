# Client Autoscribe System Documentation

## Overview

The Client Autoscribe system is a comprehensive product data management platform that handles the complete lifecycle of product data from client uploads through curation, review, and final delivery back to vendors. The system integrates with Catalogix for data curation and provides a dashboard for internal team management.

## System Architecture

The system follows a workflow where product data flows through several stages:
1. **Products POST** → **Products SAVE client-autoscribe** → **Post to Catalogix** → **Curate in Catalogix** → **Import back to client-autoscribe** → **Review** → **Post To Client**


Prod Machine: ssh ubuntu@65.21.91.53 (Hetzner)

Start: cd /home/tanuj/git/experiments/client_autoscribe/ && uwsgi --ini uwsgi_client_autoscribe_v2.ini

Restart: uwsgi --reload /home/ubuntu/logs/client_autoscribe_v2.pid

Local: uwsgi --plugin python3 --plugin http --http-socket :4009 --wsgi-file client_autoscribe_api_v2.py --callable application

## Key Components

### 1. API Endpoints

#### Main API Endpoints (client_autoscribe_api_v2.py)

**Base URL Pattern**: `/api/autoscribe/vendors/{vendor_name}/brands/{brand_name}/`

#### Core Endpoints:

1. **POST `/api/autoscribe/vendors/{vendor_name}/brands/{brand_name}/post`**
   - **Purpose**: Used by clients to upload product data
   - **Functionality**: Accepts product data from external clients
   - **Handler**: `product_post()`

2. **GET `/api/autoscribe/vendors/{vendor_name}/brands/{brand_name}/export-only-new-catalogix`**
   - **Purpose**: Export new products to Catalogix
   - **Functionality**: Exports products that haven't been pushed to Catalogix yet
   - **Handler**: `export_only_new_catalogix_get()`

3. **POST `/api/autoscribe/vendors/{vendor_name}/brands/{brand_name}/import-catalogix`**
   - **Purpose**: Import curated data from Catalogix
   - **Functionality**: Receives curated product data back from Catalogix
   - **Handler**: `import_catalogix_data_post()`
   - **Data Format**: JSON with store_uuid, marketplace, and product data array

4. **GET `/api/autoscribe/vendors/{vendor_name}/catalogix-dashboard`**
   - **Purpose**: Internal dashboard for reviewing and managing products
   - **Functionality**: Provides overview of all brands and their status
   - **Handler**: `catalogix_dashboard_get()`

5. **GET `/api/autoscribe/vendors/{vendor_name}/brands/{brand_name}/catalogix-review`**
   - **Purpose**: Review curated products from Catalogix
   - **Functionality**: Shows products ready for review
   - **Handler**: `catalogix_review_get()`

6. **POST `/api/autoscribe/vendors/{vendor_name}/brands/{brand_name}/catalogix-mark-reviewed`**
   - **Purpose**: Mark products as reviewed
   - **Functionality**: Approves products for final posting
   - **Handler**: `catalogix_mark_reviewed_post()`

7. **GET `/api/autoscribe/vendors/{vendor_name}/brands/{brand_name}/catalogix-post-to-client`**
   - **Purpose**: Post reviewed data to client/vendor
   - **Functionality**: Triggers posting of all reviewed and non-hold data to vendor APIs
   - **Handler**: `catalogix_post_to_client_get()`

### 2. Cron Job System

#### client_autoscribe_to_catalogix_cron.py
- **Purpose**: Automated batch processing to push data to Catalogix
- **Frequency**: Every 30 minutes
- **Functionality**: 
  - Identifies new products not yet pushed to Catalogix
  - Converts product data to CSV format
  - Uploads CSV to Catalogix feed system
  - Updates database to mark products as pushed

**Key Functions**:
- `get_brands_sc()`: Gets new style codes for brands
- `get_data()`: Processes and uploads data to Catalogix
- `upload_csv_to_feed()`: Uploads CSV to Catalogix feed ingest API

### 3. Database Operations

#### ClientAutoscribeDB (client_autoscribe_db_v2.py)
- **Purpose**: Manages all database operations
- **Collections**: 
  - `products`: Original product data
  - `live`: Processed product data
  - `catalogix`: Data received from Catalogix
  - `rejects`: Rejected products

**Key Methods**:
- `get_new_products_not_pushed_catalogix()`: Gets products ready for Catalogix
- `get_catalogix_products_for_review_v2()`: Gets products ready for review
- `get_products_to_post()`: Gets products ready for client posting
- `post_to_client()`: Posts data to vendor APIs

### 4. Worker System

#### client_autoscribe_worker_v2.py
- **Purpose**: Background task processing
- **Tasks**:
  - `post_to_client()`: Handles posting to vendor APIs
  - `catalogix_post_to_client()`: Manages Catalogix to client posting
  - `trigger_precompute()`: Triggers preprocessing tasks

## Complete Workflow

### 1. Client Upload Phase
```
Client → POST /api/autoscribe/vendors/{vendor}/brands/{brand}/post
```
- Clients upload product data via the POST endpoint
- Data is stored in the `products` collection
- Products are marked as new and ready for processing

### 2. Automated Catalogix Push (Every 30 minutes)
```
Cron Job → client_autoscribe_to_catalogix_cron.py
```
- Cron job identifies new products not yet pushed to Catalogix
- Converts product data to CSV format with proper mappings
- Uploads CSV to Catalogix feed system via feed ingest API
- Updates database to mark products as "pushed to catalogix"

### 3. Catalogix Curation Phase
```
Catalogix Internal Team → Curates and translates product data
```
- Internal team at Catalogix reviews and curates the uploaded data
- Products are processed through Catalogix's curation system
- Curated data is prepared for import back to client_autoscribe

### 4. Import from Catalogix
```
Catalogix → POST /api/autoscribe/vendors/{vendor}/brands/{brand}/import-catalogix
```
- Catalogix triggers import via the import-catalogix endpoint
- Curated data is received and stored in the `catalogix` collection
- Products are marked as ready for review

### 5. Internal Review Phase
```
Internal Team (Arshiya) → GET /api/autoscribe/vendors/{vendor}/catalogix-dashboard
```
- Internal team accesses the catalogix dashboard
- Reviews curated products from Catalogix
- Can mark products as:
  - **Reviewed**: Approved for posting
  - **On Hold**: Temporarily blocked from posting

### 6. Final Posting to Client
```
Internal Team → GET /api/autoscribe/vendors/{vendor}/brands/{brand}/catalogix-post-to-client
```
- Internal team triggers posting of all reviewed (non-hold) products
- System posts data to vendor APIs using their specific integrations
- Triggers vendor-specific API calls to deliver final curated data
- Updates database to mark products as posted

## Integration Points

### Vendor Integrations
The system supports multiple vendor integrations located in the `integrations/` directory:
- `abfrl_lbrd_prod.py`: ABFRL production integration (Active client abfrl prod)
- `abfrl_test.py`: ABFRL testing integration (Active client abfrl Test)
- `grupo_soma.py`: Grupo Soma integration (Unactive client)
- `farfetch.py`: Farfetch integration (Unactive client)
- `tatacliq.py`: Tata Cliq integration (Unactive client)

Each integration implements a `VendorAdapter` class that handles:
- API authentication
- Data formatting
- Error handling
- Response processing

### Catalogix Integration
- **Feed Upload API**: `https://service.feed-upload.streamoid.com/v1/upload`
- **Feed Ingest API**: `https://service.feed-upload.streamoid.com/v1/feed/{store_uuid}/upload`
- **Store Settings API**: `https://kepler-backend.staging.streamoid.com/v1/store/{store_uuid}`

## Configuration

### Vendor Configuration
- Each vendor has specific configuration for API endpoints, authentication, and data mappings
- Brand-level configurations define product mappings and processing rules
- Store UUIDs map brands to Catalogix stores

### Database Configuration
- - MongoDB collections follow naming patterns:
  - `v_{vendor}_autoscribe`
- MongoDB collections follow naming patterns:
  - `products:{brand}`
  - `catalogix:{brand}`
  - `rejects:{brand}`

## Error Handling


## Monitoring and Reporting

### Logs:

    #### API:
        - tail -f ~/logs/uwsgi/client_autoscribe_v2_log.txt 

    #### Worker (client_autoscribe_v2_abfrl / client_autoscribe_v2 / client_autoscribe_v2_misc / client_autoscribe_v2_streamoid):
        - tail -f ~/logs/{worker}/*.log



### Dashboard Features
- Real-time counts of products in each stage
- Brand-level status overview
- Export capabilities for different product states
- Bulk operations for efficiency

### Teams Integration (Needs to be updated)
- Automated notifications for important events
- Daily status reports
- Error alerts and notifications

## Security Considerations

- API key authentication for vendor integrations
- Store UUID validation for Catalogix operations
- Request ID tracking for audit trails
- Secure handling of product data and images

## Deployment

### Services
- **API Service**: Handles HTTP requests and responses
- **Worker Service**: Processes background tasks
- **Cron Service**: Handles scheduled operations
- **Listener Service**: Monitors for real-time updates

### Configuration Files
- `uwsgi_client_autoscribe_v2.ini`: API service configuration
- `uwsgi_client_autoscribe.ini`: Legacy API configuration
- `uwsgi_autoscribe_listener.ini`: Listener service configuration
- `gunicorn.conf.py`: Gunicorn server configuration

### Worker Configuration Files:
- ls ~/bin/*.conf


# People & Ownership
 
- Stakeholders / Consumers of the Service: ABFRL
- Curation Team: Arshiya Dodrajka
