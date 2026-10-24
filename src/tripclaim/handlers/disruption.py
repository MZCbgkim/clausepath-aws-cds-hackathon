"""EventBridge (source tripclaim.flights, detail-type FlightDisruption) -> proactive alerts."""
from ..app import service


def handler(event, context):
    return {"alerted": service().on_disruption(dict(event["detail"]))}
