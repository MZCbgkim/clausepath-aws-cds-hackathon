"""Channel preference optimisation (design §7).

Picks a channel per message from consent, the traveler's preference, the reply context, WhatsApp's 24-hour
session window, recent delivery failures and what the message carries; falls back down the list when a send
fails, and records why each channel was chosen so the dashboard can show it.
"""
from datetime import datetime, timedelta, timezone

from .channels import ChannelError, render_template
from .store import now_iso

WA_WINDOW = timedelta(hours=24)
UNREACHABLE_FOR = timedelta(hours=6)
DEFAULT_ORDER = ["whatsapp", "rcs", "sms", "email"]


def _parse(ts):
    return datetime.fromisoformat(ts) if ts else None


def address_for(traveler, channel):
    if channel == "web":
        return "web"
    return traveler.get("email") if channel == "email" else traveler.get("phone")


class Router:
    def __init__(self, repo, channels, clock=None):
        self.repo = repo
        self.channels = channels
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def candidates(self, traveler, purpose, reply_on=None, exclude=()):
        """Ordered [(channel, reason)]."""
        consent = traveler.get("consent", {})
        unreachable = traveler.get("unreachable", {})
        now = self.clock()
        order = []
        if purpose in ("packet", "receipt", "decision"):  # decision: the decision notice and the answer to an objection
            order.append(("email", "documents and formal notices go by email"))
        if reply_on:
            order.append((reply_on, f"replying on the channel the traveler used ({reply_on})"))
        pref = traveler.get("preferred_channel")
        if pref:
            order.append((pref, f"traveler's preferred channel ({pref})"))
        if purpose == "photo_request":
            order += [("whatsapp", "photo request: WhatsApp carries images"), ("rcs", "photo request: RCS carries images")]
        order += [(c, "fallback order") for c in DEFAULT_ORDER]

        out, seen = [], set(exclude)
        if reply_on == "web" and "web" in self.channels and "web" not in seen:
            out.append(("web", "replying in the web chat the traveler opened from our link"))
        seen.add("web")  # the web chat only answers; it never starts a conversation
        for channel, reason in order:
            if channel in seen or channel not in self.channels:
                continue
            seen.add(channel)
            if not (consent.get(channel) or (channel == "rcs" and consent.get("sms"))):
                continue
            if not address_for(traveler, channel):
                continue
            down = _parse(unreachable.get(channel))
            if down and now - down < UNREACHABLE_FOR:
                reason += f"; skipped earlier: undelivered at {unreachable[channel]}"
                continue
            out.append((channel, reason))
        return out

    def in_wa_window(self, traveler):
        last = _parse((traveler.get("last_inbound") or {}).get("whatsapp"))
        return bool(last) and self.clock() - last < WA_WINDOW

    def send(self, traveler, claim_id, purpose, text, template=None, reply_on=None, exclude=(),
             email_subject=None, email_html=None, attachments=(), note=None, extra=None):
        """template: (name, params) used where only templates are allowed (WhatsApp outside 24h).
        note: why this send happens at all (e.g. re-send after an undelivered message), kept in the reason."""
        tried = [note] if note else []
        for channel, reason in self.candidates(traveler, purpose, reply_on, exclude):
            client = self.channels[channel]
            to = address_for(traveler, channel)
            try:
                if channel == "email":
                    pid = client.send_email(to, email_subject or "Your travel claim", text, html=email_html,
                                            attachments=attachments)
                    kind, body = "email", text
                elif channel == "whatsapp":
                    if self.in_wa_window(traveler):
                        pid, kind, body = client.send_text(to, text), "text", text
                        reason += "; inside 24h session window"
                    elif template:
                        locale = traveler.get("locale", "en")
                        pid = client.send_template(to, *template, locale=locale)
                        kind, body = "template", render_template(*template, locale=locale)
                        reason += f"; outside 24h window, template {template[0]}"
                    else:
                        tried.append(f"whatsapp: outside 24h window and no template for '{purpose}'")
                        continue
                else:
                    pid, kind, body = client.send_text(to, text), "text", text
            except ChannelError as e:
                tried.append(f"{channel}: {e}")
                self.mark_unreachable(traveler, channel)
                continue
            why = (" | ".join(tried) + " -> " if tried else "") + reason
            return self.repo.add_message(claim_id, {
                "direction": "out", "channel": channel, "kind": kind, "text": body, "provider_id": pid,
                "purpose": purpose, "chosen_reason": why, "subject": email_subject if channel == "email" else None,
                **(extra or {})})
        self.repo.add_message(claim_id, {"direction": "out", "channel": None, "kind": "undelivered", "text": text,
                                         "purpose": purpose, "status": "failed", "chosen_reason": " | ".join(tried)})
        return None

    def mark_unreachable(self, traveler, channel):
        unreachable = dict(traveler.get("unreachable") or {})
        unreachable[channel] = now_iso()
        traveler["unreachable"] = unreachable
        self.repo.update_traveler(traveler["id"], unreachable=unreachable)
