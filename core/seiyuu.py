"""猜声优：日本女声优配过的三个角色（AniList 的角色图），三宫格一格格露出来，猜是谁配的。

名单：AniList 的 staff 按收藏数排，翻到它允许的最后一页（第 5000 个），挑性别女、语言日语、配过角色的。
再按日文名去 Bangumi 搜人物（只搜职业有声优的），对上了才出题：初音未来、VTuber、歌手、漫画家这样就筛掉了。
中文名、读音、罗马字、出身地、事务所和收藏数都从 Bangumi 拿；人气按 Bangumi 的收藏数排（群里认得的多是国内有名的）。
角色：她在 AniList 上收藏最多的 10 个，作品名用 Bangumi 上她的角色表里那个角色最早的动画的中文名，对不上用 AniList 的日文名。
成人向、Ecchi 作品里的角色不出（角色图常是擦边的），BLOCK_WORKS 里的作品不出，旁白、她本人演自己的角色不出，
近 20 年没再配过的角色不出（群里认不得八九十年代的老番），角色图画的是别人那一版的不出（同一部里小时候、长大后各一个人配，
角色图却只有一张，见 shared），能用的角色不到 3 个的人不出。已经去世的不出。
近 20 年要接过新角色（MIN_NEW 个是这些年才开始配的），只靠老角色的续作撑着的不出。
出题时热门（收藏数前 HOT 位）和冷门的掺着出，各占多少看 seiyuu_niche_ratio。露的角色偏新番（cast）。

AniList 的名单、角色的声优和 Bangumi 的资料都缓存一个月（seiyuu_cache.json）。
"""

import asyncio
import random
import re
import time
from collections import Counter

import aiohttp

from astrbot.api import logger

from .answer import norm
from .entries import assign_names, finish_entry, to_hans, usable_names
from .sources import Anilist, HttpError, dedupe
from .store import load_json, save_json

VERSION = "anilist-bgm-v3"  # 选人、选角色、资料、提示、叫法的规则改了就换个名字，题库重建
BGM = "https://api.bgm.tv/v0"
CACHE_DAYS = 30
SCAN = 2  # AniList 名单存的字段改了就加一，缓存的名单作废重翻（Bangumi 的资料照用）
PAGES = 100  # AniList 一页 50 个，最多翻到第 5000 个
STEPS = 4  # 一段露一个角色，第 4 段不加角色，把剩下的资料和首字给完
MIN_CHARS = 3
RECENT_YEARS = 20  # 角色在这么多年内的作品里还配过才出
MIN_NEW = 3  # 能出的角色里至少这么多个是近 RECENT_YEARS 年才开始配的，人才出
HOT = 100  # 收藏数前这么多位是热门（大众题），往后是冷门；只按收藏数均匀挑的话，大半是中不溜的，热门冷门都不显
# 舰船、国家拟人的作品不出：角色是照着真实的军舰、国家画的，容易把群聊带到战争、政治上（只能加不能删）
BLOCK_WORKS = (
    "艦隊これくしょん", "艦これ", "kancolle", "kantai collection", "舰队collection", "舰c",
    "アズールレーン", "azur lane", "碧蓝航线", "戦艦少女", "战舰少女", "warship girls",
    "蒼き鋼のアルペジオ", "arpeggio of blue steel", "苍蓝钢铁的琶音",
    "ヘタリア", "hetalia", "黑塔利亚",
)
NARRATOR = re.compile(r"ナレーション|ナレーター|narrat|旁白|解说", re.I)
# Bangumi 别名里拿来当答案的几项；昵称不要（「はやみん」这种说出来像闲聊），本名不要
ALIAS_KEYS = {"日文名", "纯假名", "罗马字", "英文名", "中文名", "第二中文名", "简体中文名", "繁体中文名", "旧艺名"}
PREF = re.compile(r"北海道|东京都|京都府|大阪府|[^\s·・,，、/]{1,3}县")
LATIN = re.compile(r"[\x00-\x7fāēīōūĀĒĪŌŪâêîôûÂÊÎÔÛ]+")  # 罗马字（可能带长音符号）


