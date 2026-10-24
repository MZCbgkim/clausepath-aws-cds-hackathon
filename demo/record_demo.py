"""Record the demo video's raw takes on the deployed stack (takes A-E: alert, text and chat, documents, decision, blocked wording), with Playwright.

Each screen is its own browser context with its own video: the traveler's phone (phone simulator, 460x940 at 2x),
the admin, reviewer 1 and reviewer 2 dashboards (1440x900 at 2x). Key moments are also saved as 2x screenshots for
sharp push-in zooms, and every beat is logged with its time in each video (beats.json) for the edit.

Run at any hour up to about 21:00 Korea time: the flight is yesterday in Japan, the replacement flight about three
hours from now and the hotel checkout this morning, so the agent's answers are the same whenever it is recorded.
The documents for those times are made here (demo/make_assets.py --date --depart --checkout).
  TRIPCLAIM_ADMIN_PW=... TRIPCLAIM_R1_PW=... TRIPCLAIM_R2_PW=... AWS_PROFILE=aau .venv/bin/python demo/record_demo.py
It starts with Reset demo data and leaves the recorded claim in place (reset again before a retake).
"""
import base64
import json
import os
import sys
import time
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = os.environ.get("TRIPCLAIM_URL") or sys.exit("set TRIPCLAIM_URL to the DashboardUrl stack output")
LANG = os.environ.get("DEMO_LANG", "en")  # en: Jordan Taylor, English desk · ko: 김지수, Korean desk, Korean texts
KO = LANG == "ko"
TX = {"en": dict(tid="trv-persona-jordan", name="Jordan Taylor", holder="Jordan Taylor", locale="en-US",
                 sms_q="Do I need to keep my receipts?", chat_q="Is my hotel last night covered?",
                 docs="Documents", sheet="Amount worksheet", reason="Checked against the photos in the packet.",
                 approved="Good news, your hotel was approved."),
      "ko": dict(tid="trv-persona-plus", name="김지수", holder="김지수", locale="ko-KR",
                 sms_q="영수증 꼭 챙겨야 하나요?", chat_q="어젯밤 호텔비도 보상되나요?",
                 docs="받은 서류", sheet="산정표", reason="패킷의 사진과 대조했어요.",
                 approved="좋은 소식이에요, 호텔비가 승인됐어요.")}[LANG]
JST = timezone(timedelta(hours=9))
NOW = datetime.now(JST)
DAY = os.environ.get("FLIGHT_DATE") or (NOW - timedelta(days=1)).date().isoformat()
_dep = NOW + timedelta(hours=3, minutes=5 - NOW.minute % 5)  # the replacement flight: about three hours ahead
if _dep.date() != NOW.date():  # late evening: the last flight of the day, if it is still at least 1.5 h away
    _dep = NOW.replace(hour=23, minute=55, second=0, microsecond=0)
    if _dep - NOW < timedelta(minutes=90):
        sys.exit("too late in the day to record: the replacement flight would be tomorrow (record before 22:25 Korea time)")
DEPART = _dep.strftime("%H:%M")
CHECKOUT = min("09:12", (NOW - timedelta(hours=1)).strftime("%H:%M"))  # the hotel checkout: already past
ASSETS = Path(__file__).parent / f"assets{'_ko' if KO else ''}_{DAY}_{DEPART.replace(':', '')}"
OUT = Path(__file__).parent / "recording" / (datetime.now().strftime("%Y%m%d-%H%M") + ("-ko" if KO else ""))
USERS = {"admin": ("reviewer@clausepath.example", os.environ.get("TRIPCLAIM_ADMIN_PW")),
         "r1": ("reviewer1@clausepath.example", os.environ.get("TRIPCLAIM_R1_PW")),
         "r2": ("reviewer2@clausepath.example", os.environ.get("TRIPCLAIM_R2_PW"))}
T0 = {}
BEATS = []


