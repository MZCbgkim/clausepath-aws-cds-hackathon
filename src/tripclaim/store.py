"""Single-table storage (design §9) over two backends: in-memory (local/sim/tests) and DynamoDB.

The Repository holds all key design; backends only implement put/get/query/update.

Keys
  TRV#<id>        META                      traveler profile (+ active_claim_id, last_inbound)
  TRV#<id>        POL#<policy_id>           policy (plan, period, purchased_at)
  TRV#<id>        SEG#<flight>#<date>       booked flight segment      GSI1: FLT#<flight>#<date> / TRV#<id>
  CONTACT#<ch>#<address>  META              contact -> traveler id
  EVT#<id>        META                      disruption event
  CLM#<id>        META                      claim                      GSI1: CLAIMS / <updated_at>
  CLM#<id>        DOC#<ts>#<doc_id>         document + extraction + flags
  CLM#<id>        MSG#<ts>#<provider_id>    message (in/out, channel, status, chosen_reason)
  CLM#<id>        ACT#<ts>                  reviewer action
  PMSG#<provider_id>  META                  provider message id -> claim/message key (dedupe + status updates)
  TOKEN#<token>   META                      secure-page token -> claim id
  AUDIT#<date>    <ts>#<actor>              audit log
"""
import hashlib
import json
import re
import secrets
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal


B62 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"


def link_token():
    """128 random bits as 22 letters and digits. No "-" or "_": phone messaging apps break a link there
    (the traveler saw a space inside the token)."""
    n, out = int.from_bytes(secrets.token_bytes(16), "big"), ""
    for _ in range(22):
        n, r = divmod(n, 62)
        out += B62[r]
    return out


LINK_TOKEN = re.compile(r"(/(?:c|secure)/)([\w-]{20,})")  # tokens are 22 chars


def redact_links(text):
    """Stored messages never keep a usable link: '/c/abc…xyz1' -> '/c/••••xyz1'. Sends use the full link."""
    return LINK_TOKEN.sub(lambda m: f"{m.group(1)}••••{m.group(2)[-4:]}", text) if isinstance(text, str) else text


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Conflict(Exception):
    """Conditional put failed (item already exists)."""


# ---------- backends ----------

class MemoryBackend:
    def __init__(self):
        self.items = {}

    def put(self, item, if_absent=False):
        key = (item["PK"], item["SK"])
        if if_absent and key in self.items:
            raise Conflict(key)
        self.items[key] = json.loads(json.dumps(item))

    def get(self, pk, sk):
        item = self.items.get((pk, sk))
        return json.loads(json.dumps(item)) if item else None

    def query(self, pk, sk_prefix=""):
        rows = [v for (p, s), v in self.items.items() if p == pk and s.startswith(sk_prefix)]
        return [json.loads(json.dumps(r)) for r in sorted(rows, key=lambda r: r["SK"])]

    def query_gsi1(self, pk):
        rows = [v for v in self.items.values() if v.get("GSI1PK") == pk]
        return [json.loads(json.dumps(r)) for r in sorted(rows, key=lambda r: r.get("GSI1SK", ""))]

    def delete(self, pk, sk):
        self.items.pop((pk, sk), None)

    def update(self, pk, sk, fields):
        item = self.items[(pk, sk)]
        item.update(json.loads(json.dumps(fields)))
        return json.loads(json.dumps(item))

    def take(self, pk, sk, cap):
        item = self.items.setdefault((pk, sk), {"PK": pk, "SK": sk, "n": 0})
        if item["n"] >= cap:
            return False
        item["n"] += 1
        return True


def _to_dynamo(value):
    return json.loads(json.dumps(value), parse_float=Decimal)


def _from_dynamo(value):
    if isinstance(value, list):
        return [_from_dynamo(v) for v in value]
    if isinstance(value, dict):
        return {k: _from_dynamo(v) for k, v in value.items()}
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    return value


