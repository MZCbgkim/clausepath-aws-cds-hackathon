"""Put the demo in the state the screens show: 김지수 part-paid ($100) with an open objection, a call-back and a team hand-off;
박서연 waiting for a second review of a denial; Jordan asked for a call back; one blocked staff message."""
import json, os, urllib.request
B = "http://localhost:8099"
HERE = os.path.dirname(os.path.abspath(__file__))
def call(m, p, body=None, user="admin@demo"):
    r = urllib.request.Request(B + p, method=m, data=json.dumps(body).encode() if body is not None else None, headers={"Content-Type": "application/json", "x-demo-user": user})
    try: return json.loads(urllib.request.urlopen(r).read())
    except urllib.error.HTTPError as e: return {"_err": e.code, **json.loads(e.read() or b"{}")}
links = json.load(open(os.path.join(HERE, "links.json")))
rows = call("GET", "/api/claims")
kim = next(r for r in rows if r["traveler"] == "김지수")["id"]; park = next(r for r in rows if r["traveler"] == "박서연")["id"]
v = call("GET", f"/api/claims/{kim}")
hotel = next(r["doc"] for r in v["estimate"]["rows"] if r.get("counted_usd", 0) >= 100)
A = f"/api/claims/{kim}/actions"
r = call("POST", A, {"action": "approve", "adjust": {hotel: {"usd": 87.47, "reason": "excluded_item"}}, "clauses": ["TD-4"],
                     "note": "호텔 영수증 중 미니바 이용료 $22.53은 약관상 보상하는 비용이 아니에요."}, "reviewer-1@demo")
r = call("POST", A, {"action": "confirm", "pid": r["pending_decision"]["pid"]}, "reviewer-2@demo"); print("decided", r.get("decision", {}).get("amount_usd"))
req = urllib.request.Request(B + links["done"].split(B)[1] + "/appeal", method="POST", headers={"Content-Type": "application/json"},
                             data=json.dumps({"text": "미니바로 빠진 $22.53은 룸서비스 저녁 식사였어요. 영수증 품목을 다시 봐 주세요."}).encode())
print("appeal", urllib.request.urlopen(req).status)
print("blocked", call("POST", A, {"action": "message", "note": "금요일쯤 들어와요."}, "agent@demo").get("_err"))
print("callback", call("POST", A, {"action": "call_log", "to": "objection", "due": "call_back", "note": "오후 3시 이후 통화 원함",
                                   "callback_at": "2026-10-12T07:00:00Z", "callback_pref": "pm"}, "agent@demo").get("_err"))
print("handoff", call("POST", A, {"action": "handoff", "to": "team:reviewers", "note": "이의 신청: 룸서비스 영수증 품목 확인 부탁", "due": "2026-10-12T09:00:00Z"}, "agent@demo").get("_err"))
r = call("POST", f"/api/claims/{park}/actions", {"action": "deny", "clauses": ["EX-2"], "note": "보내 주신 서류가 다른 분 이름이라 이 청구에 쓸 수 없어요.",
                                                 "reason": "서류 명의 불일치, 본인 서류 미제출"}, "reviewer-1@demo"); print("park", (r.get("pending_decision") or {}).get("why"))
req = urllib.request.Request(B + links["en"].split(B)[1] + "/callback", method="POST", data=json.dumps({"pref": "pm", "offset_min": -420}).encode(), headers={"Content-Type": "application/json"})
print("jordan callback", urllib.request.urlopen(req).status)
print(kim, park)
