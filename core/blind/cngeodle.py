"""盲猜中国地点：猜一个地级市，对比省份、方位、人口、面积、级别、是不是省会。

Wikidata 里的地级行政区和直辖市都能猜，只出地级市（含副省级市），中文维基浏览量前 MAIN 个是大众题。
名胜从 5A 景区里挑，查不到就只少这一条提示，一小时后再查。三沙市不出、不认（geo.CN_EXCLUDE）。
"""

import asyncio
import re

import aiohttp

from astrbot.api import logger

from .. import geo
from ..entries import assign_names, scrub, usable_names
from .base import Blind, away, entry, here, size, tiered, yes_no

VERSION = "prefs-v1"  # 选题、条目格式、提示规则（CN_SIGHT_BLOCK）改了就换个名字，题库重建
COLUMNS = ("省份", "方位", "人口", "面积", "级别", "省会")
CLUES = ("区号", "车牌", "附近", "名胜", "首字")
MAIN = 100  # 中文维基浏览量前 100 个地级市是大众题
NEAR_KM = 100  # 相距这么近以内（东部挨着的两个市多在这个距离），方位格标黄
# 自治州名里的民族，去掉才是平时的叫法（「大理白族自治州」→「大理」）；蒙古、柯尔克孜、哈萨克不带「族」
CN_ETHNIC = "朝鲜|土家|苗|侗|布依|彝|哈尼|壮|傣|白|景颇|傈僳|藏|羌|回|蒙古|柯尔克孜|哈萨克"
# 名胜不要红色景点、陵墓、领导人和战争相关的，也不要广场、纪念馆这类（只能加不能删）
CN_SIGHT_BLOCK = re.compile(
    r"革命|红色|红旗|红军|长征|烈士|纪念|故居|旧址|会址|会议|抗日|抗战|侵华|屠杀|战|军|兵|核|导弹|发射|航天|卫星|基地|"
    r"监狱|陵|墓|坟|冢|广场|政府|党|主席|领袖|毛泽东|周恩来|邓小平|朱德|刘少奇|孙中山|中山陵|井冈山|西柏坡|韶山|"
    r"延安|遵义|瑞金|古田|沙家浜|南泥湾|南湖|雨花台|花明楼|台儿庄|刘公岛|伪满|皇宫|达赖|班禅|活佛|事件|灾|地震|大学"
)


def cn_names(m: dict) -> tuple[str, list[str]]:
    """地级行政区的 (表里写的名字, 能猜的叫法)。去掉「市」「地区」「盟」也算（「苏州」「喀什」）；
    自治州写成「大理州」，说「大理州」「大理」都算，但只说「海南」不算海南州，那是省。"""
    zh = m["zh"]
    if m["level"] == "自治州" and (found := re.match(f"^(.+?)(?:(?:{CN_ETHNIC})族?)+自治州$", zh)):
        title, bases = found[1] + "州", [found[1] + "州", found[1]]
    else:
        title, bases = zh, [re.sub("(?:市|地区|盟)$", "", zh)]
    provinces = {name for name, _ in geo.CN_PROVINCES.values()}
    if m["level"] in ("自治州", "地区", "盟"):
        bases = [b for b in bases if b not in provinces]
    return title, usable_names([zh, *bases])


def cn_sights(scenic: list[dict], prefs: dict) -> dict[str, list[str]]:
    """5A 景区归到地级行政区，{键: [景区名, ...]}，按语言版本数从多到少。先看往上三层的行政区划代码（县的代码前四位
    就是所属的市），再看这几层里有没有哪个市；都没有的（庐山、衡山只有坐标）看离哪个市最近：50 公里内、
    第二近的远出一半以上才算。赤水丹霞离泸州比离遵义近，放宽了就会归错，挂到省的只在省内找。"""
    by_code = {m["code"]: k for k, m in prefs.items()}
    out: dict[str, list[str]] = {}
    for s in sorted(scenic, key=lambda s: -s["n"]):
        owner = next((by_code[c[:4]] for c in s["codes"] if c[:4] in by_code), None)
        owner = owner or next((q for q in s["up"] if q in prefs), None)
        if not owner and s["spot"]:
            provs = {c[:2] for c in s["codes"]}
            near = sorted(
                (geo.distance([[m["lat"], m["lng"]]], s["spot"]), k) for k, m in prefs.items()
                if not provs or m["prov"] in provs
            )[:2]
            if len(near) == 2 and near[0][0] <= 50 and near[1][0] >= near[0][0] * 1.5:
                owner = near[0][1]
        if owner:
            out.setdefault(owner, []).append(s["zh"])
    return out


