"""猜地点 / 猜日本地点：Google 街景随机取景，猜国家或都道府县；猜国家：看国家轮廓猜；盲猜地点：不给图，按资料对比猜国家；
盲猜日本地点：同样按资料对比，猜到市町村；盲猜中国地点：猜地级市。

国家资料：Wikidata（大洲、行驶方向、人口、面积、首都、英文名）、GeoNames（主要语言、邻国、人口 1.5 万以上的城市坐标）。
取景：在城镇中心 1 公里内随机撒点，用街景元数据接口（免费）找 600 米内的 Google 官方全景，离全景最近的城市还在这个国家才算数。
撒得太散会全是郊外公路和农田，城里才有招牌、车牌、建筑这些能猜的东西。
街景图片按张计费，出题时现取、不存盘（Google 的条款不允许长期缓存）。
轮廓用 Natural Earth 的 10m 国界数据（ne_10m_admin_0_countries_chn）。
"""

import asyncio
import io
import json
import math
import random
import zipfile

import aiohttp
import shapefile
from zhconv import convert

from .sources import UA, WIKIDATA, HttpError

GEONAMES = "https://download.geonames.org/export/dump/"
STREETVIEW = "https://maps.googleapis.com/maps/api/streetview"
NE_CHN = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/10m_cultural/ne_10m_admin_0_countries_chn."
OUTLINE_VERSION = "chn-10m-v2"  # 轮廓处理规则的版本，改了就重新生成 country_shapes.json
# 邻国底图里不画的要素：单独成块的礁石，图上只是一个点
SKIP_FEATURES = {"SCR"}
# FCLASS_CN 是这两类的要素并进所属国家，不单独画边界
MERGED_FCLASS = {"Admin-1 region", "Unrecognized"}
# 不出题的地区代码
EXCLUDE = {"TW", "HK", "MO", "XK", "CN"}
# 主权国家按联合国会员国或 Wikidata 标了主权国家的算；丹麦的会员资格挂在「丹麦王国」条目上，ISO 代码那条没有，手动补
SOVEREIGN_EXTRA = {"DK"}
# Wikidata 的中文 label 是正式全称的，改用日常叫法
TITLES = {"NL": "荷兰", "KR": "韩国", "ML": "马里", "KP": "朝鲜"}
# 提示规则（hint_data、scrub）或猜错报距离用的位置（towns、centroid）改了就换个名字，地点和国家题库重建
HINTS_VERSION = "zh-labels-v1"
CONTINENTS = {"AF": "非洲", "AS": "亚洲", "EU": "欧洲", "NA": "北美洲", "OC": "大洋洲", "SA": "南美洲", "AN": "南极洲"}
DRIVING = {"Q14565199": "靠右行驶", "Q11920728": "靠左行驶"}
PANOS_PER_COUNTRY = 6
SAMPLING = "town-1km"  # 取景规则的版本，改了规则就换个名字，旧的全景缓存作废重新找
BIG_TOWN = 50000  # 优先在这么多人口以上的城镇取景，这个国家不够 3 个才退回小城镇
PANOS_PER_PREF = 4
# 日本都道府县按 JIS 编号分八地方（冲绳归九州地方）
JP_REGIONS = [(1, "北海道"), (7, "东北"), (14, "关东"), (23, "中部"), (30, "近畿"), (35, "中国地方"), (39, "四国"), (47, "九州")]
MIN_PANOS = 3  # 只找到一两个的多半是边境误差，不出题
# 盲猜地点的「地区」：按国内地理课本的分法（中欧单列，波罗的海三国算东欧，伊朗、阿富汗算西亚，
# 墨西哥算北美），玩家凭常识就能对上。地区 -> (大洲, 国家代码)；同地区是绿格，同大洲是黄格
REGIONS = {
    "东亚": ("亚洲", "JP KR KP MN"),
    "东南亚": ("亚洲", "BN KH ID LA MY MM PH SG TH TL VN"),
    "南亚": ("亚洲", "BD BT IN LK MV NP PK"),
    "中亚": ("亚洲", "KG KZ TJ TM UZ"),
    "西亚": ("亚洲", "AE AF AM AZ BH CY GE IL IQ IR JO KW LB OM PS QA SA SY TR YE"),
    "北欧": ("欧洲", "DK FI IS NO SE"),
    "西欧": ("欧洲", "BE FR GB IE LU MC NL"),
    "中欧": ("欧洲", "AT CH CZ DE HU LI PL SK"),
    "南欧": ("欧洲", "AD AL BA BG ES GR HR IT ME MK MT PT RO RS SI SM VA"),
    "东欧": ("欧洲", "BY EE LT LV MD RU UA"),
    "北非": ("非洲", "DZ EG LY MA SD TN"),
    "西非": ("非洲", "BF BJ CI CV GH GM GN GW LR ML MR NE NG SL SN TG"),
    "中非": ("非洲", "CD CF CG CM GA GQ ST TD"),
    "东非": ("非洲", "BI DJ ER ET KE RW SC SO SS TZ UG"),
    "南部非洲": ("非洲", "AO BW KM LS MG MU MW MZ NA SZ ZA ZM ZW"),
    "北美": ("北美洲", "CA MX US"),
    "中美洲": ("北美洲", "BZ CR GT HN NI PA SV"),
    "加勒比": ("北美洲", "AG BB BS CU DM DO GD HT JM KN LC TT VC"),
    "南美": ("南美洲", "AR BO BR CL CO EC GY PE PY SR UY VE"),
    "澳新": ("大洋洲", "AU NZ"),
    "太平洋岛国": ("大洋洲", "FJ FM KI MH NR PG PW SB TO TV VU WS"),
}
REGION_OF = {cc: (region, continent) for region, (continent, codes) in REGIONS.items() for cc in codes.split()}
# 盲猜日本地点：Wikidata 里日本的市町村（市、町、村、东京的特别区）。类 -> (级别, 排位)，挂了几个类的取排位最高的；
# 东京 23 区并成一个「东京23区」，级别是特别区
# 特例市 2015 年废止，没升格成中核市的叫施行时特例市（沼津市），Wikidata 还挂着特例市的类
JP_LEVELS = {
    "Q4174776": ("村", 0), "Q1059478": ("町", 1), "Q494721": ("市", 2), "Q1145012": ("施行时特例市", 3),
    "Q1137833": ("中核市", 4), "Q1749269": ("政令市", 5), "Q5327704": ("特别区", 6),
}
JP_MUNI_TYPES = tuple(JP_LEVELS)  # 有的中核市、政令市只挂了中核市 / 政令市的类，没挂「市」，都要算上
JP_WARD = "Q5327704"
JP_CITY_WARD = "Q137773"  # 政令市下面的行政区（京都市东山区）；名胜挂在区下面，不挂在市下面


