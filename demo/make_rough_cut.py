"""Assemble a rough cut of the 3-minute demo (narration: docs/narration-en.md, docs/narration-ko.md) from a recording made by record_demo.py.

1920x1080, 30 fps, one screen at a time: the phone large on the left with callouts on the right (a crop of the
2x screenshot, linked to the element on the phone), the desk full-frame, and camera push-ins on the 2x screenshots
at the boxes record_demo.py logged per beat. The TTS narration (Amazon Polly neural) is one clip per sentence,
placed on the shot it talks about. No music: that is the editor's pass.

Run: AWS_PROFILE=aau .venv/bin/python demo/make_rough_cut.py demo/recording/<run> [--voice Matthew]
Out: demo/edit/rough_cut.mp4 (+ segments/, tts/, overlays/, timeline.json)
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
LANG = os.environ.get("DEMO_LANG", "en")  # ko: the Korean cut (Korean desk and traveler, Korean narration and captions)
KO = LANG == "ko"
OUT = ROOT / "demo" / ("edit_ko" if KO else "edit")
MUSIC = ROOT / "demo" / "edit" / "music"  # one music bed and set of effects for both cuts
SCRIPT = ROOT / "docs" / ("narration-ko.md" if KO else "narration-en.md")
W, H, FPS = 1920, 1080, 30
FONT = "/System/Library/Fonts/Supplemental/Arial.ttf"
BOLD = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
if KO:  # Hangul: Nanum Gothic (SIL OFL, downloaded by policy/make_pdfs.py)
    FONT, BOLD = (str(ROOT / "data" / "fonts" / n) for n in ("NanumGothic-Regular.ttf", "NanumGothic-Bold.ttf"))

# The Korean cut's on-screen words: every caption the edit draws goes through T()
KO_TEXT = {
    "Traveler's phone": "여행자 휴대폰", "Typhoon. Flight cancelled.": "태풍. 항공편 결항.",
    "Answered in under a minute": "1분 안에 받은 답", "The policy behind the answer": "답의 근거가 된 약관",
    "For travel insurers · the insurer's desk": "여행자보험사용 · 보험사 데스크",
    "One report → every traveler with delay cover": "한 번 등록 → 지연 담보가 있는 여행자 모두에게",
    "The alert arrives": "결항 안내 도착", "Why this channel, logged": "이 채널을 고른 이유, 기록됨",
    "Ask by text": "문자로 질문", "Text link (RCS) → Web tab · same conversation": "문자 링크 → 웹 탭 · 같은 대화",
    "Text link (SMS) → Web tab · same conversation": "문자 링크 → 웹 탭 · 같은 대화",
    "Same chat, no app": "같은 대화, 앱 설치 없이", "Claude on Amazon Bedrock · bedrock-runtime Converse": "Amazon Bedrock의 Claude · bedrock-runtime Converse",
    "Jordan asks": "지수 씨의 질문", "Every coverage answer cites the policy": "보장 답변에는 항상 약관 근거",
    "Tap: the exact clause": "번호를 누르면: 해당 조항", "The clause, highlighted": "조항 하이라이트",
    "Open the clause map": "조항 지도 열기", "The policy as a graph": "약관을 그래프로",
    "Follow the links: the limit": "연결을 따라가면: 한도", "Plan Plus: USD 150 a day": "Plus 플랜: 하루 150달러",
    "…and what is never paid": "…그리고 보상하지 않는 것", "Not paid: alcohol, minibar…": "보상 안 함: 주류, 미니바…",
    "Photos read as they arrive": "사진은 도착하는 대로 판독", "Read: letter XX123 · boarding pass · ramen ¥1,880": "판독: XX123 확인서 · 탑승권 · 라멘 ¥1,880",
    "Retake asked on the spot": "그 자리에서 재촬영 요청", "A blurry photo": "흐린 사진", "The retake, read": "다시 찍은 영수증 판독",
    "Read: ¥18,300 · minibar left out [TD-4]": "판독: ¥18,300 · 미니바 제외 [TD-4]", "The photo Jordan sent": "지수 씨가 보낸 사진",
    "Never in the chat · shown only as •••3000": "채팅에는 남지 않음 · •••3000으로만 표시", "Bank details: secure page": "계좌 정보: 보안 페이지",
    "Sent by Amazon SES · live": "Amazon SES로 실제 발송", "Each receipt: what was read, pass or fail": "영수증마다: 판독 내용과 통과·실패",
    "Insurer's desk · reviewer 1": "보험사 데스크 · 심사자 1", "Minibar ¥1,800 left out · [TD-4](a)": "미니바 ¥1,800 제외 · [TD-4](a)",
    "Payouts over USD 100: a second reviewer confirms": "100달러 넘는 지급: 두 번째 심사자 확인",
    "Email first · Amazon SES": "이메일 먼저 · Amazon SES", "Then the chat": "이어서 채팅", "The decision reaches Jordan": "지수 씨에게 결정 도착",
    "A reviewer types “approved”": "심사자가 “승인” 입력", "Blocked before it was sent": "보내기 전에 차단",
    "Rules + Bedrock Guardrails (ApplyGuardrail) check every free-text message": "규칙 + Bedrock Guardrails(ApplyGuardrail)가 모든 자유 입력 메시지 검사",
    "Decisions: fixed templates only": "결정은 정해진 템플릿으로만", "Simulated carrier · real router and agent": "통신사 발송은 시뮬레이션 · 라우터와 에이전트는 실제",
    "real Japanese receipt photos: total read correctly": "실제 일본 영수증 사진: 합계 정확히 판독",
    "public test set (JaWildText) · a person still decides every payout": "공개 데이터셋(JaWildText) · 지급 결정은 언제나 사람이",
    "Answers with proof. Decisions by people.": "근거가 있는 답. 결정은 사람이.",
    "sesv2 · SendEmail  — live": "sesv2 · SendEmail  — 실제 발송",
}


def T(text):
    """The caption in the cut's language (the English text is the key)."""
    if not KO or not isinstance(text, str):
        return text
    if text in KO_TEXT:
        return KO_TEXT[text]
    m = re.fullmatch(r"Answered (\d+) s later", text)
    if m:
        return f"{m.group(1)}초 만에 답변"
    m = re.fullmatch(r"(\d+) s later", text)
    if m:
        return f"{m.group(1)}초 뒤"
    m = re.fullmatch(r"([\d,]+) of ([\d,]+) (.*)", text)
    if m:
        return f"{m.group(2)}장 중 {m.group(1)}장 — {T(m.group(3))}"
    m = re.fullmatch(r"([\d.]+)× · real (\d+) s", text)
    if m:
        return f"{m.group(1)}배속 · 실제 {m.group(2)}초"
    return text
BLUE, RED, INK, GOLD = (31, 79, 216), (221, 52, 76), (14, 18, 32), (255, 214, 102)
ACC = (92, 140, 255)                          # rings, links and callout captions: the one blue, lighter on dark
PCROP = (34, 57, 392, 833)                    # the phone's frame inside the 460x940 phone page (CSS px)
PS = 1.2                                      # phone scale on screen
PX, PY, PW, PH = 200, 40, round(392 * PS), round(833 * PS)
RX, RW = 800, 1040                            # the panel right of the phone: headline, callouts
DS = 1552 / 1440                              # desk scale on screen: a rounded card with a shadow, a margin around it
DX, DY, DW, DH = 184, 28, 1552, 970
RAD = 18
XF = 0.4                                      # crossfade between shots: each is rendered XF longer, the next starts on its tail
GAP = 0.35                                    # pause between narration sentences


def f(size, bold=False):
    return ImageFont.truetype(BOLD if bold else FONT, size)


def ov(name):
    return OUT / "overlays" / f"{name}.png"


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(r.stderr[-2000:])


def duration(path):
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                                capture_output=True, text=True).stdout)