def beat(screen, name, page=None, shot=True, box=None):
    """Log a beat with its time in that screen's video; keep a 2x screenshot of it. box: a locator whose rectangle
    (CSS px in the screen) is what the edit zooms into for this beat."""
    t = round(time.time() - T0[screen], 2)
    rect = None
    if box is not None:
        try:
            b = box.bounding_box(timeout=3000)
            rect = [round(b["x"]), round(b["y"]), round(b["width"]), round(b["height"])] if b else None
        except Exception:
            rect = None
    BEATS.append({"screen": screen, "beat": name, "t": t, **({"box": rect} if rect else {})})
    print(f"[{screen} {t:7.1f}s] {name}", flush=True)
    if page is not None and shot:
        page.screenshot(path=str(OUT / "shots" / f"{len(BEATS):02d}-{screen}-{name}.png"))


def login(page, who):
    user, pw = USERS[who]
    page.goto(BASE + "/")
    page.wait_for_url("**/login**", timeout=30000)
    page.locator('input[name="username"]:visible').fill(user)
    page.locator('input[name="password"]:visible').fill(pw)
    page.locator('input[name="password"]:visible').press("Enter")
    page.wait_for_url(BASE + "/**", timeout=30000)
    page.wait_for_selector("#tools", state="attached", timeout=30000)
    page.wait_for_timeout(1500)


def ui_ok(page):
    """Press OK on the dashboard's in-page dialog (uiDialog)."""
    page.wait_for_selector("#ud[open]", timeout=20000)
    page.wait_for_timeout(1200)
    page.click("#udok")
    page.wait_for_timeout(500)


def dialogs(page, screen, label, reason=None, first_wait=20000):
    """Click through the in-page dialogs one action opens (a reason to type, a confirm, a notice), saving each."""
    seen, wait = [], first_wait
    while True:
        try:
            page.wait_for_selector("#ud[open]", timeout=wait)
        except Exception:
            return seen
        page.wait_for_timeout(1200)
        msg = page.inner_text("#udm")
        seen.append(msg)
        beat(screen, f"{label} dialog {len(seen)}", page, box=page.locator("#ud"))
        if page.is_visible("#udi"):
            page.fill("#udi", reason or TX["reason"])
        page.click("#udok")
        page.wait_for_timeout(800)
        wait = 4000


def open_claim(page, name=None):
    name = name or TX["name"]
    page.click(f'.row:has-text("{name}")')
    page.wait_for_timeout(2500)


def wait_phone_outs(phone, n, timeout=150):
    t = time.time()
    while time.time() - t < timeout:
        if phone.evaluate("document.querySelectorAll('#log .b.out').length") >= n and not phone.query_selector("#log .typing"):
            return
        phone.wait_for_timeout(1000)
    raise TimeoutError(f"phone: fewer than {n} replies")


def wait_chat_reply(frame, before, timeout=180):
    t = time.time()
    while time.time() - t < timeout:
        outs = frame.evaluate("document.querySelectorAll('#log > .m.out').length")
        if outs > before and not frame.query_selector("#log > .livewrap"):
            return
        frame.wait_for_timeout(1000)
    raise TimeoutError("chat: no reply")


def send_photos(link, names):
    """Documents sent off camera before take C, through the chat's public send endpoint."""
    imgs = [{"data": base64.b64encode((ASSETS / n).read_bytes()).decode(), "mime": "image/jpeg", "filename": n} for n in names]
    req = urllib.request.Request(BASE + link + "/send", method="POST", headers={"content-type": "application/json"},
                                 data=json.dumps({"text": "", "images": imgs, "client_id": uuid.uuid4().hex}).encode())
    urllib.request.urlopen(req).read()