def disputed(lat: float, lng: float) -> bool:
    """Wikidata 把北方四岛上的 6 个村也算成日本的村，按坐标去掉：不出题，也不能猜。"""
    return lng > 145.7 and lat > 43.5


# 盲猜中国地点：Wikidata 里的地级行政区，只出地级市（含副省级市），直辖市、自治州、地区、盟只能拿来猜。
# 哈尔滨只挂了副省级市、没挂地级市的类；南京还挂着民国时的直辖市，所以直辖市看行政区划代码，不看类
CN_TYPES = {
    "Q748149": "地级市", "Q250811": "副省级市", "Q1208802": "直辖市", "Q788104": "自治州", "Q1045608": "地区",
    "Q288653": "盟",
}
CN_RANKS = {"地级市": 0, "自治州": 0, "地区": 0, "盟": 0, "副省级市": 1, "直辖市": 2}
# 行政区划代码（P442，「13 01」）前两位 -> (省份简称, 大区)；港澳台没有地级市，不在这里
CN_PROVINCES = {
    "11": ("北京", "华北"), "12": ("天津", "华北"), "13": ("河北", "华北"), "14": ("山西", "华北"), "15": ("内蒙古", "华北"),
    "21": ("辽宁", "东北"), "22": ("吉林", "东北"), "23": ("黑龙江", "东北"),
    "31": ("上海", "华东"), "32": ("江苏", "华东"), "33": ("浙江", "华东"), "34": ("安徽", "华东"), "35": ("福建", "华东"),
    "36": ("江西", "华东"), "37": ("山东", "华东"),
    "41": ("河南", "华中"), "42": ("湖北", "华中"), "43": ("湖南", "华中"),
    "44": ("广东", "华南"), "45": ("广西", "华南"), "46": ("海南", "华南"),
    "50": ("重庆", "西南"), "51": ("四川", "西南"), "52": ("贵州", "西南"), "53": ("云南", "西南"), "54": ("西藏", "西南"),
    "61": ("陕西", "西北"), "62": ("甘肃", "西北"), "63": ("青海", "西北"), "64": ("宁夏", "西北"), "65": ("新疆", "西北"),
}
CN_EXCLUDE = {"4603"}  # 三沙市：南海诸岛，有争议，不出题也不能猜
CN_SCENIC = "Q6838244"  # 国家 5A 级旅游景区，名胜提示从这里挑
# 三张图的请求尺寸：左边一张大图，右边上下两张小图（cards.load_panels 再缩放拼版）
PANELS = ((640, 540), (356, 302), (356, 302))


async def _download(session: aiohttp.ClientSession, url: str, source: str = "GeoNames") -> bytes:
    timeout = aiohttp.ClientTimeout(total=300)  # GeoNames 很慢，城市表 3MB 要一两分钟
    async with session.get(url, headers={"User-Agent": UA}, timeout=timeout) as r:
        if r.status >= 400:
            raise HttpError(source, r.status)
        return await r.read()


async def fetch_cities(session: aiohttp.ClientSession) -> dict[str, list]:
    """GeoNames 人口 1.5 万以上的城市，按国家分组：{国家代码: [[纬度, 经度, 人口, 英文名, 一级行政区代码], ...]}。"""
    raw = await _download(session, GEONAMES + "cities15000.zip")
    out: dict[str, list] = {}
    for row in zipfile.ZipFile(io.BytesIO(raw)).read("cities15000.txt").decode().splitlines():
        f = row.split("\t")
        out.setdefault(f[8], []).append([float(f[4]), float(f[5]), int(f[14] or 0), f[2], f[10]])
    return out


async def fetch_admin1(session: aiohttp.ClientSession, country: str) -> dict[str, str]:
    """GeoNames 一级行政区代码 → geonameid，例如日本 {"40": "1850144"}（东京都）。"""
    out = {}
    for row in (await _download(session, GEONAMES + "admin1CodesASCII.txt")).decode().splitlines():
        f = row.split("\t")
        if f[0].startswith(country + "."):
            out[f[0].split(".", 1)[1]] = f[3]
    return out


async def fetch_country_info(session: aiohttp.ClientSession) -> dict[str, dict]:
    """GeoNames 的国家表：大洲代码、按使用人数排的语言代码、陆上邻国的代码。"""
    out = {}
    for row in (await _download(session, GEONAMES + "countryInfo.txt")).decode().splitlines():
        if row.startswith("#") or not row.strip():
            continue
        f = row.split("\t")
        langs = [code.split("-")[0] for code in f[15].split(",") if code]
        neighbours = [c for c in f[17].split(",") if c]
        out[f[0]] = {"continent": f[8], "languages": list(dict.fromkeys(langs)), "neighbours": neighbours}
    return out


