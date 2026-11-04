"""猜魔兽：魔兽世界的一张地图（野外地区和主城），九宫格揭开一张游戏内截图，猜是哪张地图。

地图的中英文名、大陆、资料片、等级、阵营、子区域来自游戏数据表（wago.tools 导出的 DB2 CSV：UiMap、UiMapAssignment、
Map、ContentTuning、AreaTable），截图来自 warcraft.wiki.gg（MediaWiki API）「Zone screenshots」分类里的图。
两边查到的都缓存一个月（wow_zones.json、wow_shots.json），每周重建只是重新挑图。
"""

import asyncio
import csv
import io
import json
import re
import time
from collections import Counter, defaultdict

import aiohttp

from .entries import finish_entry
from .sources import WIKI_UA, HttpError, dedupe
from .store import load_json, save_json

VERSION = "zones-v1"  # 选地图、挑图、提示规则改了就换个名字，题库重建
WAGO = "https://wago.tools"
WIKI_API = "https://warcraft.wiki.gg/api.php"
CACHE_DAYS = 30
EXPANSIONS = (
    "经典旧世", "燃烧的远征", "巫妖王之怒", "大地的裂变", "熊猫人之谜", "德拉诺之王", "军团再临", "争霸艾泽拉斯",
    "暗影国度", "巨龙时代", "地心之战", "至暗之夜",
)
MAIN_UNTIL = 4  # 熊猫人之谜和以前的、主城是大众题
# 主城（数据表里不算野外地区，也大多没标阵营，单独列）：UiMap ID -> (warcraft.wiki.gg 的页面名, 阵营)
CITIES = {
    84: ("Stormwind City", "联盟"), 85: ("Orgrimmar", "部落"), 87: ("Ironforge", "联盟"), 89: ("Darnassus", "联盟"),
    90: ("Undercity", "部落"), 88: ("Thunder Bluff", "部落"), 110: ("Silvermoon City", "部落"),
    103: ("The Exodar", "联盟"), 111: ("Shattrath City", "中立"), 125: ("Dalaran (Northrend)", "中立"),
    627: ("Dalaran (Broken Isles)", "中立"), 393: ("Shrine of Seven Stars", "联盟"), 391: ("Shrine of Two Moons", "部落"),
    622: ("Stormshield", "联盟"), 624: ("Warspear", "部落"), 1161: ("Boralus", "联盟"), 1165: ("Dazar'alor", "部落"),
    1670: ("Oribos", "中立"), 2112: ("Valdrakken", "中立"), 2339: ("Dornogal", "中立"),
}
ALIASES = {"奥格瑞玛": ["奥格"], "暴风城": ["暴风"]}  # 群里常说的简称
FACTIONS = {"2": "联盟", "4": "部落"}  # AreaTable 的 FactionGroupMask，别的算争夺地区
MIN_SHOTS, MAX_SHOTS = 3, 15
MIN_WIDTH, MIN_RATIO = 1000, 1.45  # 题图要居中裁成 16:9，太窄的裁掉太多
# 文件名、文件分类带这些的不是游戏内的风景截图：地图、图标、加载画面、原画、NPC、过场动画、别的游戏……
BAD_NAME = re.compile(
    r"^File:(?:VZ-|WorldMap-|Worldmap|Map |Inv |IconSmall|Achievement|Ui-|Icon|Spell |Ability |Trade |Load)"
    r"|concept|loading.?screen|[ _-]map\b|minimap|logo|key ?art|wallpaper|HS\.|Hearthstone|TCG|comic|novel",
    re.I,
)
BAD_CAT = re.compile(
    r"concept art|maps$|^Maps$|loading screens|NPC screenshots|Mob screenshots|Icons|Hearthstone|Cinematic|Comic|Novel|"
    r"TCG|RPG|Fan art|Warcraft III|Warcraft II|Film|Template|Key arts|Heroes of the Storm|Images by ",
    re.I,
)


