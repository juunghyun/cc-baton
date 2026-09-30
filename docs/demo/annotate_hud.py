"""HUD 해부도: vhs 로 캡처한 실제 HUD(docs/demo/hud-raw.png, 2x)에 번호표와 설명을 붙인다.

    vhs docs/demo/hud.tape && ffmpeg -sseof -0.3 -i docs/demo/hud-raw.mp4 -frames:v 1 -update 1 docs/demo/hud-raw.png
    python3 docs/demo/annotate_hud.py      → docs/assets/hud.png, docs/assets/hud.ko.png
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "assets"
RAW = Image.open(HERE / "hud-raw.png").convert("RGB")

PAD_X, CHAR = 40, 20.3             # hud.tape: Padding 40, FontSize 32 → 고정폭 글자 한 칸 (실측)
LINES = [(48, 87), (87, 125), (125, 164)]  # 세 줄의 세로 범위 (2x)
TEXT = [
    "● acme [WORK] · Opus 5.5[1M] ▰▰▰▱▱ high ▌ Lv.60  · ctx 12% · ~/src/app (ft/login-302*)",
    "5h ██░░░░ 31% 2h05m │ 1w █░░░░░ 11% 3d10h │ Fable ░░░░░░ 0% │ ↔ side 12%/40%, personal 4%/23% +1 more",
    "⟳ Update ▮▮▮ · ⏾ Sleep ▮▮▮ 90m · ⇄ Auto-swap ▮▮▮ same group only",
]
# (줄, 그 줄에서 가리킬 글자, 영어, 한국어)
ITEMS = [
    (0, "●", "Account and group", "계정과 그룹"),
    (0, "Opus", "Model, effort meter and level", "모델, effort 미터와 Lv"),
    (0, "ctx", "Context used", "컨텍스트 사용률"),
    (0, "~/src", "Folder and branch (* = uncommitted)", "폴더와 브랜치 (* = 커밋 안 한 변경)"),
    (1, "5h", "This account: 5-hour and weekly usage, time to reset", "이 계정의 5시간·주간 사용량, 리셋까지 남은 시간"),
    (1, "↔", "Other accounts, most recently used first", "다른 계정 (최근에 쓴 순)"),
    (2, "⟳", "Feature switches (cc-baton toggle)", "기능 스위치 (cc-baton toggle)"),
]
BG = RAW.getpixel((5, 5))
MARK, MARK_TEXT, LEGEND = (137, 180, 250), (24, 24, 37), (205, 214, 244)
GAP, R = 44, 17                   # 줄 사이 번호표 자리, 번호표 반지름
FONTS = {"en": ("/System/Library/Fonts/SFNS.ttf", 0), "ko": ("/System/Library/Fonts/AppleSDGothicNeo.ttc", 0)}


def font(lang, size, bold=False):
    path, idx = FONTS[lang]
    try:
        f = ImageFont.truetype(path, size, index=idx)
        if bold and lang == "en":
            f.set_variation_by_name("Bold")
        return f
    except Exception:
        return ImageFont.load_default()


def badge(d, cx, cy, n, lang):
    d.ellipse((cx - R, cy - R, cx + R, cy + R), fill=MARK)
    d.text((cx, cy), str(n), fill=MARK_TEXT, font=font("en", 22, bold=True), anchor="mm")


def build(lang):
    width = 2200
    strips_h = sum(GAP + (b - a) for a, b in LINES)
    legend_rows = (len(ITEMS) + 1) // 2
    top, legend_h = 20, 40 + legend_rows * 52 + 30
    img = Image.new("RGB", (width, top + strips_h + legend_h), BG)
    d = ImageDraw.Draw(img)
    y = top
    for li, (a, b) in enumerate(LINES):
        for n, (line, token, *_rest) in enumerate(ITEMS, 1):
            if line == li:
                cx = PAD_X + TEXT[li].index(token) * CHAR + R
                badge(d, int(cx), y + GAP // 2, n, lang)
        y += GAP
        img.paste(RAW.crop((0, a, width, b)), (0, y))
        y += b - a
    # 범례: 두 열
    y += 40
    col_w = (width - 2 * PAD_X) // 2
    f = font(lang, 26)
    for i, (_l, _t, en, ko) in enumerate(ITEMS):
        cx = PAD_X + (i % 2) * col_w + R
        cy = y + (i // 2) * 52 + R
        badge(d, cx, cy, i + 1, lang)
        d.text((cx + R + 16, cy), en if lang == "en" else ko, fill=LEGEND, font=f, anchor="lm")
    OUT.mkdir(exist_ok=True)
    img.save(OUT / ("hud.png" if lang == "en" else "hud.ko.png"), optimize=True)


for lang in ("en", "ko"):
    build(lang)
print("wrote", sorted(p.name for p in OUT.glob("hud*.png")))
