"""盲猜地点：猜一个国家，对比地区、方位、人口、面积、语言、行驶方向。每个国家一道题，可以猜的国家同猜地点；
只查 Wikidata 和 GeoNames，不用街景的 key。"""

from astrbot.api import logger

from .. import geo
from ..sources import zhwiki_titles
from .base import Blind, away, blank, entry, here, same, size, tiered, touching

VERSION = "views-v1"  # 条目格式、选题分档、地区划分（geo.REGIONS）改了就换个名字，题库重建
MAIN = 60  # 中文维基浏览量前这么多的国家是大众题；按人口面积分，布隆迪、布基纳法索都成了大众题
# 对比表的列，每列都能排除一大片；货币、美食、名胜在表里比不出来，放进「提示」
COLUMNS = ("地区", "方位", "人口", "面积", "语言", "行驶")
CLUES = ("货币", "美食", "名胜", "首字")
NEAR_KM = 1000  # 两国最近的城镇相距这么近以内，方位格标黄


def attrs_of(cc: str, rows: list, stats: dict) -> dict:
    """对比用的资料，见 row。rows 是 GeoNames 里这个国家的城镇，stats 见 entries.country_basics。"""
    region, continent = geo.REGION_OF.get(cc, ("", ""))
    return {
        "cc": cc, "region": region, "continent": continent, **stats,
        "center": geo.center(rows), "points": geo.thin(rows),
    }


def row(guess: dict, answer: dict) -> list[list[str]]:
    """地区：同地区绿，同大洲黄。方位：两国最近的城镇相距多远，箭头是从猜的国家看答案在哪个方向；
    接壤的写「接壤」，接壤或 NEAR_KM 以内标黄。人口、面积：箭头指答案更多（↑）还是更少（↓）。
    语言：第一语言一样绿；不一样但两国的前三种语言里有共同的，黄格写「也说 X」（X 按答案的排序挑）；
    都不沾边红格写猜的国家的第一语言。只写猜的国家的第一语言再标黄，群友会以为英语和西班牙语算接近。"""
    if guess["cc"] == answer["cc"]:  # 答案卡上答案自己那一行
        place = here()
    else:
        way = geo.arrow(guess["center"], answer["center"])
        if answer["cc"] in guess["neighbours"] or guess["cc"] in answer["neighbours"]:
            place = touching(way)
        else:
            place = away(geo.gap(guess["points"], answer["points"]), NEAR_KM, way)
    mine, theirs = guess["langs"], answer["langs"]
    if not mine:
        lang = blank()
    elif theirs and mine[0] == theirs[0]:
        lang = [mine[0], "hit", ""]
    elif shared := [x for x in theirs if x in mine]:
        lang = [f"也说{shared[0]}", "near", ""]
    else:
        lang = [mine[0], "miss", ""]
    return [
        tiered(guess["region"], answer["region"], guess["continent"] == answer["continent"]),
        place,
        *size(guess, answer),
        lang,
        same(guess["drive"], answer["drive"]),
    ]


async def build(bank) -> None:
    cities = await bank.cities("geodle")
    codes, basics = await bank.countries("geodle", cities)
    # 大众题按中文维基的浏览量挑（国内的知名度）；一条都没查到就退回人口面积
    try:
        titles = await zhwiki_titles(bank.http(), "P297", codes)
    except Exception as e:
        logger.warning(f"[guess] zhwiki titles for geodle failed: {e}")
        titles = {}
    # 国家代码对上的条目不一定是常说的那个：NL 只对上「荷兰王国」，「荷兰」条目没挂代码。日常叫法（geo.TITLES）也查一下
    for cc in codes:
        titles.setdefault(cc, []).append(basics[cc]["zh"])
    views = await bank.views("geodle", [t for ts in titles.values() for t in ts], monthly=True)
    fame = {cc: max(views.get(t, 0) for t in ts) for cc, ts in titles.items()}
    if any(fame.values()):
        main = set(sorted(codes, key=lambda cc: fame.get(cc, 0), reverse=True)[:MAIN])
    else:
        main = {cc for cc in codes if basics[cc]["big"]}
    entries, places = [], {}
    for cc in codes:
        b, attrs = basics[cc], attrs_of(cc, cities[cc], basics[cc]["stats"])
        zh, en = b["zh"], b["en"]
        entries.append(entry("gd", cc, zh, en, f"{zh}（{en}）" if en else zh, b["names"], b["hints"], attrs, cc in main))
        places[cc] = {"title": zh, "names": b["names"], "attrs": attrs}
    if missing := [cc for cc in codes if cc not in geo.REGION_OF]:
        logger.warning(f"[guess] no region in geo.REGIONS for {missing}")
    bank.commit("geodle", entries, places=places)


MODE = Blind(
    cat="geodle", command="盲猜地点", desc="不给图，猜一个国家看地区、方位、人口等对比", header="国家",
    columns=COLUMNS, clues=CLUES, row=row, build=build,
    settings=(VERSION, sorted(geo.EXCLUDE), geo.HINTS_VERSION),
    aliases=("盲猜国家",),  # 猜国家下线了，群友顺手会说「盲猜国家」
)