# 中文标签：很多货币、语言只有 zh 或 zh-hans 的标签，没有 zh-cn（只认 zh-cn 时三分之二的国家查不到货币），
# 按 zh-cn、zh-hans、zh 的顺序取，取到的再转简体
ZH_LABEL = """OPTIONAL {{ {item} rdfs:label ?l1 FILTER(LANG(?l1) = "zh-cn") }}
      OPTIONAL {{ {item} rdfs:label ?l2 FILTER(LANG(?l2) = "zh-hans") }}
      OPTIONAL {{ {item} rdfs:label ?l3 FILTER(LANG(?l3) = "zh") }} BIND(COALESCE(?l1, ?l2, ?l3) AS {label})"""


async def _sparql(session: aiohttp.ClientSession, query: str, timeout: int = 70) -> list:
    """Wikidata 那边一个查询最多跑 60 秒，这里的超时按它来，不用会话默认的。它忙的时候会回 502 / 503 / 504，
    查得太勤会回 429（每分钟的查询时间有限额），都等一会儿重试，429 按它的 Retry-After 等。"""
    headers = {"User-Agent": UA, "Accept": "application/sparql-results+json"}
    for attempt in range(4):
        async with session.post(
            WIKIDATA, data={"query": query}, headers=headers, timeout=aiohttp.ClientTimeout(total=timeout)
        ) as r:
            if r.status in (429, 502, 503, 504) and attempt < 3:
                wait = int(r.headers.get("retry-after") or 0) if r.status == 429 else 0
                await asyncio.sleep(min(120, wait or 15 * (attempt + 1)))
                continue
            r.raise_for_status()
            return (await r.json(content_type=None))["results"]["bindings"]
    return []


async def fetch_facts(session: aiohttp.ClientSession, codes: list[str]) -> dict[str, dict]:
    """按 ISO 国家代码查 Wikidata：是否主权国家、英文名、行驶方向、人口、面积、首都、大洲、货币（可能有好几种）。"""
    values = " ".join(json.dumps(c) for c in codes)
    # 只按 ?iso 分组：多值属性（行驶方向、大洲…）会把一个国家拆成好几行
    rows = await _sparql(session, f"""SELECT ?iso (SAMPLE(?label) AS ?en) (SAMPLE(?side) AS ?driving) (MAX(?flag) AS ?sovereign)
      (MAX(?pop) AS ?population) (MAX(?area) AS ?size) (GROUP_CONCAT(DISTINCT ?cur; separator="|") AS ?curs)
      (GROUP_CONCAT(DISTINCT ?cap; separator="|") AS ?caps) (GROUP_CONCAT(DISTINCT ?cont; separator="|") AS ?conts) WHERE {{
      VALUES ?iso {{ {values} }} ?item wdt:P297 ?iso . FILTER NOT EXISTS {{ ?item wdt:P576 ?end }}
      OPTIONAL {{ ?item rdfs:label ?label FILTER(LANG(?label) = "en") }}
      OPTIONAL {{ {{ ?item wdt:P463 wd:Q1065 }} UNION {{ ?item wdt:P31 wd:Q3624078 }} BIND(1 AS ?flag) }}
      OPTIONAL {{ ?item wdt:P1622 ?side }}
      OPTIONAL {{ ?item wdt:P1082 ?pop }} OPTIONAL {{ ?item wdt:P2046 ?area }}
      OPTIONAL {{ ?item wdt:P36 ?c . ?c rdfs:label ?cap FILTER(LANG(?cap) = "zh-cn") }}
      OPTIONAL {{ ?item wdt:P30 ?ct . ?ct rdfs:label ?cont FILTER(LANG(?cont) = "zh-cn") }}
      OPTIONAL {{ ?item wdt:P38 ?cu . {ZH_LABEL.format(item="?cu", label="?cur")} }}
    }} GROUP BY ?iso""")
    out = {}
    for r in rows:
        v = {k: x["value"] for k, x in r.items()}
        out.setdefault(v["iso"], {
            "sovereign": "sovereign" in v or v["iso"] in SOVEREIGN_EXTRA,
            "en": v.get("en", ""),
            "driving": DRIVING.get(v.get("driving", "").rsplit("/", 1)[-1], ""),
            "population": float(v["population"]) if v.get("population") else 0,
            "area": float(v["size"]) if v.get("size") else 0,
            "capital": (v.get("caps") or "").split("|")[0],
            "continent": " / ".join(c for c in (v.get("conts") or "").split("|") if c),
            "currencies": [convert(c, "zh-cn") for c in (v.get("curs") or "").split("|") if c],
        })
    return out


async def fetch_prefectures(session: aiohttp.ClientSession, geonames: list[str]) -> dict[str, dict]:
    """按 GeoNames ID 查日本都道府县：JIS 编号、县厅所在地、英文名。"""
    values = " ".join(json.dumps(g) for g in geonames)
    rows = await _sparql(session, f"""SELECT ?gn (SAMPLE(?code) AS ?iso) (SAMPLE(?label) AS ?en) (SAMPLE(?cap) AS ?capital)
      WHERE {{
      VALUES ?gn {{ {values} }} ?item wdt:P1566 ?gn ; wdt:P31 wd:Q50337 ; wdt:P300 ?code .
      OPTIONAL {{ ?item rdfs:label ?label FILTER(LANG(?label) = "en") }}
      OPTIONAL {{ ?item wdt:P36 ?c . ?c rdfs:label ?cap FILTER(LANG(?cap) = "zh-cn") }}
    }} GROUP BY ?gn""")
    out = {}
    for r in rows:
        v = {k: x["value"] for k, x in r.items()}
        jis = int(v["iso"].split("-")[-1]) if v["iso"].split("-")[-1].isdigit() else 0
        out[v["gn"]] = {"jis": jis, "en": v.get("en", ""), "capital": v.get("capital", "")}
    return out


