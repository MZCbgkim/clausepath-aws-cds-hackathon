"""Register the RCS agent for testing and add a test device (AWS End User Messaging SMS/RCS).

  python -m tripclaim.rcs_setup register --agent rcs-xxx --site https://<api>/      # brand assets + test registration
  python -m tripclaim.rcs_setup status   --agent rcs-xxx
  python -m tripclaim.rcs_setup device   --agent rcs-xxx --phone +8210xxxxxxxx      # tester invite arrives in Google Messages

Test registrations usually complete within minutes. The tester accepts "Make me a tester" from RBM Tester Management.
"""
import argparse
import io
import json
from pathlib import Path

import boto3

BRAND = {
    "agentDetails.brandName": "ClausePath",
    "agentDetails.serviceName": "ClausePath travel claims",
    "agentDetails.senderDisplayName": "ClausePath",
    "agentDetails.agentDescription": "Help with travel-delay insurance claims: what to keep and what is covered.",
    "agentDetails.accentColor": "#1F4FD8",
    "agentDetails.contactWebsiteLabel": "About ClausePath",
    "agentDetails.privacyPolicyLabel": "Privacy policy",
    "agentDetails.termsAndConditionsLabel": "Terms of use",
    "agentDetails.monthlyRcsVolume": "100",
}
CHOICES = {"agentDetails.useCase": ["TRANSACTIONAL"], "agentDetails.averageMonthlyRcsFrequency": ["10"],
           "agentDetails.billingCategory": ["CONVERSATIONAL"]}


def images():
    """Logo 224x224 (<50KB) and banner 1440x448 (<200KB), drawn locally."""
    from PIL import Image, ImageDraw, ImageFont
    bold = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
    regular = "/System/Library/Fonts/Supplemental/Arial.ttf"
    blue, white = (31, 79, 216), (255, 255, 255)

    logo = Image.new("RGB", (224, 224), blue)
    d = ImageDraw.Draw(logo)
    d.text((112, 104), "TC", font=ImageFont.truetype(bold, 92), fill=white, anchor="mm")
    d.text((112, 170), "ClausePath", font=ImageFont.truetype(bold, 24), fill=white, anchor="mm")

    banner = Image.new("RGB", (1440, 448), blue)
    d = ImageDraw.Draw(banner)
    for i in range(448):  # soft vertical gradient
        d.line([(0, i), (1440, i)], fill=(31, 79 - i // 16, 216 - i // 8))
    d.text((120, 170), "ClausePath", font=ImageFont.truetype(bold, 96), fill=white)
    d.text((124, 290), "Flight cancelled? Keep the right receipts, claim without the back-and-forth.",
           font=ImageFont.truetype(regular, 34), fill=(225, 233, 255))

    out = []
    for img, limit in ((logo, 50_000), (banner, 200_000)):
        buf = io.BytesIO()
        img.save(buf, "PNG", optimize=True)
        if buf.tell() > limit:
            buf = io.BytesIO()
            img.save(buf, "JPEG", quality=85)
        assert buf.tell() <= limit, buf.tell()
        out.append(buf.getvalue())
    return out


def register(c, agent, site):
    site = site.rstrip("/")
    logo, banner = images()
    Path("demo/out").mkdir(parents=True, exist_ok=True)
    Path("demo/out/rcs_logo.png").write_bytes(logo)
    Path("demo/out/rcs_banner.png").write_bytes(banner)
    reg = c.create_registration(RegistrationType="TEST_RCS_LAUNCH_REGISTRATION",
                                Tags=[{"Key": "project", "Value": "tripclaim"}])["RegistrationId"]
    # URLs first: their labels are only accepted once the URL field is set.
    fields = {"agentDetails.contactWebsite": f"{site}/about", "agentDetails.privacyPolicyUrl": f"{site}/privacy",
              "agentDetails.termsAndConditionsUrl": f"{site}/terms", **BRAND}
    for path, value in fields.items():
        c.put_registration_field_value(RegistrationId=reg, FieldPath=path, TextValue=value)
    for path, choices in CHOICES.items():
        c.put_registration_field_value(RegistrationId=reg, FieldPath=path, SelectChoices=choices)
    for path, body in (("agentDetails.logoImage", logo), ("agentDetails.bannerImage", banner)):
        att = c.create_registration_attachment(AttachmentBody=body)["RegistrationAttachmentId"]
        c.put_registration_field_value(RegistrationId=reg, FieldPath=path, RegistrationAttachmentId=att)
    c.create_registration_association(RegistrationId=reg, ResourceId=agent)
    res = c.submit_registration_version(RegistrationId=reg)
    print("registration", reg, "->", res.get("RegistrationVersionStatus"))


def status(c, agent):
    a = c.describe_rcs_agents(RcsAgentIds=[agent])["RcsAgents"][0]
    print("agent:", a.get("Status"), json.dumps(a.get("TestingAgent") or {}, default=str))
    for r in c.describe_registrations(Filters=[{"Name": "registration-type", "Values": ["TEST_RCS_LAUNCH_REGISTRATION"]}])["Registrations"]:
        print("registration:", r["RegistrationId"], r.get("RegistrationStatus"))
    for v in c.describe_verified_destination_numbers().get("VerifiedDestinationNumbers", []):
        print("device:", v["DestinationPhoneNumber"], v.get("Status"))


def device(c, agent, phone):
    v = c.create_verified_destination_number(DestinationPhoneNumber=phone, RcsAgentId=agent,
                                             Tags=[{"Key": "project", "Value": "tripclaim"}])
    print("test device", v["VerifiedDestinationNumberId"], v.get("Status"), "- accept the tester invite in Google Messages")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["register", "status", "device"])
    ap.add_argument("--agent", required=True)
    ap.add_argument("--site")
    ap.add_argument("--phone")
    ap.add_argument("--region", default="us-east-1")
    a = ap.parse_args()
    c = boto3.client("pinpoint-sms-voice-v2", region_name=a.region)
    {"register": lambda: register(c, a.agent, a.site), "status": lambda: status(c, a.agent),
     "device": lambda: device(c, a.agent, a.phone)}[a.action]()


if __name__ == "__main__":
    main()
