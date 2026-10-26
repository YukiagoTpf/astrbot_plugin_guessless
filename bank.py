"""题库：动画取 Bangumi 最热门的作品，游戏从 Metacritic 高分游戏里按热度和评分分大众 / 小众两档，用 Bangumi 补中文名和制作信息；
宝可梦取 PokeAPI 全图鉴，前几个世代算大众题；地点是 Google 街景随机取景（见 geo.py）。

题图用不带字的截图：动画是 B 站分集封面，没有就用 AniList 横幅；游戏要配 IGDB 的 key，没配就用封面。
找不到图的作品不进题库。译名另外从 Wikidata、AniList、IGDB 补。题库、匹配结果和图片都缓存在插件数据目录，出题时只有首次用到的图片需要联网。
"""

import asyncio
import hashlib
import json
import os
import random
import re
import time
from pathlib import Path

import aiohttp
from zhconv import convert

from astrbot.api import logger

from . import geo, hint_data
from .answer import candidates, norm
from .media import (
    POKEMON_ART,
    POKEMON_LANGS,
    Anilist,
    Bilibili,
    HttpError,
    Igdb,
    anilist_names,
    dedupe,
    igdb_media,
    pokemon_species,
    steam_shots,
    wikidata,
    year_close,
)

BANK_VERSION = 4  # 题目格式或选题规则变了就加一，旧题库会自动重建
BGM_API = "https://api.bgm.tv/v0"
BGM_UA = "YukiagoTpf/astrbot_plugin_guessless"
# Metacritic 评分榜是网页前端用的非官方接口，要带网页里内嵌的 apiKey，每次构建从 MC_PAGE 的源码里现找
MC_FINDER = "https://backend.metacritic.com/finder/metacritic/web"
MC_PAGE = "https://www.metacritic.com/game/the-legend-of-zelda-breath-of-the-wild/"
MC_IMG = "https://www.metacritic.com/a/img/catalog"
WEB_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)

ANIME_KINDS = {"TV": "TV 动画", "剧场版": "剧场版", "WEB": "网络动画"}
ANIME_STAFF = ("动画制作", "导演", "原作", "系列构成", "人物设定", "音乐")
GENERIC_TAGS = {"TV", "日本", "剧场版", "WEB", "OVA"}
GAME_INFO = ("游戏类型", "开发", "开发商", "发行", "发行商", "导演", "制作人", "音乐")
PLATFORMS = {
    "PlayStation": "PS1", "PlayStation 2": "PS2", "PlayStation 3": "PS3", "PlayStation 4": "PS4",
    "PlayStation 5": "PS5", "PlayStation Vita": "PSV", "Xbox One": "XB1", "Xbox Series X": "XSX",
    "Nintendo Switch": "Switch", "Nintendo Switch 2": "Switch 2", "Nintendo 64": "N64",
    "GameCube": "NGC", "Game Boy Advance": "GBA",
}
GENERATIONS = {1: "一（关都）", 2: "二（城都）", 3: "三（丰缘）", 4: "四（神奥）", 5: "五（合众）",
               6: "六（卡洛斯）", 7: "七（阿罗拉）", 8: "八（伽勒尔）", 9: "九（帕底亚）"}
MIN_POOL = 20
_BRACKETED = re.compile(r"[（(「『【\[][^（(「『【\[）)」』】\]]*[）)」』】\]]")


def load_json(path: Path):
    return json.loads(path.read_text("utf-8")) if path.exists() else None


def save_json(path: Path, data) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), "utf-8")
    os.replace(tmp, path)


def infobox(subject: dict) -> dict:
    return {i["key"]: i["value"] for i in subject.get("infobox") or []}


def aliases(info: dict) -> list[str]:
    out = [info["中文名"]] if isinstance(info.get("中文名"), str) else []
    raw = info.get("别名") or []
    for a in [raw] if isinstance(raw, str) else raw:
        v = a.get("v") if isinstance(a, dict) else a
        if isinstance(v, str):
            out.append(v)
    return out


def clip(value, limit: int = 40) -> str:
    if isinstance(value, list):
        value = "、".join(v.get("v", "") if isinstance(v, dict) else str(v) for v in value)
    # 括号里常是作品名（如 原作：某某（文库「作品名」系列）），会直接泄露答案；括号可能嵌套，从最内层剥
    value = str(value)
    while True:
        stripped = _BRACKETED.sub("", value)
        if stripped == value:
            break
        value = stripped
    value = re.sub(r"\s+", " ", re.sub(r"[（(「『【\[）)」』】\]]", "", value)).strip(" 、，,")
    return value if len(value) <= limit else value[:limit] + "…"


def popularity(subject: dict) -> int:
    c = subject.get("collection") or {}
    return c.get("collect", 0) + c.get("doing", 0)


def clean_mc_title(title: str) -> str:
    # Metacritic 用「标题 (1998)」区分同名作品
    return re.sub(r"\s*\(\d{4}\)$", "", title)


def shape_hint(title: str) -> list[str]:
    chars = norm(title)
    if chars.isascii():
        words = len(re.findall(r"[A-Za-z0-9]+", title))
        return ["首字母", f"{chars[:1].upper()}，{words} 个单词"]
    return ["首字", f"「{chars[:1]}」，{len(chars)} 个字"]


def finish_entry(entry: dict) -> dict:
    """去掉会泄露答案的提示，最多 8 条，最后补一条首字提示，凑满 9 格。"""
    leaks = {c for c in candidates(entry["names"]) if len(c) >= 3}
    hints = [h for h in entry["hints"] if h[1] and not any(c in norm(h[1]) for c in leaks)]
    entry["hints"] = hints[:8] + [shape_hint(entry["title"])]
    return entry