def main():
    missing = [k for k, (_, pw) in USERS.items() if not pw]
    if missing:
        sys.exit(f"set the passwords {missing}")
    if not ASSETS.exists():  # documents dated for this recording: yesterday's flight, today's replacement and checkout
        import subprocess
        subprocess.run([sys.executable, str(Path(__file__).parent / "make_assets.py"), "--date", DAY, "--depart", DEPART,
                        "--checkout", CHECKOUT], check=True)
    print(f"flight {DAY}, replacement flight today {DEPART}, hotel checkout {CHECKOUT} (Japan time)")
    (OUT / "shots").mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        desk = lambda: dict(viewport={"width": 1440, "height": 900}, device_scale_factor=2, locale=TX["locale"],
                            record_video_dir=str(OUT), record_video_size={"width": 1440, "height": 900})  # video is 1x; shots/ are 2x
        ctx = {k: browser.new_context(**desk()) for k in ("admin", "r1", "r2")}
        for c in ctx.values():
            c.add_init_script(f"localStorage.setItem('lang', '{LANG}')")
        pages = {k: c.new_page() for k, c in ctx.items()}
        for k in pages:
            T0[k] = time.time()
        for k in ("admin", "r1", "r2"):
            login(pages[k], k)
        admin = pages["admin"]
        token = admin.evaluate("sessionStorage.getItem('idt')")

        # setup (off camera in the edit): reset, personas on yesterday's flight
        admin.click("#tools summary")
        admin.wait_for_timeout(500)
        admin.click("#demoreset")
        ui_ok(admin)
        ui_ok(admin)  # the "removed: ..." notice
        admin.click("#tools summary")
        admin.fill('#evt [name="date"]', DAY)
        admin.evaluate(f"sessionStorage.setItem('demo_depart', '{DEPART}')")  # the personas' replacement flight
        admin.click("#personas")
        admin.wait_for_timeout(3000)
        beat("admin", "personas", admin)

        # the traveler's phone: its own context and video, signed in with the admin's token like the dashboard popup
        pctx = browser.new_context(viewport={"width": 460, "height": 940}, device_scale_factor=2, locale=TX["locale"],
                                   record_video_dir=str(OUT), record_video_size={"width": 460, "height": 940})
        pctx.add_init_script(f"sessionStorage.setItem('idt', {json.dumps(token)}); localStorage.setItem('lang', '{LANG}')")
        phone = pctx.new_page()
        T0["phone"] = time.time()
        phone.goto(BASE + f"/phone?t={TX['tid']}")
        phone.wait_for_timeout(3000)
        beat("phone", "empty phone", phone)

        # Take A: report the disruption, preview, send; the alert reaches the phone
        admin.click("#tools summary")
        admin.wait_for_timeout(600)
        beat("admin", "operations open", admin)
        admin.click('#evt button.primary')
        admin.wait_for_selector("#bulk[open]", timeout=30000)
        admin.wait_for_timeout(1500)
        beat("admin", "A preview", admin, box=admin.locator("#bulk"))
        admin.check("#bk_ok")
        admin.wait_for_timeout(800)
        admin.click("#bk_go")
        admin.wait_for_timeout(1500)
        if admin.query_selector("#ud[open]"):
            ui_ok(admin)  # "alerted n"
        wait_phone_outs(phone, 1, 60)
        phone.wait_for_timeout(1500)
        beat("phone", "A alert arrives", phone, box=phone.locator("#log .b.out").last)
        admin.reload()
        admin.wait_for_selector(".row", timeout=30000)
        open_claim(admin)
        why = admin.locator(".why:has-text('channel'), .why:has-text('채널')").first
        if why.count():
            why.scroll_into_view_if_needed()  # the message log is below the fold of the detail pane
            admin.wait_for_timeout(800)
        beat("admin", "A claim with reason line", admin, box=why)

        # Take B: text back, then the link opens the chat on the same phone
        phone.click("#t")
        phone.wait_for_timeout(600)
        beat("phone", "B typing", phone, box=phone.locator("#cmp"))
        phone.locator("#t").press_sequentially(TX["sms_q"], delay=55 if not KO else 110)  # typed on camera
        phone.wait_for_timeout(500)
        beat("phone", "B question typed", phone, box=phone.locator("#cmp"))
        phone.click("#go")
        try:
            wait_phone_outs(phone, 3, 90)  # "checking your policy", then the answer
        except TimeoutError:
            wait_phone_outs(phone, 2, 30)  # no interim message when the agent answered without the policy check
        phone.wait_for_timeout(2000)
        beat("phone", "B text answer", phone, box=phone.locator("#log .b.out").last)
        phone.click("#log a[data-href]")
        phone.wait_for_timeout(4000)
        chat = phone.frame_locator("#frame")
        frame = phone.frames[-1]
        beat("phone", "B chat opened (W3)", phone)
        before = frame.evaluate("document.querySelectorAll('#log > .m.out').length")
        chat.locator("#t").click()
        phone.wait_for_timeout(600)
        beat("phone", "B chat typing", phone)
        chat.locator("#t").press_sequentially(TX["chat_q"], delay=55 if not KO else 110)  # typed on camera
        phone.wait_for_timeout(500)
        beat("phone", "B chat question typed", phone)
        chat.locator("#send").click()
        beat("phone", "B chat question sent", phone)
        frame.wait_for_selector("#log > .livewrap", timeout=30000)
        wait_chat_reply(frame, before)
        phone.wait_for_timeout(2500)
        beat("phone", "B answer with source card", phone, box=chat.locator("#log > .m.out").last)
        ref = chat.locator("#log .ref").first
        if ref.count():
            ref.click()
            phone.wait_for_timeout(3000)
            beat("phone", "B W4 highlight", phone, box=chat.locator("#log .card:not(.fold) .crop").last)  # the highlighted clause
        # the linked-clauses view: how this clause connects to the rest of the policy
        graph = chat.locator(".card .hd .graph").last
        if graph.count():
            graph.click()
            phone.wait_for_timeout(4500)
            beat("phone", "B graph open", phone, box=chat.locator("#ev"))
            # what Jordan did not know: the hotel has a daily limit, and some costs are not paid at all
            for cid, name in (("PLAN-2", "B graph limit"), ("TD-4", "B graph exception")):
                node = chat.locator(f'#gwrap .tc[data-id$="#{cid}"], #gwrap .tc[data-id="{cid}"]').first  # ids are "policy_en#PLAN-2"
                if not node.count():
                    print("  graph: no node", cid)
                    continue
                try:
                    node.click(timeout=5000)
                    phone.wait_for_timeout(3500)
                    beat("phone", name, phone, box=chat.locator("#ev .gdet") if chat.locator("#ev .gdet").count() else chat.locator("#ev"))  # the tapped clause
                except Exception as e:
                    print("  graph:", cid, e)
            chat.locator("#evclose").click()
            phone.wait_for_timeout(1000)

        # off camera: the letter, boarding pass and dinner receipt
        link = urllib.parse.urlsplit(frame.url).path
        before = frame.evaluate("document.querySelectorAll('#log > .m.out').length")
        send_photos(link, ["airline_letter.jpg", "boarding_pass.jpg", "dinner_receipt.jpg"])
        beat("phone", "pre-step photos sent", phone)
        wait_chat_reply(frame, before)
        phone.wait_for_timeout(2000)
        beat("phone", "pre-step documents in", phone, box=chat.locator("#log > .m.out").last)  # what was read from each

        # Take C: blurry receipt -> retake -> clear one; the secure page
        before = frame.evaluate("document.querySelectorAll('#log > .m.out').length")
        chat.locator("#file").set_input_files(str(ASSETS / "hotel_minibar_receipt_blurry.jpg"))
        chat.locator("#send").click()
        beat("phone", "C blurry sent", phone)
        wait_chat_reply(frame, before)
        phone.wait_for_timeout(2500)
        beat("phone", "C needs a retake (W5)", phone, box=chat.locator("#log .sentph").last)
        before = frame.evaluate("document.querySelectorAll('#log > .m.out').length")
        chat.locator("#file").set_input_files(str(ASSETS / "hotel_minibar_receipt.jpg"))
        chat.locator("#send").click()
        wait_chat_reply(frame, before)
        phone.wait_for_timeout(2500)
        beat("phone", "C clear receipt passes", phone, box=chat.locator("#log > .m.out").last)  # what was read, minibar left out
        frame.goto(BASE + link + "/secure")
        frame.wait_for_selector("#sf", timeout=30000)
        phone.wait_for_timeout(1500)
        beat("phone", "C secure page", phone)
        frame.fill('[name="signed_name"]', TX["holder"])
        frame.set_input_files("#idf", str(ASSETS / "boarding_pass.jpg"))  # the demo does not upload ID photos
        frame.select_option("#cty", "other")  # an IBAN abroad: no real bank's name on screen
        frame.wait_for_timeout(300)
        frame.fill('[name="holder"]', TX["holder"])
        frame.fill('[name="iban"]', "XX00DEMO00000000003000")  # visibly fictional ("XX", "DEMO"): no real bank code
        frame.fill('[name="swift"]', "DEMOXXXX")
        frame.check('input[name="other_insurance"][value="no"]')
        frame.check('input[name="airline_paid"][value="no"]')
        frame.check('#sf input[type="checkbox"][required]')
        beat("phone", "C secure page filled", phone)
        frame.locator('#sf > button').click()
        frame.wait_for_selector("#ck[open]", timeout=10000)  # "Pay to ... ?" asked inside the page
        phone.wait_for_timeout(1200)
        beat("phone", "C account check", phone, box=frame.locator("#ck"))
        frame.click("#cky")
        frame.wait_for_function("!document.getElementById('sf')", timeout=30000)  # the "done" page replaces the form
        phone.wait_for_timeout(2000)
        beat("phone", "C secure page done", phone)
        frame.goto(BASE + link)
        phone.wait_for_timeout(4000)

        # Take D: submit, reviewer 1 decides a partial payment, reviewer 2 confirms
        chat.locator("#readyb").click()
        phone.wait_for_timeout(1200)
        beat("phone", "D submit sheet", phone, box=chat.locator("#askd"))
        chat.locator("#asky").click()
        phone.wait_for_timeout(8000)
        beat("phone", "D submitted", phone)
        r1 = pages["r1"]
        r1.reload()
        r1.wait_for_selector(".row", timeout=30000)
        open_claim(r1)
        beat("r1", "D claim with checks", r1)
        docs = r1.locator(".card.wide", has=r1.locator("h3", has_text=TX["docs"])).first  # each receipt: what was read, pass/fail
        if docs.count():
            docs.scroll_into_view_if_needed()
            r1.wait_for_timeout(1500)
            beat("r1", "D receipt checks", r1, box=docs)
            hotel_row = docs.locator("tr", has_text="ホテル").first  # "Item not covered: minibar … [TD-4](a)"
            if hotel_row.count():
                beat("r1", "D receipt minibar", r1, box=hotel_row)
        ses = r1.locator(".why .ses").first  # the packet went to the claims inbox through Amazon SES
        if ses.count():
            ses.scroll_into_view_if_needed()
            r1.wait_for_timeout(800)
            beat("r1", "D packet sent by SES", r1, box=r1.locator(".why:has(.ses)").first)
            r1.evaluate("window.scrollTo(0, 0); document.querySelector('#detail') && (document.querySelector('#detail').scrollTop = 0)")
        # the worksheet: the hotel bill counted without its minibar line, under [TD-4](a), before anyone decides
        sheet = r1.locator(".card.wide", has=r1.locator("h3", has_text=TX["sheet"])).first
        hotel = sheet.locator("tr", has_text="ホテル").first
        if hotel.count():
            hotel.scroll_into_view_if_needed()
            r1.wait_for_timeout(1200)
            beat("r1", "D worksheet minibar", r1, box=hotel)
        r1.wait_for_selector("#amt", state="visible", timeout=90000)  # the decision form comes once the claim view settles
        r1.locator(".decide").scroll_into_view_if_needed()
        beat("r1", "D decision set", r1, box=r1.locator(".decide"))  # the estimate as it stands: no reviewer edits
        r1.click("button[onclick=\"act('approve')\"]")
        print(dialogs(r1, "r1", "D decide"))
        r2 = pages["r2"]
        r2.reload()
        r2.wait_for_selector(".row", timeout=30000)
        open_claim(r2)
        beat("r2", "D waiting for second reviewer", r2, box=r2.locator("button[onclick=\"confirmDecision(true)\"]"))
        r2.click("button[onclick=\"confirmDecision(true)\"]")
        print(dialogs(r2, "r2", "D confirm"))
        r2.wait_for_timeout(3000)
        beat("r2", "D confirmed", r2)
        frame.wait_for_function("(document.getElementById('ready') || {}).offsetParent", timeout=120000)  # the decision card
        phone.wait_for_timeout(3000)
        # the same decision by email, sent for real through Amazon SES: the phone's Mail tab
        phone.click("#tabe")
        phone.wait_for_function("document.querySelectorAll('#mlist .mi').length >= 2", timeout=60000)  # receipt + decision
        phone.wait_for_timeout(1200)
        beat("phone", "D mail inbox", phone)
        phone.click("#mlist .mi")  # newest first: the decision
        phone.wait_for_timeout(1500)
        beat("phone", "D decision email (SES id)", phone, box=phone.locator("#mbody .proof"))
        phone.click("#mback")
        phone.click("#tabw")  # the Web tab: the chat the text's link opened is still there
        chat.locator("#ready").wait_for(state="visible", timeout=30000)
        phone.wait_for_timeout(2500)
        beat("phone", "D decision card", phone, box=chat.locator("#ready"))
        chat.locator("#readyb").click()  # Details: the decision and the lines behind it
        phone.wait_for_timeout(1500)
        beat("phone", "D decision details", phone, box=chat.locator("#cl"))
        chat.locator("#clx").click()
        phone.wait_for_timeout(800)

        # Take E: "approved" in free text is refused (also the cold open)
        r1.reload()
        r1.wait_for_selector(".row", timeout=30000)
        open_claim(r1)
        r1.locator("#smsg").scroll_into_view_if_needed()
        r1.locator("#smsg").click()
        beat("r1", "E typing", r1, box=r1.locator("#smsg"))
        r1.locator("#smsg").press_sequentially(TX["approved"], delay=45 if not KO else 90)  # typed on camera (the cold open)
        r1.wait_for_timeout(500)
        beat("r1", "E typed approved", r1, box=r1.locator("#smsg"))
        r1.click("button[onclick=\"act('message')\"]")
        print(dialogs(r1, "r1", "E send"))  # the confirm, then the refusal (W6)
        r1.wait_for_timeout(1000)
        canned = r1.locator("p.canned:has(button[onclick^=\"canned(\"])").first  # the reply templates (other .canned are in closed dialogs)
        canned.scroll_into_view_if_needed()
        r1.wait_for_timeout(600)
        beat("r1", "E template buttons", r1, box=canned)

        videos = {k: pg.video.path() for k, pg in [*pages.items(), ("phone", phone)]}
        for c in [*ctx.values(), pctx]:
            c.close()
        browser.close()
    for k, v in videos.items():  # name the videos after their screens
        Path(v).rename(OUT / f"{k}.webm")
    (OUT / "beats.json").write_text(json.dumps({"flight_date": DAY, "beats": BEATS, "videos": [f"{k}.webm" for k in videos]}, indent=1))
    print("done:", OUT)


if __name__ == "__main__":
    main()
