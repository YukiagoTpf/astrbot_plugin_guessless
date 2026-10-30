"""盲猜日本地点：猜一个市町村，对比都道府县、方位、人口、面积、级别、是不是县厅所在地。

Wikidata 里日本的市町村都能猜，东京 23 区并成一个。中文维基浏览量前 POOL 个出题（前 MAIN 个是大众题，村不出），
其余只能拿来猜。只查 Wikidata 和维基百科，不用街景的 key。
"""

import re
import time

from astrbot.api import logger

from .. import geo
from ..entries import assign_names, scrub, usable_names
from ..store import load_json, save_json
from .base import Blind, away, entry, here, num, size, tiered, touching, yes_no

VERSION = "munis-v5"  # 选题、条目格式、提示规则改了就换个名字，题库重建
COLUMNS = ("都道府县", "方位", "人口", "面积", "级别", "县厅")
CLUES = ("景点", "邻居", "名胜", "首字")  # 景点是次有名的名胜，名胜是最有名的
MAIN, POOL = 100, 300  # 中文维基浏览量前 100 个是大众题，前 300 个出题，其余只能拿来猜
NEAR_KM = 50  # 相距这么近以内（差不多一个都市圈），方位格标黄
TOKYO = "tokyo23"  # 东京 23 区并成一个，键用这个
# 名胜只要这些类的（寺社、城、山水、温泉、公园、祭典……），车站、大学、行政区、事件不要
LANDMARK_CLASS = re.compile(
    r"寺|神社|大社|神宫|城|要塞|山|岳|峰|湖|岛|屿|温泉|公园|庭园|瀑布|海滩|滩|岬|湾|峡|溪谷|祭|桥|塔|"
    r"天主堂|教堂|教会|正教|圣殿|聚落|古道|洞窟|高原|海岸|沙丘|游乐园|动物园|水族馆|景点|名胜"
)
# 类里有这些的不要，哪怕也对得上上面（「城市」「大学城」带城，炮台的类是「台场 (军事)」）
LANDMARK_CLASS_BLOCK = re.compile(r"城市|城镇|行政区|站|机场|大学|学校|道路|公路|铁路|体育|医院|公司|企业|军|战")
# 不涉政、不碰战争和核：和平公园、原爆、靖国、护国神社、端岛（军舰岛）、领土争议的岛、皇室、陵墓、事件都不要
LANDMARK_BLOCK = re.compile(
    r"原爆|原子|核|和平|战|军|舰|靖国|护国|慰灵|空袭|遇刺|暗杀|事件|案|端岛|竹岛|独岛|尖阁|钓鱼|北方|皇|御所|陵|墓|"
    r"刑|监狱|收容|纪念|遗址|大学|站|线|政|议|厅|署"
)


def landmark_hints(found: list, words: list[str], title: str) -> list[str]:
    """fetch_landmarks 查到的候选里挑名胜：类对得上 LANDMARK_CLASS、名字不碰 LANDMARK_BLOCK，
    按条目数从多到少，最多两个。名字里带地名的（宇治上神社、京都御所）跳过，盖掉地名只剩半截没法看。"""
    out = []
    for _, name, classes in found:
        if (
            not any(LANDMARK_CLASS.search(c) for c in classes)
            or any(LANDMARK_CLASS_BLOCK.search(c) for c in classes)
            or LANDMARK_BLOCK.search(name)
        ):
            continue
        name = re.sub(r"\s*[（(][^）)]*[）)]$", "", name)
        if name and scrub(name, words, title) == name and name not in out:
            out.append(name)
        if len(out) == 2:
            break
    return out


