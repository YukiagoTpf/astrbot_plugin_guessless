"""猜游戏的全年龄视觉小说补充池；VNDB 数据按 ODbL，截图版权归原作。"""

import asyncio
import re
import time

import aiohttp

from .answer import norm
from .entries import GAME_INFO, TagStats, aliases, bgm_tags, clip, game_entry, infobox, people
from .sources import HttpError, dedupe
from .store import load_json, save_json

API = "https://api.vndb.org/kana"
SOURCE = "https://vndb.org/"
LICENSE = "ODbL-1.0"
LICENSE_URL = "https://vndb.org/d17"
VERSION = 2  # 规则变动同时更新 entries.GAME_VERSION，让旧题库重建
DEFAULT_SIZE = 80
MAX_CANDIDATES = 1200
PAGE_SIZE = 20
MIN_VOTES = 50
# 图片分级不覆盖武器、镜头及文字擦边；抽查不合适的整部排除，只能加不能删。
NO_ANSWER = {
    "v60": "题图含走光构图",
    "v696": "题图含刀剑",
    "v842": "题图文字含性暗示",
    "v1919": "题图含刀剑",
    "v969": "D.C. 主系列以成人版本为主，全年龄外传也不出题",
    "v10419": "题图含枪械",
    "v11186": "题图文字含性暗示",
    "v13059": "题图含武器",
    "v18057": "题图文字含性暗示",
    "v18666": "その花びら主系列为成人作品，全年龄外传也不出题",
    "v30812": "ネコぱら主系列为成人作品，全年龄外传也不出题",
    "v33904": "泳装题图构图擦边",
    "v4617": "题图含持枪动作",
}
# 分级不等于内容合规；疑似红线作品整部跳过，不把标签藏掉后继续出。
BLOCK = re.compile(
    r"\b(?:nukige|netorare|netori|ntr|rape|incest|lolicon|shotacon|porn\w*|eroge|"
    r"war|warfare|military|army|soldier\w*|weapon\w*|gun\w*|nuclear|atomic|politic\w*|"
    r"government|parliament|terroris\w*|nazi\w*|fascis\w*|communis\w*|"
    r"suicid\w*|self[- ](?:harm|mutilation)|drug\w*|cocaine|heroin|cannabis|cannibal\w*|murder\w*|gore|torture|"
    r"kill(?:ing|er)s?|massacre|genocide|battle|combat|assassin\w*|death game|abuse|domestic violence|"
    r"sexual (?:innuendo|harassment|assault|violence)|"
    r"racis\w*|slur\w*|profanity|discrimination|fuck\w*|shit|r[- ]?18|h[- ]?scenes?)\b|"
    r"拔作|抜きゲー|寝取|エロゲ|色情|成人向|性爱|性交|性行为|H场景|hシーン|"
    r"战争|戦争|军事|軍事|军队|軍隊|军人|軍人|兵器|武器|核武|核能|核电|原爆|"
    r"政治|政府|议会|議会|纳粹|自杀|自殺|自残|自傷|毒品|强奸|強姦|乱伦|亂倫|猎奇|獵奇|凌辱|"
    r"杀人|殺人|屠杀|屠殺|虐待|死亡游戏|死亡遊戲|大战|大戰|大戦|薄[樱櫻桜]鬼|性暗示|性骚扰|性騷擾|草泥[马馬]|"
    # 成人主系列的全年龄外传也不收；D.C. 保留标点，不能把普通的 DC 缩写一并拦掉。
    r"その花びら|花びらにくちづけ|そのはなびら|sono[\s_-]*hanabira|hanabira[\s_-]*ni[\s_-]*kuchizuke|"
    r"a[\s_-]+kiss[\s_-]+for[\s_-]+the[\s_-]+petals|kissing[\s_-]+the[\s_-]+petal|"
    r"亲吻那片花瓣|親吻那片花瓣|花吻|ネコぱら|ネコパラ|ねこぱら|neko[\s_-]*para|猫娘乐园|貓娘樂園|猫郎乐园|貓郎樂園|"
    r"\bd\.c(?:\.|(?=\W|$))|\bdcgs\b|ダ[・･]?カーポ|da[\s_-]*capo|初音[岛島]",
    re.I,
)
# 成人平台和类型词不能进入提示；非色情作品也常被打上这些标签。
ADULT_TAG = re.compile(r"r[-_ ]?18|18禁|十八禁|エロ|拔作|抜き|ntr|netor|h[场場シ]|成人|色情|性描写", re.I)
PLATFORMS = {
    "win": "PC", "mac": "Mac", "lin": "Linux", "swi": "Nintendo Switch", "sw2": "Nintendo Switch 2",
    "psp": "PlayStation Portable", "psv": "PlayStation Vita", "ps1": "PlayStation", "ps2": "PlayStation 2",
    "ps3": "PlayStation 3", "ps4": "PlayStation 4", "ps5": "PlayStation 5", "nds": "Nintendo DS",
    "n3d": "Nintendo 3DS", "drc": "Dreamcast", "and": "Android", "ios": "iOS",
}
# 只认正式、完整、已发售且明确无色情内容的版本，试玩版和补丁不能作全年龄依据。
RELEASE_FILTER = [
    "and", ["minage", "<=", 15], ["official", "=", 1], ["has_ero", "!=", 1],
    ["patch", "!=", 1], ["rtype", "=", "complete"], ["released", "<=", "today"],
]
FIELDS = (
    "title,alttitle,titles{lang,title,latin},aliases,released,votecount,"
    "tags{name,category,lie},screenshots{url,sexual,violence,votecount},developers{name,original}"
)


