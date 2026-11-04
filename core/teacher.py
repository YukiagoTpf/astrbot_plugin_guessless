"""猜老师：日本 AV 女优的官方头像高斯模糊，「提示」一段段变清楚，猜是谁。

名单来自 Wikidata（国籍日本、有 FANZA 女优 ID、日文或中文维基上有条目的；职业有的标 Q488111 有的标 Q1079215，不看），资料（读音、生日、身高、出道期间、专属厂牌）
优先读日文维基百科的信息框，没有再用 Wikidata 的。人气按日文和中文维基最近 60 天的浏览量排。
头像是 FANZA 女优页上的那张（pics.dmm.co.jp/mono/actjpgs/<罗马字>.jpg，只拍脸，125×125），地址按读音拼
（训令式：し→si、ち→ti、つ→tu、ふ→hu、じ→zi），拼出来的那张不存在（302 到 now printing）就不出题。

不出：出道那年减出生那年不到 19 的（确保出道时已经成年）、已经去世的、2000 年以前出道的（那时的图是写真扫描，
不全是只拍脸的头像，有露出的）、NO_IMAGE 里的。三围、作品内容这类不进提示也不进答案卡。
"""

import asyncio
import json
import re
import time

import aiohttp

from astrbot.api import logger

from .entries import assign_names, finish_entry, to_hans, usable_names
from .sources import WIKI_UA, WIKIDATA, ZH_API, dedupe, zhwiki_views
from .store import load_json, save_json

VERSION = "fanza-v4"  # 选人、资料、提示、叫法、拼头像地址的规则改了就换个名字，题库重建
JA_API = "https://ja.wikipedia.org/w/api.php"
PICS = "https://pics.dmm.co.jp/mono/actjpgs/{}.jpg"
CACHE_DAYS = 30
STAGES = (18, 12, 7, 3)  # 每一段高斯模糊的半径（608 像素高的图区里），「提示」一次清楚一段；资料比段数多，最后一段全给
MAIN_TOP = 150  # 浏览量前这么多名是大众题
POOL = 500  # 只出浏览量前这么多名，再往后多是没人认识的
MIN_DEBUT_AGE = 19  # 出道那年减出生那年至少这么多：只知道年份时，这样才确定出道时满 18
MIN_DEBUT_YEAR = 2000  # FANZA 上 2000 年以前出道的头像是写真扫描，有露出的；之后的都是只拍脸的标准头像
# 头像不合适的（不是只拍脸、有挑逗动作、太糊认不出），按头像文件名的罗马字拉黑（只能加不能删）。2026-11 审过前 500 名
NO_IMAGE = {"saeki_yumika", "ogawa_asami"}
# 星座：(这个星座最后一天 月×100+日, 名字)
ZODIAC = (
    (119, "摩羯座"), (218, "水瓶座"), (320, "双鱼座"), (419, "白羊座"), (520, "金牛座"), (621, "双子座"),
    (722, "巨蟹座"), (822, "狮子座"), (922, "处女座"), (1023, "天秤座"), (1122, "天蝎座"), (1221, "射手座"),
    (1231, "摩羯座"),
)
QUERY = """SELECT ?p ?dmm ?ja ?kana ?zh ?hans ?tw ?en ?birth ?height ?start ?end ?dead ?jawiki ?zhwiki WHERE {
  ?p wdt:P9781 ?dmm; wdt:P27 wd:Q17.
  OPTIONAL { ?p rdfs:label ?ja FILTER(lang(?ja) = "ja") }
  OPTIONAL { ?p rdfs:label ?zh FILTER(lang(?zh) = "zh") }
  OPTIONAL { ?p rdfs:label ?hans FILTER(lang(?hans) = "zh-hans") }
  OPTIONAL { ?p rdfs:label ?tw FILTER(lang(?tw) = "zh-tw") }
  OPTIONAL { ?p rdfs:label ?en FILTER(lang(?en) = "en") }
  OPTIONAL { ?p wdt:P1814 ?kana }
  OPTIONAL { ?p wdt:P569 ?birth }
  OPTIONAL { ?p wdt:P2048 ?height }
  OPTIONAL { ?p wdt:P2031 ?start }
  OPTIONAL { ?p wdt:P2032 ?end }
  OPTIONAL { ?p wdt:P570 ?dead }
  OPTIONAL { ?a schema:about ?p; schema:isPartOf <https://ja.wikipedia.org/>; schema:name ?jawiki }
  OPTIONAL { ?b schema:about ?p; schema:isPartOf <https://zh.wikipedia.org/>; schema:name ?zhwiki }
  FILTER(BOUND(?jawiki) || BOUND(?zhwiki))
}"""

