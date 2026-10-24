"""Runs the full flow against DynamoDB Local when DYNAMODB_ENDPOINT is set:
   docker run -d -p 8000:8000 amazon/dynamodb-local
   DYNAMODB_ENDPOINT=http://localhost:8000 pytest tests/test_dynamo.py
"""
import os
import uuid

import boto3
import pytest

from tripclaim.store import DynamoBackend, Repository

from . import test_flow

ENDPOINT = os.environ.get("DYNAMODB_ENDPOINT")
pytestmark = pytest.mark.skipif(not ENDPOINT, reason="DYNAMODB_ENDPOINT not set")


@pytest.fixture
def dynamo_env(env):
    name = f"tripclaim-test-{uuid.uuid4().hex[:6]}"
    ddb = boto3.client("dynamodb", endpoint_url=ENDPOINT, region_name="us-east-1",
                       aws_access_key_id="x", aws_secret_access_key="x")
    ddb.create_table(TableName=name, BillingMode="PAY_PER_REQUEST",
                     AttributeDefinitions=[{"AttributeName": a, "AttributeType": "S"} for a in ("PK", "SK", "GSI1PK", "GSI1SK")],
                     KeySchema=[{"AttributeName": "PK", "KeyType": "HASH"}, {"AttributeName": "SK", "KeyType": "RANGE"}],
                     GlobalSecondaryIndexes=[{"IndexName": "GSI1", "Projection": {"ProjectionType": "ALL"},
                                              "KeySchema": [{"AttributeName": "GSI1PK", "KeyType": "HASH"},
                                                            {"AttributeName": "GSI1SK", "KeyType": "RANGE"}]}])
    os.environ.setdefault("AWS_ACCESS_KEY_ID", "x")
    os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "x")
    svc = env["svc"]
    memory = svc.repo
    svc.repo = svc.router.repo = Repository(DynamoBackend(name, "us-east-1", ENDPOINT))
    # copy the seeded traveler/policy/segment into DynamoDB
    for item in memory.db.items.values():
        svc.repo.db.put(item)
    yield env
    ddb.delete_table(TableName=name)


def test_full_flow_on_dynamodb(dynamo_env):
    test_flow.test_full_claim_flow(dynamo_env)


def test_dedupe_on_dynamodb(dynamo_env):
    test_flow.test_duplicate_inbound_is_ignored(dynamo_env)
