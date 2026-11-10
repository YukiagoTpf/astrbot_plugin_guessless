"""猜地点匹配：同一片地区四个国家的街景各一张（2×2，标图 1–4），下面列出这四个国家（A–D，打乱），
按图号说出各是哪个，比如「BADC」。群友提的「四张背景相仿的图，顺序匹配」。猜日本地点匹配一样，换成同一地方的四个都道府县。

街景用猜地点 / 猜日本地点的题库（每国、每县几个全景挑一个，朝向也用它的），不单独建题库。四个从同一组地区里挑
（GROUPS，按 geo.REGIONS / 日本的八地方合并：凑不够四个的地区并到相邻的），背景才像。猜错回对了几个；「提示」给一张图的
一条线索（国家是语言、货币、美食、名胜，日本是特产、名胜，挑另外三个没有的），最多 CLUES 条。纯函数，不联网。
"""

import random
import re
import unicodedata

from . import geo
from .answer import norm

# 一组地区里挑四个；只有两三个国家有街景的地区并进相邻的。日本按八地方（geo.JP_REGIONS），北海道只有一个、
# 四国正好四个（每道都是那四县），分别并进东北、中国地方
GROUPS = {
    "geomatch": (
        ("东亚", "中亚"), ("东南亚",), ("南亚",), ("西亚", "北非"), ("北欧",), ("西欧",), ("中欧",), ("南欧",), ("东欧",),
        ("西非", "中非", "东非"), ("南部非洲", "澳新", "太平洋岛国"), ("北美", "中美洲", "加勒比"), ("南美",),
    ),
    "jpgeomatch": (("北海道", "东北"), ("关东",), ("中部",), ("近畿",), ("中国地方", "四国"), ("九州",)),
}
# 以色列、巴勒斯坦不出：两个放进一道题里对着认，容易把群聊带到政治上（只能加不能删）
SKIP = {"IL", "PS"}
COUNT = 4
LETTERS = "ABCD"
CLUES = 3  # 「提示」最多给几条（几张图各一条），全给了等于把答案说了
# 一张图的线索先挑哪种。日本不给「县厅所在地」：只说首字，青森、长崎这些和县名同一个字开头
CLUE_KINDS = {"geomatch": ("主要语言", "货币", "美食", "名胜"), "jpgeomatch": ("特产", "名胜")}
SHOT = (640, 360)  # 一张街景要的尺寸；按张计费，大小不影响价钱
SEP = re.compile(r"[\s,，、.。/|\\\-—>→=:：;；]+")


def region(cat: str, entry: dict) -> str:
    """条目在哪个地区：国家看 geo.REGIONS，都道府县看提示里的「地方」。"""
    if cat == "jpgeomatch":  # 北海道的「地方」就是它自己，题库里当泄露答案去掉了
        return next((v for k, v in entry["hints"] if k == "地方"), "北海道")
    return geo.REGION_OF.get(entry["group"], ("",))[0]


def groups(cat: str, entries: list[dict]) -> dict[str, list[str]]:
    """能出题的地区组：{组名: 有街景的国家 / 都道府县代码}，凑不够 COUNT 个的不要。entries 是猜地点 / 猜日本地点的题目。"""
    have: dict[str, set] = {}
    for e in entries:
        if e["group"] not in SKIP:
            have.setdefault(region(cat, e), set()).add(e["group"])
    out = {}
    for names in GROUPS[cat]:
        codes = sorted({cc for n in names for cc in have.get(n, ())})
        if len(codes) >= COUNT:
            out["·".join(names)] = codes
    return out


def pick(cat: str, entries: list[dict], recent) -> dict:
    """出一道：从最近出得最少的地区组里随机挑一组（recent 是最近出过的组名，从旧到新），组里随机四个，每个随机一个全景。
    返回题目条目：spots 是四个题库条目（图 1–4 的顺序），key 是答案「BADC」，options 是 A–D 那四行，
    clues 是「提示」依次给的线索，hints 是答案卡上图 1–4 各是哪国（县）、在哪个城镇附近。"""
    pool = groups(cat, entries)
    if not pool:
        raise RuntimeError("题库里没有哪片地区凑得够四个")
    last = {g: i for i, g in enumerate(recent)}
    oldest = min(last.get(g, -1) for g in pool)
    group = random.choice([g for g in pool if last.get(g, -1) == oldest])
    codes = random.sample(pool[group], COUNT)
    spots = [random.choice([e for e in entries if e["group"] == cc]) for cc in codes]
    order = random.sample(codes, COUNT)  # A–D 是哪四个
    key = "".join(LETTERS[order.index(cc)] for cc in codes)
    title = {e["group"]: e["title"] for e in spots}
    return {
        "id": f"{cat}-" + "-".join(e["id"].split("-", 1)[1] for e in spots), "group": group, "title": key, "original": key,
        "answer": f"{key}（" + "、".join(f"{i} {e['title']}" for i, e in enumerate(spots, 1)) + "）",
        "names": [], "key": key, "spots": spots,
        "options": [[letter, title[cc]] for letter, cc in zip(LETTERS, order)],
        "clues": clues(spots, CLUE_KINDS[cat]),
        "hints": [[f"图{i}", f"{k} {e['title']} · {e['original'].split(' · ')[-1]}"]
                  for i, (k, e) in enumerate(zip(key, spots), 1)],
    }


def clues(spots: list[dict], kinds: tuple) -> list[list[str]]:
    """随机 CLUES 张图各一条线索：[「图几」, 「种类 内容」]。按 kinds 的顺序挑另外三个没有的（北欧几国的货币都是克朗，
    说了等于没说），带了四个里哪个的名字的不要（都道府县连「爱知」这种省了「县」的也算）。题库的提示里已经去掉了它自己的名字。"""
    names = {n for e in spots for x in e["names"] if not x.isascii() and len(n := norm(x)) >= 2}
    out = []
    for i in random.sample(range(COUNT), CLUES):
        others = {v for j, e in enumerate(spots) if j != i for _, v in e["hints"]}
        hit = next((
            f"{kind} {v}" for kind in kinds for k, v in spots[i]["hints"]
            if k == kind and v not in others and not any(n in norm(v) for n in names)
        ), None)
        if hit:
            out.append([f"图{i + 1}", hit])
    return out


def parse(text: str) -> str | None:
    """群友这句是不是一组答案：四个字母「BADC」（图 1–4 依次是哪个，大小写、空格、逗号随意），或者带图号的
    「1B 2A 3D 4C」（顺序随意）。四个字母要各不相同；别的都当闲聊，返回 None。"""
    s = SEP.sub("", unicodedata.normalize("NFKC", text)).upper()
    if re.fullmatch(r"[ABCD]{4}", s):
        letters = s
    elif re.fullmatch(r"(?:[1-4][ABCD]){4}", s):
        pairs = dict(re.findall(r"([1-4])([ABCD])", s))
        if len(pairs) < COUNT:
            return None
        letters = "".join(pairs[str(i)] for i in range(1, COUNT + 1))
    else:
        return None
    return letters if len(set(letters)) == COUNT else None


def right(guess: str, key: str) -> int:
    """对了几个（图号和字母对上的）。"""
    return sum(a == b for a, b in zip(guess, key))