async def fetch_municipalities(session: aiohttp.ClientSession) -> dict[str, dict]:
    """日本的市町村：{Wikidata ID: {zh, ja, en, level, rank, pop, area, lat, lng, pref, wiki, neighbours}}。
    pref 是都道府县的 ISO 代码（JP-26），wiki 是中文维基的条目名，neighbours 是接壤的市町村。缺坐标、人口、所属县的不要。
    拆成几个小查询：标签、所属县、维基条目都放一个 GROUP BY 里会超时。"""
    head = f"VALUES ?type {{ {' '.join(f'wd:{t}' for t in JP_MUNI_TYPES)} }} ?m wdt:P31 ?type ."
    qid = lambda r, k="m": r[k]["value"].rsplit("/", 1)[-1]  # noqa: E731
    munis: dict[str, dict] = {}
    # 面积有的按平方公里录、有的按平方米录（长崎市是 406350000），取换算成平方米的值（psn）再除
    for r in await _sparql(session, f"""SELECT ?m ?pop ?area ?coord WHERE {{ {head} FILTER NOT EXISTS {{ ?m wdt:P576 ?end }}
      OPTIONAL {{ ?m wdt:P1082 ?pop }} OPTIONAL {{ ?m wdt:P625 ?coord }}
      OPTIONAL {{ ?m p:P2046 ?st . ?st psn:P2046/wikibase:quantityAmount ?area ; wikibase:rank ?rank
        FILTER(?rank != wikibase:DeprecatedRank) }} }}"""):
        m = munis.setdefault(qid(r), {"pop": 0.0, "area": 0.0, "coord": ""})
        m["pop"] = max(m["pop"], float(r.get("pop", {}).get("value") or 0))
        m["area"] = max(m["area"], float(r.get("area", {}).get("value") or 0) / 1e6)
        m["coord"] = m["coord"] or r.get("coord", {}).get("value", "")
    levels = " ".join(f"wd:{t}" for t in JP_LEVELS)
    for r in await _sparql(session, f"SELECT ?m ?t WHERE {{ VALUES ?t {{ {levels} }} ?m wdt:P31 ?t }}"):
        if m := munis.get(qid(r)):
            level = JP_LEVELS[qid(r, "t")]
            if level[1] >= m.get("rank", -1):
                m["level"], m["rank"] = level
    # 所属都道府县看全国地方公共团体代码（P429）的前两位；顺着 P131 往上找会超时
    for r in await _sparql(session, f"SELECT ?m ?code WHERE {{ {head} ?m wdt:P429 ?code }}"):
        if (m := munis.get(qid(r))) and (code := r["code"]["value"])[:2].isdigit():
            m["pref"] = f"JP-{code[:2]}"
    labels: dict[str, dict] = {}
    keys = sorted(munis)
    for i in range(0, len(keys), 500):  # 一次查全部要四十多秒，离 Wikidata 的 60 秒上限太近
        values = " ".join(f"wd:{k}" for k in keys[i : i + 500])
        for r in await _sparql(session, f"""SELECT ?m ?l WHERE {{ VALUES ?m {{ {values} }} ?m rdfs:label ?l .
          FILTER(LANG(?l) IN ("zh-cn", "zh-hans", "zh", "ja", "en")) }}"""):
            labels.setdefault(qid(r), {})[r["l"]["xml:lang"]] = r["l"]["value"]
    for r in await _sparql(session, f"""SELECT ?m ?title WHERE {{ {head}
      ?page schema:about ?m ; schema:isPartOf <https://zh.wikipedia.org/> ; schema:name ?title }}"""):
        if m := munis.get(qid(r)):
            m["wiki"] = r["title"]["value"]
    out = {}
    for key, m in munis.items():
        lab = labels.get(key, {})
        zh = next((lab[k] for k in ("zh-cn", "zh-hans", "zh") if k in lab), "") or lab.get("ja", "")
        if not (m["coord"] and m["pop"] and m.get("pref") and zh and "level" in m):
            continue
        lng, lat = (float(x) for x in m["coord"].removeprefix("Point(").removesuffix(")").split())
        if disputed(lat, lng):
            continue
        out[key] = {
            "zh": convert(zh, "zh-cn"), "ja": lab.get("ja", ""), "en": lab.get("en", ""),
            "level": m["level"], "rank": m["rank"], "pop": m["pop"], "area": m["area"],
            "lat": round(lat, 4), "lng": round(lng, 4), "pref": m["pref"], "wiki": m.get("wiki", ""), "neighbours": [],
        }
    for r in await _sparql(session, f"SELECT ?m ?n WHERE {{ {head} ?m wdt:P47 ?n }}"):
        m, n = qid(r), qid(r, "n")
        if m in out and n in out and n not in out[m]["neighbours"]:
            out[m]["neighbours"].append(n)
    return out


async def fetch_jp_prefectures(session: aiohttp.ClientSession) -> dict[str, dict]:
    """都道府县：{ISO 代码: {zh, jis, capital}}，capital 是县厅所在地的 Wikidata ID。"""
    rows = await _sparql(session, """SELECT ?iso ?l ?cap WHERE { ?p wdt:P31 wd:Q50337 ; wdt:P300 ?iso .
      OPTIONAL { ?p wdt:P36 ?cap } OPTIONAL { ?p rdfs:label ?l FILTER(LANG(?l) IN ("zh-cn", "zh-hans", "zh")) } }""")
    out: dict[str, dict] = {}
    for r in rows:
        iso = r["iso"]["value"]
        rec = out.setdefault(iso, {
            "zh": "", "jis": int(iso.split("-")[-1]) if iso.split("-")[-1].isdigit() else 0, "labels": {},
            "capital": r.get("cap", {}).get("value", "").rsplit("/", 1)[-1],
        })
        if "l" in r:
            rec["labels"][r["l"]["xml:lang"]] = r["l"]["value"]
    for rec in out.values():
        labels = rec.pop("labels")
        rec["zh"] = convert(next((labels[k] for k in ("zh-cn", "zh-hans", "zh") if k in labels), ""), "zh-cn")
    return {iso: rec for iso, rec in out.items() if rec["zh"]}


