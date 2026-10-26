"""题目条目和提示：把各数据源查到的资料拼成一道题（标题、答案、可接受的名字、题图、提示）。

这里都是纯函数，不联网；构建和刷新在 bank.py。
"""

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from zhconv import convert

from . import geo, hint_data
from .answer import candidates, norm
from .sources import POKEMON_ART, POKEMON_LANGS, dedupe

MC_IMG = "https://www.metacritic.com/a/img/catalog"
ANIME_KINDS = {"TV": "TV 动画", "剧场版": "剧场版", "WEB": "网络动画"}
# 动画的改编来源，看 Bangumi 的官方标签（meta_tags）
ADAPTATIONS = {"漫画改": "漫画", "轻小说改": "轻小说", "小说改": "小说", "游戏改": "游戏", "原创": "原创动画"}
# 用户标签里不算提示的：播出形式、平台、地区、评价和收藏状态；带年份的另外去掉
TAG_STOP = {
    "tv", "web", "ova", "oad", "剧场版", "动画", "日本", "日本动画", "游戏", "pc", "steam", "ns", "switch", "switch2",
    "ps", "ps1", "ps2", "ps3", "ps4", "ps5", "psv", "psp", "xbox", "xbox360", "xboxone", "xboxseriesx", "xsx", "xb1",
    "ios", "android", "3ds", "nds", "wii", "wiiu", "gba", "gbc", "gb", "n64", "ngc", "fc", "sfc", "nes", "snes", "md",
    "dc", "ss", "mac", "macos", "linux", "windows", "单机", "必买", "神作", "满分神作", "神", "经典", "名作", "佳作", "良作", "粪作",
    "补番", "补完", "已购", "想玩", "官中", "汉化", "中文", "国行", "通关", "未通关", "白金", "全成就", "必玩", "推荐",
    "喜欢", "童年", "回忆", "情怀", "个人", "未完成", "补旧番", "总集篇", "tva", "映画", "电影", "动画电影", "巨坑", "坑",
    # 改编来源单独一条提示
    "漫画改", "漫改", "轻小说改", "轻改", "小说改", "游戏改", "原创",
}
TAG_MAX_LEN = 10  # 再长的多是玩梗的整句，常带剧情
# 日文新字体 → 简体（zhconv 只管繁体）：标签里的人名常写成简体，infobox 和角色表里是日文写法
_JP_HANS = str.maketrans(
    "恵沢浜斎広竜桜戦歩辺関剣実楽黒亜栄絵帰暁駆経継軽県険権厳済雑児釈収従渋縦処焼浄縄嬢譲図穂瀬斉専銭総騒増蔵続対帯"
    "滝単団弾遅聴転伝闘徳読悩脳廃売発髪抜晩払仏変弁豊毎満黙訳薬様覧両涙霊齢労寛郷円気覚巻歓観戯挙勲鶏芸撃検験鉱剤"
    "獣奨醸摂臓択庁逓揺猟塁",
    "惠泽滨斋广龙樱战步边关剑实乐黑亚荣绘归晓驱经继轻县险权严济杂儿释收从涩纵处烧净绳娘让图穗濑齐专钱总骚增藏续对带"
    "泷单团弹迟听转传斗德读恼脑废卖发发拔晚拂佛变辩丰每满默译药样览两泪灵龄劳宽乡圆气觉卷欢观戏举勋鸡艺击检验矿剂"
    "兽奖酿摄脏择厅递摇猎垒",
)
# infobox 里不是人名、公司名的字段
NOT_PEOPLE = {
    "中文名", "别名", "话数", "放送开始", "放送星期", "播放结束", "上映年度", "片长", "官方网站", "website", "发行日期",
    "其他", "备注", "Copyright", "类型", "游戏类型", "游玩人数", "售价", "平台", "游戏引擎",
}
# 整个题库里超过这个比例的作品都有的标签算大类标签（「日常」「开放世界」），其余算特色标签
COMMON_TAG_RATIO = 0.04
WORK_HINTS_VERSION = "tags-v1"  # 动画、游戏的提示规则改了就换个名字，这两个题库会重建
GAME_INFO = ("游戏类型", "开发", "开发商", "导演", "游玩人数", "游戏引擎")
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


def _flat(value) -> str:
    if isinstance(value, list):
        return " ".join(v.get("v", "") if isinstance(v, dict) else str(v) for v in value)
    return str(value)


