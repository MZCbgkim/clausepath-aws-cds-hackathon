"""Submit the WhatsApp templates (English and Korean) to Meta through AWS End User Messaging Social.

  python -m tripclaim.wa_templates --waba waba-xxxxxxxx [--region us-east-1]          # submit missing ones
  python -m tripclaim.wa_templates --waba waba-xxxxxxxx --status                      # show review status

Texts come from i18n.TEMPLATES, so what Meta approves is exactly what the code renders for SMS/RCS fallbacks.
"""
import argparse
import json
import re

import boto3

from .i18n import TEMPLATE_BUTTONS, TEMPLATE_EXAMPLES, TEMPLATES, WA_LANGUAGE, wa_template_name


def meta_definition(name, locale):
    body = re.sub(r"\{(\d+)\}", r"{{\1}}", TEMPLATES[locale][name])
    components = [{"type": "BODY", "text": body, "example": {"body_text": [TEMPLATE_EXAMPLES[locale][name]]}}]
    buttons = TEMPLATE_BUTTONS.get(name, {}).get(locale)
    if buttons:
        components.append({"type": "BUTTONS", "buttons": [{"type": "QUICK_REPLY", "text": b} for b in buttons]})
    return {"name": wa_template_name(name, locale), "language": WA_LANGUAGE[locale], "category": "UTILITY",
            "components": components}


def existing(client, waba):
    out, token = [], None
    while True:
        kwargs = {"id": waba, **({"nextToken": token} if token else {})}
        page = client.list_whatsapp_message_templates(**kwargs)
        out += page.get("templates", [])
        token = page.get("nextToken")
        if not token:
            return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--waba", required=True)
    ap.add_argument("--region", default="us-east-1")
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()
    client = boto3.client("socialmessaging", region_name=args.region)
    have = {(t.get("templateName"), t.get("templateLanguage")): t for t in existing(client, args.waba)}
    if args.status:
        for (name, lang), t in sorted(have.items()):
            print(f"{name:28} {lang:6} {t.get('templateStatus')} {t.get('templateCategory', '')}")
        return
    for locale in TEMPLATES:
        for name in TEMPLATES[locale]:
            if (wa_template_name(name, locale), WA_LANGUAGE[locale]) in have:
                print(f"skip   {name} {WA_LANGUAGE[locale]} (exists)")
                continue
            res = client.create_whatsapp_message_template(
                id=args.waba, templateDefinition=json.dumps(meta_definition(name, locale), ensure_ascii=False).encode())
            print(f"submit {name} {WA_LANGUAGE[locale]} -> {res.get('templateStatus')} {res.get('metaTemplateId', '')}")


if __name__ == "__main__":
    main()