async def fetch_landmarks(session: aiohttp.ClientSession, places: dict[str, list[str]]) -> tuple[dict[str, list], set[str]]:
    """盲猜日本地点的名胜候选：在这个地方（P131）、至少 8 个语言版本有条目的东西，按条目数从多到少。
    places 是 {键: [Wikidata ID, ...]}：东京23区是 23 个区，政令市是市加下面的行政区（fetch_city_wards）。
    返回 ({键: [[条目数, 中文名, [类的中文名, ...]], ...]}, 没查成的键)，挑哪些当提示见 entries.landmark_hints。
    大城市底下的条目上千：四个一批，超时了再一个个查，还不行的算没查成。往下多找一层（P131/P131?）慢到查不完。"""
    owner = {q: key for key, qs in places.items() for q in qs}
    found: dict[str, dict] = {}  # 条目 -> {"key", "n", "classes"}
    failed: set[str] = set()

    async def query(qs: list[str]) -> list:
        values = " ".join(f"wd:{q}" for q in qs)
        return await _sparql(session, f"""SELECT ?m ?x ?n ?c WHERE {{ VALUES ?m {{ {values} }}
          ?x wdt:P131 ?m ; wikibase:sitelinks ?n . FILTER(?n >= 8) OPTIONAL {{ ?x wdt:P31 ?c }} }}""")

    qids = sorted(owner)
    for i in range(0, len(qids), 4):
        batch = qids[i : i + 4]
        try:
            rows = await query(batch)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            rows = []
            for q in batch:
                try:
                    rows += await query([q])
                except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
                    failed.add(owner[q])
        for r in rows:
            x = r["x"]["value"].rsplit("/", 1)[-1]
            rec = found.setdefault(x, {"key": owner[r["m"]["value"].rsplit("/", 1)[-1]], "n": int(r["n"]["value"]), "classes": set()})
            if "c" in r:
                rec["classes"].add(r["c"]["value"].rsplit("/", 1)[-1])
        await asyncio.sleep(3)  # 连着查会被 429
    labels = await _zh_labels(session, sorted({*found, *(c for rec in found.values() for c in rec["classes"])}))
    out: dict[str, list] = {}
    for x, rec in sorted(found.items(), key=lambda kv: -kv[1]["n"]):
        if x in labels and rec["key"] not in failed:
            out.setdefault(rec["key"], []).append([rec["n"], labels[x], sorted(labels[c] for c in rec["classes"] if c in labels)])
    return out, failed


async def fetch_cn_prefectures(session: aiohttp.ClientSession) -> dict[str, dict]:
    """中国的地级行政区和直辖市：{Wikidata ID: {zh, level, pop, area, lat, lng, code, prov, capital, wiki, plates, dial}}。
    code 是行政区划代码的前四位（直辖市两位），prov 是前两位，capital 是不是省会（代码后两位是 01），plates 是车牌代码，dial 是长途区号。
    人口取首选的、最新的一条（多是 2020 年七普）。缺坐标、人口、代码的不要。"""
    qid = lambda r, k="x": r[k]["value"].rsplit("/", 1)[-1]  # noqa: E731
    types = " ".join(f"wd:{t}" for t in CN_TYPES)
    found: dict[str, dict] = {}
    for r in await _sparql(session, f"""SELECT ?x ?t ?code WHERE {{ VALUES ?t {{ {types} }}
      ?x wdt:P31 ?t ; wdt:P442 ?code . FILTER NOT EXISTS {{ ?x wdt:P576 ?end }} }}"""):
        rec = found.setdefault(qid(r), {"types": set(), "code": ""})
        rec["types"].add(qid(r, "t"))
        rec["code"] = rec["code"] or "".join(c for c in r["code"]["value"] if c.isdigit())[:4]
    found = {k: v for k, v in found.items() if v["code"][:2] in CN_PROVINCES and v["code"] not in CN_EXCLUDE}
    values = " ".join(f"wd:{k}" for k in sorted(found))
    pops: dict[str, tuple] = {}
    for r in await _sparql(session, f"""SELECT ?x ?pop ?rank ?when WHERE {{ VALUES ?x {{ {values} }}
      ?x p:P1082 ?st . ?st ps:P1082 ?pop ; wikibase:rank ?rank . FILTER(?rank != wikibase:DeprecatedRank)
      OPTIONAL {{ ?st pq:P585 ?when }} }}"""):
        best = (r["rank"]["value"].endswith("PreferredRank"), r.get("when", {}).get("value", ""), float(r["pop"]["value"]))
        pops[qid(r)] = max(pops.get(qid(r), best), best)
    # 面积同日本的市町村，取换算成平方米的值（psn）
    for r in await _sparql(session, f"""SELECT ?x ?area ?coord WHERE {{ VALUES ?x {{ {values} }}
      OPTIONAL {{ ?x wdt:P625 ?coord }} OPTIONAL {{ ?x p:P2046 ?st . ?st psn:P2046/wikibase:quantityAmount ?area ;
        wikibase:rank ?rank FILTER(?rank != wikibase:DeprecatedRank) }} }}"""):
        rec = found[qid(r)]
        rec["area"] = max(rec.get("area", 0.0), float(r.get("area", {}).get("value") or 0) / 1e6)
        rec["coord"] = rec.get("coord") or r.get("coord", {}).get("value", "")
    for r in await _sparql(session, f"""SELECT ?x ?plate ?dial WHERE {{ VALUES ?x {{ {values} }}
      OPTIONAL {{ ?x wdt:P395 ?plate }} OPTIONAL {{ ?x wdt:P473 ?dial }} }}"""):
        rec = found[qid(r)]
        if plate := r.get("plate", {}).get("value", "").strip():
            rec.setdefault("plates", set()).add(plate)
        if dial := "".join(c for c in r.get("dial", {}).get("value", "") if c.isdigit()).removeprefix("86"):
            rec["dial"] = rec.get("dial") or "0" + dial.lstrip("0")
    for r in await _sparql(session, f"""SELECT ?x ?title WHERE {{ VALUES ?x {{ {values} }}
      ?page schema:about ?x ; schema:isPartOf <https://zh.wikipedia.org/> ; schema:name ?title }}"""):
        found[qid(r)]["wiki"] = r["title"]["value"]
    labels = await _zh_labels(session, sorted(found))
    out = {}
    for key, rec in found.items():
        if not (rec.get("coord") and key in pops and labels.get(key)):
            continue
        code, kinds = rec["code"], [CN_TYPES[t] for t in CN_TYPES if t in rec["types"]]
        level = "直辖市" if len(code) == 2 else "副省级市" if "副省级市" in kinds else kinds[0]
        if level == "直辖市" and len(code) != 2:  # 只挂了直辖市的类、代码却是地级的，数据不对
            continue
        lng, lat = (float(x) for x in rec["coord"].removeprefix("Point(").removesuffix(")").split())
        out[key] = {
            "zh": labels[key], "level": level, "pop": pops[key][2], "area": rec.get("area", 0.0),
            "lat": round(lat, 4), "lng": round(lng, 4), "code": code, "prov": code[:2], "capital": code[2:] == "01",
            "wiki": rec.get("wiki", ""), "plates": sorted(rec.get("plates", [])), "dial": rec.get("dial", ""),
        }
    return out