async def _text(session: aiohttp.ClientSession, url: str, source: str, params: dict | None = None) -> str:
    timeout = aiohttp.ClientTimeout(total=90)
    for attempt in range(3):
        try:
            async with session.get(url, params=params, headers={"User-Agent": WIKI_UA}, timeout=timeout) as r:
                if r.status >= 400:
                    raise HttpError(source, r.status)
                return await r.text()
        except (aiohttp.ClientConnectionError, asyncio.TimeoutError):
            if attempt == 2:
                raise
            await asyncio.sleep(5)
    return ""


async def _table(session: aiohttp.ClientSession, name: str, build: str, locale: str = "enUS") -> list[dict]:
    """wago.tools 导出的一张 DB2 表。要带 build，不带给的是测试服的。"""
    text = await _text(session, f"{WAGO}/db2/{name}/csv", "wago.tools", {"build": build, "locale": locale})
    return list(csv.DictReader(io.StringIO(text)))


async def fetch_zones(session: aiohttp.ClientSession) -> list[dict]:
    """能出题的地图：野外地区（UiMap 类型 3、所在的 Map 是野外、有等级设置的），加上 CITIES 里的主城。
    同名的只留资料片最早的（重做过的提瑞斯法林地、奥丹姆……），外域和德拉诺的纳格兰、影月谷是两个地方，各留一个。"""
    build = json.loads(await _text(session, f"{WAGO}/api/builds/latest", "wago.tools"))["wow"]["version"]
    names = {loc: {r["ID"]: r["Name_lang"] for r in await _table(session, "UiMap", build, loc)} for loc in ("zhCN", "zhTW")}
    uimaps = {r["ID"]: r for r in await _table(session, "UiMap", build)}
    maps = {r["ID"]: r for r in await _table(session, "Map", build)}
    tuning = {r["ID"]: r for r in await _table(session, "ContentTuning", build)}
    areas = {r["ID"]: r for r in await _table(session, "AreaTable", build, "zhCN")}
    en_areas = {r["ID"]: r["AreaName_lang"] for r in await _table(session, "AreaTable", build)}
    assigned: dict[str, Counter] = defaultdict(Counter)
    area_of: dict[str, Counter] = defaultdict(Counter)
    for r in await _table(session, "UiMapAssignment", build):
        assigned[r["UiMapID"]][r["MapID"]] += 1
        area_of[r["UiMapID"]][r["AreaID"]] += 1
    children: dict[str, list[str]] = defaultdict(list)
    for r in sorted(areas.values(), key=lambda r: int(r["ID"])):
        children[r["ParentAreaID"]].append(r["AreaName_lang"])

    def continent(i: str) -> str:
        seen = set()
        while i in uimaps and i not in seen:
            seen.add(i)
            if uimaps[i]["Type"] == "2":
                return names["zhCN"].get(i, "")
            i = uimaps[i]["ParentUiMapID"]
        return ""

    def wild(i: str) -> bool:
        u, mid = uimaps[i], assigned[i].most_common(1)[0][0] if assigned[i] else ""
        return (u["Type"] == "3" and maps.get(mid, {}).get("InstanceType") == "0" and u["ContentTuningID"] != "0"
                and " - " not in u["Name_lang"])

    # 下面还挂着别的野外地区的不出题（拆成北荆棘谷和荆棘谷海角以前的荆棘谷、瓦丝琪尔），截图都和子地区重复
    parents = {u["ParentUiMapID"] for i, u in uimaps.items() if wild(i)}
    out, seen = [], set()
    for i, u in sorted(uimaps.items(), key=lambda kv: int(kv[0])):
        mid = assigned[i].most_common(1)[0][0] if assigned[i] else ""
        m, city = maps.get(mid, {}), int(i) in CITIES
        if not city and (not wild(i) or i in parents):
            continue
        tune = tuning.get(u["ContentTuningID"], {})
        exp = int((m if city else tune).get("ExpansionID") or 0)
        en, zh = u["Name_lang"], names["zhCN"].get(i, "")
        # 外域和德拉诺各有一个纳格兰、影月谷，wiki 上德拉诺的那页叫「(alternate universe)」（按编号先遇到外域的）
        title = CITIES[int(i)][0] if city else en
        if exp == 5 and title in seen and continent(i) == "德拉诺":
            title += " (alternate universe)"
        if not zh or not continent(i) or title in seen:
            continue
        seen.add(title)
        # 地图自己对应的区域：分配到的区域里不是子区域的那个，用来查阵营和子区域
        own = [a for a, _ in area_of[i].most_common() if a in areas and areas[a]["ParentAreaID"] == "0"]
        own = next((a for a in own if en_areas.get(a) == en), own[0] if own else "")
        level = [tune.get("MinLevelSquish"), tune.get("MaxLevelSquish")] if not city else None
        subs = dedupe(n for n in children.get(own, []) if n and not n.isascii() and "[" not in n and zh not in n)
        faction = CITIES[int(i)][1] if city else FACTIONS.get(areas.get(own, {}).get("FactionGroupMask", ""), "")
        out.append({
            "key": f"wow-{i}", "en": en, "zh": zh, "tw": names["zhTW"].get(i, ""), "title": title, "city": city,
            "expansion": exp, "continent": continent(i), "level": level,
            "faction": faction, "subzones": subs[:6],
        })
    return out