def _trim(s: dict) -> dict | None:
    """AniList 的一个 staff 只留要用的；不是日语女声优、没配过角色的返回 None。"""
    name = s.get("name") or {}
    if s.get("gender") != "Female" or s.get("languageV2") != "Japanese" or not name.get("native"):
        return None
    chars = []
    for e in (s.get("characters") or {}).get("edges") or []:
        c, media = e.get("node") or {}, sorted(e.get("media") or [], key=lambda m: -(m.get("popularity") or 0))
        if not c or not media:
            continue
        cname = c.get("name") or {}
        years = [y for m in media if (y := (m.get("startDate") or {}).get("year"))]
        chars.append({
            "id": c["id"], "name": cname.get("native") or cname.get("full") or "", "full": cname.get("full") or "",
            "image": (c.get("image") or {}).get("large") or "", "fav": c.get("favourites") or 0,
            "work": (media[0].get("title") or {}).get("native") or (media[0].get("title") or {}).get("romaji") or "",
            "titles": dedupe(t for m in media[:5] for t in ((m.get("title") or {}).get("native"), (m.get("title") or {}).get("romaji"))),
            "nsfw": any(m.get("isAdult") or "Ecchi" in (m.get("genres") or []) for m in media),
            # 她头一回和最近一次配这个角色的作品是哪年开播的
            "first": min(years, default=0), "last": max(years, default=0),
        })
    if not chars:
        return None
    return {
        "id": s["id"], "native": name["native"], "full": name.get("full") or "", "fav": s.get("favourites") or 0,
        "born": (s.get("dateOfBirth") or {}).get("year"), "dead": bool((s.get("dateOfDeath") or {}).get("year")),
        "debut": (s.get("yearsActive") or [None])[0], "chars": chars,
    }


async def _scan(session: aiohttp.ClientSession, status) -> list[dict]:
    anilist, out = Anilist(session), []
    for page in range(1, PAGES + 1):
        status(f"正在翻 AniList 的声优 {page}/{PAGES}（已找到 {len(out)} 位）")
        try:
            staff, more = await anilist.staff(page)
        except HttpError as e:
            if e.status == 400 and out:  # 翻过了它允许的深度
                break
            raise
        out += [t for s in staff if (t := _trim(s))]
        if not more:
            break
    return out


def _value(person: dict, key: str):
    return next((x.get("value") for x in person.get("infobox") or [] if x.get("key") == key), None)


def _text(value) -> list[str]:
    """Bangumi 信息框的值：字符串，或者 [{"k", "v"}] 的列表，都摊成字符串列表。"""
    if isinstance(value, str):
        return [value]
    return [x["v"] for x in value or [] if isinstance(x, dict) and isinstance(x.get("v"), str)]


async def _bangumi(bank, s: dict) -> dict:
    """按日文名去 Bangumi 搜人物（职业有声优的）：{"person": 资料, "works": {角色名: 作品名}}，搜不到 person 是 None。
    作品名取 Bangumi 上她的角色表里这个角色最早的动画（不算客串），没有动画的取最早的游戏等。"""
    data = await bank._post(f"{BGM}/search/persons", {"keyword": s["native"], "filter": {"career": ["seiyu"]}},
                            params={"limit": 10})
    await asyncio.sleep(0.3)
    hits = [p for p in data.get("data") or [] if norm(p.get("name") or "") == norm(s["native"]) and p.get("gender") != "male"]
    if not hits:
        return {"person": None, "works": {}}
    p = max(hits, key=lambda p: (p.get("stat") or {}).get("collects") or 0)
    rows = await bank._get(f"{BGM}/persons/{p['id']}/characters")
    await asyncio.sleep(0.3)
    want, works = {norm(c["name"]) for c in s["chars"]}, {}
    for r in sorted(rows or [], key=lambda r: (r.get("subject_type") != 2, r.get("staff") == "客串", r.get("subject_id") or 0)):
        if (k := norm(r.get("name") or "")) in want and k not in works:
            works[k] = r.get("subject_name_cn") or r.get("subject_name") or ""
    person = {"id": p["id"], "name": p.get("name") or "", "collects": (p.get("stat") or {}).get("collects") or 0,
              "birth_year": p.get("birth_year"), "infobox": p.get("infobox") or []}
    return {"person": person, "works": works}


def _blocked(*titles: str) -> bool:
    words = [norm(w) for w in BLOCK_WORKS]
    return any(w in norm(t) for t in titles if t for w in words)


def _since() -> int:
    return time.localtime().tm_year - RECENT_YEARS


def shared(me: int, roles: list[list[int]]) -> bool:
    """角色图画的是不是别人配的那一版：她配过这个角色的那几部里，有一半以上还有别的日语声优也在配，而且那人配的部数
    不比她少（《新世界より》的渡辺早季两个人各配一半；五条悟、卡卡西小时候是女声优配的，图是长大后的；
    《ポプテピピック》每集换人）。柯南也挂着新一的声优，但她配的部数多，图是她那一版，照出；偶尔客串一部的（鸣人
    婴儿时）不算。roles 是 Anilist.casts 给的每部作品的声优，查不到就当不是。"""
    mine = [r for r in roles if me in r]
    total = Counter(v for r in roles for v in r)
    others = Counter(v for r in mine for v in r if v != me)
    return any(n * 2 >= len(mine) and total[v] >= total[me] for v, n in others.items())


