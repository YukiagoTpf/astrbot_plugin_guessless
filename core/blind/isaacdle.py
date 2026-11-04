"""盲猜以撒道具：猜一个《以撒的结合》的道具（collectible），对比品质、主动 / 被动、道具池、充能、版本、解锁。

资料来自以撒官方 wiki（bindingofisaacrebirth.wiki.gg）的 Cargo 表：道具、品质、道具池、充能、拾取台词，都取忏悔+
（dlc 位掩码带 16）现行的那一行。简中名是官方译名，取自 EID 模组的 names/zh_cn.lua（按道具 ID）。缓存一个月
（isaac_items.json）。七百多个道具都能猜；涉黄、自残、自杀、战争的（NO_ANSWER）不出题，还能猜。
"""

import asyncio
import re
import time

from ..answer import norm
from ..sources import WIKI_UA, dedupe
from ..store import load_json, save_json
from .base import Blind, blank, entry, num, same

VERSION = "rep-plus-v1"  # 选题（NO_ANSWER、MAIN_QUALITY）、条目格式、提示、叫法规则改了就换个名字，题库重建
COLUMNS = ("品质", "类型", "道具池", "充能", "版本", "解锁")
CLUES = ("台词", "编号", "首字")
API = "https://bindingofisaacrebirth.wiki.gg/api.php"
ZH_NAMES = "https://raw.githubusercontent.com/wofsauge/External-Item-Descriptions/master/descriptions/names/zh_cn.lua"
CACHE_DAYS = 30
REP_PLUS = 16  # Cargo 表的 dlc 是位掩码：1 重生、2 胎衣、4 胎衣†、8 忏悔、16 忏悔+，这一行在哪些版本里成立
DLCS = ("重生", "胎衣", "胎衣†", "忏悔")  # 最早出现在哪个版本，位掩码的最低位；忏悔+ 没有加新道具
MAIN_QUALITY = 3  # 品质 3、4 的和重生就有的是大众题
# 道具池的中文名，排在前面的先当这个道具的「道具池」显示（宝箱房、恶魔房的道具也常在抓娃娃机里，写宝箱房）；
# 贪婪模式的池不算
POOLS = {
    "Treasure Room": "宝箱房", "Devil Room": "恶魔房", "Angel Room": "天使房", "Boss": "Boss房", "Shop": "商店",
    "Secret Room": "隐藏房", "Ultra Secret Room": "超隐", "Curse Room": "诅咒房", "Library": "图书馆",
    "Planetarium": "星象房", "Golden Chest": "金箱子", "Red Chest": "红箱子", "Mom's Chest": "妈妈的箱子",
    "Old Chest": "旧箱子", "Wooden Chest": "木箱子", "Beggar": "乞丐", "Devil Beggar": "恶魔乞丐",
    "Key Master": "钥匙乞丐", "Bomb Bum": "炸弹乞丐", "Battery Bum": "电池乞丐", "Rotten Beggar": "腐烂乞丐",
    "Crane Game": "抓娃娃机", "Shell Game": "猜杯子", "Baby Shop": "宝宝商店",
}
# 不出题（还能猜）：涉黄、擦边的（内裤、胸罩、卫生巾、胎盘、胎儿、衣架），自残、自杀、毒品的（剃刀片、计划C、安乐死、
# 混合麻药），战争的（神风）。只能加不能删
NO_ANSWER = {29, 32, 39, 40, 41, 52, 126, 143, 168, 218, 475, 486, 496, 508, 555}
ALIASES = {114: ["妈刀"], 118: ["硫磺"], 105: ["D6"], 331: ["神头"]}  # 官方译名以外大家常叫的