def merge_tokyo(munis: dict) -> dict:
    """东京 23 区并成一个 TOKYO（群友说「东京」想的就是这一片）：人口面积相加，位置按人口加权，邻居是各区在 23 区外的邻居。
    各区原来的键记在 wards 里（查名胜、判县厅用），中文维基条目在 wikis 里（浏览量相加），
    名字在 names 里（「新宿」「涩谷」也算猜东京）。"""
    wards = {k: m for k, m in munis.items() if m["level"] == "特别区"}
    if not wards:
        return munis
    out = {k: m for k, m in munis.items() if k not in wards}
    for m in out.values():
        if any(n in wards for n in m["neighbours"]):
            m["neighbours"] = [n for n in m["neighbours"] if n not in wards] + [TOKYO]
    pop = sum(m["pop"] for m in wards.values())
    out[TOKYO] = {
        "zh": "东京23区", "ja": "東京都区部", "en": "Tokyo", "level": "特别区", "rank": geo.JP_LEVELS[geo.JP_WARD][1],
        "pop": pop, "area": sum(m["area"] for m in wards.values()),
        "lat": round(sum(m["lat"] * m["pop"] for m in wards.values()) / pop, 4),
        "lng": round(sum(m["lng"] * m["pop"] for m in wards.values()) / pop, 4),
        "pref": next(iter(wards.values()))["pref"], "wiki": "", "wikis": [m["wiki"] for m in wards.values() if m["wiki"]],
        "neighbours": sorted({n for m in wards.values() for n in m["neighbours"] if n not in wards}),
        "wards": sorted(wards), "names": ["东京", "東京", "东京都区部", *(n for m in wards.values() for n in jp_names(m))],
    }
    return out


def jp_names(m: dict) -> list[str]:
    """市町村的叫法：中文名、日文名、英文名，再加去掉市町村区后缀的（「京都」「箱根」，剩一个字的不要）。"""
    labels = [m["zh"], m["ja"], m["en"]]
    bases = [n[:-1] for n in labels[:2] if n and n[-1:] in "市町村区區"]
    return usable_names([*labels, *bases, *m.get("names", [])])


def attrs_of(key: str, m: dict, pref: dict, capital: bool) -> dict:
    """对比用的资料，见 row。"""
    region = next((name for last, name in geo.JP_REGIONS if pref["jis"] <= last), "")
    return {
        "key": key, "pref": pref["zh"], "region": region, "pop": m["pop"], "area": m["area"],
        "level": m["level"], "rank": m["rank"], "capital": capital, "spot": [m["lat"], m["lng"]],
        "neighbours": m["neighbours"],
    }


def row(guess: dict, answer: dict) -> list[list[str]]:
    """都道府县：同县绿、同地方（八地方）黄。方位：接壤写「接壤」，不接壤写两地相距多远，接壤或 NEAR_KM 以内标黄，
    箭头是从猜的地方看答案在哪个方向。人口、面积同盲猜地点。级别：村 < 町 < 市 < 施行时特例市 < 中核市 < 政令市 < 特别区，
    箭头指答案更高（↑）还是更低（↓）。县厅：是不是都道府县的县厅所在地。"""
    if guess["key"] == answer["key"]:
        place = here()
    else:
        way = geo.arrow(guess["spot"], answer["spot"])
        if answer["key"] in guess["neighbours"] or guess["key"] in answer["neighbours"]:
            place = touching(way)
        else:
            place = away(geo.distance([guess["spot"]], answer["spot"]), NEAR_KM, way)
    return [
        tiered(guess["pref"], answer["pref"], guess["region"] == answer["region"]),
        place,
        *size(guess, answer),
        num(guess["rank"], answer["rank"], guess["level"]),
        yes_no(guess["capital"], answer["capital"]),
    ]


