"""猜地点 / 猜日本地点：Google 街景随机取景，猜国家或都道府县；猜国家：看国家轮廓猜。

国家资料：Wikidata（大洲、行驶方向、人口、面积、首都、英文名）、GeoNames（主要语言、人口 1.5 万以上的城市坐标）。
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

from .media import WIKIDATA, UA, HttpError

GEONAMES = "https://download.geonames.org/export/dump/"
STREETVIEW = "https://maps.googleapis.com/maps/api/streetview"
NE_CHN = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/10m_cultural/ne_10m_admin_0_countries_chn."
OUTLINE_VERSION = "chn-10m-v1"  # 轮廓处理规则的版本，改了就重新生成 country_shapes.json
# 不出题的地区代码
EXCLUDE = {"TW", "HK", "MO", "XK", "CN"}
# 主权国家按联合国会员国或 Wikidata 标了主权国家的算；丹麦的会员资格挂在「丹麦王国」条目上，ISO 代码那条没有，手动补
SOVEREIGN_EXTRA = {"DK"}
# Wikidata 的中文 label 是正式全称的，改用日常叫法
TITLES = {"NL": "荷兰", "KR": "韩国", "ML": "马里", "KP": "朝鲜"}
HINTS_VERSION = "specials-v3"  # 提示规则（hint_data、scrub）改了就换个名字，地点和国家题库重建
CONTINENTS = {"AF": "非洲", "AS": "亚洲", "EU": "欧洲", "NA": "北美洲", "OC": "大洋洲", "SA": "南美洲", "AN": "南极洲"}
DRIVING = {"Q14565199": "靠右行驶", "Q11920728": "靠左行驶"}
PANOS_PER_COUNTRY = 6
SAMPLING = "town-1km"  # 取景规则的版本，改了规则就换个名字，旧的全景缓存作废重新找
BIG_TOWN = 50000  # 优先在这么多人口以上的城镇取景，这个国家不够 3 个才退回小城镇
PANOS_PER_PREF = 4
# 日本都道府县按 JIS 编号分八地方（冲绳归九州地方）
JP_REGIONS = [(1, "北海道"), (7, "东北"), (14, "关东"), (23, "中部"), (30, "近畿"), (35, "中国地方"), (39, "四国"), (47, "九州")]
MIN_PANOS = 3  # 只找到一两个的多半是边境误差，不出题
# 三张图的请求尺寸：左边一张大图，右边上下两张小图（grid.load_panels 再缩放拼版）
PANELS = ((640, 540), (356, 302), (356, 302))


async def _download(session: aiohttp.ClientSession, url: str) -> bytes:
    timeout = aiohttp.ClientTimeout(total=300)  # GeoNames 很慢，城市表 3MB 要一两分钟
    async with session.get(url, headers={"User-Agent": UA}, timeout=timeout) as r:
        if r.status >= 400:
            raise HttpError("GeoNames", r.status)
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
    """GeoNames 的国家表：大洲代码和按使用人数排的语言代码。"""
    out = {}
    for row in (await _download(session, GEONAMES + "countryInfo.txt")).decode().splitlines():
        if row.startswith("#") or not row.strip():
            continue
        f = row.split("\t")
        langs = [code.split("-")[0] for code in f[15].split(",") if code]
        out[f[0]] = {"continent": f[8], "languages": list(dict.fromkeys(langs))}
    return out


async def _sparql(session: aiohttp.ClientSession, query: str) -> list:
    headers = {"User-Agent": UA, "Accept": "application/sparql-results+json"}
    async with session.post(WIKIDATA, data={"query": query}, headers=headers) as r:
        r.raise_for_status()
        return (await r.json(content_type=None))["results"]["bindings"]


async def fetch_facts(session: aiohttp.ClientSession, codes: list[str]) -> dict[str, dict]:
    """按 ISO 国家代码查 Wikidata：是否主权国家、英文名、行驶方向、人口、面积、首都、大洲、货币。"""
    values = " ".join(json.dumps(c) for c in codes)
    # 只按 ?iso 分组：多值属性（行驶方向、大洲…）会把一个国家拆成好几行
    rows = await _sparql(session, f"""SELECT ?iso (SAMPLE(?label) AS ?en) (SAMPLE(?side) AS ?driving) (MAX(?flag) AS ?sovereign)
      (MAX(?pop) AS ?population) (MAX(?area) AS ?size) (SAMPLE(?cur) AS ?currency)
      (GROUP_CONCAT(DISTINCT ?cap; separator="|") AS ?caps) (GROUP_CONCAT(DISTINCT ?cont; separator="|") AS ?conts) WHERE {{
      VALUES ?iso {{ {values} }} ?item wdt:P297 ?iso . FILTER NOT EXISTS {{ ?item wdt:P576 ?end }}
      OPTIONAL {{ ?item rdfs:label ?label FILTER(LANG(?label) = "en") }}
      OPTIONAL {{ {{ ?item wdt:P463 wd:Q1065 }} UNION {{ ?item wdt:P31 wd:Q3624078 }} BIND(1 AS ?flag) }}
      OPTIONAL {{ ?item wdt:P1622 ?side }}
      OPTIONAL {{ ?item wdt:P1082 ?pop }} OPTIONAL {{ ?item wdt:P2046 ?area }}
      OPTIONAL {{ ?item wdt:P36 ?c . ?c rdfs:label ?cap FILTER(LANG(?cap) = "zh-cn") }}
      OPTIONAL {{ ?item wdt:P30 ?ct . ?ct rdfs:label ?cont FILTER(LANG(?cont) = "zh-cn") }}
      OPTIONAL {{ ?item wdt:P38 ?cu . ?cu rdfs:label ?cur FILTER(LANG(?cur) = "zh-cn") }}
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
            "currency": v.get("currency", ""),
        })
    return out