# 平假名 -> 训令式罗马字（FANZA 头像文件名的拼法）。拗音、促音、长音符在 romaji 里处理
_ROW = {
    "あいうえお": ("a", "i", "u", "e", "o"), "かきくけこ": ("ka", "ki", "ku", "ke", "ko"),
    "さしすせそ": ("sa", "si", "su", "se", "so"), "たちつてと": ("ta", "ti", "tu", "te", "to"),
    "なにぬねの": ("na", "ni", "nu", "ne", "no"), "はひふへほ": ("ha", "hi", "hu", "he", "ho"),
    "まみむめも": ("ma", "mi", "mu", "me", "mo"), "やゆよ": ("ya", "yu", "yo"), "らりるれろ": ("ra", "ri", "ru", "re", "ro"),
    "わゐゑを": ("wa", "i", "e", "o"), "がぎぐげご": ("ga", "gi", "gu", "ge", "go"), "ざじずぜぞ": ("za", "zi", "zu", "ze", "zo"),
    "だぢづでど": ("da", "zi", "zu", "de", "do"), "ばびぶべぼ": ("ba", "bi", "bu", "be", "bo"),
    "ぱぴぷぺぽ": ("pa", "pi", "pu", "pe", "po"), "ぁぃぅぇぉ": ("a", "i", "u", "e", "o"), "ゔ": ("vu",), "ん": ("n",),
}
KANA = {k: v for row, vals in _ROW.items() for k, v in zip(row, vals)}
SMALL = {"ゃ": "a", "ゅ": "u", "ょ": "o", "ぁ": "a", "ぃ": "i", "ぅ": "u", "ぇ": "e", "ぉ": "o"}


def hira(text: str) -> str:
    return "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in text)


def romaji(kana: str) -> str:
    """平假名或片假名的读音拼成训令式罗马字；有拼不了的字（汉字、符号）返回空。"""
    out, i, text = "", 0, hira(kana)
    while i < len(text):
        c, nxt = text[i], text[i + 1] if i + 1 < len(text) else ""
        if c == "っ":
            following = romaji(nxt) if nxt else ""
            out += following[:1] if following[:1].isalpha() and following[:1] not in "aiueon" else ""
        elif c == "ー":
            out += next((ch for ch in reversed(out) if ch in "aiueo"), "")
        elif c in KANA and nxt in SMALL and c not in SMALL:
            # 拗音：きゃ→kya、しゃ→sya、じゃ→zya、ちゃ→tya；外来音的小写元音同理，じぇ→zye
            base = KANA[c]
            out += base[:-1] + ("y" if base[-1] == "i" else "") + SMALL[nxt]
            i += 1
        elif c in KANA:
            out += KANA[c]
        else:
            return ""
        i += 1
    return out


def clean(value: str) -> str:
    """信息框的值去掉注释、引用、模板、文件，链接只留显示的字，<br> 换成换行。"""
    value = re.sub(r"<!--.*?-->|<ref[^>/]*/>|<ref[^>]*>.*?</ref>", "", value, flags=re.S)
    value = re.sub(r"\[\[(?:File|ファイル|画像):[^\]]*\]\]", "", value, flags=re.I)
    while re.search(r"\{\{[^{}]*\}\}", value):
        value = re.sub(r"\{\{[^{}]*\}\}", "", value)
    value = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]*)\]\]", r"\1", value)
    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.I)
    return re.sub(r"<[^>]+>", "", value).strip()


