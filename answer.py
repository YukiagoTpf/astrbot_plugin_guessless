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
    if not g:
        return False
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
