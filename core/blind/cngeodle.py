"""盲猜中国地点：猜一个地级市（北上广深猜区），对比省份、方位、人口、面积、GDP、线级。

Wikidata 里的地级行政区、直辖市、省辖县级市和北上广深的区都能猜。出题的是地级市（含副省级市）、直辖市、
省辖县级市和北上广深的区，北上广深整个市不出题（cn_data.SPLIT），自治州、地区、盟也不出；不分大众小众，
每道题一样容易抽到。GDP 和线级 Wikidata 上没有，手写在 core/cn_data.py，缺这两样的不出题。名胜从 5A 景区里挑，查不到就只少这一条提示，一小时后再查。
三沙市不出、不认（geo.CN_EXCLUDE）。
"""

import asyncio
import re

import aiohttp

from astrbot.api import logger

from .. import cn_data, geo
from ..entries import assign_names, scrub, usable_names
from .base import Blind, away, blank, entry, here, num, ratio, same, size, tiered

VERSION = "split-v2"  # 选题、条目格式、提示规则（CN_SIGHT_BLOCK）、cn_data 改了就换个名字，题库重建
COLUMNS = ("省份", "方位", "人口", "面积", "GDP", "线级")
CLUES = ("区号", "车牌", "附近", "名胜", "首字")
LEVELS = ("地级市", "副省级市", "直辖市", "省辖县级市")  # 出题的级别，再加上北上广深的区
# 不在线级榜上的：北上广深的区、省辖县级市，线级格直接写是哪一类
UNRANKED = {"市辖区": "区县", "省辖县级市": "省辖县级市"}
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
    """地级行政区和区的 (表里写的名字, 能猜的叫法)。去掉「市」「地区」「盟」「区」也算（「苏州」「喀什」「海淀」，
    「浦东新区」说「浦东」）；自治州写成「大理州」，说「大理州」「大理」都算，但只说「海南」不算海南州，那是省。"""
    zh = m["zh"]
    if m["level"] == "自治州" and (found := re.match(f"^(.+?)(?:(?:{CN_ETHNIC})族?)+自治州$", zh)):
        title, bases = found[1] + "州", [found[1] + "州", found[1]]
    else:
        title, bases = zh, [re.sub("(?:新区|地区|市|盟|区)$", "", zh)]
    provinces = {name for name, _ in geo.CN_PROVINCES.values()}
    if m["level"] in ("自治州", "地区", "盟"):
        bases = [b for b in bases if b not in provinces]
    return title, usable_names([zh, *bases])