def bgm_tags(raw) -> list[list]:
    """Bangumi 的用户标签 [{name, count}] → [[名字, 打这个标签的人数]]，人数从多到少。"""
    return [[t["name"], t.get("count") or 0] for t in raw or [] if t.get("name")]


def name_key(text: str) -> str:
    """比较人名用：日文新字体也转成简体。"""
    return norm(text.translate(_JP_HANS))


def people(info: dict, extra: list[str] | None = None) -> list[str]:
    """infobox 里的人名和公司名（staff、制作公司……）加上 extra（角色名、声优、开发商），归一化后按分隔符拆开。"""
    values = [_flat(v) for k, v in info.items() if k not in NOT_PEOPLE] + list(extra or [])
    return [n for v in values for part in re.split(r"[、,，;；/&·]", v) if (n := name_key(part))]


@dataclass
class TagStats:
    """整个题库的标签统计：每个标签在多少部作品里出现、作品数、所有作品的 staff 和声优（标签里的人名、公司名要去掉）。"""

    df: Counter = field(default_factory=Counter)
    pool: int = 0
    people: set = field(default_factory=set)

    @classmethod
    def build(cls, tag_lists: list, people_lists: list) -> "TagStats":
        df = Counter(norm(n) for tags in tag_lists for n in {name for name, _ in tags})
        return cls(df, len(tag_lists), {n for names in people_lists for n in names})


def _overlap(a: str, b: str) -> int:
    return SequenceMatcher(None, a, b, autojunk=False).find_longest_match(0, len(a), 0, len(b)).size


def clean_tags(tags: list, titles: list[str], own: list[str], stats: TagStats, ascii_ok: bool) -> list[list]:
    """去掉没区分度的（播出形式、平台、年份、评价）、会泄露答案的（跟作品名连着两个字以上一样）、
    人名和公司名（这部的 staff、声优，或者整个题库里出现过的；另有专门的提示）和玩梗的长句。
    动画的英文标签几乎都是公司名，ascii_ok=False 时不要；游戏的英文标签多是玩法（Roguelike、co-op），留着。"""
    title_set = {n for n in (name_key(t) for t in titles if t) if n}
    title_keys = "\0".join(title_set)
    own_keys = "\0".join(own)
    out, seen = [], set()
    for name, count in tags:
        key = name_key(name)
        if (not key or key in seen or key in TAG_STOP or len(key) > TAG_MAX_LEN or re.search(r"\d{4}", name)
                or (key.isascii() and not ascii_ok) or key in stats.people or key in title_set):
            continue
        if _overlap(key, title_keys) >= (4 if key.isascii() else 2) or _overlap(key, own_keys) >= 3:
            continue
        seen.add(key)
        out.append([name, count])
    return out


def split_tags(tags: list, stats: TagStats, summary: str = "") -> tuple[list[str], list[str]]:
    """分两档：大类标签（很多作品都有，「日常」「开放世界」）按人数取前 3；特色标签（只有少数作品有）
    按「这部作品里打的人多、整个题库里少见」取前 3。只有这一部有、又出现在简介里的多半是角色名，不要。"""
    if not tags or not stats.pool:  # 没有整个题库的统计（首次构建边匹配边出的临时题目）就先不给标签
        return [], []
    top = tags[0][1] or 1
    cut = max(3, stats.pool * COMMON_TAG_RATIO)
    common = [n for n, _ in tags if stats.df[norm(n)] >= cut][:3]
    scored = []
    for name, count in tags:
        d = stats.df[norm(name)] or 1
        if d >= cut or count < max(5, top * 0.02) or (d == 1 and name in summary):
            continue
        scored.append((count / top * math.log(stats.pool / d), name))
    return common, [n for _, n in sorted(scored, reverse=True)[:3]]


def finish_entry(entry: dict) -> dict:
    """去掉会泄露答案的提示，最多 8 条，最后补一条首字提示，凑满 9 格。"""
    leaks = {c for c in candidates(entry["names"]) if len(c) >= 3}
    hints = [h for h in entry["hints"] if h[1] and not any(c in norm(h[1]) for c in leaks)]
    entry["hints"] = hints[:8] + [shape_hint(entry["title"])]
    return entry


