"""外部数据源：Wikidata（各地区译名、跨站 ID）、B 站 / AniList（动画）、AnimeThemes（动画 OP / ED）、
IGDB / Steam（游戏）、PokeAPI（宝可梦）。"""

import asyncio
import json
import re
from datetime import datetime, timezone

import aiohttp
from zhconv import convert

from .answer import norm

UA = "YukiagoTpf/astrbot_plugin_guessless"
WIKIDATA = "https://query.wikidata.org/sparql"
# 中文 label 按这个顺序挑一个当题目的中文名，繁体会转成简体
ZH_LANGS = ("zh-cn", "zh-hans", "zh", "zh-sg", "zh-my", "zh-hant", "zh-tw", "zh-hk", "zh-mo")
NAME_LANGS = (*ZH_LANGS, "ja", "en", "mul")
BILI_HOME = "https://www.bilibili.com/"
BILI_SEARCH = "https://api.bilibili.com/x/web-interface/search/type"
ANILIST = "https://graphql.anilist.co"
ANILIST_FIELDS = "id title { romaji english native } synonyms bannerImage startDate { year } format"
# Bangumi 的放送类型 → AniList 的 format，只靠年份匹配时用来排除 OVA、特别篇
ANILIST_FORMATS = {"TV": {"TV", "TV_SHORT"}, "WEB": {"ONA", "TV", "TV_SHORT"}, "剧场版": {"MOVIE"}}
# 免 key，限速每分钟 90 次；按 AniList / MAL 的 ID 查，音频是几 MB 的 ogg
ANIMETHEMES = "https://api.animethemes.moe/anime"
TWITCH_TOKEN = "https://id.twitch.tv/oauth2/token"
IGDB_GAMES = "https://api.igdb.com/v4/games"
IGDB_IMG = "https://images.igdb.com/igdb/image/upload/t_screenshot_huge/{}.jpg"
IGDB_FIELDS = (
    "fields slug,name,first_release_date,total_rating_count,game_type,version_parent,parent_game,"
    "alternative_names.name,game_localizations.name,screenshots.image_id;"
)
# DLC、资料片、合集、季票之类不出题
IGDB_SKIP_TYPES = {1, 2, 3, 5, 6, 7, 13, 14}
# 重制 / 加强版 / 移植：本体也在候选里时不出（截图和本体一样，热度也算在本体上）
IGDB_EDITION_TYPES = {9, 10, 11}
STEAM_API = "https://store.steampowered.com/api/appdetails"
POKEAPI = "https://beta.pokeapi.co/graphql/v1beta"
POKEMON_ART = "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/other/official-artwork/{}.png"
# PokeAPI 的语言 ID：1 日文假名、2 日文罗马音、4 繁中、9 英文、11 日文、12 简中
POKEMON_LANGS = {1: "ja-hrkt", 2: "ja-roma", 4: "zh-hant", 9: "en", 11: "ja", 12: "zh-hans"}
POKEMON_QUERY = """query {
  pokemon_v2_pokemonspecies(order_by: {id: asc}) {
    id generation_id is_legendary is_mythical evolves_from_species_id
    pokemon_v2_pokemonspeciesnames(where: {language_id: {_in: [1, 2, 4, 9, 11, 12]}}) { name genus language_id }
    pokemon_v2_pokemoncolor { pokemon_v2_pokemoncolornames(where: {language_id: {_eq: 12}}) { name } }
    pokemon_v2_pokemons(where: {is_default: {_eq: true}}) {
      height weight
      pokemon_v2_pokemontypes(order_by: {slot: asc}) {
        pokemon_v2_type { pokemon_v2_typenames(where: {language_id: {_eq: 12}}) { name } }
      }
      pokemon_v2_pokemonabilities(order_by: {slot: asc}) {
        is_hidden pokemon_v2_ability { pokemon_v2_abilitynames(where: {language_id: {_eq: 12}}) { name } }
      }
    }
  }
}"""
MAX_SHOTS = 12


class HttpError(Exception):
    """不带 URL 的 HTTP 错误：请求里可能带着 key，原始异常信息会把它打进日志和群里的构建状态。"""

    def __init__(self, source: str, status: int):
        super().__init__(f"{source} HTTP {status}")
        self.status = status


def dedupe(names) -> list[str]:
    return [n for n in dict.fromkeys(n.strip() for n in names if isinstance(n, str)) if n]


