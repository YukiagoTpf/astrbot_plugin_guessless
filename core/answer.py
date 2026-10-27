"""答案归一化与判定。"""

import math
import re
import unicodedata

from zhconv import convert

# 主标题和副标题之间的分隔符，用来生成「只说主标题也算对」的候选
_SEP = re.compile(r"[:：\-–—~～|/／]")
_TRAILING_NUM = re.compile(r"(\d+|[ivx]+)$")


def norm(text: str) -> str:
    """全半角统一、繁转简、转小写、去掉空格和标点。"""
    text = convert(unicodedata.normalize("NFKC", text), "zh-hans").lower()
    return "".join(ch for ch in text if ch.isalnum())


def candidates(names: list[str]) -> set[str]:
    out = set()
    for name in names:
        if not name:
            continue
        full = norm(name)
        if full:
            out.add(full)
        head = norm(_SEP.split(name, 1)[0])
        if head and head != full:
            out.add(head)
            # 「荒野大镖客：救赎2」→ 也接受「荒野大镖客2」
            m = _TRAILING_NUM.search(full)
            if m and not head.endswith(m.group(1)):
                out.add(head + m.group(1))
    return out


def is_correct(guess: str, cands: set[str]) -> bool:
    g = norm(guess)
    return bool(g) and _hit(g, cands)


def find(guess: str, index: list[tuple[str, set[str]]], prefer: str = "") -> str | None:
    """这句话说的是 index [(键, 候选答案), ...] 里的哪一个，都不沾边（闲聊）返回 None。

    完全说中的优先，「几内亚」就不会因为是「赤道几内亚」的一大段被认成后者；
    几个都完全说中（同一个别名）时优先 prefer，一般是本题答案。
    """
    g = norm(guess)
    if not g:
        return None
    exact = [key for key, cands in index if g in cands]
    if exact:
        return prefer if prefer in exact else exact[0]
    return next((key for key, cands in index if _hit(g, cands)), None)


def _hit(g: str, cands: set[str]) -> bool:
    for c in cands:
        if g == c:
            return True
        # 说出标题的一大段也算对（「旷野之息」→ 塞尔达传说 旷野之息）；短标题必须说全
        if len(c) >= 5 and len(g) >= 3 and g in c and len(g) >= math.ceil(len(c) * 0.4):
            return True
        # 句子里包含完整标题（「是不是极乐迪斯科」）
        if len(c) >= 4 and c in g:
            return True
    return False
