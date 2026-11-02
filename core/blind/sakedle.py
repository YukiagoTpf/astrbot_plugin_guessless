"""盲猜日本酒：猜一个銘柄，对比产地（都道府县）、方位、人气、华丽、浓醇、辛口。

数据是さけのわデータ（sources.sakenowa）：銘柄、酒蔵和所在的都道府县、人气排名（全国前 100 和各县的榜）、风味
（六项打分和风味标签）。全国前 100 和各县榜前 AREA_TOP 名出题（没有风味数据的不出），所有銘柄都能猜。
这份数据要注明出处：卡片角注和答案卡上写さけのわ（sakenowa.com）。县的位置按县厅所在地算（geo.fetch_jp_prefectures）。
"""

import re

from .. import geo, sake_data
from ..entries import assign_names, scrub, to_hans
from ..sources import dedupe, sakenowa
from .base import Blind, away, blank, entry, here, num, tiered

VERSION = "sakenowa-v1"  # 选题、条目格式、提示规则、sake_data 改了就换个名字，题库重建
COLUMNS = ("产地", "方位", "人气", "华丽", "浓醇", "辛口")
CLUES = ("风味", "酒蔵", "首字")
AREA_TOP = 5  # 各县榜前几名出题（加上全国前 100，两百多道）
NEAR_KM = 150  # 两县的县厅相距这么近以内，方位格标黄
FLAVORS = (("f1", "华丽"), ("f2", "浓醇"), ("f5", "辛口"))  # さけのわ六项风味里拿来比的：華やか、芳醇、ドライ
FLAVOR_NEAR = 8  # 风味打分（0–100）差这么多以内标黄
# 人气分档，从热门到冷门：在全国榜上的看全国名次，不在的看县榜
TIERS = ("全国前10", "全国前30", "全国前100", "县内前3", "县内前10", "县榜上", "没上榜")
CREDIT = "さけのわ（sakenowa.com）"  # さけのわデータ要求注明出处
# 銘柄名、酒蔵名带这些的不出题（还能猜）：皇国、尊皇、旭日、战胜、水军……（只能加不能删）
NO_ANSWER = re.compile(r"皇国|尊皇|皇軍|旭日|戦勝|軍|艦|特攻|神風|靖国|護国|大和魂|零戦|東郷|乃木|帝国|満州|愛国|報国")
KANA = re.compile(r"[぀-ヿ]")


def tier_of(overall: int | None, area: int | None) -> int:
    """TIERS 里的第几档。"""
    if overall:
        return 0 if overall <= 10 else 1 if overall <= 30 else 2
    if area:
        return 3 if area <= 3 else 4 if area <= 10 else 5
    return 6


def sake_names(name: str) -> tuple[str, list[str]]:
    """(表里写的名字, 能猜的叫法)。全是汉字的写成简体（獺祭→獭祭、黒龍→黑龙），带假名的照原样写；
    「W (ダブリュー)」括号里是读音，表里只写括号前的。叫法有原名、简体、括号前后各一半，「の」写成「之」的（「风之森」），
    sake_data.ALIASES 里另记的（「九平次」）。单字的也算（「作」）：盲猜日本酒只认整句就是这个名字的；
    两个字母以内的英文不算，群里随口就说了。"""
    base = re.sub(r"\s*[（(][^）)]*[）)]\s*$", "", name) or name
    hans = to_hans(base)
    names = [name, base, hans, *re.findall(r"[（(]([^）)]+)[）)]", name), *sake_data.ALIASES.get(name, [])]
    if "の" in hans and not KANA.search(hans.replace("の", "")):
        names.append(hans.replace("の", "之"))
    names = [n for n in dedupe(names) if not (n.isascii() and len(n) <= 2)]
    return (base if KANA.search(base) else hans), names


def attrs_of(key: str, pref: dict | None, tier: int, chart: dict | None) -> dict:
    """对比用的资料，见 row。pref 是酒蔵所在的都道府县（海外的酒蔵是 None）。"""
    region = next((name for last, name in geo.JP_REGIONS if pref and pref["jis"] <= last), "")
    return {
        "key": key, "pref": pref["zh"] if pref else "海外", "region": region, "spot": pref["spot"] if pref else None,
        "tier": tier, **({f: round(chart[f] * 100) for f, _ in FLAVORS} if chart else {}),
    }