def size(config) -> int:
    """数量同时充当开关，限制扫描量，避免误填后无限请求。"""
    return max(0, min(150, int(config.get("game_galgame_size", DEFAULT_SIZE))))


def names(vn: dict) -> list[str]:
    return dedupe([
        vn.get("alttitle"), vn.get("title"), *vn.get("aliases", []),
        *(t.get("title") for t in vn.get("titles", []) if t.get("lang") in {"ja", "en", "zh-Hans", "zh-Hant"}),
    ])


def safe_images(vn: dict) -> list[str]:
    """不把缺少评分当安全；性内容、暴力均为零，且至少有人投过图片分级票。"""
    return dedupe(
        s.get("url") for s in vn.get("screenshots", [])
        if s.get("sexual") == 0 and s.get("violence") == 0 and s.get("votecount", 0) > 0
        and str(s.get("url", "")).startswith(("https://s.vndb.org/sf/", "https://t.vndb.org/sf/"))
    )


def safe_work(vn: dict) -> bool:
    tags = [t for t in vn.get("tags", []) if not t.get("lie")]
    # 原作含色情的移植也保守跳过，避免只靠低龄移植掩盖原作的成人卖点。
    return (
        vn.get("id") not in NO_ANSWER and bool(tags)
        and not any(t.get("category") == "ero" or BLOCK.search(t["name"]) for t in tags)
        and not any(BLOCK.search(n) for n in names(vn))
    )


def allowed_entry(entry: dict) -> bool:
    """离线回退也执行现行黑名单，避免来源故障时恢复已排除的题目。"""
    return entry.get("id", "").removeprefix("vndb-") not in NO_ANSWER and not any(
        BLOCK.search(n) for n in dedupe([entry.get("title"), entry.get("original"), *entry.get("names", [])])
    )


def safe_release(release: dict, vid: str) -> bool:
    age = release.get("minage")
    return (
        type(age) is int and 0 <= age <= 15 and release.get("official") is True
        and release.get("has_ero") is False and release.get("patch") is False
        and any(v.get("id") == vid and v.get("rtype") == "complete" for v in release.get("vns", []))
    )


class Vndb:
    """所有请求经 Bank 带连接重试；串行间隔 5 秒，429 额外等一分钟。"""

    def __init__(self, bank):
        self.bank = bank
        self.next_request = 0.0

    async def query(self, endpoint: str, payload: dict) -> dict:
        for attempt in range(3):
            await asyncio.sleep(max(0, self.next_request - time.monotonic()))
            try:
                return await self.bank._post(f"{API}/{endpoint}", payload)
            except aiohttp.ClientResponseError as exc:
                if exc.status != 429 or attempt == 2:
                    raise HttpError("VNDB", exc.status) from None
                await asyncio.sleep(60)
            finally:
                self.next_request = time.monotonic() + 5
        raise HttpError("VNDB", 429)


async def match_bgm(bank, vn: dict) -> dict | None:
    """逐个原名搜索，必须名字和年份都对上；旧 MC 匹配不含 nsfw，不能直接信任。"""
    wanted = {norm(n) for n in names(vn)}
    year = str(vn.get("released") or "")[:4]
    if not year.isdigit():
        return None
    for title in dedupe([vn.get("alttitle"), vn.get("title")]):
        try:
            data = await bank._post(
                "https://api.bgm.tv/v0/search/subjects", {"keyword": title, "filter": {"type": [4]}}, params={"limit": 10},
            )
        except aiohttp.ClientResponseError as exc:
            raise HttpError("Bangumi", exc.status) from None
        await asyncio.sleep(1)
        for s in data.get("data") or []:
            info = infobox(s)
            found = {norm(n) for n in [s.get("name", ""), s.get("name_cn", ""), *aliases(info)]}
            date = str(s.get("date") or "")[:4]
            if not (wanted & found and date.isdigit() and abs(int(date) - int(year)) <= 1):
                continue
            if s.get("nsfw") is not False or not re.search(r"[\u3400-\u9fff]", s.get("name_cn") or ""):
                continue
            raw_tags = bgm_tags(s.get("tags"))
            if (any(BLOCK.search(n) for n, _ in raw_tags) or BLOCK.search(s.get("summary") or "")
                    or any(BLOCK.search(n) for n in [s["name"], s["name_cn"], *aliases(info)])):
                continue
            return {
                "id": s["id"], "name": s["name"], "name_cn": s["name_cn"], "nsfw": False,
                "aliases": aliases(info), "info": {k: clip(info[k]) for k in GAME_INFO if info.get(k)},
                "tags": [[n, c] for n, c in raw_tags if not ADULT_TAG.search(n)],
                "summary": "", "staff": people(info),
            }
    return None