async def fetch_cn_scenic(session: aiohttp.ClientSession) -> list[dict]:
    """国家 5A 级旅游景区：[{zh, n（语言版本数）, up（往上三层的 P131）, codes（这几层的行政区划代码）, spot}]。
    在哪个市看 codes 和 up，都没有的再看坐标，见 entries.cn_sights。"""
    rows = await _sparql(session, f"""SELECT ?x ?n ?coord ?a ?b ?c ?ac ?bc ?cc WHERE {{
      ?x wdt:P31 wd:{CN_SCENIC} ; wikibase:sitelinks ?n . OPTIONAL {{ ?x wdt:P625 ?coord }}
      OPTIONAL {{ ?x wdt:P131 ?a . OPTIONAL {{ ?a wdt:P442 ?ac }} OPTIONAL {{ ?a wdt:P131 ?b . OPTIONAL {{ ?b wdt:P442 ?bc }}
        OPTIONAL {{ ?b wdt:P131 ?c . OPTIONAL {{ ?c wdt:P442 ?cc }} }} }} }} }}""")
    found: dict[str, dict] = {}
    for r in rows:
        rec = found.setdefault(r["x"]["value"].rsplit("/", 1)[-1], {
            "n": int(r["n"]["value"]), "up": set(), "codes": set(), "spot": None,
        })
        rec["up"] |= {r[k]["value"].rsplit("/", 1)[-1] for k in ("a", "b", "c") if k in r}
        rec["codes"] |= {"".join(ch for ch in r[k]["value"] if ch.isdigit()) for k in ("ac", "bc", "cc") if k in r}
        if "coord" in r and not rec["spot"]:
            lng, lat = (float(x) for x in r["coord"]["value"].removeprefix("Point(").removesuffix(")").split())
            rec["spot"] = [lat, lng]
    labels = await _zh_labels(session, sorted(found))
    return [{"zh": labels[x], "n": rec["n"], "up": sorted(rec["up"]), "codes": sorted(rec["codes"]), "spot": rec["spot"]}
            for x, rec in found.items() if x in labels]


async def fetch_city_wards(session: aiohttp.ClientSession, cities: list[str]) -> dict[str, list[str]]:
    """政令市下面的行政区：{市的 Wikidata ID: [区的 Wikidata ID, ...]}。"""
    values = " ".join(f"wd:{q}" for q in cities)
    out: dict[str, list[str]] = {}
    for r in await _sparql(session, f"SELECT ?m ?w WHERE {{ VALUES ?m {{ {values} }} ?w wdt:P131 ?m ; wdt:P31 wd:{JP_CITY_WARD} }}"):
        out.setdefault(r["m"]["value"].rsplit("/", 1)[-1], []).append(r["w"]["value"].rsplit("/", 1)[-1])
    return out


async def _zh_labels(session: aiohttp.ClientSession, qids: list[str]) -> dict[str, str]:
    """中文名，按 zh-cn、zh-hans、zh 挑，转简体。三种都取回来在这边挑，在查询里用 COALESCE 挑会慢到 504。"""
    labels: dict[str, dict] = {}
    for i in range(0, len(qids), 300):
        values = " ".join(f"wd:{q}" for q in qids[i : i + 300])
        for r in await _sparql(session, f"""SELECT ?i ?l WHERE {{ VALUES ?i {{ {values} }}
          ?i rdfs:label ?l FILTER(LANG(?l) IN ("zh-cn", "zh-hans", "zh")) }}"""):
            labels.setdefault(r["i"]["value"].rsplit("/", 1)[-1], {})[r["l"]["xml:lang"]] = r["l"]["value"]
    return {q: convert(next(lab[k] for k in ("zh-cn", "zh-hans", "zh") if k in lab), "zh-cn") for q, lab in labels.items()}


async def fetch_languages(session: aiohttp.ClientSession) -> dict[str, str]:
    """ISO 639-1 语言代码 → 中文名。"""
    rows = await _sparql(session, f"""SELECT ?code ?name WHERE {{
      ?l wdt:P218 ?code . {ZH_LABEL.format(item="?l", label="?name")} FILTER(BOUND(?name)) }}""")
    return {r["code"]["value"]: convert(r["name"]["value"], "zh-cn") for r in rows}


