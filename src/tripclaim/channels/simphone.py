"""A simulated phone for demos (SIM_PHONE=on): SMS and RCS sends are accepted without a carrier.

The router, the texts and the reasons are the real ones; only delivery is simulated. The demo's phone page
(/phone) shows what was sent to a traveler's number and posts their replies back as inbound SMS/RCS, through the
same inbound path a carrier event takes. Provider ids start with "sim-" so a simulated send is never mistaken for a
delivered one.
"""
import uuid


class SimPhone:
    send_template = None

    def __init__(self, name):
        self.name = name

    def send_text(self, to, text, suggestions=()):
        return f"sim-{self.name}-{uuid.uuid4().hex[:10]}"
