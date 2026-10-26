"""出题卡片：正方形，中间 16:9 是图（九宫格，未揭开的格子盖色块），上下黑边写标题和提示。"""

import base64
import io
import math
import random
from functools import cache

from PIL import Image, ImageDraw, ImageFont

SIZE = 1080
PIC_H = 608  # 16:9
BAR = (SIZE - PIC_H) // 2
PAD = 40
COL_GAP = 40
ROW_H = 50
TOP_ROWS, BOTTOM_ROWS = 2, 3
HALF = (SIZE - PAD * 2 - COL_GAP) / 2  # 一行两条提示，每条的宽度
GAP = 4
BG = (0, 0, 0)
COVER = (38, 40, 46)
MARK = (96, 100, 110)
KEY = (242, 184, 75)
TEXT = (238, 238, 238)
DIM = (140, 144, 152)
ART_BG = (236, 238, 242)  # 透明底立绘的底色
ART_PAD = 28
MAP_BG = (28, 31, 38)  # 国家轮廓：深色底、浅色剪影
MAP_FILL = (236, 238, 242)
# AstrBot 镜像自带 Noto Sans CJK；ttc 里按名字找简中字形
FONT_DIR = "/usr/share/fonts/opentype/noto"


@cache
def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    path = f"{FONT_DIR}/NotoSansCJK-{'Bold' if bold else 'Regular'}.ttc"
    try:
        for i in range(10):
            f = ImageFont.truetype(path, size, index=i)
            if f.getname()[0] == "Noto Sans CJK SC":
                return f
    except OSError:
        pass
    return ImageFont.load_default(size=size)


def load(data: bytes, contain: bool = False) -> Image.Image:
    """居中裁成 16:9 再缩放，不拉伸。contain 用于透明底的立绘，见 _contain。"""
    if contain:
        return _contain(Image.open(io.BytesIO(data)))
    return _cover_fit(Image.open(io.BytesIO(data)).convert("RGB"), SIZE, PIC_H)


def load_panels(images: list[bytes]) -> Image.Image:
    """猜地点：左边一张大图，右边上下两张小图，三张都直接给看，不画九宫格。"""
    out = Image.new("RGB", (SIZE, PIC_H), BG)
    big_w = round(SIZE * 2 / 3) - GAP // 2
    small_w, small_h = SIZE - big_w - GAP, (PIC_H - GAP) // 2
    boxes = [
        (0, 0, big_w, PIC_H),
        (big_w + GAP, 0, small_w, small_h),
        (big_w + GAP, small_h + GAP, small_w, PIC_H - small_h - GAP),
    ]
    for data, (x, y, w, h) in zip(images, boxes):
        out.paste(_cover_fit(Image.open(io.BytesIO(data)).convert("RGB"), w, h), (x, y))
    out.info["grid"] = None
    return out


def load_outline(rings: list) -> Image.Image:
    """猜国家：国家剪影铺满图区，不画九宫格。rings 见 geo.outline()；按中心纬度压缩经度，近似等积，不至于高纬国家变胖。"""
    outer = [pt for hole, ring in rings if not hole for pt in ring]
    lat0 = (min(p[1] for p in outer) + max(p[1] for p in outer)) / 2
    k = math.cos(math.radians(lat0))
    xs, ys = [p[0] * k for p in outer], [-p[1] for p in outer]
    pad, ss = 36, 2  # 2 倍大小画再缩小，边缘平滑
    scale = min((SIZE - pad * 2) / max(max(xs) - min(xs), 1e-6), (PIC_H - pad * 2) / max(max(ys) - min(ys), 1e-6))
    ox = (SIZE - (max(xs) - min(xs)) * scale) / 2 - min(xs) * scale
    oy = (PIC_H - (max(ys) - min(ys)) * scale) / 2 - min(ys) * scale
    big = Image.new("RGB", (SIZE * ss, PIC_H * ss), MAP_BG)
    draw = ImageDraw.Draw(big)
    for hole, ring in sorted(rings, key=lambda r: r[0]):  # 先画外环再挖内洞
        pts = [((x * k * scale + ox) * ss, (-y * scale + oy) * ss) for x, y in ring]
        draw.polygon(pts, fill=MAP_BG if hole else MAP_FILL, outline=MAP_BG if hole else MAP_FILL, width=ss)
    out = big.resize((SIZE, PIC_H), Image.Resampling.LANCZOS)
    out.info["grid"] = None
    return out


def _cover_fit(img: Image.Image, width: int, height: int) -> Image.Image:
    """按目标比例居中裁切再缩放，不拉伸。"""
    w, h = img.size
    if w * height > h * width:
        cw, ch = round(h * width / height), h
    else:
        cw, ch = w, round(w * height / width)
    left, top = (w - cw) // 2, (h - ch) // 2
    return img.crop((left, top, left + cw, top + ch)).resize((width, height), Image.Resampling.LANCZOS)


