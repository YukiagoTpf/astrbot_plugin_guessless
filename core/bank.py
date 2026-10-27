"""题库：各题型的构建、定期刷新和出题。题目条目怎么拼见 entries.py，数据源见 sources.py 和 geo.py。

动画取 Bangumi 最热门的作品，游戏从 Metacritic 高分游戏里按热度和评分分大众 / 小众两档，宝可梦取 PokeAPI 全图鉴，
地点和日本地点是 Google 街景随机取景，国家是 Natural Earth 轮廓。找不到图的作品不进题库。
听歌猜番是猜动画题库里在 AnimeThemes 有 OP / ED 的作品；猜萝卜是 Bangumi 上高达系列动画和超级机器人大战里的机体。
盲猜宝可梦、盲猜萝卜不单独建题库，用宝可梦、萝卜题库里带对比属性的条目。
题库、各数据源的查询结果和图片都缓存在插件数据目录，出题时只有首次用到的图片和街景需要联网。
"""

import asyncio
import hashlib
import random
import re
import time
from pathlib import Path

import aiohttp

from astrbot.api import logger

from . import audio, cards, geo
from .answer import candidates, find, norm
from .entries import (
    ANIME_KINDS,
    GAME_INFO,
    MECHA_VERSION,
    POKEMON_VERSION,
    WORK_HINTS_VERSION,
    TagStats,
    aliases,
    anime_entry,
    bgm_tags,
    clean_mc_title,
    clip,
    country_basics,
    country_entry,
    game_entry,
    geo_entry,
    infobox,
    match_gundam_wiki,
    mecha_entry,
    people,
    pokemon_entry,
    popularity,
    pref_entry,
    pref_names,
    split_tiers,
)
from .sources import (
    ANIMETHEMES,
    Anilist,
    Bilibili,
    HttpError,
    Igdb,
    anilist_names,
    anime_themes,
    dedupe,
    gundam_wiki,
    gundam_wiki_images,
    igdb_media,
    pokemon_species,
    steam_shots,
    wikidata,
    year_close,
)
from .store import load_json, save_json

BANK_VERSION = 4  # 题目格式或选题规则变了就加一，旧题库会自动重建
ANISONG_VERSION = "themes-v1"  # 听歌猜番的选曲规则（sources.anime_themes）改了就换个名字
BGM_API = "https://api.bgm.tv/v0"
BGM_UA = "YukiagoTpf/astrbot_plugin_guessless"
# Metacritic 评分榜是网页前端用的非官方接口，目前不用 key
MC_FINDER = "https://backend.metacritic.com/finder/metacritic/web"
WEB_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)
MIN_POOL = 20
# 盲猜题型 -> 用哪个题库（只出带 attrs 的条目）
DERIVED = {"pokedle": "pokemon", "mechadle": "mecha"}


