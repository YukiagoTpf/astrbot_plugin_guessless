"""出题卡片：正方形，中间 16:9 是图（九宫格，未揭开的格子盖色块），上下黑边写标题和提示。

图区还有几种不分格的：街景三联、猜地点匹配的四宫格、猜声优的一排角色、国家剪影、猜国家猜错时的世界地图、盲猜的对比表、听歌猜番的进度条、猜K线的 K 线图。
"""

import base64
import io
import math
import random
from functools import cache

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageStat

from .lines import say

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
MAP_BG = (28, 31, 38)  # 国家轮廓：深色海、浅色剪影、灰色邻国
MAP_FILL = (236, 238, 242)
MAP_LAND = (74, 80, 94)
MAP_PAD = 0.35  # 答案卡画邻国时，题目国家四周各留出它自身尺寸的这么多
MAP_MARGIN = 36  # 出题只画剪影时，剪影离图区边缘的像素
WORLD_LAT = (84, -58)  # 世界地图的纬度范围，南极洲不画
# 猜国家猜错时按两国中心距离上色：越近越红，(公里, 颜色) 之间线性插值
HEAT = ((0, (200, 36, 36)), (1000, (232, 86, 44)), (2500, (240, 146, 54)), (5000, (244, 204, 96)), (10000, (240, 232, 204)))
# 盲猜对比表格子的颜色：一致、接近、不符、没资料
PEEK = 176  # 盲猜萝卜表旁边那一格设定图的边长
CELL = {"hit": (56, 142, 60), "near": (196, 146, 32), "miss": (150, 54, 54), "none": (62, 66, 76)}
K_BG, K_FUTURE, K_GRID = (22, 25, 32), (34, 38, 48), (46, 50, 60)  # 猜K线：底色、之后那段的底色、网格
K_UP, K_DOWN = (232, 72, 72), (38, 166, 154)
K_MA = ((5, (238, 238, 238)), (10, (242, 184, 75)), (20, (176, 140, 255)))  # 均线：天数、颜色
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


def brightness(img: Image.Image) -> float:
    """平均亮度 0–255：猜魔兽地图有的截图是洞里，几乎全黑。"""
    return ImageStat.Stat(img.convert("L").resize((64, 36))).mean[0]


def load(data: bytes, contain: bool = False) -> Image.Image:
    """居中裁成 16:9 再缩放，不拉伸。contain 用于透明底的立绘，见 _contain。"""
    if contain:
        return _contain(Image.open(io.BytesIO(data)))
    return _cover_fit(Image.open(io.BytesIO(data)).convert("RGB"), SIZE, PIC_H)


def load_panels(images: list[bytes]) -> Image.Image:
    """猜地点：左边一张大图，右边上下两张小图，三张都直接给看，不画九宫格。尺寸见 geo.PANELS。"""
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