def infobox(text: str) -> dict[str, str]:
    """日文维基「{{AV女優」信息框里的各项，值已经 clean 过。"""
    start = text.find("{{AV女優")
    if start < 0:
        return {}
    depth, i = 0, start
    while i < len(text):
        if text.startswith("{{", i):
            depth, i = depth + 1, i + 2
        elif text.startswith("}}", i):
            depth, i = depth - 1, i + 2
            if depth == 0:
                break
        else:
            i += 1
    body, out = text[start + 2:i - 2], {}
    for part in re.split(r"\n\s*\|", body)[1:]:
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = clean(v)
    return out


def zodiac(month: int, day: int) -> str:
    return next(name for end, name in ZODIAC if month * 100 + day <= end)


def career(box: dict, wd: dict) -> tuple[int | None, int | None]:
    """(出道那年, 结束那年)，结束是 None 就是现役。「2018年 2021年 -」这种中断过的，按最早一年和最后一段算。"""
    span = box.get("AV出演期間", "")
    if years := [int(y) for y in re.findall(r"(19\d\d|20\d\d)", span)]:
        return years[0], None if re.search(r"[-–—〜~]\s*$", span) or "現在" in span else years[-1]
    start, end = wd.get("start"), wd.get("end")
    return (int(start[:4]) if start else None), (int(end[:4]) if end else None)


async def _sparql(session: aiohttp.ClientSession) -> dict[str, dict]:
    headers = {"User-Agent": WIKI_UA, "Accept": "application/sparql-results+json"}
    async with session.post(WIKIDATA, data={"query": QUERY}, headers=headers, timeout=aiohttp.ClientTimeout(total=90)) as r:
        r.raise_for_status()
        rows = (json.loads(await r.text()))["results"]["bindings"]
    out: dict[str, dict] = {}
    for row in rows:
        item = out.setdefault(row["p"]["value"].rsplit("/", 1)[-1], {})
        for k, v in row.items():
            item.setdefault(k, v["value"])
    return out


async def _jawiki(session: aiohttp.ClientSession, titles: list[str], status) -> dict[str, dict]:
    """日文维基条目的信息框，一次 50 条。"""
    out: dict[str, dict] = {}
    for i in range(0, len(titles), 50):
        status(f"正在读日文维基的信息框 {i}/{len(titles)}")
        params = {"action": "query", "prop": "revisions", "rvprop": "content", "rvslots": "main", "redirects": "1",
                  "titles": "|".join(titles[i:i + 50]), "format": "json", "formatversion": "2"}
        async with session.get(JA_API, params=params, headers={"User-Agent": WIKI_UA},
                               timeout=aiohttp.ClientTimeout(total=60)) as r:
            r.raise_for_status()
            data = await r.json(content_type=None)
        q = data.get("query") or {}
        back = {m["to"]: m["from"] for m in (q.get("normalized") or []) + (q.get("redirects") or [])}
        for p in q.get("pages") or []:
            title = p["title"]
            while title in back:
                title = back[title]
            text = ((p.get("revisions") or [{}])[0].get("slots") or {}).get("main", {}).get("content", "")
            out[title] = infobox(text)
        await asyncio.sleep(1)
    return out


async def _zh_redirects(session: aiohttp.ClientSession, titles: list[str], status) -> dict[str, list[str]]:
    """中文维基上指向这些条目的重定向，{条目名: [重定向名, ...]}。群里常用的中文译名、旧艺名多半在这里。"""
    out: dict[str, list[str]] = {}
    for i in range(0, len(titles), 50):
        status(f"正在查中文维基的译名 {i}/{len(titles)}")
        cont: dict = {}
        while True:
            params = {"action": "query", "prop": "redirects", "rdprop": "title", "rdnamespace": "0", "rdlimit": "max",
                      "titles": "|".join(titles[i:i + 50]), "format": "json", "formatversion": "2", **cont}
            async with session.get(ZH_API, params=params, headers={"User-Agent": WIKI_UA},
                                   timeout=aiohttp.ClientTimeout(total=60)) as r:
                r.raise_for_status()
                data = await r.json(content_type=None)
            q = data.get("query") or {}
            back = {m["to"]: m["from"] for m in q.get("normalized") or []}
            for p in q.get("pages") or []:
                out.setdefault(back.get(p["title"], p["title"]), []).extend(x["title"] for x in p.get("redirects") or [])
            await asyncio.sleep(1)
            if "continue" not in data:
                break
            cont = data["continue"]
    return out