def cn_sight_hint(sights: list[str], words: list[str], title: str) -> str:
    """挑一个名胜：名字不碰 CN_SIGHT_BLOCK，也不带答案的地名（「苏州园林」盖掉就只剩「园林」了）。"""
    for name in sights:
        name = re.sub(r"\s*[（(][^）)]*[）)]$", "", name)
        name = name[:-1] if name.endswith("镇镇") else name  # 乌镇的条目叫「乌镇镇」
        if name and not CN_SIGHT_BLOCK.search(name) and scrub(name, words, title) == name:
            return name
    return ""


def attrs_of(key: str, m: dict) -> dict:
    """对比用的资料，见 row。直辖市不分省会不省会，记 None。"""
    prov, region = geo.CN_PROVINCES[m["prov"]]
    return {
        "key": key, "prov": prov, "region": region, "pop": m["pop"], "area": m["area"], "level": m["level"],
        "rank": geo.CN_RANKS[m["level"]], "capital": None if m["level"] == "直辖市" else m["capital"],
        "spot": [m["lat"], m["lng"]],
    }


def row(guess: dict, answer: dict) -> list[list[str]]:
    """省份：同省绿、同大区黄。方位：两地相距多远，NEAR_KM 以内标黄，箭头是从猜的地方看答案在哪个方向
    （Wikidata 的接壤关系只有三成的市有，不写「接壤」）。人口、面积同盲猜地点。
    级别：地级（地级市、自治州、地区、盟）< 副省级市 < 直辖市，一样的绿；不一样的红，级别不同带箭头。
    省会：是不是省会，直辖市写「—」。"""
    if guess["key"] == answer["key"]:
        place = here()
    else:
        km = geo.distance([guess["spot"]], answer["spot"])
        place = away(km, NEAR_KM, geo.arrow(guess["spot"], answer["spot"]))
    if guess["level"] == answer["level"]:
        level = [guess["level"], "hit", ""]
    else:
        way = "" if guess["rank"] == answer["rank"] else "↑" if answer["rank"] > guess["rank"] else "↓"
        level = [guess["level"], "miss", way]
    return [
        tiered(guess["prov"], answer["prov"], guess["region"] == answer["region"]),
        place,
        *size(guess, answer),
        level,
        yes_no(guess["capital"], answer["capital"]),
    ]


async def build(bank) -> None:
    session = bank.http()
    bank.status["cngeodle"] = "正在查中国的地级市"
    prefs = await geo.fetch_cn_prefectures(session)
    views = await bank.views("cngeodle", [m["wiki"] for m in prefs.values()], monthly=True)
    fame = {k: views.get(m["wiki"], 0) for k, m in prefs.items()}
    order = sorted(prefs, key=lambda k: (-fame[k], -prefs[k]["pop"]))
    titles, names = {}, {}
    for k in order:
        titles[k], names[k] = cn_names(prefs[k])
    names = assign_names(names, order)
    attrs = {k: attrs_of(k, m) for k, m in prefs.items()}
    places = {k: {"title": titles[k], "names": names[k], "attrs": attrs[k]} for k in order if names[k]}
    try:
        sights = cn_sights(await geo.fetch_cn_scenic(session), prefs)
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
        logger.warning(f"[guess] 5A scenic areas failed: {e!r}")
        sights = None
    pool = [k for k in order if k in places and prefs[k]["level"] in ("地级市", "副省级市")]
    entries = []
    for i, k in enumerate(pool):
        m, a = prefs[k], attrs[k]
        # 附近：离得最近的三个里挑浏览量最高的两个，比只给最近的两个好认
        near = sorted((geo.distance([[o["lat"], o["lng"]]], [m["lat"], m["lng"]]), n) for n, o in prefs.items() if n != k)
        near = sorted((n for _, n in near[:3]), key=lambda n: -fame[n])[:2]
        words = [n for n in names[k] if not n.isascii()]
        hints = [
            ["区号", m["dial"]], ["车牌", "、".join(m["plates"][:2])], ["附近", "、".join(titles[n] for n in near)],
            ["名胜", cn_sight_hint((sights or {}).get(k, []), words, titles[k])],
        ]
        original, answer = f"{a['prov']} · {a['level']}", f"{titles[k]}（{a['prov']}）"
        entries.append(entry("cd", k, titles[k], original, answer, names[k], hints, a, i < MAIN))
    bank.commit("cngeodle", entries, places=places, complete=sights is not None)


MODE = Blind(
    cat="cngeodle", command="盲猜中国地点", desc="不给图，猜一个地级市看省份、方位、人口等对比", header="地级市",
    columns=COLUMNS, clues=CLUES, row=row, build=build, settings=(VERSION,), exact=True,
)
