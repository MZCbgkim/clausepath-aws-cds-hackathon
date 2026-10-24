"""SES receipt rule stores raw mail in S3 -> S3 event -> Lambda."""
import logging
from urllib.parse import unquote_plus

import boto3

from ..app import service
from ..inbound import parse_email

log = logging.getLogger()
log.setLevel(logging.INFO)


def handler(event, context):
    svc = service()
    s3 = boto3.client("s3", region_name=svc.s.region)
    for record in event.get("Records", []):
        bucket, key = record["s3"]["bucket"]["name"], unquote_plus(record["s3"]["object"]["key"])
        inb = parse_email(s3.get_object(Bucket=bucket, Key=key)["Body"].read())
        result = svc.handle_inbound(inb)
        log.info("email %s -> %s", key, result and result["claim_id"])
    return {"ok": True}
