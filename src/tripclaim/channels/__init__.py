"""Channel clients. Every client returns a provider message id; failures raise ChannelError.

Only the SDK clients the hackathon accepts are used: socialmessaging (WhatsApp),
pinpoint-sms-voice-v2 (SMS, RCS) and sesv2 (email).
"""


import re



class ChannelError(Exception):
    pass


URL = re.compile(r"https?://\S+")
GSM7 = re.compile(r"^[A-Za-z0-9 @£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ!\"#¤%&'()*+,\-./:;<=>?¡ÄÖÑÜ§¿äöñüà^{}\\\[~\]|€]*$")


def sms_parts(text):
    """Split an SMS so no link is ever cut across parts.

    Any Korean character makes the whole SMS UCS-2 (70 chars per part), and carriers on international routes often
    deliver multipart SMS unjoined. If the text does not fit one part, send the words and each link separately:
    links are plain ASCII, so a link up to 160 chars always arrives whole."""
    limit = 160 if GSM7.match(text) else 70
    if len(text) <= limit or not URL.search(text):
        return [text]
    # Parts can arrive in any order, so each must read on its own: words without a dangling ":", and every
    # link labelled with the sender (ASCII label keeps it in the 160-char GSM-7 alphabet).
    words = URL.sub("", text).strip().rstrip(":").strip()
    label = link_label(text)
    return ([words] if words else []) + [f"{label}: {u}" for u in URL.findall(text)]


def link_label(text):
    """The link SMS is signed by the insurer too. ASCII on purpose: one Hangul character turns the SMS into UCS-2
    (70 characters per part) and the link would be cut again."""
    import os
    return os.environ.get("INSURER_SMS_TAG", "SampleIns")


from ..i18n import TEMPLATES, render_template  # noqa: E402,F401  (templates per language live in i18n)