async def _wiki(session: aiohttp.ClientSession, params: dict) -> list[dict]:
    """MediaWiki API 一次查询，接着翻 continue 拿全。一秒一个请求；UA 要是机器人式的，浏览器 UA 会被 Cloudflare 拦。"""
    base, cont, out = {"format": "json", "formatversion": "2", **params}, {}, []
    while True:
        data = json.loads(await _text(session, WIKI_API, "warcraft.wiki.gg", {**base, **cont}))
        out.append(data)
        await asyncio.sleep(1)
        if "continue" not in data:
            return out
        cont = data["continue"]


async def fetch_shots(session: aiohttp.ClientSession, titles: list[str], status=None) -> dict[str, list[str]]:
    """warcraft.wiki.gg 上每个地图的截图（1280 宽的缩略图地址）：地图页里的图，加上「Category:<地图> subzones」里
    子区域页的首图和直接放在这个分类里的图。只要「Zone screenshots」分类里、够宽、不是地图图标原画这类的（BAD_NAME、BAD_CAT）。"""
    canon, files = {}, defaultdict(set)
    for i in range(0, len(titles), 50):
        for d in await _wiki(session, {"action": "query", "titles": "|".join(titles[i:i + 50]), "redirects": "1",
                                       "prop": "images", "imlimit": "max"}):
            q = d.get("query", {})
            moved = {r["from"]: r["to"] for r in q.get("normalized", []) + q.get("redirects", [])}
            for t in titles[i:i + 50]:
                n = moved.get(t, t)  # 先规范大小写（normalized），再跟重定向
                canon.setdefault(t, moved.get(n, n))
            for p in q.get("pages", []):
                files[p["title"]] |= {x["title"] for x in p.get("images", [])}
    for n, t in enumerate(titles):
        if status:
            status(f"正在找截图 {n}/{len(titles)}")
        page = canon.get(t, t)
        for d in await _wiki(session, {"action": "query", "generator": "categorymembers",
                                       "gcmtitle": f"Category:{page} subzones", "gcmlimit": "max",
                                       "prop": "pageimages", "piprop": "name"}):
            for p in d.get("query", {}).get("pages", []):
                if p["ns"] == 0 and p.get("pageimage"):
                    files[page].add("File:" + p["pageimage"].replace("_", " "))
                elif p["ns"] == 6:
                    files[page].add(p["title"])
    wanted = sorted({f for fs in files.values() for f in fs if not BAD_NAME.search(f)})
    info: dict[str, dict] = defaultdict(lambda: {"cats": []})
    for i in range(0, len(wanted), 50):
        if status:
            status(f"正在查截图信息 {i}/{len(wanted)}")
        for d in await _wiki(session, {"action": "query", "titles": "|".join(wanted[i:i + 50]),
                                       "prop": "imageinfo|categories", "iiprop": "url|size|mime",
                                       "iiurlwidth": "1280", "cllimit": "max"}):
            for p in d.get("query", {}).get("pages", []):
                e = info[p["title"]]
                for ii in p.get("imageinfo", [])[:1]:
                    e.update(url=ii.get("thumburl") or ii.get("url"), w=ii.get("width") or 0,
                             h=ii.get("height") or 1, mime=ii.get("mime"))
                e["cats"] += [c["title"].removeprefix("Category:") for c in p.get("categories", [])]

    def good(f: str) -> bool:
        e = info.get(f)
        return bool(e and e.get("url") and e["w"] >= MIN_WIDTH and e["w"] / e["h"] >= MIN_RATIO
                    and e.get("mime") in ("image/jpeg", "image/png", "image/webp") and "Zone screenshots" in e["cats"]
                    and not any(BAD_CAT.search(c) for c in e["cats"]))

    return {t: sorted(info[f]["url"] for f in files.get(canon.get(t, t), ()) if good(f)) for t in titles}


