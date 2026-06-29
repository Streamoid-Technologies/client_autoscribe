# Client Autoscribe v2 Queuing Design

## Tasks
As per client_autoscribe_worker_v2 module:

### Active
- post_to_client: (highest priority)
  - better to split by load / priority vendors
  - client_autoscribe_v2_abfrl, client_autoscribe_v2_streamoid, client_autoscribe_v2_misc
- translate_and_save: (medium priority)
  - expected to be fast; can be managed with single queue
  - client_autoscribe_v2
- trigger_precompute: (lowest priority)
  - on new products; can be slow in case of large number of products
  - client_autoscribe_v2_misc
   
### Deprecated
- queue_post_to_teams
- post_rpa_files
- fetch_files_and_store
- update_rpa_status

## Queues
- client_autoscribe_v2
- client_autoscribe_v2_misc
- client_autoscribe_v2_abfrl
- client_autoscribe_v2_streamoid