# ---------- overlays (PIL; this ffmpeg has no drawtext) ----------

def pill(name, text, fg=(255, 255, 255), bg=BLUE, size=26, pad=(18, 10), radius=22):
    text = T(text)
    font = f(size, True)
    w = int(font.getlength(text)) + 2 * pad[0]
    img = Image.new("RGBA", (w, size + 2 * pad[1] + 4), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, img.width - 1, img.height - 1], radius, fill=bg + (235,))
    d.text((pad[0], pad[1]), text, font=font, fill=fg)
    img.save(ov(name))
    return ov(name)


def centered(png, y):
    """(png, x, y) for a pill centred horizontally."""
    return png, (W - Image.open(png).width) // 2, y


def card(name, lines, bg=INK, blur_from=None):
    """A full-frame card: [(text, size, bold, colour)], centred."""
    if blur_from:
        img = Image.open(blur_from).convert("RGB").resize((W, H)).filter(ImageFilter.GaussianBlur(18))
        img = Image.blend(img, Image.new("RGB", (W, H), bg), 0.72)
    else:
        img = Image.new("RGB", (W, H), bg)
    d = ImageDraw.Draw(img)
    total = sum(s + 26 for _, s, _, _ in lines)
    y = (H - total) // 2
    for text, size, bold, col in ((T(a), b, c, d) for a, b, c, d in lines):
        font = f(size, bold)
        d.text(((W - font.getlength(text)) / 2, y), text, font=font, fill=col)
        y += size + 26
    img.save(ov(name))
    return ov(name)


def headline(name, kicker, text):
    """The phone shot's title in the right panel."""
    img = Image.new("RGBA", (RW, 150), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.text((0, 0), T(kicker).upper(), font=f(24, True), fill=(130, 160, 255))
    d.text((0, 40), T(text), font=f(52, True), fill=(255, 255, 255))
    img.save(ov(name))
    return ov(name)


def stamp(name, text="BLOCKED"):
    font = f(96, True)
    w = int(font.getlength(text)) + 80
    img = Image.new("RGBA", (w, 170), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([4, 4, w - 5, 165], 22, outline=RED + (255,), width=10, fill=(255, 255, 255, 215))
    d.text((40, 30), text, font=font, fill=RED)
    img.rotate(8, expand=True, resample=Image.BICUBIC).save(ov(name))
    return ov(name)


def phone_xy(x, y):
    """A point of the phone page (CSS px) on screen."""
    return PX + (x - PCROP[0]) * PS, PY + (y - PCROP[1]) * PS


def callout(name, shot_png, box, caption=None, maxh=600, maxs=1.25):
    """A full-frame overlay: the element ringed on the phone, and a crop of the 2x screenshot as a card on the right."""
    x, y, w, h = box
    pad = 8
    x0, y0 = max(x - pad, PCROP[0]), max(y - pad, PCROP[1])
    x1, y1 = min(x + w + pad, PCROP[0] + PCROP[2]), min(y + h + pad, PCROP[1] + PCROP[3])
    crop = Image.open(shot_png).convert("RGB").crop((int(x0 * 2), int(y0 * 2), int(x1 * 2), int(y1 * 2)))
    s = min((RW - 40) / crop.width, maxh / crop.height, maxs)
    crop = crop.resize((int(crop.width * s), int(crop.height * s)), Image.LANCZOS)
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    (ax, ay), (bx, by) = phone_xy(x0, y0), phone_xy(x1, y1)
    d.rounded_rectangle([ax - 4, ay - 4, bx + 4, by + 4], 14, outline=ACC + (255,), width=6)
    cap_h = 56 if caption else 0
    top = min(max((ay + by) / 2 - (crop.height + cap_h) / 2, 250), 1050 - crop.height - cap_h)
    cx = RX + 20
    if caption:
        d.text((cx, top), T(caption), font=f(34, True), fill=ACC)
    cy = top + cap_h
    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle([cx - 6, cy + 10, cx + crop.width + 18, cy + crop.height + 24], 22, fill=(0, 0, 0, 150))
    img = Image.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(14)), img)
    d = ImageDraw.Draw(img)
    d.line([(bx + 6, (ay + by) / 2), (cx - 14, cy + crop.height / 2)], fill=ACC + (210,), width=4)
    d.rounded_rectangle([cx - 12, cy - 12, cx + crop.width + 12, cy + crop.height + 12], 20, fill=(255, 255, 255, 255))
    mask = Image.new("L", crop.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, crop.width - 1, crop.height - 1], 12, fill=255)
    img.paste(crop, (int(cx), int(cy)), mask)
    img.save(ov(name))
    return ov(name)


def gradient(w, h):
    g = Image.new("RGB", (2, 2))
    g.putdata([(14, 18, 32), (20, 26, 48), (20, 26, 48), (27, 35, 64)])
    return g.resize((w, h), Image.BILINEAR)


def desk_mask(scale=1):
    """The desk card's rounded corners (L mask) at `scale`."""
    m = Image.new("L", (DW * scale, DH * scale), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, DW * scale - 1, DH * scale - 1], RAD * scale, fill=255)
    return m


