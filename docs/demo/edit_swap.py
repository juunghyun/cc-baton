"""README GIF 2 편집: swap.tape 녹화본을 빠르게 줄이고, 계정이 바뀌는 순간 HUD 를 확대하고,
대화가 그대로 따라왔다는 것을 강조한다.

    python3 docs/demo/edit_swap.py      docs/demo/swap-raw.gif → docs/demo-swap.gif (ffmpeg 필요)

shortcut: 아래 시각·좌표는 지금 swap-raw.gif 의 실측값이다. 다시 녹화하면 다시 잰다.
"""
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
RAW, OUT = HERE / "swap-raw.gif", HERE.parent / "demo-swap.gif"
FPS = 25                                     # 녹화본과 같은 프레임 간격(40ms)
MARK, MARK_TEXT = (137, 180, 250), (24, 24, 37)  # annotate_hud.py 와 같은 색
FONT = ImageFont.truetype("/System/Library/Fonts/SFNS.ttf", 26)
FONT.set_variation_by_name("Bold")

with Image.open(RAW) as im:
    W, H = im.size
    SRC = []
    for i in range(im.n_frames):
        im.seek(i)
        SRC.append(im.convert("RGB"))

FULL = (0, 0, W, H)
ZOOM = (0, H / 2, W / 2, H)                  # 왼쪽 아래 1/4(입력창과 HUD)를 2배로
CW, X0 = 9.22, 35                            # 고정폭 글자 한 칸, 첫 글자 x (실측)


def text(col, n, y0, y1):
    """한 줄에서 col 번째 글자부터 n 글자를 두르는 상자 (녹화본 좌표)."""
    return (X0 + col * CW - 6, y0 - 5, X0 + (col + n) * CW + 6, y1 + 5)


WORK, PERSONAL = text(0, 13, 584, 599), text(0, 10, 584, 599)   # "● work [TEAM]", "● personal"
EARLIER = (14, 18, W - 14, 408)              # 스왑 전 대화 전체
CODENAME, ANSWER = text(49, 8, 26, 38), text(0, 8, 470, 481)   # 첫 프롬프트의 BLUEBIRD, 답의 BLUEBIRD


def ease(p):
    return p * p * (3 - 2 * p)


def lerp(a, b, p):
    return tuple(x + (y - x) * p for x, y in zip(a, b))


def render(t, zoom, boxes, caption, cap_y):
    img = SRC[min(round(t * FPS), len(SRC) - 1)]
    if zoom != FULL:  # 확대가 움직이는 동안은 NEAREST: 매끈하게 보간한 전체 프레임은 GIF 에서 장당 ~190KB
        img = img.resize((W, H), Image.LANCZOS if zoom == ZOOM else Image.NEAREST, box=zoom)
    else:
        img = img.copy()
    d = ImageDraw.Draw(img)
    sx, sy = W / (zoom[2] - zoom[0]), H / (zoom[3] - zoom[1])
    for x0, y0, x1, y1 in boxes:
        d.rounded_rectangle(((x0 - zoom[0]) * sx, (y0 - zoom[1]) * sy, (x1 - zoom[0]) * sx, (y1 - zoom[1]) * sy),
                            radius=8, outline=MARK, width=3)
    if caption:
        l, t_, r, b = d.textbbox((0, 0), caption, font=FONT)
        cx, w, h = W - 40 - (r - l) / 2, r - l + 36, b - t_ + 22
        d.rounded_rectangle((cx - w / 2, cap_y - h / 2, cx + w / 2, cap_y + h / 2), radius=h / 2, fill=MARK)
        d.text((cx, cap_y), caption, fill=MARK_TEXT, font=FONT, anchor="mm")
    return img


def shot(a, b, secs=None, zoom=(FULL, FULL), boxes=(), caption=None, cap_y=60):
    """녹화본 a~b초를 secs 초로. secs 가 더 짧으면 빨리 감고, 더 길면 a~b 를 되풀이한다(커서 깜빡임 유지)."""
    secs = b - a if secs is None else secs
    n = max(1, round(secs * FPS))
    for k in range(n):
        t = a + (b - a) * k / n if secs <= b - a else a + (k / FPS) % (b - a)
        z = lerp(*zoom, ease((k // 2) / ((n - 1) // 2))) if zoom[0] != zoom[1] else zoom[0]  # 2프레임마다 한 단계
        yield render(t, z, boxes, caption, cap_y)


ZC = 270                                     # 확대 화면에서 비어 있는 띠의 가운데
SHOTS = [
    shot(1.30, 5.40, 1.6),                                   # 첫 프롬프트 타이핑 (work)
    shot(5.40, 10.70, 1.3),                                  # 응답 대기 → Noted.
    shot(10.70, 11.50),
    shot(15.50, 16.70),                                      # /swap personal → 그룹 확인
    shot(16.70, 18.40),
    shot(20.30, 21.72),                                      # /swap personal --yes
    shot(21.72, 22.80),
    shot(22.80, 23.28, zoom=(FULL, ZOOM)),                   # HUD 로 확대
    shot(23.40, 24.60, zoom=(ZOOM, ZOOM), boxes=[WORK], caption="work account  ·  Ctrl+D", cap_y=ZC),
    shot(25.84, 27.02, 1.5, zoom=(ZOOM, ZOOM), boxes=[PERSONAL], caption="Now on personal", cap_y=ZC),
    shot(25.90, 27.02, 0.48, zoom=(ZOOM, FULL)),             # 다시 전체로
    shot(25.90, 27.02, 2.2, boxes=[EARLIER], caption="The whole conversation came along", cap_y=250),
    shot(27.20, 29.40, 1.1),                                 # 다른 계정에서 질문
    shot(29.40, 31.20, 1.2),
    shot(31.20, 34.20, boxes=[CODENAME, ANSWER], caption="It still remembers", cap_y=250),
]

with tempfile.TemporaryDirectory() as tmp:
    n = 0
    for s in SHOTS:
        for f in s:
            f.save(f"{tmp}/{n:04d}.png")
            n += 1
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-framerate", str(FPS), "-i", f"{tmp}/%04d.png", "-vf",
                    "split[a][b];[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=none:diff_mode=rectangle",
                    str(OUT)], check=True)
print(f"wrote {OUT.relative_to(HERE.parent.parent)}: {n} frames, {n / FPS:.1f}s, {OUT.stat().st_size // 1024} KB")
