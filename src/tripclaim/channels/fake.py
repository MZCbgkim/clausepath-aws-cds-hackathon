"""In-process channels for tests and the simulator. Records every send in an outbox."""
import uuid

from . import ChannelError, render_template, sms_parts


class FakeChannel:
    def __init__(self, name, outbox, fail=None):
        self.name = name
        self.outbox = outbox
        self.fail = fail or set()   # destinations that should fail (simulates "no data / undelivered")
        self.media = {}             # media_id -> bytes

    def _record(self, to, kind, text, extra=None):
        if to in self.fail:
            raise ChannelError(f"{self.name}: delivery to {to} failed")
        pid = f"{self.name}-{uuid.uuid4().hex[:10]}"
        self.outbox.append({"channel": self.name, "to": to, "kind": kind, "text": text, "provider_id": pid, **(extra or {})})
        return pid

    def send_text(self, to, text, suggestions=()):
        if self.name == "sms":
            ids = [self._record(to, "text", part) for part in sms_parts(text)]
            return ids[0]
        return self._record(to, "text", text)

    def send_template(self, to, name, params, locale="en"):
        return self._record(to, "template", render_template(name, params, locale), {"template": name, "locale": locale})

    def send_email(self, to, subject, text, html=None, attachments=()):
        return self._record(to, "email", text, {"subject": subject, "html": html,
                                                 "attachments": [a[0] for a in attachments]})

    def fetch_media(self, media_id, bucket, key):
        return self.media[media_id]


def fake_channels(outbox):
    chans = {n: FakeChannel(n, outbox) for n in ("whatsapp", "sms", "rcs", "email", "web")}
    chans["web"].send_template = None
    chans["sms"].send_template = None
    chans["rcs"].send_template = None
    chans["email"].send_template = None
    return chans