def anime_entry(s: dict, media: dict | None = None, wd: dict | None = None) -> dict:
    info = infobox(s)
    cn, name = s["name_cn"], s["name"]
    hints = []
    date = s.get("date") or ""
    if re.match(r"\d{4}-\d{2}", date):
        hints.append(["放送时间", f"{date[:4]} 年 {int(date[5:7])} 月"])
    kind = ANIME_KINDS[s["platform"]]
    eps = s.get("eps") or s.get("total_episodes")
    hints.append(["类型", f"{kind}，共 {eps} 话" if eps and s["platform"] != "剧场版" else kind])
    tags = list(dict.fromkeys(t for t in s.get("meta_tags") or [] if t not in GENERIC_TAGS))[:4]
    if tags:
        hints.append(["标签", " / ".join(tags)])
    rating = s.get("rating") or {}
    if rating.get("score"):
        hints.append(["Bangumi", f"{rating['score']} 分（排名 #{rating.get('rank')}）"])
    for key in ANIME_STAFF:
        if info.get(key):
            hints.append([key, clip(info[key])])
    return finish_entry({
        "id": f"bgm-{s['id']}",
        "title": cn,
        "original": name,
        "answer": f"{cn}（{name}）" if cn != name else cn,
        "names": dedupe([name, cn, *aliases(info), *(wd or {}).get("names", []), *(media or {}).get("names", [])]),
        "images": (media or {}).get("images") or [s["images"]["large"]],
        "hints": hints,
    })


def game_entry(item: dict, bgm: dict | None, wd: dict | None = None, media: dict | None = None) -> dict:
    en = clean_mc_title(item["title"])
    bgm, wd = bgm or {}, wd or {}
    cn = bgm.get("name_cn") or wd.get("zh") or ""
    if norm(cn) == norm(en):  # Wikidata 的中文 label 有时就是英文原名
        cn = ""
    info = bgm.get("info") or {}
    platforms = [PLATFORMS.get(p["name"], p["name"]) for p in item.get("platforms") or [] if p.get("name")]
    genres = [g["name"] for g in item.get("genres") or [] if g.get("name")]
    hints = [["发售年份", str(item.get("premiereYear") or (item.get("releaseDate") or "")[:4])]]
    if platforms:
        hints.append(["平台", " / ".join(platforms[:4]) + (" 等" if len(platforms) > 4 else "")])
    if info.get("游戏类型") or genres:
        hints.append(["类型", info.get("游戏类型") or " / ".join(genres)])
    hints.append(["Metacritic", f"{item['criticScoreSummary']['score']} 分"])
    dev = info.get("开发") or info.get("开发商")
    pub = info.get("发行") or info.get("发行商")
    if dev:
        hints.append(["开发", dev])
    if pub and pub != dev:
        hints.append(["发行", pub])
    for key in ("导演", "制作人", "音乐"):
        if info.get(key):
            hints.append([key, info[key]])
    names = [en, cn, bgm.get("name", ""), *bgm.get("aliases", []), *wd.get("names", []), *(media or {}).get("names", [])]
    return finish_entry({
        "id": f"mc-{item['slug']}",
        "title": cn or en,
        "original": en,
        "answer": f"{cn}（{en}）" if cn else en,
        "names": dedupe(names),
        "images": (media or {}).get("images") or [MC_IMG + item["image"]["bucketPath"]],
        "hints": hints,
    })


def split_tiers(items: list, media: dict, size: int, niche_size: int) -> list[tuple[dict, str]]:
    """items 按 MC 分从高到低排。大众题按热度取前 size 款；小众题从剩下的里按「MC 分排名 + 热度排名」取前 niche_size 款，
    即高分又不至于完全没人听过的，免得全是二十年前的美式体育年货。

    没截图的、DLC 之类的、本体也在候选里的重制 / 加强版不要；Metacritic 偶尔把同一款游戏拆成两个条目，按 IGDB ID 去重。
    """
    ids = {m["id"] for it in items if (m := media.get(it["slug"])) and m.get("id")}
    usable, seen = [], set()
    for it in items:
        m = media.get(it["slug"])
        if not m or not m["images"] or m.get("skip") or m.get("parent") in ids:
            continue
        if m.get("id"):
            if m["id"] in seen:
                continue
            seen.add(m["id"])
        usable.append(it)
    pop = lambda it: media[it["slug"]]["pop"]  # noqa: E731
    main = sorted(usable, key=lambda it: -pop(it))[:size]  # sorted 是稳定的，同热度保持 MC 分顺序
    taken = {it["slug"] for it in main}
    rest = [it for it in usable if it["slug"] not in taken]
    pop_rank = {it["slug"]: r for r, it in enumerate(sorted(rest, key=lambda it: -pop(it)))}
    niche = sorted(range(len(rest)), key=lambda r: r + pop_rank[rest[r]["slug"]])[:niche_size]
    return [(it, "main") for it in main] + [(rest[r], "niche") for r in niche]


