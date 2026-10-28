"""猜群友：挑人、拉 QQ 头像、定好每段露哪几格、认群友说的是谁。

群成员每次开题现拉（OneBot 的 get_group_member_list）；头像只在内存里出图，不存盘。
"""

import hashlib
import io
import math
import random
import statistics
import time

import aiohttp
from PIL import Image

from .answer import norm

# QQ 公开头像接口，免登录；第一个拉不到再试第二个
AVATARS = ("https://q1.qlogo.cn/g?b=qq&nk={}&s=640", "https://q.qlogo.cn/headimg_dl?dst_uin={}&spec=640")
# 四段：(棋盘边长, 露出几格)，越往后格子越大
STAGES = ((5, 1), (5, 2), (4, 1), (3, 1))
LABELS = ("1/25", "2/25", "1/16", "1/9")
ACTIVE_DAYS = 30  # 优先出这么多天内在群里说过话的人
MIN_ACTIVE = 8  # 活跃的人比这少就从全体里挑
TRIES = 12  # 最多试这么多人的头像，都不行（默认头像、纯色）就不出了

# 同一张图出现在两个号上 = 默认头像。只记在内存里，重启后重新攒
_owners: dict[str, set[str]] = {}


def display_name(m: dict) -> str:
    return str(m.get("card") or m.get("nickname") or m.get("user_id") or "").strip()


def pick(members: list[dict], exclude: set[str]) -> list[dict]:
    """可以出题的群友，打乱顺序：最近说过话的优先。"""
    pool = [m for m in members if str(m.get("user_id")) not in exclude]
    since = time.time() - ACTIVE_DAYS * 86400
    active = [m for m in pool if (m.get("last_sent_time") or 0) > since]
    pool = active if len(active) >= MIN_ACTIVE else pool
    return random.sample(pool, len(pool))


async def avatar(session: aiohttp.ClientSession, uid: str) -> Image.Image | None:
    """群友的头像，裁成正方形；默认头像、太小、几乎纯色的返回 None。"""
    for url in AVATARS:
        try:
            async with session.get(url.format(uid), timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status != 200:
                    continue
                data = await r.read()
        except (aiohttp.ClientError, TimeoutError):
            continue
        if len(data) < 1500:
            continue
        owners = _owners.setdefault(hashlib.md5(data).hexdigest(), set())
        owners.add(uid)
        if len(owners) > 1:
            return None
        try:
            img = Image.open(io.BytesIO(data)).convert("RGB")
        except OSError:
            continue
        side = min(img.size)
        if side < 100:
            return None
        left, top = (img.width - side) // 2, (img.height - side) // 2
        img = img.crop((left, top, left + side, top + side)).resize((600, 600), Image.Resampling.LANCZOS)
        return img if _std(img) >= 12 else None
    return None


def _std(img: Image.Image) -> float:
    return statistics.pstdev(img.convert("L").resize((40, 40)).getdata())


def _cells(img: Image.Image, grid: int) -> list[int]:
    """切成 grid×grid，挑看得出东西的格子（灰度起伏够大），纯色背景的不露；都不够就取起伏最大的几格。"""
    step = img.width / grid
    scored = []
    for i in range(grid * grid):
        row, col = divmod(i, grid)
        box = (round(col * step), round(row * step), round((col + 1) * step), round((row + 1) * step))
        scored.append((_std(img.crop(box)), i))
    good = [i for s, i in scored if s >= 18]
    return good or [i for _, i in sorted(scored, reverse=True)[:grid]]


def plan(img: Image.Image) -> list[tuple[int, list[int]]]:
    """开题时就定好四段各露哪几格：[(棋盘边长, [格子编号]), ...]。第二段在第一段的基础上再多露一格。"""
    out = []
    for grid, count in STAGES:
        cells = _cells(img, grid)
        if out and out[-1][0] == grid:
            prev = out[-1][1]
            extra = [i for i in cells if i not in prev] or [i for i in range(grid * grid) if i not in prev]
            out.append((grid, prev + random.sample(extra, count - len(prev))))
        else:
            out.append((grid, random.sample(cells, min(count, len(cells)))))
    return out


class Roster:
    """开题那一刻的群成员快照，用来认一句话说的是谁：@ 了谁、QQ 号、群名片或昵称，
    或者名片 / 昵称里只属于一个人的一段（至少两个字，且不少于名字的四成，免得「可以」「什么」也算猜了人）。"""

    def __init__(self, members: list[dict]):
        self.names = {str(m["user_id"]): {n for n in (norm(m.get("card") or ""), norm(m.get("nickname") or "")) if n}
                      for m in members if m.get("user_id")}

    def find(self, text: str, ats: list[str], prefer: str) -> str | None:
        if ats := [a for a in ats if a in self.names]:
            return prefer if prefer in ats else ats[0]
        g = norm(text)
        if not g or len(g) > 30:
            return None
        if g.isdigit():
            return g if g in self.names else None
        exact = [uid for uid, names in self.names.items() if g in names]
        if exact:
            return prefer if prefer in exact else exact[0]
        holders = [uid for uid, names in self.names.items() if any(g in n for n in names)]
        if len(g) < 2 or len(holders) != 1:
            return None
        uid = holders[0]
        return uid if any(g in n and len(g) >= math.ceil(len(n) * 0.4) for n in self.names[uid]) else None