async def _has_image(session: aiohttp.ClientSession, slug: str) -> bool:
    """FANZA 的头像在不在：在是 200，没有的 302 到 now printing。"""
    async with session.get(PICS.format(slug), allow_redirects=False, timeout=aiohttp.ClientTimeout(total=20)) as r:
        return r.status == 200


def names_of(wd: dict, kana: str, slug: str, jawiki: str, redirects: list[str] = ()) -> list[str]:
    """能猜的叫法：日文名（也转成简体，沢→泽）、中文名（简繁）、中文维基的重定向（常用译名、旧艺名）、读音、
    英文名（去掉长音符号、姓名倒过来也行）、头像文件名那种罗马字。"""
    en = wd.get("en", "")
    plain = en.translate(str.maketrans("āēīōūĀĒĪŌŪ", "aeiouAEIOU"))
    long = en.translate(str.maketrans({"ā": "aa", "ē": "ee", "ī": "ii", "ō": "ou", "ū": "uu"}))
    flips = [" ".join(reversed(n.split())) for n in (plain, long) if len(n.split()) == 2]
    # 中文标签有的把几个艺名写在一起（「橋本有菜, 新有菜」）
    zh = [x.strip() for n in (wd.get("hans"), wd.get("zh"), wd.get("tw")) if n for x in re.split(r"[,，、/]", n) if x.strip()]
    ja = [wd.get("ja", ""), re.sub(r"\s*[（(][^）)]*[）)]$", "", jawiki)]
    zh += [re.sub(r"\s*[（(][^）)]*[）)]$", "", n) for n in redirects]
    return usable_names(dedupe([
        *ja, *(to_hans(n) for n in ja if n), *zh, *(to_hans(n) for n in zh),
        kana.replace(" ", ""), plain, long, *flips, slug.replace("_", " "), " ".join(reversed(slug.split("_"))),
    ]))


