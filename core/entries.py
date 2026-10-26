"""题目条目和提示：把各数据源查到的资料拼成一道题（标题、答案、可接受的名字、题图、提示）。

这里都是纯函数，不联网；构建和刷新在 bank.py。
"""

import re

from zhconv import convert

from . import geo, hint_data
from .answer import candidates, norm
from .sources import POKEMON_ART, POKEMON_LANGS, dedupe

MC_IMG = "https://www.metacritic.com/a/img/catalog"
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
_BRACKETED = re.compile(r"[（(「『【\[][^（(「『【\[）)」』】\]]*[）)」』】\]]")


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
