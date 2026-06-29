import logging

from time import time

logger = logging.getLogger(__name__)


class GoogleBucketWriter(object):
    def __init__(self, bucket_name, cred_path=None):
        from google.cloud import storage

        if cred_path is not None:
            self.client = storage.Client.from_service_account_json(cred_path)
        else:
            self.client = storage.Client()
        self.bucket = self.client.get_bucket(bucket_name)

    def get_file(self, key):
        t1 = time()
        blob = self.bucket.get_blob(key)
        if blob is None:
            return
        logger.info('Time taken [get-file]: %f sec', time() - t1)
        return blob.download_as_bytes()

    def file_exists(self, key):
        return self.bucket.blob(key).exists()

    def save_file(self, key, data, content_type='text/plain'):
        t1 = time()
        blob = self.bucket.blob(key)
        blob.upload_from_string(data, content_type=content_type)
        logger.info('Time taken [save-file]: %f sec', time() - t1)

    def list_files(self, prefix=None):
        return [x.name for x in self.client.list_blobs(self.bucket, prefix=prefix)]

    def get_image(self, image_id, image_dir='images'):
        return self.get_file('%s/%s' % (image_dir, image_id))

    def save_image(self, image_id, imageData, image_dir='images'):
        self.save_file('%s/%s' % (image_dir, image_id), imageData, 'image/jpeg')

    def remove_file(self, path):
        self.bucket.delete_blob(path)