def usable(s: dict, works: dict, casts: dict | None = None) -> list[dict]:
    """能出的角色，收藏多的在前：有图、近 RECENT_YEARS 年还配过、不是成人向 / Ecchi / BLOCK_WORKS 的、不是旁白、不是她本人、
    作品名里没有她的名字、图不是别人配的那一版（casts 是 Anilist.casts 的结果，不给就不查）。作品名换成 Bangumi 的中文名。"""
    me = {k for k in (norm(s["native"]), norm(s["full"])) if len(k) >= 2}
    since, out, seen = _since(), [], set()
    for c in s["chars"]:
        work = works.get(norm(c["name"])) or c["work"]
        key = norm(c["name"])
        if (not c["image"] or c["image"].endswith("/default.jpg") or c["image"] in seen or c["nsfw"] or c["last"] < since
                or _blocked(work, *c["titles"]) or NARRATOR.search(f"{c['name']} {c['full']}")
                or any(k in key or k in norm(c["full"]) or k in norm(work) for k in me)
                or shared(s["id"], (casts or {}).get(str(c["id"])) or [])):
            continue
        seen.add(c["image"])
        out.append({"id": c["id"], "name": c["name"], "work": work, "image": c["image"], "fav": c["fav"], "first": c["first"]})
    return out


def active(chars: list[dict]) -> bool:
    """近 RECENT_YEARS 年还在接新角色：能出的角色（usable 的）里够 MIN_NEW 个是这些年才开始配的。"""
    return sum(c["first"] >= _since() for c in chars) >= MIN_NEW


def _latin(text: str) -> list[str]:
    """罗马字的几种写法：去掉长音符号、长音写成两个字母，姓名倒过来也算。"""
    plain = text.translate(str.maketrans("āēīōūĀĒĪŌŪ", "aeiouAEIOU"))
    long = text.translate(str.maketrans({"ā": "aa", "ē": "ee", "ī": "ii", "ō": "ou", "ū": "uu"}))
    return dedupe([plain, long, *(" ".join(reversed(n.split())) for n in (plain, long) if len(n.split()) == 2)])


def names_of(s: dict, person: dict | None) -> list[str]:
    """能猜的叫法：日文名（也转成简体）、Bangumi 的中文名和别名里的日文名、读音、罗马字、英文名、旧艺名，AniList 的罗马字。"""
    out = [s["native"], *_latin(s["full"])]
    if person:
        out += [person["name"], *_text(_value(person, "简体中文名"))]
        for x in _value(person, "别名") or []:
            if isinstance(x, dict) and x.get("k") in ALIAS_KEYS and isinstance(x.get("v"), str):
                for n in (n.strip() for n in re.split(r"[=/、，,]", x["v"])):
                    out += _latin(n) if LATIN.fullmatch(n) else [n]
    return usable_names(dedupe([*out, *(to_hans(n) for n in out if not n.isascii())]))


def _year(values: list[str]) -> int | None:
    """「2003（声优）」「1996（女优）」这种，有写声优的取它，没有取第一个年份。"""
    for v in sorted(values, key=lambda v: "声优" not in v):
        if m := re.search(r"(19|20)\d\d", v):
            return int(m[0])
    return None


def _origin(person: dict) -> str:
    """出身地：「日本·千叶县」「熊本県熊本市」「東京都」都只留都道府县，简体；认不出都道府县的原样给（去掉「日本」）。"""
    for key in ("出身地", "出生地", "出身"):
        if values := _text(_value(person, key)):
            text = re.sub(r"^日本[\s·・]*", "", to_hans(values[0])).strip()
            return m[0] if (m := PREF.search(text)) else text[:10]
    return ""


def _agency(person: dict) -> str:
    """事务所：有几个的取最后一个（一般是现在的），去掉括号里的年份。"""
    values = _text(_value(person, "事务所"))
    return re.sub(r"\s*[（(][^）)]*[）)]", "", values[-1]).strip()[:20] if values else ""


def entry(s: dict, person: dict | None, chars: list[dict], names: list[str], main: bool) -> dict:
    """提示：出道、出身地、事务所、出生，首字留到最后一段（clues）。"""
    zh = next(iter(_text(_value(person, "简体中文名"))), "") if person else ""
    title = to_hans(zh or s["native"])
    debut = s.get("debut") or (_year(_text(_value(person, "出道时间"))) if person else None)
    born = s.get("born") or (person or {}).get("birth_year")
    return finish_entry({
        "id": f"cv-{s['id']}", "group": f"cv-{s['id']}", "title": title, "original": s["native"],
        "answer": f"{title}（{s['native']}）" if title != s["native"] else title, "names": names,
        "chars": chars, "tier": "main" if main else "niche",
        "hints": [["出道", f"{debut} 年" if debut else ""], ["出身", _origin(person) if person else ""],
                  ["事务所", _agency(person) if person else ""], ["出生", f"{born} 年" if born else ""]],
    })