async def build(bank, existing=(), background=()) -> list[dict]:
    """在同一个游戏题库追加，固定投票顺序和 ID；同一输入生成相同条目，不产出离线题库。"""
    target = size(bank.config)
    if not target:
        return []
    path = bank.dir / "galgame_vndb.json"
    cache = load_json(path) or {}
    if cache.get("version") != VERSION or time.time() - cache.get("built_at", 0) > 7 * 86400:
        cache = {"version": VERSION, "built_at": time.time(), "source": API, "license": LICENSE,
                 "license_url": LICENSE_URL, "pages": {}, "releases": {}, "matches": {}}
    if cache.get("min_votes") != MIN_VOTES:
        cache["pages"], cache["min_votes"] = {}, MIN_VOTES
    api = Vndb(bank)
    chars_path = bank.dir / "bgm_characters.json"
    chars = load_json(chars_path) or {}
    taken = {norm(n) for entry in existing for n in entry["names"]}
    selected = []
    try:
        for page in range(1, MAX_CANDIDATES // PAGE_SIZE + 1):
            bank.status["game"] = f"正在筛选全年龄视觉小说（第 {page} 页，已收 {len(selected)} 款）"
            key = str(page)
            if key not in cache["pages"]:
                cache["pages"][key] = await api.query("vn", {
                    "filters": ["and", ["olang", "=", "ja"], ["devstatus", "=", 0], ["votecount", ">=", MIN_VOTES],
                                ["release", "=", RELEASE_FILTER]],
                    "fields": FIELDS, "sort": "votecount", "reverse": True, "results": PAGE_SIZE, "page": page,
                })
                save_json(path, cache)
            data = cache["pages"][key]
            for vn in sorted(data["results"], key=lambda v: (-v["votecount"], int(v["id"][1:]))):
                images, vid = safe_images(vn), vn["id"]
                if not safe_work(vn) or len(images) < 9:
                    continue
                if vid not in cache["matches"]:
                    cache["matches"][vid] = await match_bgm(bank, vn)
                    save_json(path, cache)
                bgm = cache["matches"][vid]
                if (not bgm or bgm.get("nsfw") is not False
                        or any(BLOCK.search(n) for n, _ in bgm.get("tags", []))):
                    continue
                all_names = dedupe([*names(vn), bgm["name_cn"], bgm["name"], *bgm["aliases"]])
                if any(BLOCK.search(n) or norm(n) in taken for n in all_names):
                    continue
                if vid not in cache["releases"]:
                    releases = await api.query("release", {
                        "filters": ["and", ["vn", "=", ["id", "=", vid]], RELEASE_FILTER],
                        "fields": "title,minage,official,has_ero,patch,released,platforms,vns{id,rtype}",
                        "sort": "released", "results": 10,
                    })
                    cache["releases"][vid] = next((r for r in releases["results"] if safe_release(r, vid)), None)
                    save_json(path, cache)
                release = cache["releases"][vid]
                if not release or not safe_release(release, vid):
                    continue
                await bank._characters(chars, bgm["id"])
                selected.append((vn, bgm, release, images, all_names))
                taken.update(norm(n) for n in all_names)
                if len(selected) >= target:
                    break
            if len(selected) >= target or not data.get("more"):
                break
    finally:
        save_json(path, cache)
        save_json(chars_path, chars)
    all_bgm = [*background, *(b for _, b, _, _, _ in selected)]
    stats = TagStats.build([b.get("tags") or [] for b in all_bgm],
                          [people(b.get("info") or {}) + b.get("staff", []) for b in all_bgm])
    out = []
    for index, (vn, bgm, release, images, all_names) in enumerate(selected):
        platforms = [PLATFORMS[p] for p in release["platforms"] if p in PLATFORMS]
        item = {"slug": vn["id"], "title": vn.get("alttitle") or vn["title"],
                "premiereYear": str(vn["released"])[:4], "genres": [{"name": "视觉小说"}]}
        entry = game_entry(item, bgm, media={"images": images, "names": all_names}, stats=stats,
                           dev=dedupe(d.get("original") or d["name"] for d in vn.get("developers", [])),
                           chars=chars.get(str(bgm["id"])), platforms=platforms)
        entry["hints"] = [h for h in entry["hints"] if not BLOCK.search(h[1]) and not ADULT_TAG.search(h[1])]
        out.append({**entry, "id": f"vndb-{vn['id']}", "tier": "main" if index < max(1, len(selected) // 2) else "niche",
                    "source": f"{SOURCE}{vn['id']}", "license": LICENSE,
                    "age_basis": {"source": f"{SOURCE}{release['id']}", "minage": release["minage"],
                                  "official": True, "has_ero": False, "patch": False, "rtype": "complete"},
                    "image_license": "截图版权归原作", "votecount": vn["votecount"]})
    return out
