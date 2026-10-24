"""Binary storage for documents (S3 in AWS, a dict locally)."""


class MemoryDocStore:
    def __init__(self):
        self.objects = {}

    def put(self, key, data, mime="application/octet-stream"):
        self.objects[key] = (data, mime)
        return key

    def get(self, key):
        return self.objects[key][0]

    def delete(self, key):
        self.objects.pop(key, None)


class S3DocStore:
    def __init__(self, bucket, region):
        import boto3
        self.bucket = bucket
        self.s3 = boto3.client("s3", region_name=region)

    def put(self, key, data, mime="application/octet-stream"):
        self.s3.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=mime, ServerSideEncryption="aws:kms")
        return key

    def get(self, key):
        return self.s3.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def delete(self, key):
        self.s3.delete_object(Bucket=self.bucket, Key=key)


def docstore(settings):
    return S3DocStore(settings.bucket, settings.region) if settings.bucket else MemoryDocStore()