async def fetch_prefectures(session: aiohttp.ClientSession, geonames: list[str]) -> dict[str, dict]:
    """按 GeoNames ID 查日本都道府县：JIS 编号、人口、面积、县厅所在地、英文名。"""
    values = " ".join(json.dumps(g) for g in geonames)
    rows = await _sparql(session, f"""SELECT ?gn (SAMPLE(?code) AS ?iso) (SAMPLE(?label) AS ?en) (MAX(?pop) AS ?population)
      (MAX(?area) AS ?size) (SAMPLE(?cap) AS ?capital) WHERE {{
      VALUES ?gn {{ {values} }} ?item wdt:P1566 ?gn ; wdt:P31 wd:Q50337 ; wdt:P300 ?code .
      OPTIONAL {{ ?item rdfs:label ?label FILTER(LANG(?label) = "en") }}
      OPTIONAL {{ ?item wdt:P1082 ?pop }} OPTIONAL {{ ?item wdt:P2046 ?area }}
      OPTIONAL {{ ?item wdt:P36 ?c . ?c rdfs:label ?cap FILTER(LANG(?cap) = "zh-cn") }}
    }} GROUP BY ?gn""")
    out = {}
    for r in rows:
        v = {k: x["value"] for k, x in r.items()}
        jis = int(v["iso"].split("-")[-1]) if v["iso"].split("-")[-1].isdigit() else 0
        out[v["gn"]] = {
            "jis": jis,
            "en": v.get("en", ""),
            "population": float(v["population"]) if v.get("population") else 0,
            "area": float(v["size"]) if v.get("size") else 0,
            "capital": v.get("capital", ""),
        }
    return out


async def fetch_languages(session: aiohttp.ClientSession) -> dict[str, str]:
    """ISO 639-1 语言代码 → 中文名。"""
    rows = await _sparql(session, """SELECT ?code ?name WHERE {
      ?l wdt:P218 ?code . ?l rdfs:label ?name FILTER(LANG(?name) = "zh-cn") }""")
    return {r["code"]["value"]: r["name"]["value"] for r in rows}


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


async def fetch_outlines(session: aiohttp.ClientSession) -> dict[str, list]:
    """Natural Earth 10m 国界 → {国家代码: 轮廓}，轮廓格式见 outline()。同一代码有多条（本土 + 属地）取点最多的。"""
    shp, dbf = await asyncio.gather(_download(session, NE_CHN + "shp"), _download(session, NE_CHN + "dbf"))
    reader = shapefile.Reader(shp=io.BytesIO(shp), dbf=io.BytesIO(dbf))
    fields = [f[0] for f in reader.fields[1:]]
    best: dict[str, shapefile.Shape] = {}
    for sr in reader.iterShapeRecords():
        cc = dict(zip(fields, sr.record)).get("ISO_A2_EH") or ""
        if len(cc) == 2 and cc.isalpha() and len(sr.shape.points) > len(getattr(best.get(cc), "points", [])):
            best[cc] = sr.shape
    return {cc: outline(shape) for cc, shape in best.items()}


def outline(shape) -> list:
    """[[是否内洞, [[经度, 纬度], ...]], ...]。

    只留本土附近的部分（离最大那块 20° 以内），不然法国带上南美的圭亚那、美国带上夏威夷，轮廓缩成一个点；
    跨 180° 经线的（俄罗斯、斐济）把西经挪到东边接上；点按 0.02° 抽稀。
    """
    bounds = list(shape.parts) + [len(shape.points)]
    rings = [shape.points[a:b] for a, b in zip(bounds, bounds[1:])]
    if shape.bbox[2] - shape.bbox[0] > 180:
        rings = [[(x + 360 if x < 0 else x, y) for x, y in ring] for ring in rings]

    def signed_area(ring):
        return sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1])) / 2

    def center(ring):
        xs, ys = [p[0] for p in ring], [p[1] for p in ring]
        return (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2

    main = max(rings, key=lambda r: abs(signed_area(r)))
    mx, my = center(main)
    out = []
    for ring in rings:
        cx, cy = center(ring)
        if math.hypot((cx - mx) * math.cos(math.radians(my)), cy - my) > 20:
            continue
        kept = [ring[0]]
        for x, y in ring[1:]:
            if abs(x - kept[-1][0]) + abs(y - kept[-1][1]) >= 0.02:
                kept.append((x, y))
        if len(kept) >= 3:
            # Shapefile 外环顺时针、内洞逆时针
            out.append([signed_area(ring) > 0, [[round(x, 3), round(y, 3)] for x, y in kept]])
    return out