class DynamoBackend:
    def __init__(self, table_name, region, endpoint_url=None):
        import boto3
        from boto3.dynamodb.conditions import Key
        self.Key = Key
        self.table = boto3.resource("dynamodb", region_name=region, endpoint_url=endpoint_url).Table(table_name)

    def put(self, item, if_absent=False):
        kwargs = {"Item": _to_dynamo(item)}
        if if_absent:
            kwargs["ConditionExpression"] = "attribute_not_exists(PK)"
        try:
            self.table.put_item(**kwargs)
        except self.table.meta.client.exceptions.ConditionalCheckFailedException as e:
            raise Conflict(item["PK"]) from e

    def get(self, pk, sk):
        return _from_dynamo(self.table.get_item(Key={"PK": pk, "SK": sk}).get("Item"))

    def _query_all(self, **kwargs):
        items, start = [], None
        while True:
            if start:
                kwargs["ExclusiveStartKey"] = start
            page = self.table.query(**kwargs)
            items += page["Items"]
            start = page.get("LastEvaluatedKey")
            if not start:
                return [_from_dynamo(i) for i in items]

    def query(self, pk, sk_prefix=""):
        cond = self.Key("PK").eq(pk)
        if sk_prefix:
            cond = cond & self.Key("SK").begins_with(sk_prefix)
        return self._query_all(KeyConditionExpression=cond)

    def query_gsi1(self, pk):
        return self._query_all(IndexName="GSI1", KeyConditionExpression=self.Key("GSI1PK").eq(pk))

    def delete(self, pk, sk):
        self.table.delete_item(Key={"PK": pk, "SK": sk})

    def update(self, pk, sk, fields):
        names = {f"#k{i}": k for i, k in enumerate(fields)}
        values = {f":v{i}": _to_dynamo(v) for i, v in enumerate(fields.values())}
        expr = "SET " + ", ".join(f"#k{i} = :v{i}" for i in range(len(fields)))
        res = self.table.update_item(Key={"PK": pk, "SK": sk}, UpdateExpression=expr,
                                     ExpressionAttributeNames=names, ExpressionAttributeValues=values,
                                     ReturnValues="ALL_NEW")
        return _from_dynamo(res["Attributes"])

    def take(self, pk, sk, cap):
        """Add one to a counter unless it already reached cap; atomic, so every Lambda shares the same count."""
        try:
            self.table.update_item(Key={"PK": pk, "SK": sk}, UpdateExpression="ADD n :one",
                                   ConditionExpression="attribute_not_exists(n) OR n < :cap",
                                   ExpressionAttributeValues={":one": 1, ":cap": cap})
            return True
        except self.table.meta.client.exceptions.ConditionalCheckFailedException:
            return False


# ---------- repository ----------

def _strip(item):
    return {k: v for k, v in (item or {}).items() if k not in ("PK", "SK", "GSI1PK", "GSI1SK")} if item else None