async def _cargo(bank, table: str, fields: str) -> list[dict]:
    """Cargo 表里忏悔+ 现行的行，一次最多 500 行，一秒一页。"""
    rows, offset = [], 0
    while True:
        params = {"action": "cargoquery", "format": "json", "tables": table, "fields": fields,
                  "where": f"(dlc & {REP_PLUS}) > 0", "limit": 500, "offset": offset}
        page = [r["title"] for r in (await bank._get(API, params=params, ua=WIKI_UA)).get("cargoquery", [])]
        rows += page
        await asyncio.sleep(1)
        if len(page) < 500:
            return rows
        offset += 500


async def fetch_items(bank) -> list[dict]:
    """所有道具，按 ID 排。pools 是非贪婪模式的道具池（英文），charge 是充能（「4」「10s」「unlimited」……，被动道具空）。"""
    items = await _cargo(bank, "collectible", "id,alias,dlc,is_activated,unlocked_by")
    quality = {r["collectible"]: r["quality"] for r in await _cargo(bank, "collectible_quality", "collectible,quality")}
    charge = {r["collectible"]: r["recharge"] for r in await _cargo(bank, "collectible_recharge", "collectible,recharge")}
    quote = {r["collectible"]: r["quote"] for r in await _cargo(bank, "collectible_quote", "collectible,quote")}
    pools: dict[str, list[str]] = {}
    for r in await _cargo(bank, "pool_collectible", "pool,collectible"):
        if r["pool"] in POOLS:
            pools.setdefault(r["collectible"], []).append(r["pool"])
    async with bank.http().get(ZH_NAMES, headers={"User-Agent": WIKI_UA}) as r:
        r.raise_for_status()
        lua = await r.text()
    # 「[C_ID .. 118] = "硫磺火", -- Brimstone」，道具那一节在饰品（trinkets）前面
    zh = {int(i): n for i, n in re.findall(r'\[C_ID \.\. (\d+)\] = "([^"]*)"', lua.split("---------trinkets")[0])}
    out = []
    for r in items:
        en, dlc = r["alias"], int(r["dlc"] or 0)
        out.append({
            "id": int(r["id"]), "en": en, "zh": zh.get(int(r["id"])) or "",
            "quality": int(quality[en]) if str(quality.get(en, "")).isdigit() else None,
            "active": r["is activated"] == "1", "charge": charge.get(en, ""), "quote": quote.get(en, ""),
            "pools": sorted(set(pools.get(en, [])), key=list(POOLS).index),
            "dlc": next((i for i in range(len(DLCS)) if dlc >> i & 1), 0), "unlock": bool(r["unlocked by"]),
        })
    return sorted(out, key=lambda x: x["id"])


def charge_of(raw: str) -> tuple[str, str, float]:
    """充能怎么写、单位、数：「4」是 4 格，「10s」是 10 秒，「1f」是每帧，unlimited 是不用充，one time 是一次性。"""
    if m := re.fullmatch(r"(\d+(?:\.\d+)?)([sf]?)", raw):
        n, unit = float(m[1]), m[2]
        return {"": f"{n:g}格", "s": f"{n:g}秒", "f": "每帧"}[unit], unit or "格", n
    return {"unlimited": "无限", "one time": "一次性", "variable": "不定", "damage": "受伤充"}.get(raw, raw), raw, 0


def attrs_of(it: dict) -> dict:
    """对比用的资料，见 row。"""
    return {"key": str(it["id"]), "quality": it["quality"], "active": it["active"], "charge": it["charge"],
            "pools": it["pools"], "dlc": it["dlc"], "unlock": it["unlock"]}