def anime_entry(s: dict, media: dict | None = None, wd: dict | None = None, stats: TagStats | None = None,
                chars: dict | None = None) -> dict:
    """提示从宽到窄：放送季度和话数、改编来源、大类标签、动画制作、导演、特色标签、主角声优、原作。

    stats 是整个题库的标签统计，没有就不出标签提示；chars 是 Bangumi 角色表，见 Bank._characters。
    """
    info = infobox(s)
    cn, name = s["name_cn"], s["name"]
    names = dedupe([name, cn, *aliases(info), *(wd or {}).get("names", []), *(media or {}).get("names", [])])
    date = s.get("date") or ""
    eps = s.get("eps") or s.get("total_episodes")
    form = f"{ANIME_KINDS[s['platform']]} {eps} 话" if eps and s["platform"] != "剧场版" else ANIME_KINDS[s["platform"]]
    when = f"{date[:4]} 年 {int(date[5:7])} 月 · " if re.match(r"\d{4}-\d{2}", date) else ""
    adapt = next((v for k, v in ADAPTATIONS.items() if k in (s.get("meta_tags") or [])), "")
    stats, chars = stats or TagStats(), chars or {}
    tags = clean_tags(bgm_tags(s.get("tags")), names, people(info, chars.get("names")), stats, ascii_ok=False)
    common, rare = split_tags(tags, stats, s.get("summary") or "")
    hints = [
        ["放送", when + form],
        ["改编自", adapt],
        ["标签", " / ".join(common)],
        ["动画制作", clip(info.get("动画制作") or "")],
        ["导演", clip(info.get("导演") or "")],
        ["特色标签", " / ".join(rare)],
        ["主角声优", " / ".join(chars.get("cv", [])[:2])],
        ["原作", clip(info.get("原作") or "")],
    ]
    return finish_entry({
        "id": f"bgm-{s['id']}",
        "title": cn,
        "original": name,
        "answer": f"{cn}（{name}）" if cn != name else cn,
        "names": names,
        "images": (media or {}).get("images") or [s["images"]["large"]],
        "hints": hints,
    })


def game_entry(item: dict, bgm: dict | None, wd: dict | None = None, media: dict | None = None,
               stats: TagStats | None = None, dev: list[str] | None = None, chars: dict | None = None) -> dict:
    """提示从宽到窄：发售年份和平台、类型、大类标签、游玩人数、引擎、开发、导演、特色标签。

    stats、chars 同 anime_entry；dev 是 IGDB 记的开发商（Bangumi 的「开发」「发行」常是日本代理，不准）。
    """
    en = clean_mc_title(item["title"])
    bgm, wd = bgm or {}, wd or {}
    cn = bgm.get("name_cn") or wd.get("zh") or ""
    if norm(cn) == norm(en):  # Wikidata 的中文 label 有时就是英文原名
        cn = ""
    info = bgm.get("info") or {}
    names = dedupe([en, cn, bgm.get("name", ""), *bgm.get("aliases", []), *wd.get("names", []),
                    *(media or {}).get("names", [])])
    platforms = [PLATFORMS.get(p["name"], p["name"]) for p in item.get("platforms") or [] if p.get("name")]
    genres = [g["name"] for g in item.get("genres") or [] if g.get("name")]
    year = str(item.get("premiereYear") or (item.get("releaseDate") or "")[:4])
    plat = " / ".join(platforms[:3]) + (" 等" if len(platforms) > 3 else "")
    players = re.sub(r"\s+", "", info.get("游玩人数") or "")
    if re.fullmatch(r"[\d\-~～]+", players):
        players += "人"
    stats = stats or TagStats()
    own = people(info, [*(dev or []), *(chars or {}).get("names", [])]) + bgm.get("staff", [])
    common, rare = split_tags(clean_tags(bgm.get("tags") or [], names, own, stats, ascii_ok=True), stats,
                              bgm.get("summary") or "")
    hints = [
        ["发售", " · ".join(x for x in (f"{year} 年" if year else "", plat) if x)],
        ["类型", info.get("游戏类型") or " / ".join(genres)],
        ["标签", " / ".join(common)],
        ["游玩人数", players],
        ["引擎", info.get("游戏引擎") or ""],
        ["开发", clip(" / ".join(dev or []) or info.get("开发") or info.get("开发商") or "")],
        ["导演", info.get("导演") or ""],
        ["特色标签", " / ".join(rare)],
    ]
    return finish_entry({
        "id": f"mc-{item['slug']}",
        "title": cn or en,
        "original": en,
        "answer": f"{cn}（{en}）" if cn else en,
        "names": names,
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