def cn_sights(scenic: list[dict], places: dict) -> dict[str, list[str]]:
    """5A 景区归到地级行政区或区，{键: [景区名, ...]}，按语言版本数从多到少。先看往上三层的行政区划代码
    （区是六位，县的代码前四位就是所属的市），再看这几层里有没有哪个区、哪个市；都没有的（庐山、衡山只有坐标）
    看离哪个最近：50 公里内、第二近的远出一半以上才算。赤水丹霞离泸州比离遵义近，放宽了就会归错，挂到省的只在省内找。"""
    by_code = {m["code"]: k for k, m in places.items()}
    out: dict[str, list[str]] = {}
    for s in sorted(scenic, key=lambda s: -s["n"]):
        owner = next((by_code[c[:n]] for n in (6, 4) for c in s["codes"] if len(c) >= n and c[:n] in by_code), None)
        # 往上几层里既有区又有市的，归到区（代码更长）
        ups = [q for q in s["up"] if q in places]
        owner = owner or max(ups, key=lambda q: len(places[q]["code"]), default=None)
        if not owner and s["spot"]:
            provs = {c[:2] for c in s["codes"]}
            near = sorted(
                (geo.distance([[m["lat"], m["lng"]]], s["spot"]), k) for k, m in places.items()
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


def attrs_of(key: str, m: dict, city: dict) -> dict:
    """对比用的资料，见 row。city 是所属的市（市自己就是 m）：区的 GDP 按「市名 + 区名」查。"""
    prov, region = geo.CN_PROVINCES[m["prov"]]
    gdp = cn_data.GDP.get(m["zh"] if city is m else city["zh"] + m["zh"], 0)
    return {
        "key": key, "prov": prov, "region": region, "pop": m["pop"], "area": m["area"], "gdp": gdp,
        "tier": UNRANKED.get(m["level"]) or cn_data.TIER.get(m["zh"], ""), "spot": [m["lat"], m["lng"]],
    }


def money(yi: float) -> str:
    """GDP（亿元）写成表里放得下的，两位有效数字：5.7万亿、2800亿、110亿。"""
    n = float(f"{yi:.2g}")
    return f"{n / 1e4:g}万亿" if n >= 1e4 else f"{n:g}亿"


def row(guess: dict, answer: dict) -> list[list[str]]:
    """省份：同省绿、同大区黄。方位：两地相距多远，NEAR_KM 以内标黄，箭头是从猜的地方看答案在哪个方向
    （Wikidata 的接壤关系只有三成的市有，不写「接壤」）。人口、面积同盲猜地点，GDP 也一样按倍数比。
    线级：一线、新一线、二线……五线，一样绿、差一档黄、差得多红，箭头指答案的线级更高（↑）还是更低（↓）；
    区县、省辖县级市（UNRANKED）不分线级，和答案是一类的绿，不是的红，不带箭头。
    改版前建的题库没有 GDP、线级，换完之前这两格写「—」。"""
    if guess["key"] == answer["key"]:
        place = here()
    else:
        km = geo.distance([guess["spot"]], answer["spot"])
        place = away(km, NEAR_KM, geo.arrow(guess["spot"], answer["spot"]))
    mine, theirs = guess.get("tier"), answer.get("tier")
    if not (mine and theirs):
        tier = blank()
    elif mine in cn_data.TIERS and theirs in cn_data.TIERS:
        x, y = cn_data.TIERS.index(mine), cn_data.TIERS.index(theirs)
        tier = num(x, y, mine, abs(x - y) == 1)
    else:
        tier = same(mine, theirs)
    return [
        tiered(guess["prov"], answer["prov"], guess["region"] == answer["region"]),
        place,
        *size(guess, answer),
        ratio(guess.get("gdp", 0), answer.get("gdp", 0), money(guess.get("gdp", 0))),
        tier,
    ]


async def build(bank) -> None:
    session = bank.http()
    bank.status["cngeodle"] = "正在查中国的地级市"
    prefs = await geo.fetch_cn_prefectures(session)
    split = {k for k, m in prefs.items() if m["zh"] in cn_data.SPLIT}
    bank.status["cngeodle"] = "正在查北上广深的区"
    districts = await geo.fetch_cn_districts(session, sorted(split))
    bank.status["cngeodle"] = "正在查省辖县级市"
    counties = await geo.fetch_cn_counties(session)
    units = {**prefs, **counties, **districts}
    views = await bank.views("cngeodle", [m["wiki"] for m in units.values()], monthly=True)
    fame = {k: views.get(m["wiki"], 0) for k, m in units.items()}
    # 同名的叫法归更出名的：「朝阳」是北京朝阳区还是辽宁朝阳市看浏览量
    order = sorted(units, key=lambda k: (-fame[k], -units[k]["pop"]))
    titles, names = {}, {}
    for k in order:
        titles[k], names[k] = cn_names(units[k])
    names = assign_names(names, order)
    city = {k: units[m["city"]] if k in districts else m for k, m in units.items()}
    attrs = {k: attrs_of(k, m, city[k]) for k, m in units.items()}
    places = {k: {"title": titles[k], "names": names[k], "attrs": attrs[k]} for k in order if names[k]}
    try:
        sights = cn_sights(await geo.fetch_cn_scenic(session), units)
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
        logger.warning(f"[guess] 5A scenic areas failed: {e!r}")
        sights = None
    if missing := sorted(titles[k] for k in places if not attrs[k]["gdp"] or not attrs[k]["tier"]):
        logger.warning(f"[guess] no GDP or tier in cn_data for {missing}")
    # 缺 GDP、线级的不出题（胡杨河市只查到第七师合在一起的 GDP），表里空一格太好猜
    pool = [
        k for k in order if k in places and attrs[k]["gdp"] and attrs[k]["tier"]
        and (k in districts or (units[k]["level"] in LEVELS and k not in split))
    ]
    entries = []
    for k in pool:
        m, a, c = units[k], attrs[k], city[k]
        # 附近：离得最近的三个里挑浏览量最高的两个，比只给最近的两个好认。地级市在地级行政区里挑；
        # 区在区和别的市里挑（不算自己的市）；省辖县级市在地级行政区和别的省辖县级市里挑（仙桃、潜江、天门挨着）
        group = units if k in districts else prefs if k in prefs else {**prefs, **counties}
        around = [n for n in group if n not in (k, m.get("city"))]
        near = sorted((geo.distance([[units[n]["lat"], units[n]["lng"]]], [m["lat"], m["lng"]]), n) for n in around)
        near = sorted((n for _, n in near[:3]), key=lambda n: -fame[n])[:2]
        words = [n for n in names[k] if not n.isascii()]
        # 区号、车牌按市给：区的和市的一样，有的区在 Wikidata 上没填
        hints = [
            ["区号", c["dial"]], ["车牌", "、".join(c["plates"][:2])], ["附近", "、".join(titles[n] for n in near)],
            ["名胜", cn_sight_hint((sights or {}).get(k, []), words, titles[k])],
        ]
        if k in districts:
            original, answer = f"{a['prov']} · {titles[m['city']]}辖区", f"{titles[k]}（{titles[m['city']]}）"
        else:  # 直辖市不用再写一遍省份
            original = f"{a['prov']} · {m['level']}"
            answer = titles[k] if m["level"] == "直辖市" else f"{titles[k]}（{a['prov']}）"
        entries.append(entry("cd", k, titles[k], original, answer, names[k], hints, a, True))
    bank.commit("cngeodle", entries, places=places, complete=sights is not None)


MODE = Blind(
    cat="cngeodle", command="盲猜中国地点", desc="不给图，猜一个地级市（北上广深猜区）看省份、方位、GDP 等对比",
    header="地方", columns=COLUMNS, clues=CLUES, row=row, build=build, settings=(VERSION,), exact=True,
)
