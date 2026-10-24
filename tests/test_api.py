import json

from tripclaim.handlers import api

from .conftest import EVENT


def call(method, path, body=None):
    res = api.handler({"requestContext": {"http": {"method": method}}, "rawPath": path,
                       "body": body if isinstance(body, str) or body is None else json.dumps(body)}, None)
    ctype = res["headers"]["content-type"]
    return res["statusCode"], (json.loads(res["body"]) if ctype == "application/json" else res["body"])


def test_dashboard_events_claims_actions_and_secure_page(env):
    api.SERVICE = env["svc"]
    try:
        status, page = call("GET", "/")
        assert status == 200 and "Claims desk" in page
        assert "window.TRIPCLAIM" in call("GET", "/config.js")[1]
        for page, text in (("/about", "ClausePath"), ("/privacy", "Privacy policy"), ("/terms", "Terms of use")):
            assert text in call("GET", page)[1]

        status, out = call("POST", "/api/events", dict(EVENT))
        [claim_id] = out["alerted"]
        rows = call("GET", "/api/claims")[1]
        assert rows[0]["id"] == claim_id and rows[0]["status"] == "alerted"
        view = call("GET", f"/api/claims/{claim_id}")[1]
        assert view["messages"][0]["kind"] == "template"

        link = env["svc"].secure_link(claim_id)
        token = link.rsplit("/", 1)[1]
        assert "Secure claims page" in call("GET", f"/secure/{token}")[1]
        assert "Please check the account number" in call("POST", f"/secure/{token}", "signed_name=Jordan+Taylor&country=us&holder=J&routing=12&us_account=1")[1]
        assert "Received" in call("POST", f"/secure/{token}", "signed_name=Jordan+Taylor&country=us&holder=Jordan+Taylor&routing=021000021&us_account=123456789&acct_type=Checking")[1]
        form = env["svc"].repo.get_claim(claim_id)["claim_form"]
        assert form["account_last4"] == "6789" and "123456789" not in str(form)   # only the last 4 digits are kept
        assert env["svc"].repo.get_claim(claim_id)["secure_submitted"] is True
        assert call("GET", "/secure/bad-token")[0] == 404

        out = call("POST", f"/api/claims/{claim_id}/actions", {"action": "note", "note": "called traveler"})[1]
        assert out["status"] == "alerted" and env["svc"].repo.actions(claim_id)[0]["note"] == "called traveler"
        assert call("GET", "/nope")[0] == 404
    finally:
        api.SERVICE = None