def zone_entry(z: dict, shots: list[str]) -> dict:
    """提示从宽到窄：资料片、大陆、类型、阵营、等级、两个子区域（finish_entry 再补首字）。"""
    lo, hi = z["level"] or (None, None)
    level = (f"{lo} 级" if lo == hi else f"{lo}-{hi} 级") if lo and hi and hi != "0" else ""
    return finish_entry({
        "id": z["key"], "group": z["key"], "title": z["zh"], "original": z["en"],
        "answer": f"{z['zh']}（{z['en']}）", "names": dedupe([z["zh"], z["en"], z["tw"], *ALIASES.get(z["zh"], [])]),
        "images": shots, "tier": "main" if z["city"] or z["expansion"] <= MAIN_UNTIL else "niche",
        "hints": [
            ["资料片", EXPANSIONS[z["expansion"]] if z["expansion"] < len(EXPANSIONS) else ""],
            ["大陆", z["continent"]], ["类型", "主城" if z["city"] else "野外"],
            ["阵营", f"{z['faction']}主城" if z["city"] else f"{z['faction']}领地" if z["faction"] else "争夺地区"],
            ["等级", level],
            ["子区域", "、".join(z["subzones"][:2])],
        ],
    })


async def build(bank) -> None:
    session, now = bank.http(), time.time()
    zones_path, shots_path = bank.dir / "wow_zones.json", bank.dir / "wow_shots.json"
    cached = load_json(zones_path) or {}
    if now - cached.get("at", 0) > CACHE_DAYS * 86400:
        bank.status["wow"] = "正在从 wago.tools 拉地图资料"
        cached = {"at": now, "zones": await fetch_zones(session)}
        save_json(zones_path, cached)
    zones = cached["zones"]
    shots = load_json(shots_path) or {}
    todo = [z["title"] for z in zones if now - shots.get(z["title"], [0, []])[0] > CACHE_DAYS * 86400]
    if todo:
        found = await fetch_shots(session, todo, lambda s: bank.status.__setitem__("wow", s))
        shots.update({t: [now, urls] for t, urls in found.items()})
        save_json(shots_path, shots)
    # 同一张图挂在好几个地图下（子区域分类有重叠，主城也在所在地区的子区域里）：主城的归主城，别的谁都不给，
    # 免得答案有两个
    owners = Counter(u for z in zones for u in shots.get(z["title"], [0, []])[1])
    entries = []
    for z in zones:
        urls = [u for u in shots.get(z["title"], [0, []])[1] if z["city"] or owners[u] == 1]
        if len(urls) >= MIN_SHOTS:
            entries.append(zone_entry(z, urls[:MAX_SHOTS] if len(urls) <= MAX_SHOTS else _spread(urls, MAX_SHOTS)))
    bank.commit("wow", entries)


def _spread(items: list, n: int) -> list:
    """均匀挑 n 个（截图按文件名排，挨着的常是同一个子区域）。"""
    return [items[round(i * (len(items) - 1) / (n - 1))] for i in range(n)]