def pokemon_entry(s: dict, by_id: dict, wd: dict | None) -> dict | None:
    """提示从泛到具体：世代、属性、颜色、身高体重、分类、特性、进化、图鉴编号。"""
    names = {POKEMON_LANGS[n["language_id"]]: n for n in s["pokemon_v2_pokemonspeciesnames"]}
    zh = names.get("zh-hans") or {}
    hant = names.get("zh-hant") or {}
    cn = zh.get("name") or convert(hant.get("name") or "", "zh-cn")
    en = (names.get("en") or {}).get("name") or ""
    if not cn:
        return None
    zh_name = lambda rows: (rows[0]["name"] if rows else "")  # noqa: E731
    poke = (s["pokemon_v2_pokemons"] or [{}])[0]
    types = [zh_name(t["pokemon_v2_type"]["pokemon_v2_typenames"]) for t in poke.get("pokemon_v2_pokemontypes") or []]
    abilities = [
        zh_name(a["pokemon_v2_ability"]["pokemon_v2_abilitynames"]) + ("（隐藏）" if a["is_hidden"] else "")
        for a in poke.get("pokemon_v2_pokemonabilities") or []
    ]
    color = zh_name((s.get("pokemon_v2_pokemoncolor") or {}).get("pokemon_v2_pokemoncolornames") or [])
    genus = zh.get("genus") or convert(hant.get("genus") or "", "zh-cn")
    if s["is_legendary"] or s["is_mythical"]:
        genus = "，".join(x for x in (genus, "传说的宝可梦" if s["is_legendary"] else "幻之宝可梦") if x)
    stage, prev = 1, by_id.get(s["evolves_from_species_id"])
    while prev:
        stage += 1
        prev = by_id.get(prev["evolves_from_species_id"])
    evolves = any(x["evolves_from_species_id"] == s["id"] for x in by_id.values())
    if stage > 1:
        parent = by_id[s["evolves_from_species_id"]]
        parent_name = next((n["name"] for n in parent["pokemon_v2_pokemonspeciesnames"] if n["language_id"] == 12), "")
        evolution = f"第 {stage} 阶段，由{parent_name}进化" + ("，还能进化" if evolves else "")
    else:
        evolution = "基础形态，能进化" if evolves else "不会进化"
    height, weight = poke.get("height"), poke.get("weight")
    hints = [
        ["世代", f"第{GENERATIONS.get(s['generation_id'], s['generation_id'])}"],
        ["属性", " / ".join(t for t in types if t)],
        ["颜色", color],
        ["身高体重", f"{height / 10:g} 米，{weight / 10:g} 公斤" if height and weight else ""],
        ["分类", genus],
        ["特性", " / ".join(a for a in abilities if a)],
        ["进化", evolution],
        ["图鉴编号", f"#{s['id']:04d}"],
    ]
    other = [(names.get(k) or {}).get("name") for k in ("zh-hant", "ja", "ja-hrkt", "ja-roma")]
    return finish_entry({
        "id": f"pm-{s['id']}",
        "title": cn,
        "original": en,
        "answer": f"{cn}（{en}）" if en else cn,
        "names": dedupe([cn, en, *other, *(wd or {}).get("names", [])]),
        "images": [POKEMON_ART.format(s["id"])],
        "fit": "contain",  # 透明底立绘，整张缩进正方形，不裁
        "hints": hints,
    })


def fmt_pop(pop: float) -> str:
    return f"约 {pop / 1e8:.1f} 亿" if pop >= 1e8 else f"约 {pop / 1e4:.0f} 万" if pop >= 1e4 else ""


def fmt_area(area: float) -> str:
    return f"约 {area / 1e4:.1f} 万平方公里" if area >= 1e4 else f"约 {area:.0f} 平方公里" if area else ""


def usable_names(names) -> list[str]:
    """Wikidata 别名里有「日」「米」「佛」这种单字和 us、jp 这种代码，群里随口一句就算答对了，去掉。"""
    return [n for n in dedupe(names) if len(norm(n)) > (2 if norm(n).isascii() else 1)]


def scrub(value: str, words: list[str], title: str) -> str:
    """提示里不能带答案的地名：整词换成 □，「法式」「日元」「泰语」这种用国名 / 县名首字的也盖掉；
    盖在开头结尾的直接删（「巴西雷亚尔」→「雷亚尔」），删完中间还有窟窿（「现代□□语」）或者
    大半都被盖掉的提示没意义，直接不要。"""
    for w in sorted({w for w in words if len(w) >= 2 and not w.isascii()}, key=len, reverse=True):
        value = value.replace(w, "□" * len(w))
    value = re.sub(f"{re.escape(title[:1])}(?=[式语元])", "□", value)
    if value.count("□") * 2 >= len(value):
        return ""
    value = value.strip("□·/ ")
    return "" if "□" in value else value


def place_words(names: list[str], capital: str) -> list[str]:
    """要从提示里盖掉的地名：答案的各种叫法，加上首都 / 县厅所在地（去掉市、区、都之类的后缀也算）。"""
    words = [n for n in names if not n.isascii()]
    if capital:
        words += [capital, re.sub("[市区都县府]$", "", capital)]
    return words


def country_basics(cc: str, facts: dict, wd: dict, info: dict, languages: dict) -> dict | None:
    """猜地点、猜国家共用的国名和国家层面的提示（不含半球，半球看具体位置）。

    提示按有用程度排：大洲、行驶方向、货币、名胜（次出名的）、主要语言、美食、名胜（最出名的）。
    人口、面积不太有用，只在没手写美食名胜的小国才用。
    """
    zh, en = geo.TITLES.get(cc) or wd.get("zh") or "", facts.get("en") or ""
    if not zh:
        return None
    capital = facts.get("capital") or ""
    names = usable_names([zh, wd.get("zh"), en, *wd.get("names", [])])
    words = place_words(names, capital)
    foods, sights = hint_data.COUNTRY.get(cc, ([], []))
    foods = [x for f in foods if (x := scrub(f, words, zh))]
    sights = [x for f in sights if (x := scrub(f, words, zh))]
    # GeoNames 按使用人数排
    lang = next((languages[c] for c in info.get("languages", []) if c in languages), "")
    hints = [
        ["大洲", facts.get("continent") or geo.CONTINENTS.get(info.get("continent"), "")],
        ["行驶方向", facts.get("driving") or ""],
        ["货币", scrub(facts.get("currency") or "", words, zh)],
        ["名胜", sights[1] if len(sights) > 1 else ""],
        ["主要语言", scrub(lang, words, zh)],
        ["美食", " / ".join(foods[:2])],
        ["名胜", sights[0] if sights else ""],
    ]
    if not sights:
        hints += [
            ["人口", fmt_pop(facts.get("population") or 0)],
            ["面积", fmt_area(facts.get("area") or 0)],
            ["首都", f"「{capital[0]}」开头" if capital else ""],
        ]
    return {"zh": zh, "en": en, "names": names, "hints": hints, "big": (
        (facts.get("population") or 0) >= 1e7 or (facts.get("area") or 0) >= 5e5
    )}