async def build(bank) -> None:
    session, now = bank.http(), time.time()
    path = bank.dir / "teacher_cache.json"  # {"at": …, "people": {Q 号: {…}}, "images": {罗马字: [有没有, 查的时间]}}
    cache = load_json(path) or {}
    if now - cache.get("at", 0) > CACHE_DAYS * 86400:
        bank.status["teacher"] = "正在从 Wikidata 查名单"
        people = await _sparql(session)
        boxes = await _jawiki(session, sorted({p["jawiki"] for p in people.values() if p.get("jawiki")}),
                              lambda s: bank.status.__setitem__("teacher", s))
        for p in people.values():
            p["box"] = boxes.get(p.get("jawiki", ""), {})
        cache = {"at": now, "people": people, "images": cache.get("images", {})}
        save_json(path, cache)
    if "zh_redirects" not in cache:  # 跟着名单一个月一换
        cache["zh_redirects"] = await _zh_redirects(
            session, sorted({p["zhwiki"] for p in cache["people"].values() if p.get("zhwiki")}),
            lambda s: bank.status.__setitem__("teacher", s))
        save_json(path, cache)
    people, images, redirects = cache["people"], cache["images"], cache["zh_redirects"]
    rows = []
    for qid, p in people.items():
        box = p.get("box") or {}
        kana = re.sub(r"[\s・]+", " ", hira(box.get("ふりがな") or p.get("kana") or "")).strip()
        parts = [romaji(x) for x in kana.split()]
        slug = "_".join(parts) if len(parts) == 2 and all(parts) else ""
        birth_year = int(box["生年"]) if re.fullmatch(r"\d{4}", box.get("生年", "")) else (
            int(p["birth"][:4]) if p.get("birth", "")[:1].isdigit() else None)
        month = int(box["生月"]) if re.fullmatch(r"\d{1,2}", box.get("生月", "")) else (
            int(p["birth"][5:7]) if birth_year and p.get("birth", "")[:4] == str(birth_year) else None)
        day = int(box["生日"]) if re.fullmatch(r"\d{1,2}", box.get("生日", "")) else (
            int(p["birth"][8:10]) if month and p.get("birth", "")[:4] == str(birth_year) else None)
        debut, end = career(box, p)
        if not (slug and p.get("ja") and birth_year and debut) or p.get("dead") or slug in NO_IMAGE:
            continue
        if debut - birth_year < MIN_DEBUT_AGE or debut < MIN_DEBUT_YEAR:
            continue
        height = re.match(r"\d{3}", box.get("身長", "")) or re.match(r"\d{3}", p.get("height", ""))
        rows.append({"qid": qid, "p": p, "box": box, "kana": kana, "slug": slug, "birth": (birth_year, month, day),
                     "debut": debut, "end": end, "height": height[0] if height else ""})
    todo = [r["slug"] for r in rows if now - images.get(r["slug"], [False, 0])[1] > CACHE_DAYS * 86400]
    for i, slug in enumerate(todo):
        bank.status["teacher"] = f"正在查 FANZA 的头像 {i}/{len(todo)}"
        try:
            images[slug] = [await _has_image(session, slug), now]
        except (aiohttp.ClientError, asyncio.TimeoutError):
            continue
        await asyncio.sleep(0.2)
    if todo:
        save_json(path, cache)
    rows = [r for r in rows if images.get(r["slug"], [False])[0]]
    bank.status["teacher"] = "正在查维基百科的浏览量"
    ja_views = await zhwiki_views(session, [r["p"]["jawiki"] for r in rows if r["p"].get("jawiki")], api=JA_API)
    zh_views = await bank.views("teacher", [r["p"]["zhwiki"] for r in rows if r["p"].get("zhwiki")])
    for r in rows:
        r["views"] = ja_views.get(r["p"].get("jawiki", ""), 0) + zh_views.get(r["p"].get("zhwiki", ""), 0)
    rows = sorted(rows, key=lambda r: -r["views"])[:POOL]
    names = assign_names({r["qid"]: names_of(r["p"], r["kana"], r["slug"], r["p"].get("jawiki", ""),
                                             redirects.get(r["p"].get("zhwiki", ""), [])) for r in rows},
                         [r["qid"] for r in rows])
    entries = []
    for rank, r in enumerate(rows):
        if not names[r["qid"]]:
            continue
        entries.append(entry(r, names[r["qid"]], rank < MAIN_TOP))
    logger.info(f"[guess] teacher: {len(people)} on Wikidata, {len(entries)} with a FANZA headshot and a known debut")
    bank.commit("teacher", entries)


def entry(r: dict, names: list[str], main: bool) -> dict:
    """提示从宽到窄：出道（年份、现役还是引退）、出生（年份、星座）、身高、专属厂牌，最后是首字。"""
    p, box = r["p"], r["box"]
    zh = re.split(r"[,，、/]", p.get("hans") or p.get("zh") or p.get("tw") or "")[0].strip()
    title = to_hans(zh) if zh else p["ja"]
    year, month, day = r["birth"]
    end = r["end"]
    debut = f"{r['debut']} 年 · 现役" if end is None else f"{r['debut']} 年 · 已引退" if end == r["debut"] \
        else f"{r['debut']}–{end} 年"
    born = f"{year} 年" + (f" · {zodiac(month, day)}" if month and day else "")
    # 专属厂牌：一行一个，去掉括号里的时间和名义，最多两个
    labels = [re.sub(r"[（(][^）)]*[）)]?", "", x).strip(" 、,") for x in box.get("専属契約", "").splitlines()]
    label = "、".join([x for x in labels if x][:2])
    return finish_entry({
        "id": f"av-{r['qid']}", "group": f"av-{r['qid']}", "title": title, "original": p["ja"],
        "answer": f"{title}（{p['ja']}）" if title != p["ja"] else title, "names": names,
        "images": [PICS.format(r["slug"])], "tier": "main" if main else "niche",
        "hints": [["出道", debut], ["出生", born], ["身高", f"{r['height']} cm" if r["height"] else ""],
                  ["专属", label[:24]]],
    })