def row(guess: dict, answer: dict) -> list[list[str]]:
    """产地：同县绿、同地方（八地方）黄。方位：同县写「同一个县」，不同县写两县县厅相距多远，NEAR_KM 以内标黄，
    箭头是从猜的县看答案在哪个方向。人气：同一档绿、差一档黄，箭头指答案更热门（↑）还是更冷门（↓）。
    华丽、浓醇、辛口：さけのわ的风味打分（0–100），差 FLAVOR_NEAR 以内黄，箭头指答案更高还是更低；没有风味数据的写「—」。"""
    if guess["key"] == answer["key"]:
        place = here()
    elif guess["pref"] == answer["pref"]:
        place = ["同一个县", "hit", ""]
    elif guess["spot"] and answer["spot"]:
        km = geo.distance([guess["spot"]], answer["spot"])
        place = away(km, NEAR_KM, geo.arrow(guess["spot"], answer["spot"]))
    else:
        place = blank()
    x, y = guess["tier"], answer["tier"]
    flavors = [
        num(guess[f], answer[f], str(guess[f]), abs(guess[f] - answer[f]) <= FLAVOR_NEAR)
        if f in guess and f in answer else blank()
        for f, _ in FLAVORS
    ]
    return [
        tiered(guess["pref"], answer["pref"], bool(guess["region"]) and guess["region"] == answer["region"]),
        place,
        num(-x, -y, TIERS[x], abs(x - y) == 1),
        *flavors,
    ]


async def build(bank) -> None:
    session = bank.http()
    bank.status["sakedle"] = "正在拉さけのわ的日本酒数据"
    data = await sakenowa(session)
    bank.status["sakedle"] = "正在查都道府县"
    prefs = {p["jis"]: p for p in (await geo.fetch_jp_prefectures(session)).values()}
    brands = {b["id"]: b for b in data["brands"]["brands"]}
    breweries = {b["id"]: b for b in data["breweries"]["breweries"]}
    charts = {c["brandId"]: c for c in data["flavor-charts"]["flavorCharts"]}
    tag_names = {t["id"]: t["tag"] for t in data["flavor-tags"]["tags"]}
    tags = {t["brandId"]: t["tagIds"] for t in data["brand-flavor-tags"]["flavorTags"]}
    overall = {r["brandId"]: r["rank"] for r in data["rankings"]["overall"]}
    area: dict[int, int] = {}
    for a in data["rankings"]["areas"]:
        for r in a["ranking"]:
            area.setdefault(r["brandId"], r["rank"])
    # 热门的排前面，同名的叫法归它（男山有好几家）
    order = sorted(brands, key=lambda b: (overall.get(b, 999), area.get(b, 999), b))
    brewery = {b: breweries.get(brands[b]["breweryId"]) or {"name": "", "areaId": 0} for b in brands}
    titles, names = {}, {}
    for b in order:
        titles[b], names[b] = sake_names(brands[b]["name"])
    names = assign_names(names, order)
    pref = {b: prefs.get(brewery[b]["areaId"]) for b in brands}
    attrs = {b: attrs_of(str(b), pref[b], tier_of(overall.get(b), area.get(b)), charts.get(b)) for b in brands}
    places = {str(b): {"title": titles[b], "names": names[b], "attrs": attrs[b]} for b in order if names[b]}
    # 表里写的名字打不出来的（「W」只认「W (ダブリュー)」）不出题
    pool = [
        b for b in order
        if (b in overall or area.get(b, 999) <= AREA_TOP) and b in charts and pref[b] and titles[b] in names[b]
        and not NO_ANSWER.search(brands[b]["name"] + brewery[b]["name"])
    ]
    entries = []
    for b in pool:
        words = [n for n in names[b] if not n.isascii()]
        flavor = dedupe(sake_data.TAGS[t] for t in (tag_names.get(i) for i in tags.get(b, [])) if t in sake_data.TAGS)
        maker = to_hans(brewery[b]["name"])
        hints = [["风味", "、".join(flavor[:3])], ["酒蔵", scrub(maker, words, titles[b])], ["数据", CREDIT]]
        p = attrs[b]["pref"]
        original = brands[b]["name"] if brands[b]["name"] != titles[b] else ""
        main = b in overall or area.get(b) == 1  # 全国前 100 和各县第一是大众题
        entries.append(entry("sk", str(b), titles[b], original or maker, f"{titles[b]}（{p} · {maker}）", names[b],
                             hints, attrs[b], main))
    bank.commit("sakedle", entries, places=places)


MODE = Blind(
    cat="sakedle", command="盲猜日本酒", desc="不给图，猜一个日本酒銘柄看产地、人气、风味对比", header="酒",
    columns=COLUMNS, clues=CLUES, row=row, build=build, settings=(VERSION,), exact=True,
    aliases=("猜日本酒", "盲猜清酒", "猜清酒"),
)