def quad(images: list[bytes], labels: list[str]) -> Image.Image:
    """猜地点匹配：四张街景排成 2×2，左上角标图号；答案卡在底下压一条国家名（labels 是空串就不压）。"""
    out = Image.new("RGB", (SIZE, PIC_H), BG)
    draw = ImageDraw.Draw(out, "RGBA")
    w, h = (SIZE - GAP) // 2, (PIC_H - GAP) // 2
    num, line = font(40, bold=True), font(30)
    for i, (data, label) in enumerate(zip(images, labels)):
        x, y = (i % 2) * (w + GAP), (i // 2) * (h + GAP)
        out.paste(_cover_fit(Image.open(io.BytesIO(data)).convert("RGB"), w, h), (x, y))
        draw.rounded_rectangle((x + 12, y + 12, x + 68, y + 68), 10, fill=(0, 0, 0, 176))
        draw.text((x + 40, y + 40), str(i + 1), fill=KEY, font=num, anchor="mm")
        if label:
            draw.rectangle((x, y + h - 50, x + w - 1, y + h - 1), fill=(0, 0, 0, 176))
            draw.text((x + w / 2, y + h - 25), fit(label, line, w - 24), fill=TEXT, font=line, anchor="mm")
    out.info["grid"] = None
    return out


def mosaic(img: Image.Image, grid: int | None = None, cells: list[int] = ()) -> Image.Image:
    """猜群友：正方形头像放在图区正中，切成 grid×grid，只露 cells 那几格，其余盖色块打问号。
    grid 为 None 是答案卡，整张头像都露出来。"""
    out = Image.new("RGB", (SIZE, PIC_H), BG)
    x0 = (SIZE - PIC_H) // 2
    out.paste(img.convert("RGB").resize((PIC_H, PIC_H), Image.Resampling.LANCZOS), (x0, 0))
    if grid:
        draw = ImageDraw.Draw(out)
        edges = [x0 + round(PIC_H * i / grid) for i in range(grid + 1)]
        mark = font(PIC_H // (grid * 2), bold=True)
        for i in range(grid * grid):
            if i in cells:
                continue
            row, col = divmod(i, grid)
            box = (edges[col], edges[row] - x0, edges[col + 1], edges[row + 1] - x0)
            draw.rectangle(box, fill=COVER)
            draw.text(((box[0] + box[2]) / 2, (box[1] + box[3]) / 2), "?", fill=MARK, font=mark, anchor="mm")
        for e in edges[1:-1]:
            draw.rectangle((e - GAP // 2, 0, e + GAP // 2 - 1, PIC_H - 1), fill=BG)
            draw.rectangle((x0, e - x0 - GAP // 2, x0 + PIC_H - 1, e - x0 + GAP // 2 - 1), fill=BG)
    out.info["grid"] = None
    return out


def square(data: bytes) -> Image.Image:
    """猜老师的头像：居中裁成正方形。"""
    img = Image.open(io.BytesIO(data)).convert("RGB")
    side = min(img.size)
    x, y = (img.width - side) // 2, (img.height - side) // 2
    return img.crop((x, y, x + side, y + side))


def blur(img: Image.Image, radius: int | None = None) -> Image.Image:
    """猜老师：正方形头像放在图区正中，按 radius（图区里的像素）高斯模糊。radius 为 None 是答案卡，不模糊。"""
    out = Image.new("RGB", (SIZE, PIC_H), BG)
    face = img.convert("RGB").resize((PIC_H, PIC_H), Image.Resampling.LANCZOS)
    if radius:
        face = face.filter(ImageFilter.GaussianBlur(radius))
    out.paste(face, ((SIZE - PIC_H) // 2, 0))
    out.info["grid"] = None
    return out


def roles(images: list[bytes], shown: int, labels: list[list[str]]) -> Image.Image:
    """猜声优：几个角色图（一般四个）并排，前 shown 个露出来，其余盖色块打问号。露出来的底下压一条字：
    出题时是作品名，答案卡是角色名和作品名（labels 里每个是一两行）。Bangumi 的游戏角色图常是长长的全身立绘，
    上下裁的时候多留上面，脸才在格子里。"""
    out = Image.new("RGB", (SIZE, PIC_H), BG)
    draw = ImageDraw.Draw(out, "RGBA")
    n = max(1, len(images))
    w = (SIZE - GAP * (n - 1)) // n
    mark, line = font(PIC_H // 6, bold=True), font(26 if n <= 3 else 24)
    for i, (data, label) in enumerate(zip(images, labels)):
        x = i * (w + GAP)
        if i >= shown:
            draw.rectangle((x, 0, x + w - 1, PIC_H - 1), fill=COVER)
            draw.text((x + w / 2, PIC_H / 2), "?", fill=MARK, font=mark, anchor="mm")
            continue
        out.paste(_cover_fit(Image.open(io.BytesIO(data)).convert("RGB"), w, PIC_H, upper=0.1), (x, 0))
        if label := [t for t in label if t]:
            top = PIC_H - 14 - 36 * len(label)
            draw.rectangle((x, top, x + w - 1, PIC_H - 1), fill=(0, 0, 0, 176))
            for j, text in enumerate(label):
                draw.text((x + w / 2, top + 25 + 36 * j), fit(text, line, w - 24), fill=TEXT, font=line, anchor="mm")
    out.info["grid"] = None
    return out


def leaderboard(title: str, sections: list) -> str:
    """整点播报的排行榜，返回 base64 JPEG。sections 是 [(题型名, [(名次, 名字, 答对次数, 这段时间新增), ...]), ...]，
    两列排，新增的次数用黄字标在后面。"""
    m, gap, head_h, row_h, rows = PAD, 24, 58, 44, max((len(r) for _, r in sections), default=0)
    box_w = (SIZE - m * 2 - gap) / 2
    box_h = head_h + rows * row_h + 16
    lines = math.ceil(len(sections) / 2)
    out = Image.new("RGB", (SIZE, 130 + lines * (box_h + gap) + m - gap), BG)
    draw = ImageDraw.Draw(out)
    draw.text((m, 70), title, fill=TEXT, font=font(46, bold=True), anchor="ls")
    name_font, num_font = font(28), font(28, bold=True)
    for k, (kind, board) in enumerate(sections):
        x, y = m + (k % 2) * (box_w + gap), 110 + (k // 2) * (box_h + gap)
        draw.rounded_rectangle((x, y, x + box_w, y + box_h), 12, fill=COVER)
        draw.text((x + 20, y + 36), kind, fill=KEY, font=font(30, bold=True), anchor="ls")
        for i, (rank, name, count, gained) in enumerate(board):
            ry = y + head_h + i * row_h + row_h / 2
            draw.text((x + 20, ry), f"{rank}", fill=DIM, font=num_font, anchor="lm")
            right = f"+{gained}" if gained else ""
            right_w = num_font.getlength(right) + 12 if right else 0
            count_x = x + box_w - 20 - right_w
            if right:
                draw.text((x + box_w - 20, ry), right, fill=KEY, font=num_font, anchor="rm")
            draw.text((count_x, ry), str(count), fill=TEXT, font=num_font, anchor="rm")
            name_w = count_x - num_font.getlength(str(count)) - 16 - (x + 62)
            draw.text((x + 62, ry), fit(printable(name), name_font, name_w), fill=TEXT, font=name_font, anchor="lm")
    return encode(out)


def printable(text: str) -> str:
    """去掉思源黑体里没有的 emoji、变体选择符这类字符，免得画成方框。"""
    def drawable(ch: str) -> bool:
        cp = ord(ch)
        return cp < 0x1F000 and not 0xD800 <= cp < 0xF900 and ch not in "‍︎️"

    return "".join(ch for ch in text if drawable(ch)).strip() or "?"


def load_outline(rings: list, world: dict | None = None, target: str = "") -> Image.Image:
    """猜国家：国家剪影居中，不画九宫格。rings 见 geo.outline()；按中心纬度压缩经度，近似等积，不至于高纬国家变胖。

    出题只画剪影，铺满图区。答案卡给 world（邻国底图 {组名: 轮廓}，geo.fetch_outlines 的 "groups"）和
    target（题目国家的组名）：四周留出一圈，邻国画成灰色，国与国之间留一道深色缝，题目国家整组（含图里的属地）画成剪影色。
    """
    outer = [pt for hole, ring in rings if not hole for pt in ring]
    lat0 = (min(p[1] for p in outer) + max(p[1] for p in outer)) / 2
    k = math.cos(math.radians(lat0))
    xs, ys = [p[0] * k for p in outer], [-p[1] for p in outer]
    w, h = max(max(xs) - min(xs), 1e-6), max(max(ys) - min(ys), 1e-6)
    ss = 2  # 2 倍大小画再缩小，边缘平滑
    pad, margin = (max(w, h) * MAP_PAD, 0) if world else (0, MAP_MARGIN)
    scale = min((SIZE - margin * 2) / (w + pad * 2), (PIC_H - margin * 2) / (h + pad * 2))
    ox = (SIZE - w * scale) / 2 - min(xs) * scale
    oy = (PIC_H - h * scale) / 2 - min(ys) * scale

    def project(ring, shift=0.0):
        return [(((x + shift) * k * scale + ox) * ss, (-y * scale + oy) * ss) for x, y in ring]

    lons = (-ox / scale / k, (SIZE - ox) / scale / k)
    lats = ((oy - PIC_H) / scale, oy / scale)
    # 只画剪影时当成只有它一个国家的底图
    out = _neighbors(world or {target: rings}, target, project, lons, lats, ss)
    out = out.resize((SIZE, PIC_H), Image.Resampling.LANCZOS)
    out.info["grid"] = None
    return out


def _neighbors(world: dict, target: str, project, lons: tuple, lats: tuple, ss: int) -> Image.Image:
    """先在灰度图上按国家编号填色（0 是海、1 是题目国家），编号变化的地方就是国界和海岸线。"""
    center = (lons[0] + lons[1]) / 2
    visible = []
    for name, grings in world.items():
        parts, area = [], 0.0
        for hole, ring in grings:
            rx, ry = [p[0] for p in ring], [p[1] for p in ring]
            if not hole:
                area += (max(rx) - min(rx)) * (max(ry) - min(ry))
            # 跨 180° 经线时把整个环平移 360°，挪到离画面中心近的一边
            shift = 360 * round((center - (min(rx) + max(rx)) / 2) / 360)
            if max(rx) + shift >= lons[0] and min(rx) + shift <= lons[1] and max(ry) >= lats[0] and min(ry) <= lats[1]:
                parts.append((hole, ring, shift))
        if parts:
            visible.append((area, name, parts))

    ids = Image.new("L", (SIZE * ss, PIC_H * ss), 0)
    draw = ImageDraw.Draw(ids)
    # 大国先画、内洞填回海，小国后画：被包住的国家（莱索托、圣马力诺）才不会被盖掉
    for n, (_, name, parts) in enumerate(sorted(visible, key=lambda v: v[0], reverse=True), 2):
        color = 1 if name == target else min(n, 255)
        for hole, ring, shift in sorted(parts, key=lambda p: p[0]):
            draw.polygon(project(ring, shift), fill=0 if hole else color)

    wd, ht = ids.size
    edges = Image.new("L", ids.size, 0)
    edges.paste(ImageChops.difference(ids.crop((0, 0, wd - 1, ht)), ids.crop((1, 0, wd, ht))), (0, 0))
    down = Image.new("L", ids.size, 0)
    down.paste(ImageChops.difference(ids.crop((0, 0, wd, ht - 1)), ids.crop((0, 1, wd, ht))), (0, 0))
    edges = ImageChops.lighter(edges, down).point(lambda v: 255 if v else 0).filter(ImageFilter.MaxFilter(3))

    big = Image.new("RGB", ids.size, MAP_BG)
    big.paste(MAP_LAND, mask=ids.point(lambda v: 255 if v > 1 else 0))
    big.paste(MAP_FILL, mask=ids.point(lambda v: 255 if v == 1 else 0))
    big.paste(MAP_BG, mask=edges)
    return big


def heat(km: int) -> tuple:
    for (d0, c0), (d1, c1) in zip(HEAT, HEAT[1:]):
        if km <= d1:
            t = (km - d0) / (d1 - d0)
            return tuple(round(a + (b - a) * t) for a, b in zip(c0, c1))
    return HEAT[-1][1]


class WorldMap:
    """猜国家猜错时发的世界地图：等距圆柱投影，猜过的国家按 heat 上色，左下角放题目的剪影。

    world 同 load_outline。国家编号图（画法同 _neighbors）和国界只在第一次用时画一次，之后每次只换颜色。
    """

    def __init__(self, world: dict):
        ss, k = 2, SIZE / 360
        self.size = (SIZE * ss, round((WORLD_LAT[0] - WORLD_LAT[1]) * k) * ss)
        self.ids = Image.new("L", self.size, 0)
        draw = ImageDraw.Draw(self.ids)
        areas = {
            name: sum((max(x for x, _ in r) - min(x for x, _ in r)) * (max(y for _, y in r) - min(y for _, y in r))
                      for hole, r in rings if not hole)
            for name, rings in world.items()
        }
        self.index = {}
        for n, name in enumerate(sorted(world, key=areas.get, reverse=True), 1):
            self.index[name] = n
            for hole, ring in sorted(world[name], key=lambda r: r[0]):
                draw.polygon([((x + 180) * k * ss, (WORLD_LAT[0] - y) * k * ss) for x, y in ring], fill=0 if hole else n)
        wd, ht = self.size
        right = Image.new("L", self.size, 0)
        right.paste(ImageChops.difference(self.ids.crop((0, 0, wd - 1, ht)), self.ids.crop((1, 0, wd, ht))), (0, 0))
        down = Image.new("L", self.size, 0)
        down.paste(ImageChops.difference(self.ids.crop((0, 0, wd, ht - 1)), self.ids.crop((0, 1, wd, ht))), (0, 0))
        self.edges = ImageChops.lighter(right, down).point(lambda v: 255 if v else 0).filter(ImageFilter.MaxFilter(3))
        self.base = Image.new("RGB", self.size, MAP_BG)
        self.base.paste(MAP_LAND, mask=self.ids.point(lambda v: 255 if v else 0))

    def render(self, colors: dict, inset: Image.Image | None = None) -> Image.Image:
        """colors 是 {组名: 颜色}；inset 是题目的剪影图（load_outline 出题用的那张）。"""
        big = self.base.copy()
        for name, color in colors.items():
            if n := self.index.get(name):
                big.paste(color, mask=self.ids.point(lambda v, n=n: 255 if v == n else 0))
        big.paste(MAP_BG, mask=self.edges)
        out = Image.new("RGB", (SIZE, PIC_H), MAP_BG)
        world = big.resize((SIZE, self.size[1] // 2), Image.Resampling.LANCZOS)
        out.paste(world, (0, (PIC_H - world.height) // 2))
        if inset is not None:
            w, h = SIZE // 4, PIC_H // 4
            box = (12, PIC_H - h - 12)
            out.paste(inset.resize((w, h), Image.Resampling.LANCZOS), box)
            ImageDraw.Draw(out).rectangle((box[0] - 1, box[1] - 1, box[0] + w, box[1] + h), outline=MARK, width=2)
        out.info["grid"] = None
        return out


def quiz(question: str, answer: str = "", explanation: str = "") -> Image.Image:
    """文字题的 16:9 题面；按实际字宽换行，答案和解析仅在揭晓时传入。"""
    width = SIZE - PAD * 3

    def wrap(text, face):
        lines = []
        for paragraph in text.split("\n"):
            line = ""
            for ch in paragraph:
                if line and face.getlength(line + ch) > width:
                    lines.append(line)
                    line = ""
                line += ch
            lines.append(line)
        return lines

    # 题面不截断，长句连同答案解析一起缩小到图区内。
    for size in range(42, 17, -2):
        face = font(size)
        sections = [(wrap(question.replace("（　）", "（　　　）"), face), TEXT)]
        if answer:
            sections += [(wrap(say("quiz_answer", answer=answer), face), KEY),
                         (wrap(say("quiz_explanation", text=explanation), face), DIM)]
        height = sum(len(lines) * (size + 18) for lines, _ in sections) + 24 * (len(sections) - 1)
        if height <= PIC_H - PAD * 2:
            break
    out = Image.new("RGB", (SIZE, PIC_H), MAP_BG)
    draw = ImageDraw.Draw(out)
    y = max(PAD, (PIC_H - height) / 2)
    for lines, color in sections:
        for line in lines:
            draw.text((PAD * 1.5, y), line, fill=color, font=face, anchor="lt")
            y += size + 18
        y += 24
    out.info["grid"] = None
    return out


def compare_table(name: str, columns: tuple, rows: list, empty: str = "", inset: Image.Image | None = None) -> Image.Image:
    """盲猜的对比表：name 是第一列的表头，columns 是其余各列；rows 是 [(猜的名字, Blind.row 的结果), ...]，
    新猜的在上面，最多 9 行。还没人猜时表下写 empty。inset 是放在表右边的一小块图（peek 切出来的一格设定图）。"""
    out = Image.new("RGB", (SIZE, PIC_H), MAP_BG)
    draw = ImageDraw.Draw(out)
    head = font(24)
    m, gap, row_h = 16, 6, 56
    name_w, right = (150, SIZE - m - PEEK - 14) if inset is not None else (190, SIZE - m)
    col_w = (right - m - name_w - gap * len(columns)) / len(columns)
    xs = [m + name_w + gap + i * (col_w + gap) for i in range(len(columns))]
    draw.text((m + 8, 26), name, fill=DIM, font=head, anchor="lm")
    for x, title in zip(xs, columns):
        draw.text((x + col_w / 2, 26), fit(title, head, col_w), fill=DIM, font=head, anchor="mm")
    for i, (name, cells) in enumerate(rows[:9]):
        y = 46 + i * (row_h + gap)
        draw.rounded_rectangle((m, y, m + name_w, y + row_h), 8, fill=COVER)
        label_font = _shrink(name, name_w - 20)
        draw.text((m + 12, y + row_h / 2), fit(name, label_font, name_w - 20), fill=TEXT, font=label_font, anchor="lm")
        for x, (text, state, arrow) in zip(xs, cells):
            draw.rounded_rectangle((x, y, x + col_w, y + row_h), 8, fill=CELL[state])
            label = text + (f" {arrow}" if arrow else "")
            cell_font = _shrink(label, col_w - 10)
            draw.text((x + col_w / 2, y + row_h / 2), fit(label, cell_font, col_w - 10), fill=TEXT, font=cell_font,
                      anchor="mm")
    if not rows and empty:
        draw.text(((m + right) / 2, PIC_H / 2), empty, fill=DIM, font=font(34), anchor="mm")
    if inset is not None:
        x = SIZE - m - PEEK
        draw.text((x + PEEK / 2, 26), "设定图局部", fill=DIM, font=head, anchor="mm")
        out.paste(inset.convert("RGB").resize((PEEK, PEEK), Image.Resampling.LANCZOS), (x, 46))
        draw.rectangle((x - 1, 45, x + PEEK, 46 + PEEK), outline=MARK, width=2)
    out.info["grid"] = None
    return out


def _shrink(text: str, width: float) -> ImageFont.FreeTypeFont:
    """表格里的字放不下就缩小一号，最小 17 号，还放不下再由 fit 截断。"""
    for size in (26, 23, 20, 17):
        if font(size).getlength(text) <= width:
            break
    return font(size)


def peek(img: Image.Image) -> Image.Image:
    """从 load(contain=True) 的九宫格里随机切一格有内容的（这一格五分之一以上不是空白），给盲猜萝卜放在表旁边。"""
    x0, x1 = img.info["grid"]
    fill = img.info.get("fill") or [1.0] * 9
    i = random.choice([i for i in range(9) if fill[i] >= 0.2] or [max(range(9), key=fill.__getitem__)])
    row, col = divmod(i, 3)
    w = x1 - x0
    return img.crop((x0 + round(w * col / 3), round(PIC_H * row / 3), x0 + round(w * (col + 1) / 3), round(PIC_H * (row + 1) / 3)))


def music(clips: tuple, shown: int, label: str) -> Image.Image:
    """听歌猜番的图区：一排进度条（长短按每段秒数），已经放过的点亮，下面写这是第几段。"""
    out = Image.new("RGB", (SIZE, PIC_H), MAP_BG)
    draw = ImageDraw.Draw(out)
    draw.text((SIZE / 2, PIC_H / 2 - 120), "♪", fill=KEY, font=font(140, bold=True), anchor="mm")
    m, gap, h = 120, 10, 26
    unit = (SIZE - m * 2 - gap * (len(clips) - 1)) / sum(clips)
    x, y = m, PIC_H / 2 + 40
    for i, sec in enumerate(clips):
        w = sec * unit
        draw.rounded_rectangle((x, y, x + w, y + h), 6, fill=KEY if i < shown else COVER)
        x += w + gap
    draw.text((SIZE / 2, y + h + 60), label, fill=TEXT, font=font(36), anchor="mm")
    out.info["grid"] = None
    return out


def kline(bars: list, warm: int, shown: int, reveal: bool) -> Image.Image:
    """猜K线的图区，bars 是 [日期, 开, 收, 高, 低, 量]：前 warm 根只拿来算均线，接着 shown 根给大家看，其余是之后的走势。
    出题只画给看的那段，纵轴不标数、只写网格每格几个点，之后那段留空位打问号；答案全画出来，竖虚线隔开，纵轴写价格，
    下面标日期。纵轴范围只按画出来的那些算，出题图看不出之后涨到多高。A 股习惯红涨绿跌。"""
    s = 2  # 两倍大小画再缩小，均线不起锯齿
    w, h = SIZE * s, PIC_H * s
    out = Image.new("RGB", (w, h), K_BG)
    draw = ImageDraw.Draw(out)
    left, right, top, price_h, vol_h = 24 * s, 100 * s, 52 * s, 366 * s, 102 * s  # 上面留一条放图例
    vol_top = top + price_h + 22 * s
    step = (w - left - right) / (len(bars) - warm)
    split = left + shown * step  # 看到的和之后的分界
    drawn = range(warm, len(bars) if reveal else warm + shown)
    closes = [b[2] for b in bars]
    ma = {n: [sum(closes[i - n + 1 : i + 1]) / n if i >= n - 1 else None for i in range(len(bars))] for n, _ in K_MA}
    lo = min(min(bars[i][4] for i in drawn), *(ma[n][i] for n, _ in K_MA for i in drawn))
    hi = max(max(bars[i][3] for i in drawn), *(ma[n][i] for n, _ in K_MA for i in drawn))
    pad = (hi - lo) * 0.06 or hi * 0.01
    lo, hi = lo - pad, hi + pad

    def x(i: int) -> float:
        return left + (i - warm + 0.5) * step

    def y(p: float) -> float:
        return top + (hi - p) / (hi - lo) * price_h

    if reveal:
        draw.rectangle((split, top - 44 * s, w - right, vol_top + vol_h), fill=K_FUTURE)
    # 横向网格：答案在右边写价格。出题不标数（标在问号框旁边，「0% 到 +30%」会被当成之后的涨跌范围），
    # 网格按看到的最后一根收盘每隔整数个点画一条，图例那行写每格几个点，看得出波动多大、看不出方向
    base = closes[warm + shown - 1]
    small = font(22 * s)
    if reveal:
        gap = _nice_step(hi - lo)
        digits = 2 if hi < 100 else 1 if hi < 1000 else 0
        ticks = [(v, f"{v:.{digits}f}") for v in _ticks(lo, hi, gap)]
    else:
        gap = _nice_step((hi - lo) / base * 100)
        ticks = [(base * (1 + v / 100), "") for v in _ticks((lo / base - 1) * 100, (hi / base - 1) * 100, gap)]
    for p, label in ticks:
        draw.line((left, y(p), w - right, y(p)), fill=K_GRID, width=s)
        draw.text((w - right + 14 * s, y(p)), label, fill=DIM, font=small, anchor="lm")
    draw.line((left, vol_top - 10 * s, w - right, vol_top - 10 * s), fill=K_GRID, width=s)
    draw.text((left + 6 * s, vol_top), "成交量", fill=DIM, font=small, anchor="lt")

    body = max(s, step * 0.34)
    vmax = max(bars[i][5] for i in drawn) or 1
    for i in drawn:
        _, o, c, high, low, vol = bars[i]
        color = K_UP if c >= o else K_DOWN
        draw.line((x(i), y(high), x(i), y(low)), fill=color, width=s)
        y0, y1 = sorted((y(o), y(c)))
        draw.rectangle((x(i) - body, y0, x(i) + body, max(y1, y0 + s)), fill=color)
        # 最高只到九成，不顶到左上角的「成交量」
        draw.rectangle((x(i) - body, vol_top + vol_h * (1 - 0.9 * vol / vmax), x(i) + body, vol_top + vol_h), fill=color)
    for n, color in K_MA:
        draw.line([(x(i), y(ma[n][i])) for i in drawn], fill=color, width=2 * s, joint="curve")

    if reveal:
        _dashed(draw, split, top - 44 * s, vol_top + vol_h, TEXT, s)
        draw.text((split + 10 * s, top - 26 * s), "你们看到这里", fill=TEXT, font=small, anchor="lm")
        dates = font(20 * s)
        y_date = vol_top + vol_h + 10 * s
        draw.text((left, y_date), bars[warm][0], fill=DIM, font=dates, anchor="lt")
        draw.text((split, y_date), bars[warm + shown - 1][0], fill=DIM, font=dates, anchor="mt")
        draw.text((w - right, y_date), bars[-1][0], fill=DIM, font=dates, anchor="rt")
    else:
        x0, x1, y1 = split + 10 * s, w - left, vol_top + vol_h
        for xx in (x0, x1):
            _dashed(draw, xx, top, y1, MARK, s)
        for yy in (top, y1):
            for k in range(int((x1 - x0) // (16 * s)) + 1):
                draw.line((x0 + k * 16 * s, yy, min(x1, x0 + k * 16 * s + 8 * s), yy), fill=MARK, width=s)
        draw.text(((x0 + x1) / 2, (top + y1) / 2), "?", fill=TEXT, font=font(120 * s, bold=True), anchor="mm")

    lx = left + 8 * s
    for n, color in K_MA:
        draw.line((lx, top - 26 * s, lx + 26 * s, top - 26 * s), fill=color, width=3 * s)
        draw.text((lx + 34 * s, top - 26 * s), f"MA{n}", fill=DIM, font=small, anchor="lm")
        lx += 120 * s
    if not reveal:
        draw.text((lx, top - 26 * s), f"网格每格 {gap:g}%", fill=DIM, font=small, anchor="lm")
    out = out.resize((SIZE, PIC_H), Image.LANCZOS)
    out.info["grid"] = None
    return out


def _nice_step(span: float, n: int = 6) -> float:
    """把 span 分成不超过 n 格的整齐刻度：1、2、2.5、5 乘 10 的幂。"""
    raw = span / n if span > 0 else 1
    mag = 10 ** math.floor(math.log10(raw))
    return next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)


def _ticks(lo: float, hi: float, step: float) -> list[float]:
    return [k * step for k in range(math.ceil(lo / step), math.floor(hi / step) + 1)]


def _dashed(draw: ImageDraw.ImageDraw, x: float, y0: float, y1: float, color: tuple, s: int) -> None:
    """竖虚线。"""
    yy = y0
    while yy < y1:
        draw.line((x, yy, x, min(y1, yy + 8 * s)), fill=color, width=s)
        yy += 16 * s


def _cover_fit(img: Image.Image, width: int, height: int, upper: float = 0.5) -> Image.Image:
    """按目标比例裁切再缩放，不拉伸。左右居中；上下裁时 upper 是裁掉的部分有多少在上面，0.5 是居中。"""
    w, h = img.size
    if w * height > h * width:
        cw, ch = round(h * width / height), h
    else:
        cw, ch = w, round(w * height / width)
    left, top = (w - cw) // 2, round((h - ch) * upper)
    return img.crop((left, top, left + cw, top + ch)).resize((width, height), Image.Resampling.LANCZOS)


def _contain(img: Image.Image) -> Image.Image:
    """裁掉透明边后整张缩进图区正中的正方形（浅色底），两边留黑，九宫格只画在这个正方形上。

    立绘四角常是空白，info["empty"] 记下几乎没内容的格子，出题时最后才揭开。
    不透明的白底设定图（猜萝卜）把接近白色的地方当成空白来裁边、找空白格，底板也用白色。
    """
    art = img.convert("RGBA")
    if art.getchannel("A").getextrema()[0] == 255:
        mask = ImageChops.invert(art.convert("L")).point(lambda v: 255 if v > 18 else 0)
        bg = (255, 255, 255)
    else:
        mask, bg = art.getchannel("A"), ART_BG
    box = mask.getbbox() or (0, 0, *art.size)
    art, mask = art.crop(box), mask.crop(box)
    scale = min((PIC_H - ART_PAD * 2) / art.width, (PIC_H - ART_PAD * 2) / art.height)
    size = (max(1, round(art.width * scale)), max(1, round(art.height * scale)))
    art, mask = art.resize(size, Image.Resampling.LANCZOS), mask.resize(size, Image.Resampling.LANCZOS)
    offset = ((PIC_H - art.width) // 2, (PIC_H - art.height) // 2)
    board = Image.new("RGB", (PIC_H, PIC_H), bg)
    board.paste(art, offset, art)
    alpha = Image.new("L", (PIC_H, PIC_H), 0)
    alpha.paste(mask, offset)
    out = Image.new("RGB", (SIZE, PIC_H), BG)
    x0 = (SIZE - PIC_H) // 2
    out.paste(board, (x0, 0))
    out.info["grid"] = (x0, x0 + PIC_H)
    out.info["empty"], out.info["fill"] = set(), []  # fill 是每一格有内容的比例，peek 用
    for i in range(9):
        row, col = divmod(i, 3)
        cell = alpha.crop([round(PIC_H * k / 3) for k in (col, row, col + 1, row + 1)])
        out.info["fill"].append(sum(cell.histogram()[32:]) / (cell.width * cell.height))
        if out.info["fill"][-1] < 0.03:
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
    最后一个格位放 note。每条提示是 [名目, 内容]，或者 [名目, 内容, 色块颜色]（见 _hint）。
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
    for (x, y), hint in zip(slots, hints):
        _hint(draw, x, y, *hint)
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


def _hint(draw: ImageDraw.ImageDraw, x: float, y: float, key: str, value: str, swatch: tuple | None = None) -> None:
    """一条提示：黄色的名目加白色的内容；swatch 是名目前面的小色块（世界地图上这个国家的颜色）。"""
    key_font, value_font = font(30, bold=True), font(30)
    end = x + HALF
    if swatch:
        draw.rounded_rectangle((x, y + 6, x + 24, y + 30), 5, fill=swatch)
        x += 36
    key = fit(key, key_font, end - x - 120)
    draw.text((x, y), key, fill=KEY, font=key_font, anchor="lt")
    vx = x + key_font.getlength(key) + 14
    draw.text((vx, y), fit(value, value_font, end - vx), fill=TEXT, font=value_font, anchor="lt")


def encode(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=88)
    return base64.b64encode(buf.getvalue()).decode()