class Repository:
    def __init__(self, backend):
        self.db = backend

    def take_quota(self, name, cap):
        return self.db.take("QUOTA", name, cap)

    # travelers
    def put_traveler(self, traveler):
        t = dict(traveler)
        t.setdefault("last_inbound", {})
        self.db.put({"PK": f"TRV#{t['id']}", "SK": "META", **t})
        for channel, address in (("whatsapp", t.get("phone")), ("sms", t.get("phone")), ("email", t.get("email"))):
            if address:
                self.db.put({"PK": f"CONTACT#{channel}#{address.lower()}", "SK": "META", "traveler_id": t["id"]})
        return t

    def get_traveler(self, traveler_id):
        return _strip(self.db.get(f"TRV#{traveler_id}", "META"))

    def update_traveler(self, traveler_id, **fields):
        return _strip(self.db.update(f"TRV#{traveler_id}", "META", fields))

    def find_traveler(self, channel, address):
        channel = "sms" if channel == "rcs" else channel
        row = self.db.get(f"CONTACT#{channel}#{(address or '').lower()}", "META")
        return self.get_traveler(row["traveler_id"]) if row else None

    def put_policy(self, traveler_id, policy):
        self.db.put({"PK": f"TRV#{traveler_id}", "SK": f"POL#{policy['id']}", **policy})

    def policies(self, traveler_id):
        return [_strip(r) for r in self.db.query(f"TRV#{traveler_id}", "POL#")]

    def put_segment(self, traveler_id, segment):
        key = f"{segment['flight']}#{segment['date']}"
        self.db.put({"PK": f"TRV#{traveler_id}", "SK": f"SEG#{key}", "GSI1PK": f"FLT#{key}",
                     "GSI1SK": f"TRV#{traveler_id}", "traveler_id": traveler_id, **segment})

    def segments(self, traveler_id):
        return [_strip(r) for r in self.db.query(f"TRV#{traveler_id}", "SEG#")]

    def travelers_on_flight(self, flight, date):
        return [r["traveler_id"] for r in self.db.query_gsi1(f"FLT#{flight}#{date}")]

    # events
    def put_event(self, event):
        e = {"id": event.get("id") or f"evt-{uuid.uuid4().hex[:8]}", "received_at": now_iso(), **event}
        self.db.put({"PK": f"EVT#{e['id']}", "SK": "META", **e})
        return e

    def get_event(self, event_id):
        return _strip(self.db.get(f"EVT#{event_id}", "META"))

    def link_event_to_flight(self, event):
        self.db.put({"PK": f"EVTFLT#{event['flight']}#{event['date']}", "SK": f"EVT#{event['id']}", "event_id": event["id"]})

    def events_for_flight(self, flight, date):
        return [self.get_event(r["event_id"]) for r in self.db.query(f"EVTFLT#{flight}#{date}", "EVT#")]

    # claims
    def create_claim(self, traveler_id, event_id=None, **fields):
        cid = f"clm-{uuid.uuid4().hex[:8]}"
        ts = now_iso()
        claim = {"id": cid, "traveler_id": traveler_id, "event_id": event_id, "status": "open",
                 "coverage": None, "checklist": {}, "flags": [], "reminders_sent": 0, "last_reminder_at": None,
                 "created_at": ts, "updated_at": ts, **fields}
        self.db.put({"PK": f"CLM#{cid}", "SK": "META", "GSI1PK": "CLAIMS", "GSI1SK": ts, **claim})
        self.update_traveler(traveler_id, active_claim_id=cid)
        return claim

    def get_claim(self, claim_id):
        return _strip(self.db.get(f"CLM#{claim_id}", "META"))

    def update_claim(self, claim_id, **fields):
        fields["updated_at"] = now_iso()
        fields["GSI1SK"] = fields["updated_at"]
        return _strip(self.db.update(f"CLM#{claim_id}", "META", fields))

    def list_claims(self):
        return [_strip(r) for r in reversed(self.db.query_gsi1("CLAIMS"))]

    # documents
    def add_document(self, claim_id, doc):
        d = {"id": doc.get("id") or f"doc-{uuid.uuid4().hex[:8]}", "received_at": now_iso(), "seq": f"{time.time_ns():020d}", **doc}
        self.db.put({"PK": f"CLM#{claim_id}", "SK": f"DOC#{d['seq']}#{d['id']}", **d})  # seq keeps arrival order
        return d

    def documents(self, claim_id):
        return [_strip(r) for r in self.db.query(f"CLM#{claim_id}", "DOC#")]

    def update_document(self, claim_id, doc, **fields):
        return _strip(self.db.update(f"CLM#{claim_id}", f"DOC#{doc['seq']}#{doc['id']}", fields))

    # messages
    def claim_provider_id(self, provider_id, claim_id):
        """Dedupe inbound messages: True the first time a provider message id is seen."""
        try:
            self.db.put({"PK": f"PMSG#{provider_id}", "SK": "META", "claim_id": claim_id}, if_absent=True)
            return True
        except Conflict:
            return False

    def add_message(self, claim_id, message):
        m = {"id": message.get("provider_id") or f"msg-{uuid.uuid4().hex[:8]}", "at": now_iso(),
             "status": "received" if message.get("direction") == "in" else "sent", **message}
        m["text"] = redact_links(m.get("text"))
        sk = f"MSG#{time.time_ns():020d}#{m['id']}"  # nanosecond prefix keeps send order within a second
        self.db.put({"PK": f"CLM#{claim_id}", "SK": sk, **m})
        if m.get("provider_id") and m.get("direction") == "out":
            self.db.put({"PK": f"PMSG#{m['provider_id']}", "SK": "META", "claim_id": claim_id, "message_sk": sk})
        return m

    def messages(self, claim_id):
        return [_strip(r) for r in self.db.query(f"CLM#{claim_id}", "MSG#")]

    def message_by_provider_id(self, provider_id):
        ref = self.db.get(f"PMSG#{provider_id}", "META")
        if not ref or "message_sk" not in ref:
            return None, None
        msg = _strip(self.db.get(f"CLM#{ref['claim_id']}", ref["message_sk"]))
        return (ref["claim_id"], msg | {"_sk": ref["message_sk"]}) if msg else (None, None)

    def update_message(self, claim_id, message_sk, **fields):
        return _strip(self.db.update(f"CLM#{claim_id}", message_sk, fields))

    # reviewer actions, secure page tokens, audit
    def add_action(self, claim_id, action):
        a = {"at": now_iso(), **action}
        self.db.put({"PK": f"CLM#{claim_id}", "SK": f"ACT#{a['at']}#{uuid.uuid4().hex[:4]}", **a})
        return a

    def actions(self, claim_id):
        return [_strip(r) for r in self.db.query(f"CLM#{claim_id}", "ACT#")]

    # Link tokens: the link is the credential (no login). Only a SHA-256 of the token is stored, each token is
    # scoped to one claim and one purpose ("chat" or "secure"), and expires.
    @staticmethod
    def _token_key(token):
        return "TOKEN#" + hashlib.sha256(token.encode()).hexdigest()

    def new_link_token(self, claim_id, scope, days):
        token = link_token()
        expires = datetime.now(timezone.utc).timestamp() + days * 86400
        self.db.put({"PK": self._token_key(token), "SK": "META", "claim_id": claim_id, "scope": scope,
                     "created_at": now_iso(), "expires_at": int(expires)})
        return token

    def claim_for_link(self, token, scope):
        row = self.db.get(self._token_key(token or ""), "META")
        if not row or row.get("scope") != scope or row.get("expires_at", 0) < datetime.now(timezone.utc).timestamp():
            return None
        return row["claim_id"]

    def new_secure_token(self, claim_id):
        return self.new_link_token(claim_id, "secure", days=7)

    def claim_for_token(self, token):
        return self.claim_for_link(token, "secure")

    audit_sink = None  # callable(entry): the write-once copy (S3 Object Lock in AWS)

    def purge_traveler(self, traveler_id):
        """Remove a demo traveler and every claim of theirs: profile, policies, segments, contacts, claims with their
        documents, messages and actions, and provider-id lookups. The audit log is kept (it is write-once by design).
        Returns (claims removed, document keys to delete from storage, flight-date keys the traveler was on)."""
        t = self.get_traveler(traveler_id) or {}
        claims = [c for c in self.list_claims() if c.get("traveler_id") == traveler_id]
        doc_keys = []
        for c in claims:
            for item in self.db.query(f"CLM#{c['id']}"):
                if item["SK"].startswith("DOC#") and item.get("s3_key"):
                    doc_keys.append(item["s3_key"])
                if item["SK"].startswith("MSG#") and item.get("provider_id"):
                    self.db.delete(f"PMSG#{item['provider_id']}", "META")
                self.db.delete(item["PK"], item["SK"])
        flights = []
        for item in self.db.query(f"TRV#{traveler_id}"):
            if item["SK"].startswith("SEG#"):
                flights.append(item["SK"][4:])
            self.db.delete(item["PK"], item["SK"])
        for channel, address in (("whatsapp", t.get("phone")), ("sms", t.get("phone")), ("email", t.get("email"))):
            row = self.db.get(f"CONTACT#{channel}#{(address or '').lower()}", "META") if address else None
            if row and row.get("traveler_id") == traveler_id:  # another traveler may share the address
                self.db.delete(f"CONTACT#{channel}#{address.lower()}", "META")
        return len(claims), doc_keys, flights

    def purge_flight_events(self, flight_key):
        """Disruption events of a flight-date nobody is booked on any more."""
        flight, date = flight_key.split("#", 1)
        if self.travelers_on_flight(flight, date):
            return 0
        links = self.db.query(f"EVTFLT#{flight_key}")
        for link in links:
            self.db.delete(f"EVT#{link['event_id']}", "META")
            self.db.delete(link["PK"], link["SK"])
        return len(links)

    def audit(self, actor, action, target, before=None, after=None, **detail):
        ts = now_iso()
        item = {"PK": f"AUDIT#{ts[:10]}", "SK": f"{ts}#{actor}#{uuid.uuid4().hex[:4]}",
                "action": action, "target": target, "before": before, "after": after, **detail}
        self.db.put(item)
        if self.audit_sink:
            try:
                self.audit_sink({"at": ts, "actor": actor, **{k: v for k, v in item.items() if k not in ("PK", "SK")}})
            except Exception as e:  # the working copy is written; a failed copy is logged for alarms, not shown to users
                print(f"AUDIT_SINK_FAILED {type(e).__name__}: {e}")

    # Bulk sends over the limit wait here for a second admin: BULK / <id>
    def put_bulk(self, kind, body, by, count):
        item = {"PK": "BULK", "SK": uuid.uuid4().hex[:10], "kind": kind, "body": body, "by": by, "count": count,
                "at": now_iso(), "state": "pending"}
        self.db.put(item)
        return _strip(item) | {"id": item["SK"]}

    def bulk_requests(self, state="pending"):
        return [_strip(r) | {"id": r["SK"]} for r in self.db.query("BULK") if r.get("state") == state]

    def close_bulk(self, bulk_id, state, by):
        return self.db.update("BULK", bulk_id, {"state": state, "closed_by": by, "closed_at": now_iso()})

    def audits(self, date):
        """One UTC day of the audit log, oldest first: [{at, actor, action, target, ...}]."""
        out = []
        for r in self.db.query(f"AUDIT#{date}"):
            at, actor = r["SK"].split("#")[:2]
            out.append({"at": at, "actor": actor, **{k: v for k, v in _strip(r).items() if k not in ("PK", "SK")}})
        return out


def repository(settings):
    if settings.store == "dynamodb":
        import os
        return Repository(DynamoBackend(settings.table_name, settings.region, os.environ.get("DYNAMODB_ENDPOINT") or None))
    return Repository(MemoryBackend())
