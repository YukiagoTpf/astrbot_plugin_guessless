"""盲猜魔兽Boss：猜一个魔兽世界冒险指南里的首领，对比资料片、副本、类型、顺序、大陆。

首领资料来自游戏数据表（wow.fetch_bosses，wago.tools 导出的 DB2 CSV），简中名是国服的官方译名，缓存一个月
（wow_bosses.json）。冒险指南里的首领都能猜（一千一百多个）；出题的是团本首领和地下城的最后一个首领，几个人一起打的、
副本区域和事件（「风之议会」「天启四骑士」「潘达利亚战利品」「歌剧院」，NOT_ONE）不出。巫妖王之怒和以前的团本首领是
大众题。同名的首领算一个（熔火之心和火焰之地的拉格纳罗斯）：说名字就算对，猜错时按资料片早的那个比。
"""

import re
import time

from .. import wow
from ..answer import norm
from ..entries import usable_names
from ..sources import dedupe
from ..store import load_json, save_json
from .base import Blind, blank, entry, num, same

VERSION = "journal-v1"  # 选题（NOT_ONE、MAIN_UNTIL）、条目格式、提示规则、叫法规则改了就换个名字，题库重建
COLUMNS = ("资料片", "副本", "类型", "顺序", "大陆")
CLUES = ("地区", "技能", "同副本", "首字")
MAIN_UNTIL = 2  # 巫妖王之怒和以前的团本首领是大众题
# 不出题的：几个首领一起打的、副本区域和事件
NOT_ONE = re.compile(
    r"议会|战利品|双子|双王|双天|三宝|七贤|四骑士|冠军|猛兽|兵团|教团|秘党|法庭|审判庭|女巫会|姐妹|干将|恶犬|[和与]|"
    r"之魂|意志|背脊|末日|降临|之匣|之冕|之庭|外壳|炮舰战|阻击战|系统|广场|花园|入口|宿舍|歌剧院|象棋|逃离|正门"
)
# 名字前后的头衔，去掉的也算：「管理者埃克索图斯」说「埃克索图斯」，「迦顿男爵」说「迦顿」，「不稳定的海度斯」说「海度斯」
TITLES = (
    "检察官|督军|领主|主母|女王|亲王|王子|公主|女勋爵|女士|勋爵|教官|药剂师|传令官|大主教|主教|酋长|先知|统帅|将军|男爵|"
    "大王|国王|女皇|皇帝|大帝|指挥官|审判官|教授|总大使|大使|中士|狱长"
)
TITLE_HEAD = re.compile(rf"^(?:.{{1,3}}者(?!的)|(?:大|高阶|深水|天灾|鲜血)?(?:{TITLES}))(?=..)")
TITLE_TAIL = re.compile(rf"(?<=..)(?:{TITLES})$")
# 「不稳定的海度斯」去掉「不稳定的」：英文是「名字 the 形容词」才算，「哈卡的化身」「联盟的冠军」去掉了只剩个普通词
EPITHET = re.compile(r"^.{1,3}的(?=..)")
EPITHET_EN = re.compile(r"^[A-Z][\w'’-]* the [A-Z][\w'’-]*$")
ALIASES = {"巫妖王": ["阿尔萨斯"], "疯狂的死亡之翼": ["死亡之翼"]}  # 冒险指南的名字以外大家常叫的


def short_names(name: str, en: str = "") -> list[str]:
    """去掉头衔、只说半截的叫法：「伊利丹·怒风」说「伊利丹」，「达格兰·索瑞森大帝」说「索瑞森」，
    「奥玛斯，缚魂者」说「奥玛斯」。几个首领一起打的、事件（NOT_ONE）没有；剩下的只是头衔的（「亡语者女士」去掉
    「亡语者」剩「女士」）、「之」开头的、带「和」「与」的不算。"""
    if NOT_ONE.search(name):
        return []
    head = re.split(r"[，,]", name)[0]
    bases = [head, *(re.split(r"[·・]", head) if re.search(r"[·・]", head) else [])]
    out = [b for b in bases if b != name]
    out += [TITLE_HEAD.sub("", b) for b in bases] + [TITLE_TAIL.sub("", b) for b in bases]
    if EPITHET_EN.match(en):
        out.append(EPITHET.sub("", head))
    return [n for n in dedupe(out) if n != name and len(n) >= 2 and not re.fullmatch(f"(?:{TITLES})", n)
            and not re.match("[之的]", n) and not re.search("[和与]", n)]


def attrs_of(b: dict, key: str) -> dict:
    """对比用的资料，见 row。副本名只留逗号前的（「阿梅达希尔，梦境之愿」写「阿梅达希尔」）。"""
    return {
        "key": key, "exp": b["expansion"], "iid": b["instance_id"], "instance": re.split(r"[，,]", b["instance"])[0],
        "zone": b["zone"], "kind": b["kind"], "order": b["order"], "count": b["count"], "continent": b["continent"],
    }