def _contain(img: Image.Image) -> Image.Image:
    """裁掉透明边后整张缩进图区正中的正方形（浅色底），两边留黑，九宫格只画在这个正方形上。

    立绘四角常是空白，info["empty"] 记下几乎没内容的格子，出题时最后才揭开。
    """
    art = img.convert("RGBA")
    art = art.crop(art.getchannel("A").getbbox() or (0, 0, *art.size))
    scale = min((PIC_H - ART_PAD * 2) / art.width, (PIC_H - ART_PAD * 2) / art.height)
    art = art.resize((max(1, round(art.width * scale)), max(1, round(art.height * scale))), Image.Resampling.LANCZOS)
    offset = ((PIC_H - art.width) // 2, (PIC_H - art.height) // 2)
    board = Image.new("RGB", (PIC_H, PIC_H), ART_BG)
    board.paste(art, offset, art)
    alpha = Image.new("L", (PIC_H, PIC_H), 0)
    alpha.paste(art.getchannel("A"), offset)
    out = Image.new("RGB", (SIZE, PIC_H), BG)
    x0 = (SIZE - PIC_H) // 2
    out.paste(board, (x0, 0))
    out.info["grid"] = (x0, x0 + PIC_H)
    out.info["empty"] = set()
    for i in range(9):
        row, col = divmod(i, 3)
        box = [round(PIC_H * k / 3) for k in (col, row, col + 1, row + 1)]
        cell = alpha.crop(box)
        if sum(cell.histogram()[32:]) < cell.width * cell.height * 0.03:
            out.info["empty"].add(i)
    return out


def reveal_order(img: Image.Image) -> list[int]:
    """随机揭开顺序，几乎空白的格子排最后。"""
    empty = img.info.get("empty", set())
    full = [i for i in range(9) if i not in empty]
    return random.sample(full, len(full)) + random.sample(sorted(empty), len(empty))


def fit(text: str, f: ImageFont.FreeTypeFont, width: float) -> str:
    if f.getlength(text) <= width:
        return text
    while text and f.getlength(text + "…") > width:
        text = text[:-1]
    return text + "…"


def render(img: Image.Image, revealed: set[int] | None, title: str, status: str, hints: list, note: str = "") -> str:
    """返回 base64 JPEG。revealed 是已揭开的格子（按行从 0 编到 8），None 表示整图公开、不画格线。

    提示按固定格位排：上边黑边 2 行、下边 3 行，每行两条，第 n 条提示的位置不随揭开变化；
    最后一个格位放 note。
    """
    out = Image.new("RGB", (SIZE, SIZE), BG)
    out.paste(img, (0, BAR))
    draw = ImageDraw.Draw(out)
    if revealed is not None and (box := img.info.get("grid", (0, SIZE))):
        _cover(draw, revealed, *box)

    status_font, title_font = font(30), font(46, bold=True)
    status = fit(status, status_font, SIZE / 2 - PAD)
    draw.text((SIZE - PAD, 56), status, fill=DIM, font=status_font, anchor="rs")
    title = fit(title, title_font, SIZE - PAD * 3 - status_font.getlength(status))
    draw.text((PAD, 60), title, fill=TEXT, font=title_font, anchor="ls")

    ys = [100 + i * ROW_H for i in range(TOP_ROWS)] + [BAR + PIC_H + 30 + i * ROW_H for i in range(BOTTOM_ROWS)]
    slots = [(PAD + col * (HALF + COL_GAP), y) for y in ys for col in range(2)]
    for (x, y), (key, value) in zip(slots, hints):
        _hint(draw, x, y, key, value)
    if note:
        x, y = slots[-1]
        draw.text((x + HALF, y), fit(note, font(26), HALF), fill=DIM, font=font(26), anchor="rt")
    return encode(out)


def _cover(draw: ImageDraw.ImageDraw, revealed: set[int], x0: int, x1: int) -> None:
    xs = [x0 + round((x1 - x0) * i / 3) for i in range(4)]
    ys = [BAR + round(PIC_H * i / 3) for i in range(4)]
    mark = font(PIC_H // 6, bold=True)
    for i in range(9):
        row, col = divmod(i, 3)
        if i not in revealed:
            box = (xs[col], ys[row], xs[col + 1], ys[row + 1])
            draw.rectangle(box, fill=COVER)
            draw.text(((box[0] + box[2]) / 2, (box[1] + box[3]) / 2), "?", fill=MARK, font=mark, anchor="mm")
    for x in xs[1:3]:
        draw.rectangle((x - GAP // 2, BAR, x + GAP // 2 - 1, BAR + PIC_H - 1), fill=BG)
    for y in ys[1:3]:
        draw.rectangle((x0, y - GAP // 2, x1 - 1, y + GAP // 2 - 1), fill=BG)


def _hint(draw: ImageDraw.ImageDraw, x: float, y: float, key: str, value: str) -> None:
    key_font, value_font = font(30, bold=True), font(30)
    draw.text((x, y), key, fill=KEY, font=key_font, anchor="lt")
    vx = x + key_font.getlength(key) + 14
    draw.text((vx, y), fit(value, value_font, x + HALF - vx), fill=TEXT, font=value_font, anchor="lt")


def encode(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=88)
    return base64.b64encode(buf.getvalue()).decode()