async def wikidata(session: aiohttp.ClientSession, prop: str, ids: list[str], links: dict[str, str]) -> dict:
    """按外部 ID 批量查 Wikidata。

    返回 {外部 ID: {"names": 中日英各地区的名称和别名, "zh": 中文名或空, 链接名: 其他站点的 ID}}，
    links 形如 {"steam": "P1733"}。
    """
    langs = ", ".join(f'"{lang}"' for lang in NAME_LANGS)
    optional = " ".join(f"OPTIONAL {{ ?item wdt:{p} ?{k} }}" for k, p in links.items())
    labels: dict[str, dict] = {}
    out: dict[str, dict] = {}
    for i in range(0, len(ids), 200):
        values = " ".join(json.dumps(x) for x in ids[i : i + 200])
        query = f"""SELECT ?v ?name ?kind {" ".join("?" + k for k in links)} WHERE {{
          VALUES ?v {{ {values} }} ?item wdt:{prop} ?v . {optional}
          OPTIONAL {{
            {{ ?item rdfs:label ?name BIND("label" AS ?kind) }} UNION {{ ?item skos:altLabel ?name BIND("alias" AS ?kind) }}
            FILTER(LANG(?name) IN ({langs}))
          }}
        }}"""
        headers = {"User-Agent": UA, "Accept": "application/sparql-results+json"}
        async with session.post(WIKIDATA, data={"query": query}, headers=headers) as r:
            r.raise_for_status()
            rows = (await r.json(content_type=None))["results"]["bindings"]
        for row in rows:
            key = row["v"]["value"]
            rec = out.setdefault(key, {"names": []})
            for k in links:
                if k in row:
                    rec.setdefault(k, row[k]["value"])
            if name := row.get("name"):
                rec["names"].append(name["value"])
                if row["kind"]["value"] == "label":
                    labels.setdefault(key, {})[name.get("xml:lang")] = name["value"]
        await asyncio.sleep(1)
    for key, rec in out.items():
        rec["names"] = dedupe(rec["names"])
        zh = next((labels.get(key, {})[lang] for lang in ZH_LANGS if lang in labels.get(key, {})), "")
        rec["zh"] = convert(zh, "zh-cn") if zh else ""
    return out


def _year(s: dict) -> int | None:
    day = s.get("date") or ""
    return int(day[:4]) if day[:4].isdigit() else None


class Bilibili:
    """动画截图：B 站番剧的分集封面基本都是正片画面。免 key，但搜索要先拿 buvid3 cookie，否则 412。

    只有 B 站有版权的番搜得到，搜不到就返回空，由 AniList 横幅兜底。搜得太快（实测 1 秒 1 次、七八十次后）
    会被风控回 412，要歇一阵才恢复，所以 3 秒搜一次。
    """

    def __init__(self, session: aiohttp.ClientSession, ua: str):
        self.session = session
        self.ua = ua
        self.ready = False

    async def shots(self, s: dict, media_id: str | None) -> list[str]:
        if not self.ready:
            async with self.session.get(BILI_HOME, headers={"User-Agent": self.ua}) as r:
                await r.read()
            self.ready = True
        params = {"search_type": "media_bangumi" if s["platform"] != "剧场版" else "media_ft", "keyword": s["name_cn"]}
        headers = {"User-Agent": self.ua, "Referer": BILI_HOME}
        async with self.session.get(BILI_SEARCH, params=params, headers=headers) as r:
            if r.status >= 400:
                raise HttpError("B站", r.status)
            data = await r.json(content_type=None)
        await asyncio.sleep(3)
        if data.get("code") == -412:  # 风控
            raise HttpError("B站", 412)
        if data.get("code") != 0:  # 有些关键词会回 -111 等，当搜不到处理
            return []
        hit = _bili_pick((data.get("data") or {}).get("result") or [], s, media_id)
        covers = [e["cover"] for e in (hit or {}).get("eps") or [] if e.get("cover")]
        # 新番的封面是 2MB 左右的 png，让 B 站缩到 1280 宽转 jpg
        return [re.sub(r"^http:", "https:", c) + "@1280w.jpg" for c in _spread(covers, MAX_SHOTS)]


def _bili_pick(results: list, s: dict, media_id: str | None) -> dict | None:
    """Wikidata 给了 B 站 media ID 就按 ID 认；否则标题一致的优先，标题以中文名开头的要年份对得上。

    B 站的上线时间可能比日本首播晚好几年（老番后来才引进），所以标题完全一致时不看年份。
    """
    mid = re.sub(r"\D", "", media_id or "")
    if mid and (hit := next((r for r in results if str(r.get("media_id")) == mid), None)):
        return hit
    want = {w for w in (norm(s["name_cn"]), norm(s["name"])) if w}
    year = _year(s)
    prefixed = None
    for r in results:
        title = norm(re.sub(r"<[^>]+>", "", r.get("title") or ""))
        if title in want:
            return r
        pub = r.get("pubtime")
        close = year and pub and abs(datetime.fromtimestamp(pub, timezone.utc).year - year) <= 1
        if prefixed is None and close and any(title.startswith(w) for w in want):
            prefixed = r
    return prefixed


