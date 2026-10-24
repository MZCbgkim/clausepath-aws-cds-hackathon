"""Fill the local server with four travelers' conversations (real agent calls). Writes links.json next to this file."""
import base64, json, os, time, urllib.request
from urllib.parse import urlencode
B = "http://localhost:8099"
HERE = os.path.dirname(os.path.abspath(__file__))
def call(m, p, body=None):
    r = urllib.request.Request(B + p, method=m, data=json.dumps(body).encode() if body is not None else None, headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(r, timeout=180).read())
ps = {p["name"]: p for p in call("POST", "/api/demo/personas", {})["personas"]}
def wait(link, n):
    for _ in range(150):
        time.sleep(3); m = call("GET", link + "/messages")
        if sum(1 for x in m["messages"] if x["direction"] == "out") >= n and not m.get("activity"): return m
def flow(name, photos, finish, q=None, assets="assets_ko"):
    link = ps[name]["chat"].split(B)[1]; k = 0
    if q: call("POST", link + "/send", {"text": q, "client_id": "q"}); k += 1; wait(link, k)
    if photos:
        imgs = [{"data": base64.b64encode(open(f"demo/{assets}/{f}", "rb").read() + (name.encode() if assets == "assets" else b"")).decode(),
                 "mime": "image/jpeg", "filename": f} for f in photos]
        call("POST", link + "/send", {"text": "", "images": imgs, "client_id": "p"}); k += 1; wait(link, k)
    if finish:
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k): return None
        try: urllib.request.build_opener(NoRedirect).open(B + link + "/secure")
        except urllib.error.HTTPError as e: loc = e.headers["location"]
        urllib.request.urlopen(urllib.request.Request(loc, method="POST", data=urlencode({"signed_name": name, "holder": name, "country": "kr",
            "bank_name": "KB국민은행", "account": "123-456-7890", "other_insurance": "no", "airline_paid": "no"}).encode()))
        call("POST", link + "/submit")
    m = call("GET", link + "/messages"); print(name, B + link, m["done"], "/", m["total"], m["status"], flush=True)
    return B + link
links = {"done": flow("김지수", ("airline_letter.jpg", "hotel_receipt.jpg", "dinner_receipt.jpg", "boarding_pass.jpg"), True, "오늘 밤 호텔비도 보상되나요? 한도는 얼마예요?")}
links["partial"] = flow("박서연", ("airline_letter.jpg", "hotel_receipt.jpg", "hotel_receipt_blurry.jpg", "boarding_pass.jpg"), False, "오늘 밤 호텔비도 보상되나요? 한도는 얼마예요?")
links["asked"] = flow("강민준", None, False, "보험 연장해야 하나요?")
links["en"] = flow("Jordan Taylor", ("airline_letter.jpg", "hotel_receipt.jpg", "dinner_receipt.jpg"), False, "Is my hotel tonight covered? How much can I get?", assets="assets")
json.dump(links, open(os.path.join(HERE, "links.json"), "w")); print(links)