def _km(a: list, b: list) -> float:
    (lat1, lng1), (lat2, lng2) = (map(math.radians, p) for p in (a, b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))


def distance(points: list, spot: list) -> int:
    """points（[[纬度, 经度], ...]）里离 spot 最近的一个有多远（公里）。
    100 公里以上只留两位有效数字：报个大概，不至于拿来精确定位。"""
    km = max(1, round(min(_km(p, spot) for p in points)))
    return km if km < 100 else round(km, 2 - len(str(km)))


def towns(rows: list) -> list:
    """猜地点、猜日本地点报距离用的位置：这个国家 / 都道府县所有城镇的坐标，报最近一个到拍摄地的距离。
    用中心的话，猜俄罗斯离北海道的拍摄地有六千多公里，跟直觉对不上。"""
    return [[round(r[0], 2), round(r[1], 2)] for r in rows]


def thin(rows: list, step: float = 0.5) -> list:
    """盲猜地点算两国最近距离用的城镇坐标：每 step 度见方的格子只留一个，美国三千多个城镇抽成几百个，两两比也快。"""
    cells = {}
    for r in rows:
        cells.setdefault((math.floor(r[0] / step), math.floor(r[1] / step)), [round(r[0], 2), round(r[1], 2)])
    return list(cells.values())


def gap(a: list, b: list) -> int:
    """两组城镇坐标之间最近的一对有多远（公里），取整规则同 distance。"""
    return min(distance(a, p) for p in b)


def center(rows: list) -> list[float]:
    """按人口加权的城镇中心 [纬度, 经度]，盲猜地点指方向用。在三维单位球上平均，斐济、俄罗斯这种跨 180° 经线的也对。"""
    x = y = z = 0.0
    for lat, lng, pop, *_ in rows:
        la, lo, w = math.radians(lat), math.radians(lng), max(pop, 1)
        x, y, z = x + w * math.cos(la) * math.cos(lo), y + w * math.cos(la) * math.sin(lo), z + w * math.sin(la)
    return [round(math.degrees(math.atan2(z, math.hypot(x, y))), 2), round(math.degrees(math.atan2(y, x)), 2)]


ARROWS = "↑↗→↘↓↙←↖"


def arrow(a: list, b: list) -> str:
    """从 a 看 b 在哪个方向（八方位）。按墨卡托地图上的直线（恒向线）算，经度取差得少的那边，
    和中文世界地图（太平洋居中）上看到的一致：从美国看日本是往左，不是大圆航线那样先往北。"""
    (lat1, lng1), (lat2, lng2) = (map(math.radians, p) for p in (a, b))
    dlng = (lng2 - lng1 + math.pi) % (2 * math.pi) - math.pi
    dpsi = math.log(math.tan(math.pi / 4 + lat2 / 2) / math.tan(math.pi / 4 + lat1 / 2))
    deg = math.degrees(math.atan2(dlng, dpsi)) % 360
    return ARROWS[round(deg / 45) % 8]


def _wrap(lng: float) -> float:
    return lng - 360 if lng > 180 else lng


def centroid(rings: list) -> list[float]:
    """猜国家报距离用的位置：outline() 处理过的轮廓的面积中心 [纬度, 经度]，两国报中心之间的距离。

    先投到正弦投影（经度差乘纬度余弦，等积）再求，直接在经纬度上算会高估高纬度的面积，挪威的中心被斯瓦尔巴拉到北极圈里。
    """
    xs = [x for _, ring in rings for x, _ in ring]
    lng0 = (min(xs) + max(xs)) / 2
    area = cx = cy = 0.0
    for _, ring in rings:  # 内洞和外环方向相反，有向面积直接加起来就扣掉了
        pts = [((x - lng0) * math.cos(math.radians(y)), y) for x, y in ring]
        for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]):
            cross = x1 * y2 - x2 * y1
            area, cx, cy = area + cross, cx + (x1 + x2) * cross, cy + (y1 + y2) * cross
    lat = cy / area / 3
    return [round(lat, 3), round(_wrap(lng0 + cx / area / 3 / math.cos(math.radians(lat))), 3)]


class Nearest:
    """按 1° 网格分桶找最近的城市，用来确认全景还在这个国家（或都道府县），顺便记下附近城市名。

    返回 (国家代码, 城市名, 一级行政区代码)。
    """

    def __init__(self, cities: dict[str, list]):
        self.buckets: dict[tuple, list] = {}
        for cc, rows in cities.items():
            for lat, lng, _, name, admin1 in rows:
                self.buckets.setdefault((math.floor(lat), math.floor(lng)), []).append((lat, lng, cc, name, admin1))

    def __call__(self, lat: float, lng: float) -> tuple[str, str, str] | None:
        best, best_d = None, float("inf")
        scale = math.cos(math.radians(lat))
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                for clat, clng, cc, name, admin1 in self.buckets.get((math.floor(lat) + dy, math.floor(lng) + dx), ()):
                    d = (clat - lat) ** 2 + ((clng - lng) * scale) ** 2
                    if d < best_d:
                        best, best_d = (cc, name, admin1), d
        return best