class Anilist:
    """横幅图（B 站没有时兜底）和罗马音 / 英文名。免 key，限速每分钟 30 次，所以按 ID 一次查 50 部，搜索一次并 10 个。"""

    def __init__(self, session: aiohttp.ClientSession):
        self.session = session

    async def match(self, subjects: list[dict], wd: dict) -> dict[str, dict]:
        """Bangumi 条目 → {Bangumi ID: AniList 条目}。Wikidata 有 AniList ID 的直接按 ID 查，其余按原名搜。"""
        out = {}
        by_id = {}
        for s in subjects:
            aid = str((wd.get(str(s["id"])) or {}).get("anilist") or "")
            if aid.isdigit():
                by_id[int(aid)] = str(s["id"])
        ids = list(by_id)
        for i in range(0, len(ids), 50):
            query = f"query($ids: [Int]) {{ Page(perPage: 50) {{ media(id_in: $ids, type: ANIME) {{ {ANILIST_FIELDS} }} }} }}"
            data = await self._post(query, {"ids": ids[i : i + 50]})
            for m in (data.get("Page") or {}).get("media") or []:
                out[by_id[m["id"]]] = m
        rest = [s for s in subjects if str(s["id"]) not in out]
        for i in range(0, len(rest), 10):
            chunk = rest[i : i + 10]
            args = ", ".join(f"$s{j}: String" for j in range(len(chunk)))
            body = " ".join(
                f"q{j}: Page(perPage: 5) {{ media(search: $s{j}, type: ANIME) {{ {ANILIST_FIELDS} }} }}"
                for j in range(len(chunk))
            )
            data = await self._post(f"query({args}) {{ {body} }}", {f"s{j}": s["name"] for j, s in enumerate(chunk)})
            for j, s in enumerate(chunk):
                if m := _anilist_pick((data.get(f"q{j}") or {}).get("media") or [], s):
                    out[str(s["id"])] = m
        return out

    async def _post(self, query: str, variables: dict) -> dict:
        headers = {"User-Agent": UA, "Accept": "application/json"}
        for _ in range(3):
            async with self.session.post(ANILIST, json={"query": query, "variables": variables}, headers=headers) as r:
                if r.status == 429:
                    wait = int(r.headers.get("Retry-After") or 60)
                else:
                    if r.status >= 400:
                        raise HttpError("AniList", r.status)
                    data = await r.json(content_type=None)
                    wait = 0
            if wait:
                await asyncio.sleep(wait + 1)
                continue
            await asyncio.sleep(2.1)
            return data.get("data") or {}
        raise HttpError("AniList", 429)


def anilist_names(m: dict) -> list[str]:
    t = m.get("title") or {}
    return dedupe([t.get("native"), t.get("romaji"), t.get("english"), *(m.get("synonyms") or [])])


def anime_themes(anime: dict) -> list[dict]:
    """AnimeThemes 一部动画的 OP / ED：[{"type", "seq", "song", "artists", "audio"}, ...]。
    每首取第一个有音频、没标 NSFW 的版本；同一个音频文件挂在几首下面的只留一次。"""
    out, seen = [], set()
    for t in anime.get("animethemes") or []:
        if t.get("type") not in ("OP", "ED"):
            continue
        audio = next((
            v["audio"]["link"] for e in t.get("animethemeentries") or [] if not e.get("nsfw")
            for v in e.get("videos") or [] if (v.get("audio") or {}).get("link")
        ), None)
        if not audio or audio in seen:
            continue
        seen.add(audio)
        song = t.get("song") or {}
        out.append({
            "type": t["type"], "seq": t.get("sequence") or 0, "song": song.get("title") or "",
            "artists": [a["name"] for a in song.get("artists") or []][:3], "audio": audio,
        })
    return out


def _anilist_pick(media: list, s: dict) -> dict | None:
    """名字一致的优先（同名重制版看年份），没有再取年份和类型都对得上的第一个。"""
    want = {w for w in (norm(s["name"]), norm(s["name_cn"])) if w}
    year = _year(s)

    def close(m):
        y = (m.get("startDate") or {}).get("year")
        return bool(year and y and abs(y - year) <= 1)

    named = [m for m in media if {norm(n) for n in anilist_names(m)} & want]
    if named:
        return next((m for m in named if close(m)), named[0])
    formats = ANILIST_FORMATS.get(s.get("platform"), set())
    return next((m for m in media if close(m) and m.get("format") in formats), None)


def _spread(items: list, n: int) -> list:
    if len(items) <= n:
        return items
    return [items[round(i * (len(items) - 1) / (n - 1))] for i in range(n)]