def hemisphere(lat: float, lng: float) -> str:
    return f"{'北' if lat >= 0 else '南'}半球 · {'东' if lng >= 0 else '西'}半球"


def geo_entry(cc: str, pano: dict, basics: dict) -> dict:
    """一个全景一道题，同一国家的几道题 group 相同，出过的国家短期内不再出。提示从泛到具体。"""
    hints = [["半球", hemisphere(pano["lat"], pano["lng"])], *basics["hints"]]
    zh, en = basics["zh"], basics["en"]
    return finish_entry({
        "id": f"geo-{pano['id']}",
        "group": cc,
        "title": zh,
        "original": " · ".join(x for x in (en, pano.get("near")) if x),
        "answer": f"{zh}（{en}）" if en else zh,
        "names": basics["names"],
        "pano": {"id": pano["id"], "heading": pano["heading"]},
        "fit": "panels",
        "hints": hints,
    })


def country_entry(cc: str, rings: list, basics: dict) -> dict:
    """看轮廓猜国家。人口过千万或面积过 50 万平方公里的算大众题。"""
    outer = [pt for hole, ring in rings if not hole for pt in ring]
    lng = (min(p[0] for p in outer) + max(p[0] for p in outer)) / 2
    lat = (min(p[1] for p in outer) + max(p[1] for p in outer)) / 2
    hints = [["半球", hemisphere(lat, lng - 360 if lng > 180 else lng)], *basics["hints"]]
    zh, en = basics["zh"], basics["en"]
    return finish_entry({
        "id": f"ct-{cc}",
        "group": cc,
        "title": zh,
        "original": en,
        "answer": f"{zh}（{en}）" if en else zh,
        "names": basics["names"],
        "iso": cc,
        "fit": "outline",
        "tier": "main" if basics["big"] else "niche",
        "hints": hints,
    })


def pref_entry(code: str, pano: dict, pref: dict, wd: dict) -> dict | None:
    """日本街景猜都道府县。「东京都」「大阪府」「冲绳县」只说「东京」「大阪」「冲绳」也算（北海道的「道」不能省）。"""
    zh, en = wd.get("zh") or "", pref.get("en") or ""
    if not zh:
        return None
    labels = [zh, *wd.get("names", [])]
    bases = [n[:-1] for n in labels if n[-1:] in "都府县県" and len(n) > 2 and not n.startswith("北海")]
    jis = pref.get("jis") or 0
    capital = pref.get("capital") or ""
    # 特产、名胜从不那么出名的开始给
    words = place_words([*labels, *bases], capital)
    foods, sights = (
        [x for f in items if (x := scrub(f, words, zh))] for items in hint_data.JP.get(jis, ([], []))
    )
    hints = [["地方", next((name for last, name in geo.JP_REGIONS if jis <= last), "")]]
    for i in (2, 1, 0):
        hints += [["特产", foods[i] if i < len(foods) else ""], ["名胜", sights[i] if i < len(sights) else ""]]
    hints.append(["县厅所在地", f"「{capital[0]}」开头" if capital else ""])
    return finish_entry({
        "id": f"jp-{pano['id']}",
        "group": code,
        "title": zh,
        "original": " · ".join(x for x in (en, pano.get("near")) if x),
        "answer": f"{zh}（{en}）" if en else zh,
        "names": usable_names([*labels, *bases, en]),
        "pano": {"id": pano["id"], "heading": pano["heading"]},
        "fit": "panels",
        "hints": hints,
    })


