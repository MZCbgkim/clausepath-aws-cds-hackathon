"""SNS -> Lambda: WhatsApp and SMS/RCS inbound messages and delivery-status events."""
import logging

from ..app import service
from ..inbound import DeliveryStatus, parse_sns_message

log = logging.getLogger()
log.setLevel(logging.INFO)


def handler(event, context):
    svc = service()
    if "web_inbound" in event:  # async turn handed over by the web chat API
        from .api import handle_web_inbound
        result = handle_web_inbound(svc, event["web_inbound"])
        return {"ok": True, "channel": result and result["channel"]}
    errors = []
    for record in event.get("Records", []):
        try:
            for item in parse_sns_message(record["Sns"]["Message"], rcs_agent=svc.s.rcs_agent):
                if isinstance(item, DeliveryStatus):
                    svc.handle_status(item)
                else:
                    result = svc.handle_inbound(item)
                    log.info("inbound %s from %s -> %s", item.channel, item.sender[-4:], result and result["channel"])
        except Exception as e:  # keep other records; re-raise so SNS retries (inbound is deduplicated)
            log.exception("record failed")
            errors.append(e)
    if errors:
        raise errors[0]
    return {"ok": True}