def clues(hints: list, shown: int) -> list:
    """第 shown 段给哪些提示：一段多一条资料；首字（finish_entry 补在最后）留到最后一段，最后一段全给。"""
    return hints if shown >= STEPS else hints[:-1][:shown]


def cast(chars: list[dict]) -> list[dict]:
    """这道题露哪三个角色，按露出的顺序：冷门的先露，最后一个从她最有名的三个里挑，不至于三个都认不出。
    群友认得新番的多，所以偏新的：最有名的三个里越新的越容易挑中，前两个先从后来才配的那一半里挑。
    尽量出自三部不同的作品，底下的作品名才是三条线索。"""
    if len(chars) < 5:
        return sorted(random.sample(chars, MIN_CHARS), key=lambda c: c["fav"])
    newest = lambda c: -c.get("first", 0)  # noqa: E731
    star = random.choices(sorted(chars[:3], key=newest), weights=(3, 2, 1))[0]
    rest = sorted(chars[3:], key=newest)
    half = max(2, (len(rest) + 1) // 2)
    rest = random.sample(rest[:half], half) + random.sample(rest[half:], len(rest) - half)
    picked = []
    for c in rest:
        if len(picked) < 2 and c["work"] not in {star["work"], *(p["work"] for p in picked)}:
            picked.append(c)
    picked += [c for c in rest if c not in picked][: 2 - len(picked)]
    return [*sorted(picked, key=lambda c: c["fav"]), star]


async def build(bank) -> None:
    session, now = bank.http(), time.time()
    path = bank.dir / "seiyuu_cache.json"  # {"at": …, "scan": SCAN, "staff": [AniList 的名单], "people": {AniList ID: {"at", "person", "works"}}, "casts": {角色 ID: 各部的声优}}
    cache = load_json(path) or {}

    def status(text: str) -> None:
        bank.status["seiyuu"] = text

    if now - cache.get("at", 0) > CACHE_DAYS * 86400 or cache.get("scan") != SCAN:
        # 角色的声优跟着名单一个月一换，Bangumi 的资料各人按自己的时间过期
        cache = {"at": now, "scan": SCAN, "staff": await _scan(session, status), "people": cache.get("people", {})}
        save_json(path, cache)
    people, casts = cache["people"], cache.setdefault("casts", {})
    # 只查角色够用的，省得在 VTuber、歌手身上白跑
    staff = [s for s in cache["staff"] if not s["dead"] and len(chars := usable(s, {})) >= MIN_CHARS and active(chars)]
    todo = sorted({c["id"] for s in staff for c in usable(s, {})} - {int(k) for k in casts})
    anilist = Anilist(session)
    try:
        for i in range(0, len(todo), 50):
            status(f"正在查角色还有谁配过 {i}/{len(todo)}")
            casts.update(await anilist.casts(todo[i : i + 50]))
    finally:
        save_json(path, cache)
    staff = [s for s in staff if len(chars := usable(s, {}, casts)) >= MIN_CHARS and active(chars)]
    todo = [s for s in staff if now - people.get(str(s["id"]), {}).get("at", 0) > CACHE_DAYS * 86400]
    failures = 0
    try:
        for i, s in enumerate(todo):
            status(f"正在去 Bangumi 查声优 {i}/{len(todo)}")
            try:
                people[str(s["id"])] = {"at": now, **await _bangumi(bank, s)}
                failures = 0
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
                logger.warning(f"[guess] seiyuu: bangumi lookup of {s['id']} failed: {type(e).__name__}")
                if (failures := failures + 1) >= 10:  # 查到的都存着，一小时后接着查
                    raise RuntimeError("Bangumi 连着 10 次没查成") from e
            if i % 50 == 49:
                save_json(path, cache)
    finally:
        save_json(path, cache)

    rows = []
    for s in staff:
        rec = people.get(str(s["id"])) or {}
        if not (person := rec.get("person")):
            continue
        if len(chars := usable(s, rec.get("works") or {}, casts)) >= MIN_CHARS and active(chars):
            rows.append((person["collects"], s["fav"], s, person, chars))
    rows.sort(key=lambda r: (-r[0], -r[1]))
    rows = rows[: bank._size("seiyuu")]
    names = assign_names({str(s["id"]): names_of(s, person) for _, _, s, person, _ in rows}, [str(r[2]["id"]) for r in rows])
    entries = [entry(s, person, chars, names[str(s["id"])], i < HOT)
               for i, (_, _, s, person, chars) in enumerate(rows) if names[str(s["id"])]]
    logger.info(f"[guess] seiyuu: {len(cache['staff'])} on AniList, {len(staff)} with enough roles, {len(entries)} in the bank")
    bank.commit("seiyuu", entries)
