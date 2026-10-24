"""Live AWS channel clients."""
import json
import time
import uuid
from email.message import EmailMessage

import boto3

from ..i18n import WA_LANGUAGE, wa_template_name
from . import ChannelError, sms_parts


class WhatsApp:
    name = "whatsapp"

    def __init__(self, s):
        self.s = s
        self.client = boto3.client("socialmessaging", region_name=s.region)

    def _send(self, payload):
        try:
            res = self.client.send_whatsapp_message(
                originationPhoneNumberId=self.s.wa_phone_number_id, metaApiVersion=self.s.wa_meta_api_version,
                message=json.dumps({"messaging_product": "whatsapp", **payload}).encode())
        except Exception as e:  # botocore ClientError and validation errors
            raise ChannelError(f"whatsapp: {e}") from e
        return res["messageId"]

    def send_text(self, to, text):
        return self._send({"to": to, "type": "text", "text": {"body": text, "preview_url": True}})

    def send_template(self, to, name, params, locale="en"):
        components = [{"type": "body", "parameters": [{"type": "text", "text": str(p)} for p in params]}]
        language = WA_LANGUAGE.get(locale, self.s.wa_template_language)  # one approved template per language
        return self._send({"to": to, "type": "template",
                           "template": {"name": wa_template_name(name, locale), "language": {"code": language},
                                        "components": components}})

    def fetch_media(self, media_id, bucket, key):
        """Copies inbound WhatsApp media to S3 and returns the bytes."""
        self.client.get_whatsapp_message_media(
            mediaId=media_id, originationPhoneNumberId=self.s.wa_phone_number_id,
            destinationS3File={"bucketName": bucket, "key": key})
        return boto3.client("s3", region_name=self.s.region).get_object(Bucket=bucket, Key=key)["Body"].read()


class Sms:
    name = "sms"

    def __init__(self, s):
        self.s = s
        self.client = boto3.client("pinpoint-sms-voice-v2", region_name=s.region)

    def send_text(self, to, text):
        ids = []
        for i, part in enumerate(sms_parts(text)):  # the words and each link as separate SMS, so links are never cut
            if i:
                time.sleep(1.5)  # makes out-of-order delivery less likely (carriers do not guarantee order)
            kwargs = {"DestinationPhoneNumber": to, "MessageBody": part[:1500], "MessageType": "TRANSACTIONAL"}
            if self.s.sms_origination:  # without one, AWS uses a shared route where the destination allows it
                kwargs["OriginationIdentity"] = self.s.sms_origination
            if self.s.sms_configuration_set:
                kwargs["ConfigurationSetName"] = self.s.sms_configuration_set
            try:
                ids.append(self.client.send_text_message(**kwargs)["MessageId"])
            except Exception as e:
                raise ChannelError(f"sms: {e}") from e
        return ids[0]  # delivery events for the first part stand for the message

    send_template = None


class Rcs:
    """RCS through the RCS agent (testing agents need no carrier approval); falls back to SMS server-side."""
    name = "rcs"

    def __init__(self, s):
        self.s = s
        self.client = boto3.client("pinpoint-sms-voice-v2", region_name=s.region)

    def send_text(self, to, text, suggestions=()):
        kwargs = {"DestinationPhoneNumber": to, "OriginationIdentity": self.s.rcs_agent,
                  "RcsMessageContent": {"Content": {"TextMessage": {"Body": text}},
                                        **({"Suggestions": [{"Reply": {"Text": t, "PostbackData": t}} for t in suggestions]}
                                           if suggestions else {})}}
        if self.s.sms_origination:
            kwargs["FallbackConfiguration"] = {"Channel": ["SMS"], "MessageBody": text[:1500],
                                               "OriginationIdentity": self.s.sms_origination}
        if self.s.sms_configuration_set:
            kwargs["ConfigurationSetName"] = self.s.sms_configuration_set
        try:
            return self.client.send_rcs_message(**kwargs)["MessageId"]
        except Exception as e:
            raise ChannelError(f"rcs: {e}") from e

    send_template = None


class Email:
    name = "email"

    def __init__(self, s):
        self.s = s
        self.client = boto3.client("sesv2", region_name=s.region)

    def send_email(self, to, subject, text, html=None, attachments=()):
        """attachments: [(filename, bytes, mime_type)]. Uses Raw content when attachments are present."""
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = self.s.email_from, to, subject
        msg["Reply-To"] = self.s.claims_inbox
        msg.set_content(text)
        if html:
            msg.add_alternative(html, subtype="html")
        for filename, data, mime in attachments:
            maintype, subtype = mime.split("/", 1)
            msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=filename)
        kwargs = {"FromEmailAddress": self.s.email_from, "Destination": {"ToAddresses": [to]},
                  "Content": {"Raw": {"Data": msg.as_bytes()}}}
        if self.s.ses_configuration_set:
            kwargs["ConfigurationSetName"] = self.s.ses_configuration_set
        try:
            return self.client.send_email(**kwargs)["MessageId"]
        except Exception as e:
            raise ChannelError(f"email: {e}") from e

    def send_text(self, to, text):
        return self.send_email(to, "Your travel claim", text)

    send_template = None


class Web:
    """Web chat: the reply is the stored message the chat page polls for, so sending is just an id."""
    name = "web"
    send_template = None

    def send_text(self, to, text):
        return f"web-{new_id()}"


def live_channels(s):
    """Only channels that are configured, so the router never tries one that cannot work."""
    channels = {"email": Email(s), "web": Web()}
    if s.sms_origination:  # no sending number: no SMS (an unscoped send is refused by IAM anyway)
        channels["sms"] = Sms(s)
    if s.wa_phone_number_id:
        channels["whatsapp"] = WhatsApp(s)
    if s.rcs_agent:
        channels["rcs"] = Rcs(s)
    if s.sim_phone:  # demo: a simulated phone takes SMS and RCS (see channels/simphone.py)
        from .simphone import SimPhone
        channels["sms"], channels["rcs"] = SimPhone("sms"), SimPhone("rcs")
    return channels


def new_id():
    return uuid.uuid4().hex