class Igdb:
    """游戏截图和别名。需要 Twitch 开发者应用的 Client ID / Secret，限速 4 次每秒。"""

    def __init__(self, session: aiohttp.ClientSession, client_id: str, secret: str):
        self.session = session
        self.client_id = client_id.strip()
        self.secret = secret.strip()
        self.token = ""

    async def query(self, body: str) -> list:
        for retry in (False, True):
            if not self.token or retry:
                form = {"client_id": self.client_id, "client_secret": self.secret, "grant_type": "client_credentials"}
                async with self.session.post(TWITCH_TOKEN, data=form) as r:
                    if r.status >= 400:
                        raise HttpError("Twitch", r.status)
                    self.token = (await r.json())["access_token"]
            headers = {"Client-ID": self.client_id, "Authorization": f"Bearer {self.token}"}
            async with self.session.post(IGDB_GAMES, data=body.encode(), headers=headers) as r:
                await asyncio.sleep(0.3)
                if r.status == 401 and not retry:  # token 过期，换一个再试
                    continue
                if r.status >= 400:
                    raise HttpError("IGDB", r.status)
                return await r.json(content_type=None)
        return []

    async def by_slugs(self, slugs: list[str]) -> dict[str, dict]:
        out = {}
        for i in range(0, len(slugs), 100):
            chunk = ",".join(json.dumps(s) for s in slugs[i : i + 100])
            for g in await self.query(f"{IGDB_FIELDS} where slug = ({chunk}); limit 500;"):
                out[g["slug"]] = g
        return out

    async def developers(self, ids: list[int]) -> dict[int, list[str]]:
        """IGDB ID → 开发商。"""
        out = {}
        for i in range(0, len(ids), 500):
            chunk = ",".join(str(x) for x in ids[i : i + 500])
            fields = "fields involved_companies.developer,involved_companies.company.name;"
            for g in await self.query(f"{fields} where id = ({chunk}); limit 500;"):
                companies = g.get("involved_companies") or []
                out[g["id"]] = [c["company"]["name"] for c in companies if c.get("developer") and c.get("company")]
        return out

    async def search(self, title: str, year) -> dict | None:
        """年份 ±1 的结果里先认名字一致的，没有就取第一个；不知道年份不搜，免得认错。"""
        if not year:
            return None
        games = [g for g in await self.query(f"search {json.dumps(title, ensure_ascii=False)}; {IGDB_FIELDS} limit 10;") if year_close(g, year)]
        return next((g for g in games if norm(g.get("name") or "") == norm(title)), games[0] if games else None)


def year_close(game: dict, year) -> bool:
    released = game.get("first_release_date")
    return bool(released) and abs(datetime.fromtimestamp(released, timezone.utc).year - int(year)) <= 1


def igdb_media(game: dict) -> dict:
    """pop 是 IGDB 上的评分人数，当热度用；parent 是重制 / 加强版 / 移植对应的本体 ID。"""
    shots = [IGDB_IMG.format(x["image_id"]) for x in game.get("screenshots") or [] if x.get("image_id")]
    names = [game.get("name")]
    names += [a.get("name") for a in game.get("alternative_names") or []]
    names += [loc.get("name") for loc in game.get("game_localizations") or []]
    kind = game.get("game_type")
    return {
        "images": shots[:MAX_SHOTS],
        "names": dedupe(names),
        "pop": game.get("total_rating_count") or 0,
        "id": game.get("id"),
        "skip": kind in IGDB_SKIP_TYPES,
        "parent": (game.get("version_parent") or game.get("parent_game")) if kind in IGDB_EDITION_TYPES else None,
    }


async def pokemon_species(session: aiohttp.ClientSession) -> list[dict]:
    """PokeAPI 的 GraphQL 一次拉全部宝可梦（含名字、分类、属性、特性），免 key，限 100 次每小时。"""
    timeout = aiohttp.ClientTimeout(total=120)
    async with session.post(POKEAPI, json={"query": POKEMON_QUERY}, headers={"User-Agent": UA}, timeout=timeout) as r:
        if r.status >= 400:
            raise HttpError("PokeAPI", r.status)
        data = await r.json(content_type=None)
    if data.get("errors"):
        raise RuntimeError(f"PokeAPI: {data['errors'][0].get('message')}")
    return data["data"]["pokemon_v2_pokemonspecies"]


async def steam_shots(session: aiohttp.ClientSession, appid: str, ua: str) -> list[str]:
    params = {"appids": appid, "filters": "screenshots"}
    async with session.get(STEAM_API, params=params, headers={"User-Agent": ua}) as r:
        r.raise_for_status()
        data = ((await r.json(content_type=None)) or {}).get(str(appid)) or {}
    detail = data.get("data") if isinstance(data.get("data"), dict) else {}
    return [x["path_full"] for x in detail.get("screenshots") or [] if x.get("path_full")][:MAX_SHOTS]