class StreetView:
    def __init__(self, session: aiohttp.ClientSession, key: str):
        self.session = session
        self.key = key.strip()

    async def find(self, lat: float, lng: float, radius: int = 600) -> dict | None:
        """附近 radius 米内的官方全景（排除用户上传的），没有返回 None。元数据接口不收费。"""
        params = {"location": f"{lat:.5f},{lng:.5f}", "radius": radius, "source": "outdoor", "key": self.key}
        async with self.session.get(f"{STREETVIEW}/metadata", params=params) as r:
            if r.status >= 400:
                raise HttpError("Google 街景", r.status)
            data = await r.json(content_type=None)
        status = data.get("status")
        if status in ("REQUEST_DENIED", "OVER_QUERY_LIMIT", "INVALID_REQUEST"):
            # 没开通 API、没绑卡、key 填错都走这里；Google 的说明里不带 key，可以直接显示
            raise RuntimeError(f"Google 街景拒绝访问：{data.get('error_message') or status}")
        if status != "OK" or "Google" not in (data.get("copyright") or ""):
            return None
        return {"id": data["pano_id"], "lat": data["location"]["lat"], "lng": data["location"]["lng"]}

    async def image(self, pano: str, heading: int, size: tuple[int, int], fov: int = 90) -> bytes:
        params = {
            "pano": pano, "heading": heading, "size": f"{size[0]}x{size[1]}", "fov": fov, "pitch": 0,
            "return_error_code": "true", "key": self.key,
        }
        async with self.session.get(STREETVIEW, params=params) as r:
            if r.status >= 400:
                raise HttpError("Google 街景", r.status)
            return await r.read()


async def sample(sv: StreetView, cities: list, nearest: Nearest, accept, count: int = PANOS_PER_COUNTRY) -> list[dict]:
    """在这些城镇的中心附近（按人口开方加权，偏移 1 公里内）找最多 count 个全景。

    accept(最近城市) 判断全景是否还在要的国家 / 都道府县里。

    元数据接口返回离撒点最近的全景，城里常是用户上传的全景球（不要），官方的大概一半，
    所以连续 12 次都找不到才当这个国家没有街景覆盖（元数据查询不收费）。
    """
    big = [c for c in cities if c[2] >= BIG_TOWN]
    cities = big if len(big) >= 3 else cities
    weights = [math.sqrt(c[2] or 1) for c in cities]
    panos: dict[str, dict] = {}
    misses = 0
    while len(panos) < count and misses < 12:
        lat, lng = random.choices(cities, weights)[0][:2]
        lat += random.uniform(-0.009, 0.009)
        lng += random.uniform(-0.009, 0.009) / max(0.2, math.cos(math.radians(lat)))
        pano = await sv.find(lat, lng)
        await asyncio.sleep(0.05)
        near = pano and nearest(pano["lat"], pano["lng"])
        if not pano or not near or not accept(near) or pano["id"] in panos:
            misses += 1
            continue
        misses = 0
        panos[pano["id"]] = {**pano, "near": near[1], "heading": random.randrange(360)}
    return list(panos.values())


async def fetch_outlines(session: aiohttp.ClientSession) -> dict:
    """Natural Earth 10m 国界 → {"groups": {组名: 轮廓}, "iso": {国家代码: 组名}}，轮廓格式见 outline()。

    要素按国家归组，FCLASS_CN 标成一级行政区或不单列的并进所属国家，画邻国底图时不在中间画边界；
    同一国家代码有本土 + 属地好几条要素的，对到点最多的那条所在的组。
    """
    shp, dbf = await asyncio.gather(*(_download(session, NE_CHN + ext, "Natural Earth") for ext in ("shp", "dbf")))
    reader = shapefile.Reader(shp=io.BytesIO(shp), dbf=io.BytesIO(dbf))
    fields = [f[0] for f in reader.fields[1:]]
    groups: dict[str, list] = {}
    iso: dict[str, str] = {}
    size: dict[str, int] = {}
    for sr in reader.iterShapeRecords():
        rec = dict(zip(fields, sr.record))
        if rec["ADM0_A3"] in SKIP_FEATURES:
            continue
        group = rec["SOVEREIGNT"] if rec["FCLASS_CN"] in MERGED_FCLASS else rec["ADMIN"]
        groups.setdefault(group, []).extend(
            [hole, thin] for hole, ring in _rings(sr.shape) if (thin := _thin(ring))
        )
        cc = rec.get("ISO_A2_EH") or ""
        if len(cc) == 2 and cc.isalpha() and len(sr.shape.points) > size.get(cc, 0):
            size[cc], iso[cc] = len(sr.shape.points), group
    return {"groups": groups, "iso": iso}


def _signed_area(ring) -> float:
    return sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1])) / 2


def _rings(shape) -> list:
    """[(是否内洞, 点列), ...]；Shapefile 外环顺时针、内洞逆时针。"""
    bounds = list(shape.parts) + [len(shape.points)]
    return [(_signed_area(r) > 0, r) for r in (shape.points[a:b] for a, b in zip(bounds, bounds[1:]))]


def _thin(ring) -> list:
    """点按 0.02° 抽稀，剩不到 3 个点的（很小的岛）返回空列表。"""
    kept = [ring[0]]
    for x, y in ring[1:]:
        if abs(x - kept[-1][0]) + abs(y - kept[-1][1]) >= 0.02:
            kept.append((x, y))
    return [[round(x, 3), round(y, 3)] for x, y in kept] if len(kept) >= 3 else []


def outline(rings: list) -> list:
    """一个国家的轮廓 [[是否内洞, [[经度, 纬度], ...]], ...] 里只留本土附近的部分（离最大那块 20° 以内），
    不然法国带上南美的圭亚那、美国带上夏威夷，轮廓缩成一个点；跨 180° 经线的（俄罗斯、斐济）把西经挪到东边接上。
    """
    if max(x for _, r in rings for x, _ in r) - min(x for _, r in rings for x, _ in r) > 180:
        rings = [[hole, [[x + 360 if x < 0 else x, y] for x, y in ring]] for hole, ring in rings]

    def center(ring):
        xs, ys = [p[0] for p in ring], [p[1] for p in ring]
        return (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2

    mx, my = center(max((r for _, r in rings), key=lambda r: abs(_signed_area(r))))
    out = []
    for hole, ring in rings:
        cx, cy = center(ring)
        if math.hypot((cx - mx) * math.cos(math.radians(my)), cy - my) <= 20:
            out.append([hole, ring])
    return out