def row(guess: dict, answer: dict) -> list[list[str]]:
    """资料片：同一个绿、差一个黄，箭头指答案更新（↑）还是更老（↓）。副本：同一个绿，入口在同一个地区（都在黑石山）黄。
    类型：团本、地下城、野外。顺序：在副本里第几个打（「3/8」是八个里的第三个），连总数都一样才绿，第几个一样、总数
    不一样（答案「1/10」猜了「1/1」）或差一个黄，箭头指答案更靠后（↑）还是更靠前（↓）；野外首领写「—」。
    大陆：副本入口所在的大陆。"""
    x, y = guess["exp"], answer["exp"]
    if guess["iid"] == answer["iid"]:
        place = [guess["instance"], "hit", ""]
    else:
        place = [guess["instance"], "near" if guess["zone"] and guess["zone"] == answer["zone"] else "miss", ""]
    m, n = guess["order"], answer["order"]
    if m and n:
        text = f"{m}/{guess['count']}"
        order = num(m, n, text, abs(m - n) == 1)
        if m == n and guess["count"] != answer["count"]:
            order = [text, "near", ""]
    else:
        order = blank()
    return [
        num(x, y, wow.EXPANSIONS[x], abs(x - y) == 1),
        place,
        same(guess["kind"], answer["kind"]),
        order,
        same(guess["continent"], answer["continent"]),
    ]


def skill_hint(skills: list[str], words: list[str]) -> str:
    """冒险指南里列的两个技能，带首领名字的（「拉格纳罗斯之怒」「范克里夫的复仇」）不要。"""
    return "、".join([s for s in skills if not any(w in s for w in words)][:2])


async def build(bank) -> None:
    now, path = time.time(), bank.dir / "wow_bosses.json"
    cached = load_json(path) or {}
    if now - cached.get("at", 0) > wow.CACHE_DAYS * 86400:
        bank.status["bossdle"] = "正在从 wago.tools 拉冒险指南"
        cached = {"at": now, "bosses": await wow.fetch_bosses(bank.http())}
        save_json(path, cached)
    bosses = cached["bosses"]  # 按资料片、副本、顺序排好的
    group: dict[str, str] = {}  # 名字 -> 同名的算一个，键是先出现的那个
    for b in bosses:
        group.setdefault(norm(b["zh"]), f"boss-{b['id']}")
    members: dict[str, list[dict]] = {}
    for b in bosses:
        members.setdefault(group[norm(b["zh"])], []).append(b)
    full = {k: usable_names([n for b in ms for n in (b["zh"], b["en"], b["tw"])] + ALIASES.get(ms[0]["zh"], []))
            for k, ms in members.items()}
    taken = {norm(n) for ns in full.values() for n in ns}
    owner: dict[str, str] = {}  # 半截的叫法只归先出现的那个，也不能是别的首领的全名
    names = {}
    for k, ms in members.items():
        shorts = [n for n in usable_names(short_names(ms[0]["zh"], ms[0]["en"]))
                  if norm(n) not in taken and owner.setdefault(norm(n), k) == k]
        names[k] = full[k] + shorts
    places = {k: {"title": ms[0]["zh"], "names": names[k], "attrs": attrs_of(ms[0], k)} for k, ms in members.items()}
    entries = []
    for b in bosses:
        k = group[norm(b["zh"])]
        if not (b["kind"] == "团本" or b["kind"] == "地下城" and b["last"]) or NOT_ONE.search(b["zh"]):
            continue
        if not b["continent"] or norm(b["zh"]) not in {norm(n) for n in names[k]}:
            continue
        words = [n for n in names[k] if not n.isascii()]
        mate = next((m for m in b["mates"] if norm(m) != norm(b["zh"])), "")
        hints = [["地区", b["zone"]], ["技能", skill_hint(b["skills"], words)], ["同副本", mate]]
        e = entry("wb", b["id"], b["zh"], b["en"], f"{b['zh']}（{b['instance']}）", names[k], hints, attrs_of(b, k),
                  b["kind"] == "团本" and b["expansion"] <= MAIN_UNTIL)
        e["group"] = k  # 同名的首领算一个（Bank.match 按 places 的键认）
        entries.append(e)
    bank.commit("bossdle", entries, places=places)


MODE = Blind(
    cat="bossdle", command="盲猜魔兽Boss", desc="不给图，猜一个魔兽世界的副本首领，看资料片、副本、顺序对比", header="Boss",
    columns=COLUMNS, clues=CLUES, row=row, build=build, settings=(VERSION,), exact=True,
    aliases=("盲猜魔兽boss", "盲猜魔兽BOSS", "盲猜魔兽首领", "盲猜boss", "盲猜Boss", "盲猜BOSS", "猜魔兽Boss", "猜魔兽boss",
             "猜魔兽首领"),
)
