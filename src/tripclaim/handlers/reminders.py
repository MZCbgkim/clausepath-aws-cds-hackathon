"""EventBridge Scheduler -> document reminders on the traveler's best channel."""
from ..app import service


def handler(event, context):
    return {"reminded": service().run_reminders(force=bool((event or {}).get("force")))}