def row(guess: dict, answer: dict) -> list[list[str]]:
    """品质：0–4，差一个黄，箭头指答案更高（↑）还是更低（↓）。类型：主动、被动。道具池：写第一个（POOLS 的顺序），
    一样绿，不一样但能在同一个池里出（都进宝箱房）黄。充能：主动道具的，一样绿；单位一样（都是格、都是秒）差一格或不到
    1.5 倍黄，箭头指答案充得更多（↑）还是更少（↓）；被动道具写「—」。版本：最早出现在哪个版本，差一个黄，
    箭头指答案更新（↑）还是更老（↓）。解锁：一开始就有还是要解锁。"""
    x, y = guess["quality"], answer["quality"]
    quality = num(x, y, str(x), abs(x - y) == 1) if x is not None and y is not None else blank()
    gp, ap = guess["pools"], answer["pools"]
    if not gp:
        pool = ["特殊", "hit" if not ap else "miss", ""]  # 任务道具、拼出来的（菜刀碎片、钥匙碎片）不在任何池里
    else:
        pool = [POOLS[gp[0]], "hit" if ap and gp[0] == ap[0] else "near" if set(gp) & set(ap) else "miss", ""]
    if not guess["charge"]:
        charge = blank()
    elif guess["charge"] == answer["charge"]:
        charge = [charge_of(guess["charge"])[0], "hit", ""]
    else:
        (text, unit, m), (_, unit2, n) = charge_of(guess["charge"]), charge_of(answer["charge"])
        if unit == unit2 and m and n:
            charge = num(m, n, text, abs(m - n) <= 1 if unit == "格" else max(m, n) <= min(m, n) * 1.5)
        else:
            charge = [text, "miss", ""]
    return [
        quality,
        same("主动" if guess["active"] else "被动", "主动" if answer["active"] else "被动"),
        pool,
        charge,
        num(guess["dlc"], answer["dlc"], DLCS[guess["dlc"]], abs(guess["dlc"] - answer["dlc"]) == 1),
        same("要解锁" if guess["unlock"] else "开局有", "要解锁" if answer["unlock"] else "开局有"),
    ]


def item_names(it: dict) -> list[str]:
    """官方简中名、英文名、英文去掉开头的「The」（「The Sad Onion」说「Sad Onion」）、ALIASES。
    两个字母以内的英文不算（「D6」在 ALIASES 里另给）：盲猜以撒道具只认整句就是这个名字的，还是会和闲聊撞上。"""
    names = [it["zh"], it["en"], re.sub(r"^The ", "", it["en"])]
    names = [n for n in dedupe(names) if n and not (n.isascii() and len(n) <= 2)]
    return dedupe(names + ALIASES.get(it["id"], []))


async def build(bank) -> None:
    now, path = time.time(), bank.dir / "isaac_items.json"
    cached = load_json(path) or {}
    if now - cached.get("at", 0) > CACHE_DAYS * 86400:
        bank.status["isaacdle"] = "正在从以撒 wiki 拉道具"
        cached = {"at": now, "items": await fetch_items(bank)}
        save_json(path, cached)
    items = cached["items"]
    # 同名的叫法只归 ID 小的那个
    owner: dict[str, int] = {}
    names = {}
    for it in items:
        names[it["id"]] = [n for n in item_names(it) if owner.setdefault(norm(n), it["id"]) == it["id"]]
    places = {str(it["id"]): {"title": it["zh"] or it["en"], "names": names[it["id"]], "attrs": attrs_of(it)}
              for it in items if names[it["id"]]}
    entries = []
    for it in items:
        if it["id"] in NO_ANSWER or not it["zh"] or it["quality"] is None or it["zh"] not in names[it["id"]]:
            continue
        hints = [["台词", it["quote"]], ["编号", f"#{it['id']}"]]
        main = it["quality"] >= MAIN_QUALITY or it["dlc"] == 0
        entries.append(entry("ic", str(it["id"]), it["zh"], it["en"], f"{it['zh']}（{it['en']}）", names[it["id"]],
                             hints, attrs_of(it), main))
    bank.commit("isaacdle", entries, places=places)


MODE = Blind(
    cat="isaacdle", command="盲猜以撒道具", desc="不给图，猜一个以撒的结合的道具，看品质、道具池、充能对比", header="道具",
    columns=COLUMNS, clues=CLUES, row=row, build=build, settings=(VERSION,), exact=True,
    aliases=("盲猜以撒", "猜以撒道具", "猜以撒", "盲猜以撒的结合"),
)