class Bank:
    def __init__(self, data_dir: Path, config):
        self.dir = data_dir
        self.img_dir = data_dir / "images"
        self.img_dir.mkdir(parents=True, exist_ok=True)
        self.config = config
        self.entries: dict[str, list] = {"anime": [], "game": [], "pokemon": [], "geo": [], "country": [], "jpgeo": []}
        self.meta: dict[str, dict] = {cat: {} for cat in self.entries}
        self.status = {cat: "等待构建" for cat in self.entries}
        self._shapes: dict = {}
        self._task: asyncio.Task | None = None
        self._session: aiohttp.ClientSession | None = None

    def start(self) -> None:
        for cat in self.entries:
            data = load_json(self.dir / f"bank_{cat}.json")
            if data:
                self.entries[cat] = data.pop("entries")
                self.meta[cat] = data
                self.status[cat] = f"{len(self.entries[cat])} 题"
        self._task = asyncio.create_task(self._refresh_loop())

    async def close(self) -> None:
        if self._task:
            self._task.cancel()
        if self._session:
            await self._session.close()

    def ready(self, cat: str) -> bool:
        return len(self.entries[cat]) >= MIN_POOL

    def pick(self, cat: str, exclude) -> dict:
        """按 niche_ratio 的概率出小众题（默认大约五题一道），其余出大众题；没分档的题都算大众题。"""
        tier = "niche" if random.random() < self.config.get("niche_ratio", 0.2) else "main"
        pool = [e for e in self.entries[cat] if e.get("tier", "main") == tier] or self.entries[cat]
        return random.choice([e for e in pool if e.get("group", e["id"]) not in exclude] or pool)

    async def image(self, entry: dict) -> bytes:
        """每次从这道题的几张截图里随机挑一张。"""
        url = random.choice(entry.get("images") or [entry["image"]])
        path = self.img_dir / f"{entry['id']}-{hashlib.sha1(url.encode()).hexdigest()[:10]}.jpg"
        if path.exists():
            return path.read_bytes()
        ua = BGM_UA if "bgm.tv" in url else WEB_UA
        async with self._http().get(url, headers={"User-Agent": ua}) as r:
            r.raise_for_status()
            data = await r.read()
        path.write_bytes(data)
        return data

    async def panels(self, entry: dict) -> list[bytes]:
        """猜地点的三张街景（同一位置相差 120° 的三个方向），现取不存盘，一张按一次计费。"""
        sv = geo.StreetView(self._http(), str(self.config.get("google_maps_key") or ""))
        heading = entry["pano"]["heading"]
        return list(await asyncio.gather(*(
            sv.image(entry["pano"]["id"], (heading + 120 * i) % 360, size) for i, size in enumerate(geo.PANELS)
        )))

    def outline(self, entry: dict) -> tuple[list, dict | None, str]:
        """(国家轮廓, 邻国底图, 题目国家在底图里的组名)，参数顺序同 grid.load_outline。"""
        if not self._shapes:
            self._shapes = load_json(self.dir / "country_shapes.json") or {}
        if "groups" not in self._shapes:  # 升级前的旧格式，只有轮廓、没有底图，重建完就没了
            return self._shapes[entry["iso"]], None, ""
        group = self._shapes["iso"][entry["iso"]]
        return geo.outline(self._shapes["groups"][group]), self._shapes["groups"], group

    def _size(self, cat: str) -> int:
        return self.config.get(f"{cat}_pool_size", 1000 if cat == "anime" else 500)

    def _media_on(self, cat: str) -> bool:
        """动画、宝可梦、国家轮廓免 key，游戏要 IGDB 的 key，地点要 Google 街景的 key。"""
        if cat in ("geo", "jpgeo"):
            return bool(str(self.config.get("google_maps_key") or "").strip())
        if cat != "game":
            return True
        return all(str(self.config.get(k) or "").strip() for k in ("igdb_client_id", "igdb_client_secret"))

    def _settings(self, cat: str) -> list:
        """影响题库内容的配置，改了就重建。"""
        c = self.config
        if cat == "anime":
            return [self._size("anime")]
        if cat == "pokemon":
            return [c.get("pokemon_main_gen", 4)]
        if cat == "geo":
            return [geo.PANOS_PER_COUNTRY, geo.MIN_PANOS, sorted(geo.EXCLUDE), geo.SAMPLING, geo.HINTS_VERSION]
        if cat == "country":
            return [geo.OUTLINE_VERSION, sorted(geo.EXCLUDE), geo.HINTS_VERSION]
        if cat == "jpgeo":
            return [geo.PANOS_PER_PREF, geo.MIN_PANOS, geo.SAMPLING, geo.HINTS_VERSION]
        return [self._size("game"), c.get("game_niche_size", 300), c.get("game_min_score", 75), c.get("game_min_reviews", 10)]

    def _stale(self, cat: str) -> bool:
        meta = self.meta[cat]
        if not meta or meta.get("settings") != self._settings(cat):
            return True
        # 上次 B 站被风控没查完，一小时后接着查（查过的有缓存，不会重复查）
        if not meta.get("complete", True) and time.time() - meta.get("built_at", 0) > 3600:
            return True
        # 升级了题目格式，或者刚填 / 删了截图来源的 key
        if meta.get("version") != BANK_VERSION or meta.get("media") != self._media_on(cat):
            return True
        return time.time() - meta.get("built_at", 0) > self.config.get("refresh_days", 7) * 86400

    def _commit(self, cat: str, entries: list, size: int, media: bool, complete: bool = True) -> None:
        self.entries[cat] = entries
        self.meta[cat] = {
            "built_at": time.time(), "size": size, "version": BANK_VERSION, "media": media, "complete": complete,
            "settings": self._settings(cat),
        }
        save_json(self.dir / f"bank_{cat}.json", {**self.meta[cat], "entries": entries})
        self.status[cat] = f"{len(entries)} 题"
        logger.info(f"[guess] {cat} bank built: {len(entries)} entries")

    async def _refresh_loop(self) -> None:
        # 游戏有缓存时一两分钟就建完，动画要等 B 站慢慢搜，放后面
        builders = {
            "game": self._build_games, "pokemon": self._build_pokemon, "country": self._build_country,
            "geo": self._build_geo, "jpgeo": self._build_jpgeo, "anime": self._build_anime,
        }
        while True:
            for cat, build in builders.items():
                if not self._stale(cat):
                    continue
                try:
                    await build()
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    self.status[cat] = f"构建失败（{e}），一小时后重试"
                    logger.exception(f"[guess] building {cat} bank failed")
            await asyncio.sleep(3600)

    async def _build_anime(self) -> None:
        size = self._size("anime")
        scan = min(size * 4, 9000)
        subjects = []
        for offset in range(0, scan, 50):
            self.status["anime"] = f"正在拉取 Bangumi 排行 {offset}/{scan}"
            params = {"type": 2, "sort": "rank", "limit": 50, "offset": offset}
            page = (await self._get(f"{BGM_API}/subjects", params=params)).get("data") or []
            subjects += page
            if len(page) < 50:
                break
            await asyncio.sleep(0.5)

        subjects = [
            s for s in subjects
            if s.get("platform") in ANIME_KINDS and s.get("name_cn") and not s.get("nsfw")
            and (s.get("images") or {}).get("large")
        ]
        subjects.sort(key=popularity, reverse=True)
        picked, heads = [], []
        for s in subjects:
            # 同系列只留最热门的一部：中文名以已选作品名开头的视为续作 / 剧场版
            key = re.sub(r"^(剧场版|劇場版)", "", norm(s["name_cn"]))
            if any(key.startswith(h) for h in heads):
                continue
            if len(key) >= 3:
                heads.append(key)
            picked.append(s)

        # 找不到图的会被剔除，所以按热度一批批往后补，直到凑够
        links = {"anilist": "P8729", "bili": "P5733"}
        wd = await self._wikidata("anime", "P5732", [str(s["id"]) for s in picked[: size * 2]], links)
        cache_path = self.dir / "media_anime.json"
        cache = load_json(cache_path) or {}
        anilist, bili = Anilist(self._http()), Bilibili(self._http(), WEB_UA)
        entries, bili_errors, complete = [], 0, True
        # 首次构建没有旧题库，边找图边开放；刷新时找完再整体替换
        if not self.entries["anime"]:
            self.entries["anime"] = entries
        try:
            for i in range(0, len(picked), 50):
                if len(entries) >= size:
                    break
                chunk = picked[i : i + 50]
                found, anilist_ok = {}, True
                if todo := [s for s in chunk if str(s["id"]) not in cache]:
                    self.status["anime"] = f"正在找截图 {i}/{len(picked)}（已可出题 {len(entries)} 道）"
                    try:
                        found = await anilist.match(todo, wd)
                    except Exception as e:
                        logger.warning(f"[guess] anilist lookup failed: {e}")
                        anilist_ok = False
                for s in chunk:
                    sid = str(s["id"])
                    info = wd.get(sid) or {}
                    if (media := cache.get(sid)) is None:
                        al = found.get(sid) or {}
                        shots, cacheable = [], anilist_ok
                        # 被风控就歇 5 分钟；连续 3 次还不行，这次构建剩下的只用横幅、不写缓存，一小时后接着查
                        if bili_errors < 3:
                            try:
                                shots = await bili.shots(s, info.get("bili"))
                                bili_errors = 0
                            except Exception as e:
                                logger.warning(f"[guess] bilibili lookup of {sid} failed: {e}")
                                bili_errors += 1
                                cacheable = False
                                if isinstance(e, HttpError) and e.status == 412:
                                    await asyncio.sleep(300)
                        else:
                            cacheable = False
                        banner = [al["bannerImage"]] if al.get("bannerImage") else []
                        media = {"images": shots or banner, "names": anilist_names(al) if al else []}
                        if cacheable:
                            cache[sid] = media
                        complete = complete and cacheable
                    if media["images"]:
                        entries.append(anime_entry(s, media, info))
                    if len(entries) >= size:
                        break
                save_json(cache_path, cache)
        finally:
            save_json(cache_path, cache)
        self._commit("anime", entries, size, True, complete)

    async def _build_games(self) -> None:
        """大众题：MC 分过线的游戏里按热度（IGDB 评分人数）取前 N；小众题：剩下的按 MC 分取前 M。

        没配 IGDB 就没有热度数据，退回按 MC 分取前 N、不分档。
        """
        size = self._size("game")
        niche_size = self.config.get("game_niche_size", 300)
        min_score = self.config.get("game_min_score", 75)
        min_reviews = self.config.get("game_min_reviews", 10)
        use_media = self._media_on("game")
        mc_key = await self._mc_key()
        items, offset = [], 0
        while use_media or len(items) < size:
            self.status["game"] = f"正在拉取 Metacritic 评分榜（已拉 {len(items)} 款）"
            params = {
                "sortBy": "-metaScore", "productType": "games", "releaseYearMin": 1958,
                "releaseYearMax": time.localtime().tm_year, "offset": offset, "limit": 50, "apiKey": mc_key,
            }
            page = (await self._get(MC_FINDER, params=params, ua=WEB_UA))["data"]["items"]
            items += [
                it for it in page
                if (it.get("criticScoreSummary") or {}).get("reviewCount", 0) >= min_reviews
                and (it.get("criticScoreSummary") or {}).get("score", 0) >= min_score
                and (it.get("image") or {}).get("bucketPath")
            ]
            if len(page) < 50 or (page[-1].get("criticScoreSummary") or {}).get("score", 0) < min_score:
                break
            offset += 50
            await asyncio.sleep(0.5)

        links = {"steam": "P1733", "igdb": "P5794"}
        wd = await self._wikidata("game", "P12054", [it["slug"] for it in items], links)
        if use_media:
            media = await self._game_media(items, wd)
            picked = split_tiers(items, media, size, niche_size)
        else:
            media = {}
            picked = [(it, "main") for it in items[:size]]

        match_path = self.dir / "bgm_game_match.json"
        matches = load_json(match_path) or {}
        entries = []
        # 首次构建没有旧题库，边匹配边开放；刷新时匹配完再整体替换
        if not self.entries["game"]:
            self.entries["game"] = entries
        for i, (it, tier) in enumerate(picked):
            slug = it["slug"]
            if slug not in matches:
                self.status["game"] = f"正在用 Bangumi 补中文名 {i}/{len(picked)}（已可出题 {len(entries)} 道）"
                try:
                    matches[slug] = await self._match_bgm(clean_mc_title(it["title"]), it.get("premiereYear"))
                except Exception as e:  # 网络错误不写缓存，下次刷新再试
                    logger.warning(f"[guess] bangumi match {slug} failed: {e}")
                if i % 25 == 0:
                    save_json(match_path, matches)
                await asyncio.sleep(1)
            entries.append({**game_entry(it, matches.get(slug), wd.get(slug), media.get(slug)), "tier": tier})
        save_json(match_path, matches)
        self._commit("game", entries, size, use_media)

    async def _build_pokemon(self) -> None:
        """前 pokemon_main_gen 个世代算大众题，之后的算小众题。"""
        self.status["pokemon"] = "正在从 PokeAPI 拉图鉴"
        species = await pokemon_species(self._http())
        by_id = {s["id"]: s for s in species}
        # Wikidata 的全国图鉴编号是三位补零的字符串，用来补「比卡超」这类旧译名、港译
        wd = await self._wikidata("pokemon", "P1685", [f"{s['id']:03d}" for s in species], {})
        main_gen = self.config.get("pokemon_main_gen", 4)
        entries = []
        for s in species:
            if entry := pokemon_entry(s, by_id, wd.get(f"{s['id']:03d}")):
                entries.append({**entry, "tier": "main" if s["generation_id"] <= main_gen else "niche"})
        self._commit("pokemon", entries, len(entries), True)

    async def _cities(self, cat: str) -> dict:
        path = self.dir / "geo_cities.json"
        cities = load_json(path)
        # 旧版城市表没有一级行政区代码（猜日本地点要用），重新下载
        if not cities or len(next(iter(cities.values()))[0]) < 5:
            self.status[cat] = "正在下载 GeoNames 城市表（一两分钟）"
            cities = await geo.fetch_cities(self._http())
            save_json(path, cities)
        return cities

    async def _countries(self, cat: str, candidates) -> tuple[list[str], dict, dict]:
        """猜地点、猜国家共用：只出主权国家（格陵兰、泽西这类属地不出），返回 (国家代码, country_basics, facts)。"""
        session = self._http()
        self.status[cat] = "正在查各国资料"
        info = await geo.fetch_country_info(session)
        facts = await geo.fetch_facts(session, sorted(candidates))
        codes = sorted(cc for cc in candidates if cc not in geo.EXCLUDE and facts.get(cc, {}).get("sovereign"))
        languages = await geo.fetch_languages(session)
        wd = await self._wikidata(cat, "P297", codes, {})
        basics = {}
        for cc in codes:
            if b := country_basics(cc, facts.get(cc, {}), wd.get(cc, {}), info.get(cc, {}), languages):
                basics[cc] = b
        return [cc for cc in codes if cc in basics], basics, facts

    async def _sample_all(self, cat: str, cache_name: str, targets: list, make_entries) -> None:
        """猜地点、猜日本地点共用：targets 是 [(键, 候选城镇, accept, 个数)]，全景缓存在 cache_name，
        找到不足 MIN_PANOS 个的每次重建再补找；make_entries(键, 全景列表) 生成题目。"""
        key = str(self.config.get("google_maps_key") or "").strip()
        cache_path = self.dir / cache_name
        cache = load_json(cache_path) or {}
        if cache.get("_sampling") != geo.SAMPLING:  # 取景规则变了，旧全景作废
            cache = {"_sampling": geo.SAMPLING}
        sv, nearest = geo.StreetView(self._http(), key), geo.Nearest(await self._cities(cat))
        entries, failures = [], 0
        # 首次构建没有旧题库，边找边开放
        if not self.entries[cat]:
            self.entries[cat] = entries
        try:
            for i, (k, towns, accept, count) in enumerate(targets):
                if len(cache.get(k, [])) < geo.MIN_PANOS:
                    self.status[cat] = f"正在找街景 {i}/{len(targets)}（已可出题 {len(entries)} 道）"
                    try:
                        found = await geo.sample(sv, towns, nearest, accept, count)
                        cache[k] = list({p["id"]: p for p in cache.get(k, []) + found}.values())
                    except (aiohttp.ClientError, asyncio.TimeoutError, HttpError) as e:
                        failures = self._media_failed("Google 街景", k, e, failures)
                        continue
                    if i % 10 == 0:
                        save_json(cache_path, cache)
                if len(cache[k]) >= geo.MIN_PANOS:
                    entries.extend(make_entries(k, cache[k]))
        finally:
            save_json(cache_path, cache)
        self._commit(cat, entries, len(entries), True)

    async def _build_geo(self) -> None:
        if not self._media_on("geo"):
            self.status["geo"] = "还没配 Google 街景的 key"
            return
        cities = await self._cities("geo")
        codes, basics, _ = await self._countries("geo", cities)
        targets = [(cc, cities[cc], lambda near, cc=cc: near[0] == cc, geo.PANOS_PER_COUNTRY) for cc in codes]
        await self._sample_all(
            "geo", "geo_panos.json", targets, lambda cc, panos: [geo_entry(cc, p, basics[cc]) for p in panos]
        )

    async def _build_jpgeo(self) -> None:
        """街景只在日本，猜都道府县。GeoNames 的一级行政区代码通过 geonameid 对上 Wikidata 的都道府县。"""
        if not self._media_on("jpgeo"):
            self.status["jpgeo"] = "还没配 Google 街景的 key"
            return
        cities = await self._cities("jpgeo")
        session = self._http()
        self.status["jpgeo"] = "正在查都道府县资料"
        admin1 = await geo.fetch_admin1(session, "JP")
        prefs = await geo.fetch_prefectures(session, list(admin1.values()))
        wd = await self._wikidata("jpgeo", "P1566", list(admin1.values()), {})
        towns = {code: [c for c in cities.get("JP", []) if c[4] == code] for code in admin1}
        targets = [
            (code, towns[code], lambda near, code=code: near[0] == "JP" and near[2] == code, geo.PANOS_PER_PREF)
            for code, gn in sorted(admin1.items()) if gn in prefs and towns[code]
        ]

        def make(code, panos):
            gn = admin1[code]
            return [e for p in panos if (e := pref_entry(code, p, prefs[gn], wd.get(gn, {})))]

        await self._sample_all("jpgeo", "jp_panos.json", targets, make)

    async def _build_country(self) -> None:
        """国家轮廓：Natural Earth 10m 国界，处理好的轮廓存 country_shapes.json。"""
        path = self.dir / "country_shapes.json"
        shapes = load_json(path) or {}
        if shapes.get("_version") != geo.OUTLINE_VERSION:
            self.status["country"] = "正在下载国界数据"
            shapes = {"_version": geo.OUTLINE_VERSION, **await geo.fetch_outlines(self._http())}
            save_json(path, shapes)
        self._shapes = shapes
        groups, iso = shapes["groups"], shapes["iso"]
        codes, basics, _ = await self._countries("country", list(iso))
        # 很小的岛国抽稀后一个环都不剩，不出题
        entries = [country_entry(cc, geo.outline(groups[iso[cc]]), basics[cc]) for cc in codes if groups[iso[cc]]]
        self._commit("country", entries, len(entries), True)

    async def _game_media(self, items: list, wd: dict) -> dict:
        """IGDB 截图优先，IGDB 上没截图再用 Steam 的。结果缓存，只查新进榜的（没有热度数据的旧缓存也重查）。"""
        path = self.dir / "media_game.json"
        cache = load_json(path) or {}
        todo = [it for it in items if "pop" not in cache.get(it["slug"], {})]
        if not todo:
            return cache
        igdb = Igdb(self._http(), self.config.get("igdb_client_id", ""), self.config.get("igdb_client_secret", ""))
        self.status["game"] = f"正在从 IGDB 批量查 {len(todo)} 款游戏的截图"
        # Wikidata 给的 IGDB slug 最准；没有就试 Metacritic 的 slug，两边多半一样，但要年份对得上
        slugs = [s for it in todo for s in ((wd.get(it["slug"]) or {}).get("igdb"), it["slug"]) if s]
        try:
            by_slug = await igdb.by_slugs(list(dict.fromkeys(slugs)))
        except Exception as e:
            self._media_failed("IGDB", "batch", e, 0)
            raise
        failures = 0
        try:
            for i, it in enumerate(todo):
                slug, year = it["slug"], it.get("premiereYear")
                info = wd.get(slug) or {}
                # 两种 slug 都要年份对得上：Wikidata 有时链到的是多年后的高清重制版
                game = next(
                    (g for s in (info.get("igdb"), slug) if (g := by_slug.get(s)) and (not year or year_close(g, year))),
                    None,
                )
                if not game:
                    self.status["game"] = f"正在从 IGDB 搜索截图 {i}/{len(todo)}"
                    try:
                        game = await igdb.search(clean_mc_title(it["title"]), year)
                    except Exception as e:
                        failures = self._media_failed("IGDB", slug, e, failures)
                        continue
                rec = igdb_media(game) if game else {"images": [], "names": [], "pop": 0}
                if not rec["images"] and not rec.get("skip") and info.get("steam"):
                    try:
                        rec["images"] = await steam_shots(self._http(), info["steam"], WEB_UA)
                    except Exception as e:  # 不写缓存，下次刷新再试
                        logger.warning(f"[guess] steam screenshots of {slug} failed: {e}")
                        continue
                    await asyncio.sleep(1.5)  # Steam 商店接口限速约 200 次 / 5 分钟
                cache[slug] = rec
        finally:
            save_json(path, cache)
        return cache

    async def _wikidata(self, cat: str, prop: str, ids: list[str], links: dict) -> dict:
        """查不到不影响出题，只是少一些译名，所以失败只记日志。"""
        self.status[cat] = "正在从 Wikidata 查各地译名"
        try:
            return await wikidata(self._http(), prop, ids, links)
        except Exception as e:
            logger.warning(f"[guess] wikidata lookup for {cat} failed: {e}")
            return {}

    @staticmethod
    def _media_failed(source: str, key: str, e: Exception, failures: int) -> int:
        """单个作品查失败就跳过；key 不对或者错误太多就中止构建，保留旧题库。"""
        if isinstance(e, HttpError) and e.status in (400, 401, 403):
            raise RuntimeError(f"{source} 拒绝访问（HTTP {e.status}），检查插件配置里的 key") from e
        logger.warning(f"[guess] {source} lookup of {key} failed: {e}")
        if failures >= 30:
            raise RuntimeError(f"{source} 出错太多（{e}）") from e
        return failures + 1

    async def _match_bgm(self, title: str, year) -> dict | None:
        payload = {"keyword": title, "filter": {"type": [4]}}
        data = await self._post(f"{BGM_API}/search/subjects", payload, params={"limit": 5})
        for s in data.get("data") or []:
            d = (s.get("date") or "")[:4]
            if year and d.isdigit() and abs(int(d) - int(year)) <= 1:
                info = infobox(s)
                return {
                    "name": s["name"],
                    "name_cn": s.get("name_cn") or "",
                    "aliases": aliases(info),
                    "info": {k: clip(info[k]) for k in GAME_INFO if info.get(k)},
                }
        return None

    def _http(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
        return self._session

    async def _mc_key(self) -> str:
        async with self._http().get(MC_PAGE, headers={"User-Agent": WEB_UA}) as r:
            r.raise_for_status()
            found = re.search(r"apiKey=(\w+)", await r.text())
        if not found:
            raise RuntimeError("Metacritic 页面里没找到 apiKey，接口可能改版了")
        return found.group(1)

    async def _get(self, url: str, *, params=None, ua: str = BGM_UA):
        return await self._request("GET", url, params=params, headers={"User-Agent": ua})

    async def _post(self, url: str, payload, *, params=None):
        return await self._request("POST", url, params=params, json=payload, headers={"User-Agent": BGM_UA})

    async def _request(self, method: str, url: str, **kw):
        """连接被重置、超时这类偶发错误重试两次，免得翻 80 页排行时断一下就整个构建作废。"""
        for attempt in range(3):
            try:
                async with self._http().request(method, url, **kw) as r:
                    r.raise_for_status()
                    return await r.json(content_type=None)
            except (aiohttp.ClientConnectionError, asyncio.TimeoutError):
                if attempt == 2:
                    raise
                await asyncio.sleep(5)