def desk_shadow(scale=1):
    """A soft shadow under the desk card, full-frame RGBA at `scale`."""
    sh = Image.new("RGBA", (W * scale, H * scale), (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle([(DX - 4) * scale, (DY + 10) * scale, (DX + DW + 4) * scale, (DY + DH + 18) * scale],
                                         RAD * scale, fill=(0, 0, 0, 160))
    return sh.filter(ImageFilter.GaussianBlur(16 * scale))


def canvas2x(name, shot_png, screen):
    """The screen laid out as in its video shot, at 2x (3840x2160), for a camera push-in."""
    c = gradient(2 * W, 2 * H)
    im = Image.open(shot_png).convert("RGB")
    if screen == "phone":
        im = im.crop(tuple(int(v * 2) for v in (PCROP[0], PCROP[1], PCROP[0] + PCROP[2], PCROP[1] + PCROP[3])))
        c.paste(im.resize((2 * PW, 2 * PH), Image.LANCZOS), (2 * PX, 2 * PY))
    else:
        c = Image.alpha_composite(c.convert("RGBA"), desk_shadow(2)).convert("RGB")
        c.paste(im.resize((2 * DW, 2 * DH), Image.LANCZOS), (2 * DX, 2 * DY), desk_mask(2))
    c.save(ov(name))
    return ov(name)


def to_canvas(box, screen):
    x, y, w, h = box
    if screen == "phone":
        return 2 * (PX + (x - PCROP[0]) * PS), 2 * (PY + (y - PCROP[1]) * PS), 2 * w * PS, 2 * h * PS
    return 2 * (DX + x * DS), 2 * (DY + y * DS), 2 * w * DS, 2 * h * DS


def receipt_wall(n, length, images, big, line1, line2, fill=2.6, hold=1.2):
    """Every receipt of the test set fills the frame in order ("tick tick tick"), then the wall blurs and darkens and
    the number comes up. images: the photos (each shown as a small tile); big / line1 / line2: the result card."""
    import math
    import tempfile
    k = len(images)
    cols = max(1, round(math.sqrt(k * W / H)))
    rows = math.ceil(k / cols)
    tw, th = W / cols, H / rows
    tiles = []
    for p in images:
        im = Image.open(p).convert("RGB")
        im.thumbnail((int(tw * 2), int(th * 2)))
        c = Image.new("RGB", (int(math.ceil(tw)), int(math.ceil(th))), (24, 28, 44))
        im = im.resize((min(c.width - 2, int(im.width * min((c.width - 2) / im.width, (c.height - 2) / im.height))),
                        min(c.height - 2, int(im.height * min((c.width - 2) / im.width, (c.height - 2) / im.height)))))
        c.paste(im, ((c.width - im.width) // 2, (c.height - im.height) // 2))
        tiles.append(c)
    full = gradient(W, H)
    order = list(range(k))
    tmp = Path(tempfile.mkdtemp(prefix="wall"))
    frames = int((length + XF) * FPS)
    wall = gradient(W, H)
    shown = 0
    blurred = None
    for fi in range(frames):
        t = fi / FPS
        want = min(k, int(k * min(1, (t / fill) ** 0.8))) if t < fill else k
        while shown < want:  # tiles land in reading order
            i = order[shown]
            wall.paste(tiles[i], (int((i % cols) * tw), int((i // cols) * th)))
            shown += 1
        img = wall.copy()
        if t > fill + hold:  # the whole wall, bright, for `hold` s first
            a = min(1, (t - fill - hold) / 0.6)
            if blurred is None:
                blurred = Image.blend(wall.filter(ImageFilter.GaussianBlur(10)), Image.new("RGB", (W, H), INK), 0.55)
            img = Image.blend(wall, blurred, a)
            if t > fill + hold + 0.3:
                card_ = Image.new("RGBA", (W, H), (0, 0, 0, 0))
                d = ImageDraw.Draw(card_)
                alpha = int(255 * min(1, (t - fill - hold - 0.3) / 0.4))
                y = H // 2 - 130
                for text, size, bold, col in ((big, 130, True, (255, 255, 255)), (T(line1), 44, False, (255, 255, 255)),
                                              (T(line2), 30, False, (190, 200, 225))):
                    fnt = f(size, bold)
                    d.text(((W - fnt.getlength(text)) / 2, y), text, font=fnt, fill=col + (alpha,))
                    y += size + 28
                img = Image.alpha_composite(img.convert("RGBA"), card_).convert("RGB")
        img.save(tmp / f"{fi:05d}.png")
    out = OUT / "segments" / f"{n:02d}.mp4"
    run(["ffmpeg", "-y", "-v", "error", "-framerate", str(FPS), "-i", str(tmp / "%05d.png"), "-c:v", "libx264", "-crf", "19",
         "-preset", "medium", "-pix_fmt", "yuv420p", str(out)])
    return out


def receipt_card(name, src, mark):
    """A full-frame overlay: the photo Jordan sent, as a card on the right, with one line marked (mark: x0, y0, x1, y1 as
    fractions of the photo)."""
    im = Image.open(src).convert("RGB")
    im = im.crop((0, 0, im.width, int(im.height * 0.66)))  # the bill down to its total
    s_ = min(560 / im.width, 640 / im.height)
    im = im.resize((int(im.width * s_), int(im.height * s_)), Image.LANCZOS)
    d = ImageDraw.Draw(im)
    x0, y0, x1, y1 = (int(v) for v in (mark[0] * im.width, mark[1] / 0.66 * im.height, mark[2] * im.width, mark[3] / 0.66 * im.height))
    d.rounded_rectangle([x0, y0, x1, y1], 8, outline=RED, width=5)
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    cx, cy = RX + 20, 250
    sh = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle([cx - 6, cy + 12, cx + im.width + 18, cy + im.height + 26], 22, fill=(0, 0, 0, 150))
    img = Image.alpha_composite(sh.filter(ImageFilter.GaussianBlur(14)), img)
    d = ImageDraw.Draw(img)
    d.text((cx, cy - 56), T("The photo Jordan sent"), font=f(34, True), fill=ACC)
    mask = Image.new("L", im.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, im.width - 1, im.height - 1], 14, fill=255)
    img.paste(im, (cx, cy), mask)
    img.save(ov(name))
    return ov(name)


def phone_card(name, src):
    """A phone screenshot (its frame only) centred on the dark background (ending montage)."""
    bg = gradient(W, H)
    im = Image.open(src).convert("RGB").crop(tuple(int(v * 2) for v in (PCROP[0], PCROP[1], PCROP[0] + PCROP[2], PCROP[1] + PCROP[3])))
    s = (H - 80) / im.height
    im = im.resize((int(im.width * s), int(im.height * s)), Image.LANCZOS)
    bg.paste(im, ((W - im.width) // 2, 40))
    bg.save(ov(name))
    return ov(name)


def arch_box(src, box):
    """A box of the diagram image (its pixels) on screen, as framed() lays it out."""
    im = Image.open(src)
    s = min((W - 160) / im.width, (H - 120) / im.height)
    x, y = (W - int(im.width * s)) // 2, (H - int(im.height * s)) // 2
    return [x + box[0] * s, y + box[1] * s, x + box[2] * s, y + box[3] * s]


def ring(name, box, pad=12):
    """A full-frame overlay: a blue ring around `box` (x0, y0, x1, y1)."""
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(img).rounded_rectangle([box[0] - pad, box[1] - pad, box[2] + pad, box[3] + pad], 22, outline=BLUE + (255,), width=7)
    img.save(ov(name))
    return ov(name)


def framed(name, src):
    """A diagram image on a rounded white card over the dark background."""
    bg = Image.new("RGB", (W, H), INK)
    im = Image.open(src).convert("RGB")
    s = min((W - 160) / im.width, (H - 120) / im.height)
    im = im.resize((int(im.width * s), int(im.height * s)), Image.LANCZOS)
    x, y = (W - im.width) // 2, (H - im.height) // 2
    mask = Image.new("L", im.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, im.width - 1, im.height - 1], 24, fill=255)
    bg.paste(im, (x, y), mask)
    bg.save(ov(name))
    return ov(name)


# ---------- TTS: one clip per sentence ----------

def tts(voice):
    """{"4.3": mp3}: scene 4's third sentence. Sentences are the script's SSML split at its breaks."""
    blk = SCRIPT.read_text()
    blk = blk[blk.index("```xml") + 6: blk.index("```", blk.index("```xml") + 6)]
    clips = {}
    for line in blk.strip().splitlines():
        m = re.match(r'<!-- (\w+) --><speak><prosody rate="(\d+%)">(.*)</prosody></speak>', line.strip())
        if not m:
            continue
        scene, rate, body = m.groups()
        for i, part in enumerate((p.strip() for p in re.split(r'<break time="\d+ms"/>', body)), 1):
            ssml = f'<speak><prosody rate="{rate}">{part}</prosody></speak>'
            h = hashlib.md5(f"{voice}{ssml}".encode()).hexdigest()[:8]
            out = OUT / "tts" / f"{scene}.{i}_{h}.mp3"
            if not out.exists():
                xml = out.with_suffix(".xml")
                xml.write_text(ssml)
                subprocess.run(["aws", "polly", "synthesize-speech", "--region", "us-east-1", "--engine", "neural", "--voice-id", voice,
                                "--text-type", "ssml", "--text", f"file://{xml}", "--output-format", "mp3", str(out)],
                               check=True, capture_output=True)
            clips[f"{scene}.{i}"] = (out, duration(out))
    return clips


# ---------- shots ----------

def fade_in(i, s, e, fade):
    if not fade:
        return f"[{i}:v]format=rgba[o{i}]"
    return f"[{i}:v]format=rgba,fade=t=in:st={max(s, 0):.2f}:d=0.3:alpha=1,fade=t=out:st={max(e - 0.3, 0):.2f}:d=0.3:alpha=1[o{i}]"


def encode(n, inputs, fc, last, length):
    fc.append(f"[{last}]trim=duration={length:.3f},setpts=PTS-STARTPTS,format=yuv420p[v]")
    out = OUT / "segments" / f"{n:02d}.mp4"
    run(["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", ";".join(fc), "-map", "[v]", "-r", str(FPS),
         "-c:v", "libx264", "-crf", "19", "-preset", "medium", str(out)])
    return out


def plen(p):
    return (p[2] or (p[1] - p[0])) + (p[3] if len(p) > 3 else 0)


def video_shot(n, length, rec, src, pieces, over=(), region=None, badge=True):
    """The screen `src` plays `pieces` [(t0, t1, out[, hold])] back to back: out=None at 1x, else squeezed into `out` s
    (a speed badge shows), each piece's last frame held `hold` s, the last frame of all held to `length`.
    region: (x, y, w, h) of the desk recording (CSS px, 16:9) shown full-frame. over: [(png, start, end, x, y, fade)]."""
    over, t = list(over), 0.0
    phone = src == "phone"
    pieces = [tuple(p) + (0,) * (4 - len(p)) for p in pieces]
    for t0, t1, out, hold in pieces:
        d = (out or (t1 - t0)) + hold
        if badge and out and (t1 - t0) / out > 1.3:
            b = pill(f"speed{n}_{t0:.0f}", f"{(t1 - t0) / out:.1f}× · real {t1 - t0:.0f} s", bg=(40, 48, 72), size=24)
            over.append((b, t, t + d, RX, 218, True) if phone else (b, t, t + d, DX + DW - Image.open(b).width, DY + DH + 14, True))
        t += d
    L = length + XF
    inputs = ["-f", "lavfi", "-i", f"gradients=s={W}x{H}:c0=0x0E1220:c1=0x1B2340:x0=0:y0=0:x1={W}:y1={H}:d={L}:r={FPS}"]
    for t0, t1, *_ in pieces:
        inputs += ["-ss", f"{t0:.2f}", "-to", f"{t1:.2f}", "-i", str(rec / f"{src}.webm")]
    base = 1 + len(pieces)
    for png, *_ in over:
        inputs += ["-loop", "1", "-t", f"{L}", "-i", str(png)]
    crop, (ax, ay, aw, ah) = ((f"crop={PCROP[2]}:{PCROP[3]}:{PCROP[0]}:{PCROP[1]}", (PX, PY, PW, PH)) if phone
                              else ("crop=1440:900:0:0", (DX, DY, DW, DH)))
    card = not phone and not region
    if region:
        rx, ry, rw, rh = (int(v) for v in region)
        crop, (ax, ay, aw, ah) = f"crop={rw}:{rh}:{rx}:{ry}", (0, 0, W, H)
    if card:  # the desk card: shadow under it, rounded corners
        desk_shadow().save(ov("deskshadow"))
        desk_mask().save(ov("deskmask"))
        si = 1 + len(pieces) + len(over)  # after the overlays
        inputs += ["-loop", "1", "-t", f"{L}", "-i", str(ov("deskshadow")), "-loop", "1", "-t", f"{L}", "-i", str(ov("deskmask"))]
    fc = [f"[{1 + i}:v]{crop},setpts=(PTS-STARTPTS)/{(t1 - t0) / out if out else 1},fps={FPS},scale={aw}:{ah}:flags=lanczos,setsar=1"
          f"{f',tpad=stop_mode=clone:stop_duration={hold}' if hold else ''}[p{i}]"
          for i, (t0, t1, out, hold) in enumerate(pieces)]
    fc.append("".join(f"[p{i}]" for i in range(len(pieces))) + f"concat=n={len(pieces)}:v=1:a=0,tpad=stop_mode=clone:stop_duration={L}[act]")
    if card:
        fc += [f"[{si + 1}:v]format=gray[dm]", f"[act]format=rgba[ac]", f"[ac][dm]alphamerge[act2]",
               f"[0:v][{si}:v]overlay=0:0[bg]", f"[bg][act2]overlay={ax}:{ay}[b]"]
    else:
        fc.append(f"[0:v][act]overlay={ax}:{ay}[b]")
    last = "b"
    for i, (_, s, e, x, y, fade) in enumerate(over):
        fc.append(fade_in(base + i, s, e, fade))
        fc.append(f"[{last}][o{base + i}]overlay={int(x)}:{int(y)}[c{i}]")
        last = f"c{i}"
    return encode(n, inputs, fc, last, L)


def zoom_shot(n, length, shot_png, screen, box, over=(), push=1.0, zmax=2.8):
    """A camera push-in on the 2x screenshot of `screen`, laid out as in its video shot, onto `box` (CSS px)."""
    L = length + XF
    cv = canvas2x(f"cv{n}", shot_png, screen)
    bx, by, bw, bh = to_canvas(box, screen)
    z = max(min(2 * W / (bw * 1.3), 2 * H / (bh * 1.45), zmax), 1.12)
    cx, cy = bx + bw / 2, by + bh / 2
    N = int(push * FPS)
    p = f"min(on/{N},1)"
    e = f"({p}*{p}*(3-2*{p}))"
    x0, x1, y0, y1 = ((0, 2 * W, 0, 2 * H) if screen == "phone"
                      else (2 * DX, 2 * (DX + DW), 2 * DY, 2 * (DY + DH)))  # the desk card's edges, once zoomed in
    lo_x, lo_y = f"({x0}*{e})", f"({y0}*{e})"
    hi_x, hi_y = f"({2 * W}-({2 * W}-{x1})*{e})", f"({2 * H}-({2 * H}-{y1})*{e})"
    zp = (f"zoompan=z='1+{z - 1:.4f}*{e}':x='max({lo_x},min({hi_x}-iw/zoom,{W}+({cx:.1f}-{W})*{e}-iw/zoom/2))'"
          f":y='max({lo_y},min({hi_y}-ih/zoom,{H}+({cy:.1f}-{H})*{e}-ih/zoom/2))':d={int(L * FPS) + 2}:s={W}x{H}:fps={FPS}")
    inputs = ["-i", str(cv)]
    over = list(over)
    for png, *_ in over:
        inputs += ["-loop", "1", "-t", f"{L}", "-i", str(png)]
    fc, last = [f"[0:v]{zp}[b]"], "b"
    for i, (_, s, e_, x, y, fade) in enumerate(over):
        fc.append(fade_in(1 + i, s, e_, fade))
        fc.append(f"[{last}][o{1 + i}]overlay={int(x)}:{int(y)}[c{i}]")
        last = f"c{i}"
    return encode(n, inputs, fc, last, L)


def still_shot(n, length, png, over=(), zoom=False):
    L = length + XF
    inputs = ["-loop", "1", "-t", f"{L}", "-i", str(png)]
    for p, *_ in over:
        inputs += ["-loop", "1", "-t", f"{L}", "-i", str(p)]
    fc = [f"[0:v]scale={W * 2}:{H * 2},zoompan=z='min(1+0.0006*on,1.08)':d={int(L * FPS)}:s={W}x{H}:fps={FPS}[b]" if zoom
          else f"[0:v]scale={W}:{H},fps={FPS}[b]"]
    last = "b"
    for i, (_, s, e, x, y, fade) in enumerate(over):
        fc.append(fade_in(1 + i, s, e, fade))
        fc.append(f"[{last}][o{1 + i}]overlay={int(x)}:{int(y)}[c{i}]")
        last = f"c{i}"
    return encode(n, inputs, fc, last, L)


def lag(rec, beats, shots, screen):
    """{beat: how much earlier than its logged time it shows in the screen's video}: the recording starts after the log's
    clock, and drifts while the page is busy. Each beat's screenshot is matched against the video frames before it;
    a beat without a clear match gets the screen's median."""
    from PIL import ImageChops, ImageStat
    w, h = (460, 940) if screen == "phone" else (1440, 900)
    fh, found = int(96 * h / w), []
    mine = [b for b in beats if b["screen"] == screen and b["beat"] in shots]
    per = {}
    for b in mine:
        t0 = max(b["t"] - 3.0, 0)
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t0}", "-to", f"{b['t'] + 1.0}", "-i", str(rec / f"{screen}.webm"),
                              "-vf", f"fps=20,crop={w}:{h}:0:0,scale=96:{fh},format=gray", "-f", "rawvideo", "-"], capture_output=True).stdout
        n = len(raw) // (96 * fh)
        if not n:
            continue
        ref = Image.open(shots[b["beat"]]).convert("L").crop((0, 0, 2 * w, 2 * h)).resize((96, fh))
        d = [ImageStat.Stat(ImageChops.difference(Image.frombytes("L", (96, fh), raw[i * 96 * fh:(i + 1) * 96 * fh]), ref)).rms[0]
             for i in range(n)]
        best = min(range(n), key=d.__getitem__)
        if d[best] < 0.7 * sorted(d)[n // 2]:  # a distinct match only
            # the last frame as close as the best: the state holds until the next click
            close = [i for i in range(n) if d[i] <= d[best] * 1.05 + 0.5]
            per[b["beat"]] = b["t"] - (t0 + max(i for i in close if i >= best and all(j in close for j in range(best, i + 1))) / 20)
            found.append(per[b["beat"]])
    found.sort()
    med = found[len(found) // 2] if found else 0.0
    return {b["beat"]: per.get(b["beat"], med) for b in beats if b["screen"] == screen}


def appeared(rec, t_beat, box, look=20.0):
    """The video time (phone screen) at which `box` last changed a lot before `t_beat`: when a bubble landed."""
    from PIL import ImageChops, ImageStat
    x, y, w, h = (int(v) for v in box)
    t0 = max(t_beat - look, 0)
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t0}", "-to", f"{t_beat}", "-i", str(rec / "phone.webm"),
                          "-vf", f"fps=20,crop={w}:{h}:{x}:{y},scale=64:{max(8, int(64 * h / w))},format=gray", "-f", "rawvideo", "-"],
                         capture_output=True).stdout
    fh = max(8, int(64 * h / w))
    n = len(raw) // (64 * fh)
    frames = [Image.frombytes("L", (64, fh), raw[i * 64 * fh:(i + 1) * 64 * fh]) for i in range(n)]
    diffs = [ImageStat.Stat(ImageChops.difference(frames[i], frames[i - 1])).mean[0] for i in range(1, n)]
    big = [i for i, d in enumerate(diffs, 1) if d > 6]
    return t0 + big[-1] / 20 if big else t_beat - 1.5


def receipt_test():
    """The receipt run shown in the video: (image paths, (totals right, totals scored)). RECEIPT_RUN / RECEIPT_DATA pick
    the run (default: the full JaWildText run if it is there, else the 120-receipt sample)."""
    import os
    import re as _re
    for run_id, data in ((os.environ.get("RECEIPT_RUN"), os.environ.get("RECEIPT_DATA")),
                         ("real-full-opus55-medium", "data/jawildtext_full"), ("real-opus55-medium-v1", "data/jawildtext")):
        if not run_id:
            continue
        rep = ROOT / "eval" / "results" / run_id / "report.md"
        imgs = ROOT / data / "images"
        if rep.exists() and imgs.exists():
            dedup = rep.parent / "total_unique.json"  # the full set has duplicate rows: scored once per photo
            if dedup.exists():
                d = json.loads(dedup.read_text())
                return sorted(imgs.glob("*.jpg")), (d["correct"], d["scored"])
            m = _re.search(r"\| total \| (\d+)/(\d+)", rep.read_text())
            if m:
                return sorted(imgs.glob("*.jpg")), (int(m.group(1)), int(m.group(2)))
    return [], None


# ---------- the edit ----------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("recording")
    ap.add_argument("--voice", default="Seoyeon" if KO else "Matthew")
    args = ap.parse_args()
    rec = Path(args.recording).resolve()
    beats = json.loads((rec / "beats.json").read_text())["beats"]
    shots = {p.stem.split("-", 2)[2]: p for p in (rec / "shots").glob("*.png")}
    lags = {}
    for sc in {b["screen"] for b in beats}:
        lags.update(lag(rec, beats, shots, sc))
    print("  video lag per beat:", {k: round(v, 2) for k, v in lags.items()})
    B = {b["beat"]: b["t"] - lags[b["beat"]] for b in beats}  # beat times on each screen's video clock
    BOX = {b["beat"]: b["box"] for b in beats if b.get("box")}
    for d in ("overlays", "segments", "tts"):
        (OUT / d).mkdir(parents=True, exist_ok=True)
    clips = tts(args.voice)
    VID = ROOT / "docs" / "video"
    sim = pill("simchip", "Simulated carrier · real router and agent", fg=(225, 230, 245), bg=(40, 48, 72), size=22)

    segs, durs, audio, xfs = [], [], [], []   # audio: (key, start); xfs: the transition into each shot
    sfx = []                                    # (effect, start): demo/edit/music/<effect>.wav
    t = 0.0

    audio_end = [0.0]

    def say(lines, base=0.0):
        """[(key, at)] with at=None: right after the previous sentence. Returns the end of the last one."""
        end = base
        for key, at in lines:
            start = end + GAP if at is None else at
            if audio and t + start < audio_end[0] + GAP:  # never on top of the sentence before (this shot's or the last one's)
                start = audio_end[0] + GAP - t
            audio.append((key, t + start))
            end = start + clips[key][1]
            audio_end[0] = t + end
        return end

    kinds = []

    def add(make, length, lines=(), tail=0.8, fit=True, cut=False, kind="card"):
        """Render the shot once its length is known: at least `length`, and (fit) long enough for the sentences it
        carries; fit=False lets the last sentence run on into the next shot. cut: a hard cut into this shot, as is
        any change of screen (phone, desk, card): a dissolve between two layouts reads as a double exposure."""
        nonlocal t
        cut = cut or (kinds and kinds[-1] != kind)
        kinds.append(kind)
        end = say(lines)
        length = max(length, end + tail if lines and fit else 0)
        segs.append(make(len(segs) + 1, length))
        durs.append(length)
        xfs.append(0.04 if cut else XF)
        t += length
        return length

    def phone(pieces, head, lines=(), calls=(), length=0.0, extra=(), carrier=False, **kw):
        """calls: [(beat, start, end, caption)]: the beat's element as a callout. carrier: the shot is the text thread
        (the simulated-carrier chip shows only there: web chat and email use no carrier)."""
        over = [(headline(f"h{len(segs) + 1}", "Traveler's phone", head), 0, 99, RX, 90, False)]
        if carrier:
            over.append((sim, 0, 99, RX, 1020, False))
        for i, (bt, s, e, cap, *opt) in enumerate(calls):
            kw2 = dict(opt[0]) if opt else {}
            box = kw2.pop("box", None) or BOX[bt]
            over.append((callout(f"co{len(segs) + 1}_{i}", shots[bt], box, cap, **kw2), s, e, 0, 0, True))
        over += list(extra)
        return add(lambda n, L: video_shot(n, L, rec, "phone", pieces, over), max(length, sum(plen(p) for p in pieces)), lines, kind=f"phone:{head}", **kw)

    def desk(src, pieces, label, lines=(), length=0.0, region=None, extra=(), **kw):
        over = list(extra)
        if not region:
            over.append((pill(f"lab{len(segs) + 1}", label, size=26), 0, 99, DX, DY + DH + 14, False))
        return add(lambda n, L: video_shot(n, L, rec, src, pieces, over, region, badge=not region), max(length, sum(plen(p) for p in pieces)), lines, kind="desk", **kw)

    def zoom(bt, screen, length, lines=(), caption=None, cap_at=0.8, extra=(), zmax=2.8, box=None, cap_above=False, cap_bottom=False, cap_dy=0, push=1.0, **kw):
        b = box or BOX.get(bt)
        if not b or b[1] + b[3] > (940 if screen == "phone" else 900):  # not on screen in that shot
            print(f"  skip zoom {bt}: box {b}")
            return 0
        over = list(extra)
        bw_, bh_ = to_canvas(b, screen)[2:]
        zz = max(min(2 * W / (bw_ * 1.3), 2 * H / (bh_ * 1.45), zmax), 1.12)
        cap_y = cap_dy + int(H - 76 if cap_bottom else max(H / 2 - bh_ * zz / 4 - 82, 30) if cap_above else min(H / 2 + bh_ * zz / 4 + 22, H - 70))
        for i, (c, at) in enumerate([(caption, cap_at)] if isinstance(caption, str) else (caption or [])):
            png, x, y = centered(pill(f"zc{len(segs) + 1}_{i}", c, size=34, bg=BLUE), cap_y)
            nxt = (caption[i + 1][1] if not isinstance(caption, str) and i + 1 < len(caption) else 99)
            over.append((png, at, nxt, x, y, True))
        return add(lambda n, L: zoom_shot(n, L, shots[bt], screen, b, over, push=push, zmax=zmax), length, lines,
                   kind="phone" if screen == "phone" else "desk", **kw)

    def still(png, length, lines=(), zoom_=False, extra=(), tail=0.8, **kw):
        return add(lambda n, L: still_shot(n, L, png, extra, zoom_), length, lines, tail, **kw)

    def region_of(box, minw=760):
        """A 16:9 part of the 1440x900 desk around `box`, for a full-frame crop of the video."""
        x, y, w, h = box
        rw = min(max(w * 1.5, minw, h * 1.6 * 16 / 9), 1440)
        rh = rw * 9 / 16
        rx = min(max(x + w / 2 - rw / 2, 0), 1440 - rw)
        ry = min(max(y + h / 2 - rh / 2, 0), 900 - rh)
        return rx, ry, rw // 2 * 2, rh // 2 * 2

    R = None
    white, grey = (255, 255, 255), (170, 180, 205)

    # 1 · hook: the typhoon, the alert, the question; then a flash-forward of the answer's evidence (scene 4 shows it all)
    a = B["A alert arrives"]
    land = appeared(rec, a, BOX["A alert arrives"])  # the bubble lands on screen here (video time)
    sfx += [("storm", t), ("chime", t + 1.0)]  # the typhoon under the first seconds; the alert lands (a cabin chime) as it fades
    phone([(land - 1.0, land + 2.4, R)], "Typhoon. Flight cancelled.", [("1.1", 2.0)],
          calls=[("A alert arrives", 1.15, 99, None)], carrier=True, fit=False, length=4.6)
    zoom("A alert arrives", "phone", 3.0, [("1.2", 0.1), ("1.3", None)], zmax=1.6, tail=0.3)  # a slow push-in while the question lands
    if "B graph open" in BOX:
        g0, gb = B["B graph open"], BOX["B graph open"]
        phone([(g0 - 0.6, g0 - 0.1, R, 1.0)], "Answered in under a minute", [("1.4", 0.2)],
              calls=[("B graph open", 0.7, 99, "The policy behind the answer",
                      {"box": [gb[0], gb[1] + 58, gb[2], int(gb[3] * 0.40)], "maxh": 700, "maxs": 1.5})], tail=0.5)

    # 2 · the desk reports once; every covered traveler is messaged, each channel logged
    desk("admin", [(B["operations open"] - 0.3, B["A preview"] - 0.1, R)], "For travel insurers · the insurer's desk", [("2.1", 0.3), ("2.2", None)])
    ap = B["A preview"]
    one = pill("onerep", "One report → every traveler with delay cover", size=34, bg=BLUE)
    desk("admin", [(ap - 0.2, ap - 0.1, R, 2.1)], "", [("2.3", 0.2)], region=region_of(BOX["A preview"], 900),
         extra=[(one, 0.5, 99, (W - Image.open(one).width) // 2, H - 104, True)], fit=False)
    a = B["A alert arrives"]
    # the hook heard its chime already
    phone([(land - 0.2, land + 2.5, R)], "The alert arrives", calls=[("A alert arrives", 0.8, 99, None)], length=3.0, carrier=True)
    zoom("A claim with reason line", "desk", 3.0, [("2.4", 0.2)], caption="Why this channel, logged", cap_bottom=True)  # the empty margin under the message

    # 3 · two-way text; the agent's wait is cut
    q, ans = B["B question typed"], B["B text answer"]
    qt = B.get("B typing", q - 0.1)  # typed on camera, then sent
    p1 = (q + 1.2) - (qt - 0.3)
    jump = pill("jump", f"{(ans - 1.5) - (q + 1.2):.0f} s later", bg=(40, 48, 72), size=24)
    phone([(qt - 0.3, q + 1.2, R), (ans - 1.5, ans - 0.1, R, 2.5)], "Ask by text", [("3.1", 0.2), ("3.2", p1 + 0.5)],
          calls=[("B text answer", p1 + 1.2, 99, None)], extra=[(jump, p1, p1 + 1.4, RX, 218, True)], length=6.5, carrier=True)

    # 4 · the same conversation on the web: Jordan asks, the answer cites the policy, the clause, the clause map, and
    # what Jordan did not know (the daily limit, the minibar)
    o = B["B chat opened (W3)"]
    ct, cs, c, hl0 = B["B chat typing"], B["B chat question sent"], B["B answer with source card"], B["B W4 highlight"]
    sw = pill("smsweb", "Text link (RCS) → Web tab · same conversation", bg=BLUE, size=26)
    sfx += [("click", t + 0.6), ("whoosh", t + 0.7)]  # the link in the text, the Web tab opening
    phone([(o - 4.6, min(o + 1.0, B["B chat typing"] - 0.4), R, 0.3)], "Same chat, no app", [("4.1", 0.3), ("4.2", None), ("4.3", None)],
          extra=[(sw, 2.4, 99, RX, 218, True)], fit=False)
    p_ask = (cs + 1.6) - (ct - 0.2)
    bedrock = pill("bedrock", "Claude on Amazon Bedrock · bedrock-runtime Converse", bg=BLUE, size=26)
    took = pill("took4", f"Answered {c - cs:.0f} s later", bg=(40, 48, 72), size=24)
    phone([(ct - 0.2, cs + 1.6, R), (c - 1.0, c - 0.1, R, 1.2)], "Jordan asks",
          [("4.4", 0.3), ("4.5", None), ("4.6", p_ask + 0.3)],
          calls=[("B answer with source card", p_ask + 0.5, 99, "Every coverage answer cites the policy")],
          extra=[(bedrock, 2.6, p_ask + 0.3, RX, 218, True), (took, p_ask + 0.3, 99, RX, 218, True)])
    sfx.append(("click", t + 0.3))  # the citation number tapped (3 s before the beat)
    phone([(hl0 - 3.3, hl0 - 0.1, R, 1.0)], "Tap: the exact clause", [("4.7", 0.3)],
          calls=[("B W4 highlight", 1.4, 99, "The clause, highlighted")])
    if "B graph open" in BOX:
        g0, gb = B["B graph open"], BOX["B graph open"]
        sfx.append(("click", t + 0.2))  # the clause map button (4.5 s before the beat)
        phone([(g0 - 4.7, g0 - 1.8, R, 1.0)], "Open the clause map", [("4.8", 0.2)],
              calls=[("B graph open", 1.8, 99, "The policy as a graph",
                      {"box": [gb[0], gb[1] + 58, gb[2], int(gb[3] * 0.40)], "maxh": 700, "maxs": 1.5})])
    prev = B.get("B graph open")
    for bt, head, lines, cap in (("B graph limit", "Follow the links: the limit", [("4.9", 0.3)], "Plan Plus: USD 150 a day"),
                                 ("B graph exception", "…and what is never paid", [("4.10", 0.3), ("4.11", None)], "Not paid: alcohol, minibar…")):
        if bt in BOX:
            g = B[bt]
            t0 = max(g - 3.8, (prev or g - 3.8) - 0.3)  # from the screen before the tap: the tap itself shows
            sfx.append(("click", t + max(g - 3.55 - t0, 0.1)))  # the tap: about 3.5 s before the beat's screenshot
            phone([(t0, g - 0.1, R, 2.0)], head, lines, calls=[(bt, max(g - 3.5 - t0, 0.2) + 1.5, 99, cap, {"box": [BOX[bt][0], BOX[bt][1] + 26, BOX[bt][2], BOX[bt][3] - 26], "maxh": 640, "maxs": 1.4})])
            prev = g

    # 5 · each photo read; a blurry one gets a retake on the spot; bank details on a secure page
    # the letter, the boarding pass and a receipt land in the chat; what was read from each, after the checks
    ps = B["pre-step documents in"]
    pss = B.get("pre-step photos sent", ps - 10)
    p_in = 2.4
    later_ps = pill("later_ps", f"{(ps - 2.4) - (pss + p_in - 0.6):.0f} s later", bg=(40, 48, 72), size=24)
    phone([(pss + 0.4, pss + p_in + 0.4, R), (ps - 2.4, ps - 0.1, R, 1.6)], "Photos read as they arrive",
          [("5.1", 0.3), ("5.2", None)],
          calls=[("pre-step documents in", p_in + 0.6, 99, "Read: letter XX123 · boarding pass · ramen ¥1,880", {"maxh": 600, "maxs": 1.3})],
          extra=[(later_ps, p_in, p_in + 1.3, RX, 218, True)])
    bl, rt = B["C blurry sent"], B["C needs a retake (W5)"]
    w5 = pill("w5", "Retake asked on the spot", bg=BLUE, size=28)
    l1 = 1.6  # the blurry photo going out, then a jump cut: the retake request lands with 5.3
    rb = BOX["C needs a retake (W5)"]
    later5 = pill("later5", f"{(rt - 1.5) - (bl - 0.4 + l1):.0f} s later", bg=(40, 48, 72), size=24)
    phone([(bl - 0.4, bl - 0.4 + l1, R), (rt - 1.5, rt - 0.1, R, 2.0)], "A blurry photo",
          [("5.3", l1 + 0.3)],
          calls=[("C needs a retake (W5)", l1 + 1.3, 99, None, {"box": [rb[0], rb[1], min(rb[2], int(rb[3] * 1.15)), rb[3]], "maxs": 2.4})],
          extra=[(later5, l1, l1 + 1.25, RX, 218, True), (w5, l1 + 1.3, 99, RX, 218, True)])
    cr = B["C clear receipt passes"]  # the retake goes out (the photo itself, on the right), then what was read from it
    sent = sorted((ROOT / "demo").glob("assets_20*/hotel_minibar_receipt.jpg"), key=lambda p: p.stat().st_mtime)
    p_send = 2.6
    over5 = [(receipt_card("rcpt", sent[-1], (0.12, 0.345, 0.9, 0.385)), 0.2, p_send + 0.2, 0, 0, True)] if sent else []
    phone([(rt + 0.1, rt + 0.1 + p_send, R), (cr - 2.2, cr - 0.1, R, 1.5)], "The retake, read", [("5.4", 0.3)],
          calls=[("C clear receipt passes", p_send + 0.7, 99, "Read: ¥18,300 · minibar left out [TD-4]", {"maxh": 560, "maxs": 1.3})],
          extra=over5)
    sp, ck = B["C secure page"], B["C account check"]
    masked = pill("masked", "Never in the chat · shown only as •••3000", bg=BLUE, size=26)
    phone([(sp - 0.3, sp + 1.2, R), (ck - 0.6, ck - 0.1, R, 1.5)], "Bank details: secure page", [("5.5", 0.3)],
          calls=[("C account check", 1.6, 99, None, {"box": [BOX["C account check"][0], BOX["C account check"][1] + 26,
                                                     BOX["C account check"][2], BOX["C account check"][3] - 26]})], extra=[(masked, 1.0, 99, RX, 218, True)])

    # 6 · the packet by Amazon SES; per-receipt checks; the minibar left out; the second reviewer; email first, then chat
    if "D packet sent by SES" in BOX:
        zoom("D packet sent by SES", "desk", 3.0, [("6.1", 0.2)], caption="Sent by Amazon SES · live")
    if "D receipt checks" in BOX:
        zoom("D receipt checks", "desk", 3.0, [("6.2", 0.2)], caption="Each receipt: what was read, pass or fail", zmax=2.2, cut=True, cap_above=True, tail=2.3)  # time to read the table
    else:
        ch = B["D claim with checks"]
        desk("r1", [(ch - 0.4, ch + 0.7, R)], "Insurer's desk · reviewer 1", [("6.2", 0.2)])
    # the payoff of scene 4: the clause Jordan found, applied to the hotel bill before anyone decides
    rc = "D receipt minibar" if "D receipt minibar" in BOX else "D receipt checks" if "D receipt checks" in BOX else None
    if rc:  # the hotel row's "Checks" cell (the right ~40% of the documents table; its last row is the hotel bill)
        x, y, w, h = BOX[rc]
        cell = [x + 0.6 * w, y + h - 6 - (h if rc == "D receipt minibar" else 82), 0.4 * w - 16, min(h, 70) if rc == "D receipt minibar" else 70]
        zoom(rc, "desk", 3.0, [("6.3", 0.2)], caption="Minibar ¥1,800 left out · [TD-4](a)", cap_at=0.3, zmax=2.4, cut=True, box=cell, cap_dy=34)  # under the hotel row, clear of it
    zoom("D waiting for second reviewer", "desk", 2.6, [("6.4", 0.2)], caption="Payouts over USD 100: a second reviewer confirms", cap_at=0.5, cut=True, push=0.2)
    mi, me, dc = B["D mail inbox"], B["D decision email (SES id)"], B["D decision card"]
    # a beat's time is the last frame of its state (the next click follows): pieces end just before it, then hold
    pieces = [(mi - 0.6, me - 0.1, R, 1.0), (dc - 1.0, dc - 0.1, R, 2.0)]
    p_mail = plen(pieces[0])
    calls = [("D decision email (SES id)", (me - mi + 0.6) - 0.6, p_mail, "Email first · Amazon SES"),
             ("D decision card", p_mail + 0.6, 99, "Then the chat")]
    sfx.append(("chime", t + 0.05))  # the decision arrives: the same chime as the first alert, before the sentence
    phone(pieces, "The decision reaches Jordan", [("6.5", 1.0)], calls=calls)

    # 7 · "approved" is refused; rules and Guardrails; templates only; the number
    ty, tdone = B["E typing"], B["E typed approved"]
    desk("r1", [(ty - 0.2, tdone + 0.2, R)], "", [("7.1", 0.2)], region=region_of(BOX["E typing"]), fit=False)
    sfx.append(("thud", t + 0.4))
    zoom("E send dialog 2", "desk", 3.0, [("7.2", 0.2), ("7.3", None)], extra=[(stamp("stamp"), 0.4, 99, 1060, 80, True)], cut=True)
    tb = clips["7.4"][1]
    zoom("E template buttons", "desk", 3.0, [("7.4", 0.2), ("7.5", None)],
         caption=[("Rules + Bedrock Guardrails (ApplyGuardrail) check every free-text message", 0.3), ("Decisions: fixed templates only", 0.2 + tb + GAP)],
         cut=True, cap_bottom=True, zmax=1.7)  # on the frame's bottom margin: above and below are labels
    # no blocking-rate card: a rate invites "what got through?", and decisions never depend on it (templates only)

    # 8 · the claim end to end; live vs simulated; the numbers; the ending
    for i in range(1, 7):
        still(framed(f"step{i}", VID / f"arch-step{i}.png"), 1.5, [(f"8a.{i}", 0.15)], tail=0.15)  # brisk: one line per step
    b1, b2, b3 = 0.4, 0.4 + clips["8b.1"][1] + GAP, 0.4 + clips["8b.1"][1] + clips["8b.2"][1] + 2 * GAP
    still(framed("all", VID / "arch-all.png"), 6.0, [("8b.1", b1), ("8b.2", None), ("8b.3", None)],
          extra=[(ring("ring_live", arch_box(VID / "arch-all.png", (936, 1144, 1056, 1196))), b1, b2 - 0.1, 0, 0, True),
                 (ring("ring_sim", arch_box(VID / "arch-all.png", (934, 100, 1300, 160))), b2, 99, 0, 0, True),
                 # the SDK calls, readable: what the qualifying-services criterion looks for
                 (*centered(pill("sdk_ses", "sesv2 · SendEmail  — live", bg=(15, 138, 95), size=34), H - 76)[:1], b1, b2 - 0.1,
                  *centered(pill("sdk_ses", "sesv2 · SendEmail  — live", bg=(15, 138, 95), size=34), H - 76)[1:], True),
                 (*centered(pill("sdk_pin", "pinpoint-sms-voice-v2 · SendTextMessage / SendRcsMessage", bg=BLUE, size=34), H - 76)[:1], b2, 99,
                  *centered(pill("sdk_pin", "pinpoint-sms-voice-v2 · SendTextMessage / SendRcsMessage", bg=BLUE, size=34), H - 76)[1:], True)], tail=0.5)
    # the receipt test: every real receipt photo of the run, then its result (eval/results/<RECEIPT_RUN>)
    wall_imgs, wall_res = receipt_test()
    if wall_imgs:
        ok, n_ = wall_res
        pct = f"{100 * ok / n_:.1f}%"  # one decimal, rounded down never up: 1,143/1,146 = 99.74 → 99.7%
        add(lambda n, L: receipt_wall(n, L, wall_imgs, pct, f"{ok:,} of {n_:,} real Japanese receipt photos: total read correctly",
                                      "public test set (JaWildText) · a person still decides every payout", fill=1.6, hold=1.2), 5.2, [("8n.1", 1.6)], tail=0.5)
    else:
        still(card("num8", [("119 of 120", 120, True, white), ("real Japanese receipt photos: total read correctly", 44, False, white),
                             ("public test set (JaWildText) · a person still decides every payout", 30, False, grey)]), 3.0,
              [("8n.1", 0.3)], tail=0.6)
    mont = ["A alert arrives", "B answer with source card", "D submit sheet" if "D submit sheet" in shots else "B W4 highlight",
            "D decision email (SES id)"]
    speech = sum(clips[f"8c.{i}"][1] for i in (1, 2, 3)) + 2 * GAP + 0.6
    each = max(1.8, speech / len(mont))
    say([("8c.1", 0.3), ("8c.2", None), ("8c.3", None)])
    sfx.append(("jet_", t))  # placed below, once the end card's length is known
    for i, k in enumerate(mont):
        add(lambda n, L, k=k, i=i: still_shot(n, L, phone_card(f"mont{i}", shots[k])), each, cut=(i == 0))
    still(card("end", [("ClausePath", 110, True, white), ("Answers with proof. Decisions by people.", 46, False, white),
                       ("Amazon Bedrock · AWS End User Messaging · Amazon SES", 30, False, (150, 170, 220))],
               blur_from=phone_card("endbg", shots["B W4 highlight"])), 4.5, [("8d.1", 0.4), ("8d.2", None), ("8d.3", None)], tail=1.0)

    # the jet's loudest moment (about 9 s into jet.wav) right after the last line, in the end card's silence
    last_end = max(st + clips[k][1] for k, st in audio)
    sfx = [("jet", max(last_end + 0.5 - 9.0, 0)) if k == "jet_" else (k, at) for k, at in sfx]

    # crossfade the shots, lay the sentences, mux
    video = OUT / "video_only.mp4"
    ins, chain, off, prev = [], [], 0.0, "0:v"
    for seg in segs:
        ins += ["-i", str(seg)]
    for i in range(1, len(segs)):
        off += durs[i - 1]
        chain.append(f"[{prev}][{i}:v]xfade=transition=fade:duration={xfs[i]}:offset={off:.3f}[x{i}]")
        prev = f"x{i}"
    chain.append(f"[{prev}]trim=duration={t:.3f},setpts=PTS-STARTPTS,format=yuv420p[v]")
    run(["ffmpeg", "-y", "-v", "error", *ins, "-filter_complex", ";".join(chain), "-map", "[v]", "-r", str(FPS),
         "-c:v", "libx264", "-crf", "19", "-preset", "medium", str(video)])
    total = duration(video)
    ins, mix = [], []
    for i, (key, start) in enumerate(audio):
        ins += ["-i", str(clips[key][0])]
        mix.append(f"[{i}:a]adelay={int(start * 1000)}|{int(start * 1000)},apad=whole_dur={total}[a{i}]")
    mix.append("".join(f"[a{i}]" for i in range(len(audio))) + f"amix=inputs={len(audio)}:normalize=0,atrim=duration={total}[aout]")
    narr = OUT / "narration.m4a"
    run(["ffmpeg", "-y", "-v", "error", *ins, "-filter_complex", ";".join(mix), "-map", "[aout]", "-c:a", "aac", "-b:a", "192k", str(narr)])
    # music bed (ducked under the narration) and effects, when demo/edit/music exists (python3 demo/make_music.py)
    mus = MUSIC
    if (mus / "bed.wav").exists():
        ins = ["-i", str(narr), "-stream_loop", "-1", "-i", str(mus / "bed.wav")]
        fx = [(k, at) for k, at in sfx if (mus / f"{k}.wav").exists()]
        for k, _ in fx:
            ins += ["-i", str(mus / f"{k}.wav")]
        DUCKED = ("storm", "jet")  # beds of sound: they step back under the voice like the music
        fc = ["[0:a]asplit=2[n][sc]", f"[1:a]atrim=duration={total},volume=0.08,afade=t=out:st={max(total - 3, 0)}:d=3[bedv]"]
        for i, (k, at) in enumerate(fx):
            gain = {"storm": 0.22, "jet": 0.24, "chime": 0.15, "thud": 0.05, "ding": 0.22, "whoosh": 0.05, "click": 0.2}.get(k, 0.2)
            fc.append(f"[{2 + i}:a]volume={gain},adelay={int(at * 1000)}|{int(at * 1000)},apad=whole_dur={total}[f{i}]")
        under = "".join(f"[f{i}]" for i, (k, _) in enumerate(fx) if k in DUCKED)
        over = "".join(f"[f{i}]" for i, (k, _) in enumerate(fx) if k not in DUCKED)
        n_under, n_over = under.count("["), over.count("[")
        fc.append(f"[bedv]{under}amix=inputs={1 + n_under}:normalize=0[beds]")
        fc.append("[beds][sc]sidechaincompress=threshold=0.015:ratio=8:attack=20:release=500[bed]")
        fc.append(f"[n][bed]{over}amix=inputs={2 + n_over}:normalize=0,atrim=duration={total},loudnorm=I=-14:TP=-1.0:LRA=11,aresample=48000,afade=t=out:st={max(total - 2.0, 0):.2f}:d=2[m]")
        mixed = OUT / "mix.m4a"
        run(["ffmpeg", "-y", "-v", "error", *ins, "-filter_complex", ";".join(fc), "-map", "[m]", "-c:a", "aac", "-b:a", "192k", str(mixed)])
        narr = mixed
    final = OUT / "rough_cut.mp4"
    run(["ffmpeg", "-y", "-v", "error", "-i", str(video), "-i", str(narr), "-c:v", "copy", "-c:a", "copy", "-shortest", str(final)])
    shots_t = [round(sum(durs[:i]), 2) for i in range(len(durs))]
    (OUT / "timeline.json").write_text(json.dumps({"total": total, "shots": shots_t,
                                                   "audio": [(k, round(s, 2), round(s + clips[k][1], 2)) for k, s in audio],
                                                   "sfx": [(k, round(at, 2)) for k, at in sfx]}, indent=1))
    print(final, f"{total:.1f}s", f"{len(segs)} shots")


if __name__ == "__main__":
    main()