async def landmarks(bank, places: dict[str, list[str]]) -> dict[str, list]:
    """名胜候选（geo.fetch_landmarks），按市町村缓存在 jp_landmarks.json，一个月内查过的不再查。
    Wikidata 一批查失败就跳过这批，那几个地方只是少了名胜提示。"""
    path, now = bank.dir / "jp_landmarks.json", time.time()
    cache = load_json(path) or {}
    todo = [k for k in places if now - cache.get(k, [0, []])[0] > 30 * 86400]
    for i in range(0, len(todo), 10):
        bank.status["jpgeodle"] = f"正在查名胜 {i}/{len(todo)}"
        batch = {k: places[k] for k in todo[i : i + 10]}
        try:
            found, failed = await geo.fetch_landmarks(bank.http(), batch)
        except Exception as e:
            logger.warning(f"[guess] landmarks for {sorted(batch)} failed: {e!r}")
            continue
        if failed:
            logger.warning(f"[guess] landmarks for {sorted(failed)} failed, will retry next build")
        cache.update({k: [now, found.get(k, [])] for k in batch if k not in failed})
        save_json(path, cache)
    return {k: cache[k][1] for k in places if k in cache}


async def build(bank) -> None:
    session = bank.http()
    bank.status["jpgeodle"] = "正在查日本的市町村"
    munis = merge_tokyo(await geo.fetch_municipalities(session))
    prefs = await geo.fetch_jp_prefectures(session)
    munis = {k: m for k, m in munis.items() if m["pref"] in prefs}
    views = await bank.views("jpgeodle", [t for m in munis.values() for t in [m["wiki"], *m.get("wikis", [])]])
    # 东京 23 区的浏览量是各区加起来
    fame = {k: sum(views.get(t, 0) for t in [m["wiki"], *m.get("wikis", [])]) for k, m in munis.items()}
    order = sorted(munis, key=lambda k: (-fame[k], -munis[k]["pop"]))
    names = assign_names({k: jp_names(m) for k, m in munis.items()}, order)
    capitals = {p["capital"] for p in prefs.values()}
    # 东京都厅在新宿区，并进了东京23区
    attrs = {
        k: attrs_of(k, m, prefs[m["pref"]], k in capitals or bool(capitals & set(m.get("wards", []))))
        for k, m in munis.items()
    }
    places = {k: {"title": munis[k]["zh"], "names": names[k], "attrs": attrs[k]} for k in order if names[k]}
    # 村不出题（白马村、小笠原村都有人反馈太难），只能拿来猜
    pool = [k for k in order if k in places and munis[k]["level"] != "村"][:POOL]

    def make(found: dict) -> list:
        entries = []
        for i, k in enumerate(pool):
            m = munis[k]
            words = [n for n in names[k] if not n.isascii()]
            sights = landmark_hints(found.get(k, []), words, m["zh"])
            near = [munis[n]["zh"] for n in sorted(m["neighbours"], key=lambda n: -fame.get(n, 0)) if n in munis][:2]
            hints = [
                ["景点", sights[1] if len(sights) > 1 else ""], ["邻居", "、".join(near)],
                ["名胜", sights[0] if sights else ""],
            ]
            original = " · ".join(x for x in (m["ja"] if m["ja"] != m["zh"] else "", m["en"]) if x)
            answer = f"{m['zh']}（{attrs[k]['pref']}）"
            entries.append(entry("jd", k, m["zh"], original, answer, names[k], hints, attrs[k], i < MAIN))
        return entries

    # 名胜要查很久（大城市底下的条目上千），先用缓存里有的出题，查完再换一版。没查全的标成没建完，
    # 中途重启或者有的没查成，一小时后接着查（见 Bank._stale）
    cached = load_json(bank.dir / "jp_landmarks.json") or {}
    bank.commit("jpgeodle", make({k: v[1] for k, v in cached.items()}), places=places, complete=False)
    wards = await geo.fetch_city_wards(session, [k for k in pool if munis[k]["rank"] == geo.JP_LEVELS["Q1749269"][1]])
    found = await landmarks(bank, {k: munis[k].get("wards") or [k, *wards.get(k, [])] for k in pool})
    bank.commit("jpgeodle", make(found), places=places, complete=len(found) == len(pool))


MODE = Blind(
    cat="jpgeodle", command="盲猜日本地点", desc="不给图，猜一个市町村看都道府县、方位、人口等对比", header="市町村",
    columns=COLUMNS, clues=CLUES, row=row, build=build, settings=(VERSION,), exact=True,
)