class Bank:
    def __init__(self, data_dir: Path, config):
        self.dir = data_dir
        self.img_dir = data_dir / "images"
        self.img_dir.mkdir(parents=True, exist_ok=True)
        self.config = config
        self.entries: dict[str, list] = {
            "anime": [], "anisong": [], "game": [], "pokemon": [], "pokedle": [], "mecha": [], "mechadle": [],
            "geo": [], "country": [], "jpgeo": [],
        }
        self.meta: dict[str, dict] = {cat: {} for cat in self.entries}
        # 地点类可以猜的国家 / 都道府县 {代码: {"title", "names", "points"}}，没街景、不出题的也在里面；
        # points 是报距离用的位置，见 geo.distance
        self.places: dict[str, dict] = {cat: {} for cat in self.entries}
        self._index: dict[str, tuple] = {}
        self.status = {cat: "等待构建" for cat in self.entries}
        self._shapes: dict = {}
        self._world: cards.WorldMap | None = None
        self._task: asyncio.Task | None = None
        self._session: aiohttp.ClientSession | None = None

    def start(self) -> None:
        for cat in self.entries:
            data = load_json(self.dir / f"bank_{cat}.json")
            if data:
                self.entries[cat] = data.pop("entries")
                self.places[cat] = data.pop("places", {})
                self.meta[cat] = data
                self.status[cat] = f"{len(self.entries[cat])} 题"
        for base in DERIVED.values():
            self._derive(base)
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

    def match(self, cat: str, text: str, prefer: str = "") -> tuple[str | None, dict | None]:
        """群友这句话猜的是哪个答案，返回 (键, 资料)：地点类是国家 / 都道府县代码和 places 里那一项，
        其他题型是题目的组和题目条目（按题库里所有题的答案认）。不是任何一个答案（闲聊）返回 (None, None)。
        听歌猜番按整个猜动画题库认，题库外的番说了也不算一次。"""
        src_cat = "anime" if cat == "anisong" else cat
        src = self.places[src_cat] or self.entries[src_cat]
        cached = self._index.get(src_cat)
        # 首次构建时题库边建边开放，列表是同一个、长度在变
        if not cached or cached[0] is not src or cached[1] != len(src):
            items = list(src.items()) if self.places[src_cat] else [(e.get("group", e["id"]), e) for e in src]
            index = [(k, candidates(item["names"])) for k, item in items]
            cached = self._index[src_cat] = (src, len(src), index, dict(items))
        key = find(text, cached[2], prefer)
        return key, cached[3].get(key)

    async def image(self, entry: dict) -> bytes:
        """每次从这道题的几张截图里随机挑一张。"""
        url = random.choice(entry["images"])
        path = self.img_dir / f"{entry['id']}-{hashlib.sha1(url.encode()).hexdigest()[:10]}.jpg"
        if path.exists():
            return path.read_bytes()
        # Fandom 的图片 CDN 防盗链：浏览器 UA 不带 Referer 会 403，用插件自己的 UA 反而放行
        ua = BGM_UA if "bgm.tv" in url or "nocookie.net" in url else WEB_UA
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

    async def audio(self, url: str) -> bytes:
        """听歌猜番的整首音频（几 MB 的 ogg），出题时现下，不存盘。"""
        timeout = aiohttp.ClientTimeout(total=60)
        async with self._http().get(url, headers={"User-Agent": BGM_UA}, timeout=timeout) as r:
            r.raise_for_status()
            return await r.read()

    def outline(self, entry: dict) -> tuple[list, dict, str]:
        """(国家轮廓, 邻国底图, 题目国家在底图里的组名)，参数顺序同 cards.load_outline。"""
        shapes = self._load_shapes()
        group = shapes["iso"][entry["iso"]]
        return geo.outline(shapes["groups"][group]), shapes["groups"], group

    def world_map(self) -> tuple[cards.WorldMap, dict]:
        """(世界地图, {国家代码: 组名})，地图第一次用时才画。"""
        shapes = self._load_shapes()
        if self._world is None:
            self._world = cards.WorldMap(shapes["groups"])
        return self._world, shapes["iso"]

    def _load_shapes(self) -> dict:
        if not self._shapes:
            self._shapes = load_json(self.dir / "country_shapes.json") or {}
        return self._shapes

    def _size(self, cat: str) -> int:
        return self.config.get(f"{cat}_pool_size", 1000 if cat == "anime" else 500)

    def _media_on(self, cat: str) -> bool:
        """动画、宝可梦、国家轮廓免 key，游戏要 IGDB 的 key，地点要 Google 街景的 key，听歌猜番要 ffmpeg。"""
        if cat == "anisong":
            return audio.available()
        if cat in ("geo", "jpgeo"):
            return bool(str(self.config.get("google_maps_key") or "").strip())
        if cat != "game":
            return True
        return all(str(self.config.get(k) or "").strip() for k in ("igdb_client_id", "igdb_client_secret"))

    def _settings(self, cat: str) -> list:
        """影响题库内容的配置，改了就重建。"""
        c = self.config
        if cat == "anime":
            return [self._size("anime"), WORK_HINTS_VERSION]
        if cat == "pokemon":
            return [c.get("pokemon_main_gen", 4), POKEMON_VERSION]
        if cat == "mecha":
            return [MECHA_VERSION]
        if cat == "anisong":  # 猜动画题库重建了，这边跟着换
            return [ANISONG_VERSION, self.meta["anime"].get("built_at")]
        if cat == "geo":
            return [geo.PANOS_PER_COUNTRY, geo.MIN_PANOS, sorted(geo.EXCLUDE), geo.SAMPLING, geo.HINTS_VERSION]
        if cat == "country":
            return [geo.OUTLINE_VERSION, sorted(geo.EXCLUDE), geo.HINTS_VERSION]
        if cat == "jpgeo":
            return [geo.PANOS_PER_PREF, geo.MIN_PANOS, geo.SAMPLING, geo.HINTS_VERSION]
        return [
            self._size("game"), c.get("game_niche_size", 300), c.get("game_min_score", 75),
            c.get("game_min_reviews", 10), WORK_HINTS_VERSION,
        ]

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

    def _commit(self, cat: str, entries: list, media: bool = True, complete: bool = True, places=None) -> None:
        self.entries[cat] = entries
        self.places[cat] = places or {}
        self.meta[cat] = {
            "built_at": time.time(), "version": BANK_VERSION, "media": media, "complete": complete,
            "settings": self._settings(cat),
        }
        save_json(self.dir / f"bank_{cat}.json", {**self.meta[cat], "places": self.places[cat], "entries": entries})
        self.status[cat] = f"{len(entries)} 题"
        logger.info(f"[guess] {cat} bank built: {len(entries)} entries")
        self._derive(cat)

    def _derive(self, base: str) -> None:
        """盲猜题型用 base 题库里带对比属性（attrs）的条目；旧题库没有，重建完才有。"""
        for cat, src in DERIVED.items():
            if src == base:
                self.entries[cat] = [e for e in self.entries[base] if "attrs" in e]
                if self.entries[cat]:
                    self.status[cat] = f"{len(self.entries[cat])} 题"

    async def _refresh_loop(self) -> None:
        # 游戏有缓存时一两分钟就建完，动画要等 B 站慢慢搜，放后面
        builders = {
            "game": self._build_games, "pokemon": self._build_pokemon, "country": self._build_country,
            "geo": self._build_geo, "jpgeo": self._build_jpgeo, "anime": self._build_anime,
            "anisong": self._build_anisong, "mecha": self._build_mecha,
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
        chars_path = self.dir / "bgm_characters.json"
        chars = load_json(chars_path) or {}
        # 标签按候选作品一起统计，才分得出哪些是大类、哪些是这部的特色
        pool = picked[: size * 2]
        stats = TagStats.build([bgm_tags(s.get("tags")) for s in pool], [people(infobox(s)) for s in pool])
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
                self.status["anime"] = f"正在找截图和角色表 {i}/{len(picked)}（已可出题 {len(entries)} 道）"
                if todo := [s for s in chunk if str(s["id"]) not in cache]:
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
                        entries.append(anime_entry(s, media, info, stats, await self._characters(chars, s["id"])))
                    if len(entries) >= size:
                        break
                save_json(cache_path, cache)
                save_json(chars_path, chars)
        finally:
            save_json(cache_path, cache)
            save_json(chars_path, chars)
        self._commit("anime", entries, complete=complete)

    async def _build_mecha(self) -> None:
        """猜萝卜：Bangumi 上高达系列动画和超级机器人大战系列游戏里的「机体」。高达再按型号、日文名对上
        Gundam Wiki，用它的设定图和资料（世界观、阵营、机型、机械设计）；其他机体用 Bangumi 的图。

        Bangumi 的机体详情和作品的 [年份, 收藏人数] 缓存在 bgm_mecha.json，Gundam Wiki 的信息框缓存在 gundam_wiki.json。
        """
        path = self.dir / "bgm_mecha.json"
        cache = load_json(path) or {}
        chars, works = cache.setdefault("chars", {}), cache.setdefault("works", {})
        gundam_works, srw_works = await self._mecha_sources()
        sources = [*gundam_works, *srw_works]
        ids: set[str] = set()
        for i, sid in enumerate(sources):
            self.status["mecha"] = f"正在翻 Bangumi 的机体表 {i}/{len(sources)}"
            try:
                rows = await self._get(f"{BGM_API}/subjects/{sid}/characters")
            except Exception as e:
                logger.warning(f"[guess] bangumi characters of {sid} failed: {e}")
                continue
            ids |= {str(c["id"]) for c in rows if c.get("type") == 2}
            await asyncio.sleep(0.3)
        todo = sorted(ids - set(chars))
        try:
            for i, cid in enumerate(todo):
                self.status["mecha"] = f"正在查机体资料 {i}/{len(todo)}"
                try:
                    chars[cid] = await self._mecha_char(cid)
                except Exception as e:
                    logger.warning(f"[guess] bangumi mecha {cid} failed: {e}")
                if i % 50 == 0:
                    save_json(path, cache)
            todo = sorted({w[0] for cid in ids if cid in chars for w in chars[cid]["works"]} - set(works))
            for i, sid in enumerate(todo):
                self.status["mecha"] = f"正在查出场作品 {i}/{len(todo)}"
                try:
                    s = await self._get(f"{BGM_API}/subjects/{sid}")
                except Exception as e:
                    logger.warning(f"[guess] bangumi subject {sid} failed: {e}")
                    continue
                works[sid] = [int((s.get("date") or "0")[:4] or 0), popularity(s)]
                await asyncio.sleep(0.3)
        finally:
            save_json(path, cache)

        wiki_path = self.dir / "gundam_wiki.json"
        self.status["mecha"] = "正在拉 Gundam Wiki 的机体资料"
        try:
            pages = await gundam_wiki(self._http())
            save_json(wiki_path, pages)
        except Exception as e:  # 拉不到就用上次的；一次都没拉到过，高达也用 Bangumi 的图、不出盲猜
            logger.warning(f"[guess] gundam wiki failed: {e}")
            pages = load_json(wiki_path) or {}
        found = {cid: chars[cid] for cid in ids if cid in chars}
        # 只有在高达作品里出过场的才去对 Gundam Wiki，不然 EVA 初号机会对上某台叫「初号機」的高达
        matched = match_gundam_wiki({k: c for k, c in found.items() if gundam_works & {w[0] for w in c["works"]}}, pages)
        # 同一台机体在 Bangumi 上常按作品建了好几个条目，对上同一个 Wiki 页面的合并成一道题
        groups: dict[str, list] = {}
        for cid, c in found.items():
            groups.setdefault(matched.get(cid) or f"bgm-{cid}", []).append(c)
        files = {t: f for t in groups if t in pages and (f := pages[t].get("image", "").split("\n")[0].split(";")[0].strip())}
        self.status["mecha"] = "正在找设定图"
        try:
            images = await gundam_wiki_images(self._http(), sorted(set(files.values())))
        except Exception as e:
            logger.warning(f"[guess] gundam wiki images failed: {e}")
            images = {}
        entries = []
        for key, group in groups.items():
            group.sort(key=lambda c: c["collects"], reverse=True)
            seen = [w for c in group for w in c["works"] if (works.get(w[0]) or [0])[0]]
            # 出自：高达机体取高达系列里看的人最多的一部；其他机体取最早的动画（一般是原作，后来的多是客串、恶搞），
            # 没有动画的（超级机器人大战原创机体）取看的人最多的游戏。登场年份取最早的
            gundam = [w for w in seen if w[0] in gundam_works]
            anime = [w for w in seen if w[1] == 2]
            if gundam:
                top = max(gundam, key=lambda w: works[w[0]][1])
            elif anime:
                top = min(anime, key=lambda w: works[w[0]][0])
            else:
                top = max(seen, key=lambda w: works[w[0]][1], default=None)
            char = {
                **group[0], "cn": next((c["cn"] for c in group if c["cn"]), ""),
                "aliases": dedupe(a for c in group for a in c["aliases"]), "collects": sum(c["collects"] for c in group),
                "work": top[2] if top else "", "year": min((works[w[0]][0] for w in seen), default=0),
            }
            image = images.get(files.get(key, "")) or char["image"]
            if image and (entry := mecha_entry(char, pages.get(key), image, key if key in pages else "")):
                entries.append((char["collects"], entry))
        # 同名的（Bangumi 上同一台的几个条目没对上 Wiki、或者对上了不同版本）只留收藏多的，名字并过去
        entries.sort(key=lambda x: x[0], reverse=True)
        kept: dict[str, dict] = {}
        for _, e in entries:
            if (key := norm(e["title"])) in kept:
                kept[key]["names"] = dedupe([*kept[key]["names"], *e["names"]])
            else:
                kept[key] = e
        # 收藏人数排前一半的是大众题
        kept_list = list(kept.values())
        self._commit("mecha", [{**e, "tier": "main" if i < len(kept_list) / 2 else "niche"} for i, e in enumerate(kept_list)])

    async def _mecha_sources(self) -> tuple[set[str], set[str]]:
        """萝卜从哪些作品里挖，(高达系列动画, 超级机器人大战系列游戏) 的 Bangumi ID：
        高达搜出来的再顺着续作、前传等关系找全。"""
        self.status["mecha"] = "正在找高达和超级机器人大战的作品"

        async def search(keyword: str, kind: int, keep) -> list[str]:
            out = []
            for offset in range(0, 200, 50):
                payload = {"keyword": keyword, "filter": {"type": [kind]}}
                rows = (await self._post(f"{BGM_API}/search/subjects", payload, params={"limit": 50, "offset": offset})).get("data") or []
                out += [str(s["id"]) for s in rows if keep(f"{s['name']} {s.get('name_cn') or ''}")]
                if len(rows) < 50:
                    break
                await asyncio.sleep(0.3)
            return out

        def gundam(name: str) -> bool:
            return any(k in name.lower() for k in ("高达", "ガンダム", "gundam"))

        found = dict.fromkeys(await search("高达", 2, gundam) + await search("ガンダム", 2, gundam))
        queue = list(found)
        while queue and len(found) < 300:
            for r in await self._get(f"{BGM_API}/subjects/{queue.pop()}/subjects"):
                if r.get("type") == 2 and gundam(f"{r['name']} {r.get('name_cn') or ''}") and str(r["id"]) not in found:
                    found[str(r["id"])] = None
                    queue.append(str(r["id"]))
            await asyncio.sleep(0.3)
        srw = await search("超级机器人大战", 4, lambda n: "机器人大战" in n or "ロボット大戦" in n)
        srw += await search("スーパーロボット大戦", 4, lambda n: "机器人大战" in n or "ロボット大戦" in n)
        return set(found), set(srw) - set(found)

    async def _mecha_char(self, cid: str) -> dict:
        """Bangumi 一台机体：日文名、中文名、别名、全高、图、收藏人数、出场的动画和游戏 [[作品 ID, 类型, 名字], ...]。"""
        c = await self._get(f"{BGM_API}/characters/{cid}")
        info = infobox(c)
        rows = await self._get(f"{BGM_API}/characters/{cid}/subjects")
        await asyncio.sleep(0.3)
        return {
            "id": cid, "name": c["name"], "cn": info.get("简体中文名") if isinstance(info.get("简体中文名"), str) else "",
            "aliases": aliases(info), "info": {k: info[k] for k in ("全高", "身高") if isinstance(info.get(k), str)},
            "image": (c.get("images") or {}).get("large") or "", "collects": (c.get("stat") or {}).get("collects") or 0,
            "works": [[str(r["id"]), r["type"], r.get("name_cn") or r["name"]] for r in rows if r.get("type") in (2, 4)],
        }

    async def _build_anisong(self) -> None:
        """猜动画题库里能在 AnimeThemes 找到 OP / ED 音频的作品。结果缓存在 media_anisong.json，只查新进题库的。"""
        if not self._media_on("anisong"):
            self.status["anisong"] = "AstrBot 容器里没有 ffmpeg，放不了音频"
            return
        anime = self.entries["anime"]
        if not anime or not self.meta["anime"]:
            self.status["anisong"] = "等猜动画的题库建好"
            return
        path = self.dir / "media_anisong.json"
        cache = load_json(path) or {}
        if todo := [e["id"].removeprefix("bgm-") for e in anime if e["id"].removeprefix("bgm-") not in cache]:
            ids, failed = await self._anime_ids(todo)
            themes = await self._anime_themes(ids)
            # 查不到 ID 的下次再查；查到 ID、AnimeThemes 上没有的记成空，不再重复查
            cache.update({sid: themes.get(sid, []) for sid in todo if sid not in failed})
            save_json(path, cache)
        entries = [{**e, "themes": cache[sid]} for e in anime if cache.get(sid := e["id"].removeprefix("bgm-"))]
        self._commit("anisong", entries)

    async def _anime_ids(self, sids: list[str]) -> tuple[dict, set]:
        """Bangumi ID → {"AniList": ID, "MyAnimeList": ID}。先查 Wikidata，没有的拿 Bangumi 条目按原名去 AniList 搜。
        返回 (ID 表, 这次没查成的 Bangumi ID)。"""
        wd = await self._wikidata("anisong", "P5732", sids, {"anilist": "P8729", "mal": "P4086"})
        ids = {}
        for sid, info in wd.items():
            got = {site: str(info[k]) for site, k in (("AniList", "anilist"), ("MyAnimeList", "mal"))
                   if str(info.get(k) or "").isdigit()}
            if got:
                ids[sid] = got
        rest, subjects, failed = [sid for sid in sids if sid not in ids], [], set()
        for i, sid in enumerate(rest):
            self.status["anisong"] = f"正在用 AniList 补 ID {i}/{len(rest)}"
            try:
                subjects.append(await self._get(f"{BGM_API}/subjects/{sid}"))
            except Exception as e:
                logger.warning(f"[guess] bangumi subject {sid} failed: {e}")
                failed.add(sid)
            await asyncio.sleep(0.5)
        try:
            found = await Anilist(self._http()).match(subjects, {})
        except Exception as e:
            logger.warning(f"[guess] anilist search for anisong failed: {e}")
            return ids, failed | {str(s["id"]) for s in subjects}
        ids.update({sid: {"AniList": str(m["id"])} for sid, m in found.items()})
        return ids, failed

    async def _anime_themes(self, ids: dict) -> dict[str, list]:
        """按 AniList、MAL 的 ID 各批量查一遍 AnimeThemes：{Bangumi ID: anime_themes 的结果}。"""
        out: dict[str, list] = {}
        for site in ("AniList", "MyAnimeList"):
            rev = {v[site]: sid for sid, v in ids.items() if site in v and sid not in out}
            keys = list(rev)
            for i in range(0, len(keys), 50):
                self.status["anisong"] = f"正在查 AnimeThemes（{site} {i}/{len(keys)}）"
                params = {
                    "filter[has]": "resources", "filter[site]": site, "filter[external_id]": ",".join(keys[i : i + 50]),
                    "include": "resources,animethemes.animethemeentries.videos.audio,animethemes.song.artists",
                    "page[size]": "100",
                }
                try:
                    data = await self._get(ANIMETHEMES, params=params)
                except Exception as e:
                    logger.warning(f"[guess] animethemes lookup failed: {e}")
                    continue
                for a in data.get("anime") or []:
                    ext = next((
                        str(r["external_id"]) for r in a.get("resources") or []
                        if r.get("site") == site and str(r.get("external_id")) in rev
                    ), None)
                    if ext and (themes := anime_themes(a)):
                        out[rev[ext]] = themes
                await asyncio.sleep(1)
        return out

    async def _build_games(self) -> None:
        """大众题：MC 分过线的游戏里按热度（IGDB 评分人数）取前 N；小众题：剩下的按 MC 分取前 M。

        没配 IGDB 就没有热度数据，退回按 MC 分取前 N、不分档。
        """
        size = self._size("game")
        niche_size = self.config.get("game_niche_size", 300)
        min_score = self.config.get("game_min_score", 75)
        min_reviews = self.config.get("game_min_reviews", 10)
        use_media = self._media_on("game")
        items, offset = [], 0
        while use_media or len(items) < size:
            self.status["game"] = f"正在拉取 Metacritic 评分榜（已拉 {len(items)} 款）"
            params = {
                "sortBy": "-metaScore", "productType": "games", "releaseYearMin": 1958,
                "releaseYearMax": time.localtime().tm_year, "offset": offset, "limit": 50,
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
        chars_path = self.dir / "bgm_characters.json"
        chars = load_json(chars_path) or {}
        entries = []
        # 首次构建没有旧题库，边匹配边开放（先不带标签）；刷新时匹配完再整体替换
        if not self.entries["game"]:
            self.entries["game"] = entries
        try:
            for i, (it, tier) in enumerate(picked):
                slug = it["slug"]
                # 没存 staff 的是旧版匹配结果，缺标签、简介和人名，重新匹配
                if slug not in matches or (matches[slug] and "staff" not in matches[slug]):
                    self.status["game"] = f"正在用 Bangumi 补中文名和标签 {i}/{len(picked)}（已可出题 {len(entries)} 道）"
                    try:
                        matches[slug] = await self._match_bgm(clean_mc_title(it["title"]), it.get("premiereYear"))
                    except Exception as e:  # 网络错误不写缓存，下次刷新再试
                        logger.warning(f"[guess] bangumi match {slug} failed: {e}")
                    if i % 25 == 0:
                        save_json(match_path, matches)
                        save_json(chars_path, chars)
                    await asyncio.sleep(1)
                if bgm_id := (matches.get(slug) or {}).get("id"):
                    await self._characters(chars, bgm_id)
                entries.append({**game_entry(it, matches.get(slug), wd.get(slug), media.get(slug)), "tier": tier})
        finally:
            save_json(match_path, matches)
            save_json(chars_path, chars)

        # 标签要整个题库一起统计才分得出大类和特色，所以全部匹配完再按标签出一遍提示
        found = [matches.get(it["slug"]) or {} for it, _ in picked]
        stats = TagStats.build(
            [b.get("tags") or [] for b in found], [people(b.get("info") or {}) + b.get("staff", []) for b in found]
        )
        igdb_ids = {it["slug"]: m["id"] for it, _ in picked if (m := media.get(it["slug"])) and m.get("id")}
        devs = await self._igdb_developers(list(igdb_ids.values()))
        entries = []
        for (it, tier), b in zip(picked, found):
            slug = it["slug"]
            dev, cast = devs.get(igdb_ids.get(slug)), chars.get(str(b.get("id")))
            entry = game_entry(it, b or None, wd.get(slug), media.get(slug), stats, dev, cast)
            entries.append({**entry, "tier": tier})
        self._commit("game", entries, use_media)

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
        self._commit("pokemon", entries)

    async def _cities(self, cat: str) -> dict:
        path = self.dir / "geo_cities.json"
        cities = load_json(path)
        if not cities:
            self.status[cat] = "正在下载 GeoNames 城市表（一两分钟）"
            cities = await geo.fetch_cities(self._http())
            save_json(path, cities)
        return cities

    async def _countries(self, cat: str, candidates) -> tuple[list[str], dict]:
        """猜地点、猜国家共用：只出主权国家（格陵兰、泽西这类属地不出），返回 (国家代码, country_basics)。"""
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
        return [cc for cc in codes if cc in basics], basics

    async def _sample_all(self, cat: str, cache_name: str, targets: list, make_entries, places: dict) -> None:
        """猜地点、猜日本地点共用：targets 是 [(键, 候选城镇, accept, 个数)]，全景缓存在 cache_name，
        找到不足 MIN_PANOS 个的每次重建再补找；make_entries(键, 全景列表) 生成题目，places 见 Bank.places。"""
        key = str(self.config.get("google_maps_key") or "").strip()
        cache_path = self.dir / cache_name
        cache = load_json(cache_path) or {}
        if cache.get("_sampling") != geo.SAMPLING:  # 取景规则变了，旧全景作废
            cache = {"_sampling": geo.SAMPLING}
        sv, nearest = geo.StreetView(self._http(), key), geo.Nearest(await self._cities(cat))
        entries, failures = [], 0
        # 首次构建没有旧题库，边找边开放
        if not self.entries[cat]:
            self.entries[cat], self.places[cat] = entries, places
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
        self._commit(cat, entries, places=places)

    async def _build_geo(self) -> None:
        if not self._media_on("geo"):
            self.status["geo"] = "还没配 Google 街景的 key"
            return
        cities = await self._cities("geo")
        codes, basics = await self._countries("geo", cities)
        targets = [(cc, cities[cc], lambda near, cc=cc: near[0] == cc, geo.PANOS_PER_COUNTRY) for cc in codes]
        places = {
            cc: {"title": basics[cc]["zh"], "names": basics[cc]["names"], "points": geo.towns(cities[cc])} for cc in codes
        }
        await self._sample_all(
            "geo", "geo_panos.json", targets, lambda cc, panos: [geo_entry(cc, p, basics[cc]) for p in panos], places
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
        places = {
            code: {"title": zh, "names": pref_names(prefs[gn], wd[gn]), "points": geo.towns(towns[code])}
            for code, gn in admin1.items() if gn in prefs and towns[code] and (zh := wd.get(gn, {}).get("zh"))
        }

        def make(code, panos):
            gn = admin1[code]
            return [e for p in panos if (e := pref_entry(code, p, prefs[gn], wd.get(gn, {})))]

        await self._sample_all("jpgeo", "jp_panos.json", targets, make, places)

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
        codes, basics = await self._countries("country", list(iso))
        # 很小的岛国抽稀后一个环都不剩，不出题
        entries = [country_entry(cc, geo.outline(groups[iso[cc]]), basics[cc]) for cc in codes if groups[iso[cc]]]
        places = {e["group"]: {"title": e["title"], "names": e["names"], "points": [e["spot"]]} for e in entries}
        self._commit("country", entries, places=places)

    async def _game_media(self, items: list, wd: dict) -> dict:
        """IGDB 截图优先，IGDB 上没截图再用 Steam 的。结果缓存，只查新进榜的。"""
        path = self.dir / "media_game.json"
        cache = load_json(path) or {}
        todo = [it for it in items if it["slug"] not in cache]
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
                    "id": s["id"],
                    "name": s["name"],
                    "name_cn": s.get("name_cn") or "",
                    "aliases": aliases(info),
                    "info": {k: clip(info[k]) for k in GAME_INFO if info.get(k)},
                    "tags": bgm_tags(s.get("tags"))[:30],
                    "summary": (s.get("summary") or "")[:600],
                    "staff": people(info),  # 全部 staff 和公司，标签里的人名要去掉
                }
        return None

    async def _characters(self, cache: dict, sid) -> dict:
        """Bangumi 角色表：主角的声优（出提示用），以及全部角色名和声优（从标签里去掉人名用）。结果写进 cache。"""
        key = str(sid)
        if key not in cache:
            try:
                rows = await self._get(f"{BGM_API}/subjects/{sid}/characters")
            except Exception as e:  # 查不到只是少一条提示，下次刷新再查
                logger.warning(f"[guess] bangumi characters of {sid} failed: {e}")
                return {}
            cv = [a["name"] for c in rows if c.get("relation") == "主角" for a in (c.get("actors") or [])[:1]]
            names = [c["name"] for c in rows] + [a["name"] for c in rows for a in c.get("actors") or []]
            cache[key] = {"cv": dedupe(cv), "names": dedupe(names)}
            await asyncio.sleep(0.5)
        return cache[key]

    async def _igdb_developers(self, ids: list[int]) -> dict[int, list[str]]:
        """查不到不影响出题，退回 Bangumi 记的开发商。"""
        if not ids or not self._media_on("game"):
            return {}
        igdb = Igdb(self._http(), self.config.get("igdb_client_id", ""), self.config.get("igdb_client_secret", ""))
        try:
            return await igdb.developers(ids)
        except Exception as e:
            logger.warning(f"[guess] igdb developers lookup failed: {e}")
            return {}

    def _http(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
        return self._session

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
