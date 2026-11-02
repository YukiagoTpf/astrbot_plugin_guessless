"""猜K线：随机挑一只沪深300 成分股的一段日 K，藏起名字和日期给大家看 SHOWN 根，猜之后 AHEAD 个交易日涨跌了几个点。

股票池和 K 线用东方财富的行情接口，复权因子用新浪的，都免 key。题库只存股票池，K 线出题时现拉（一只股票的全部历史，
几百 KB），交易日历（上证指数有行情的日子）建题库时存一份，用来挑出中间没停牌的一段。
东财和腾讯的前复权是把分红直接减掉，老价格会变成负数，算涨跌幅不准，所以拉不复权的价格，自己按新浪的因子等比调整。
"""

import asyncio
import json
import random
import re
import time
import unicodedata
from pathlib import Path

import aiohttp

from .sources import WEB_UA, HttpError
from .store import load_json, save_json

VERSION = "hs300-v1"  # 选股规则、条目格式改了就换个名字，题库重建
WARM, SHOWN, AHEAD = 19, 60, 20  # 前 19 根只用来算 20 日均线，给看 60 根，猜之后 20 根
MIN_AGE = 250  # 上市满一年（约 250 个交易日）以后的才出，避开新股头几天的大涨大跌
START = "2010-01-01"  # 只出这以后的
CLIST = "https://push2.eastmoney.com/api/qt/clist/get"
KLINE = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
QFQ = "https://finance.sina.com.cn/realstock/company/{}/qfq.js"
HS300 = "BK0500"  # 东财的沪深300 板块
INDEX = "1.000001"  # 上证指数，当交易日历
# 不出军工、核相关的（红线）：申万国防军工下面的二级行业（地面兵装、航海装备、航空装备、航天装备、军工电子），
# 核力发电（三级行业，中国核电、中国广核的二级行业是「电力」，按二级拦不住），概念带「军工」的，
# 名字带这些字的（中国铀业的行业是「小金属」）
BLOCK_INDUSTRIES = {"BK1229", "BK1230", "BK1231", "BK1232", "BK1233"}
NUCLEAR = "BK1376"
BLOCK_NAME = re.compile(r"核|铀|军|兵|航发|中航|船舶")
CN_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
# 群友说的涨跌：「涨3」「跌1.5%」「+2」「-0.5个点」「涨三个点」「跌一个半点」「涨三点五」「平」。
# 不带涨跌也不带单位的光秃秃一个数不算（群里随口说的数字太多）
GUESS = re.compile(
    r"(?:我猜|我赌|猜)?(?P<sign>涨|跌|升|降|[+＋\-－−])?"
    r"(?P<num>\d+(?:\.\d+)?|半|[零〇一二两三四五六七八九十]+(?:点[零〇一二三四五六七八九]+)?)"
    r"(?P<half>个半)?(?P<unit>个百分点|个点|点|%|％)?"
)
FLAT = {"平", "持平", "不涨不跌", "横盘"}
NEED_NUMBER = {"涨", "跌", "看涨", "看跌"}  # 只说了方向，提醒一下要带数字
PLUS_ONE = {"+1", "＋1"}  # 群里「+1」是附和，不算猜涨 1%


async def _get(session: aiohttp.ClientSession, url: str, source: str, params: dict | None = None) -> str:
    timeout = aiohttp.ClientTimeout(total=30)
    for attempt in range(3):
        try:
            async with session.get(url, params=params, headers={"User-Agent": WEB_UA}, timeout=timeout) as r:
                if r.status >= 400:
                    raise HttpError(source, r.status)
                return await r.text()
        except (aiohttp.ClientConnectionError, asyncio.TimeoutError):
            if attempt == 2:
                raise
            await asyncio.sleep(3)
    return ""


async def _clist(session: aiohttp.ClientSession, board: str, fields: str) -> list[dict]:
    """东财板块的成分股，一页最多 100 只。"""
    rows, page = [], 1
    while True:
        params = {"pn": page, "pz": 100, "po": 1, "np": 1, "fltt": 2, "invt": 2, "fid": "f12", "fs": f"b:{board}",
                  "fields": fields}
        data = json.loads(await _get(session, CLIST, "东方财富", params)).get("data") or {}
        rows += data.get("diff") or []
        if not data.get("diff") or len(rows) >= data.get("total", 0):
            return rows
        page += 1


async def _klines(session: aiohttp.ClientSession, secid: str, fields: str) -> list[str]:
    """东财的全部日 K（不复权），每行是 fields 里那几列用逗号连起来，按日期从旧到新。盘中会带上今天还没收完的一根。"""
    params = {"secid": secid, "fields1": "f1,f2,f3", "fields2": fields, "klt": 101, "fqt": 0,
              "beg": 0, "end": 20500101, "lmt": 1000000}
    data = json.loads(await _get(session, KLINE, "东方财富", params)).get("data")
    if not data or not data.get("klines"):
        raise RuntimeError(f"东方财富没有 {secid} 的 K 线")
    return data["klines"]


async def _factors(session: aiohttp.ClientSession, symbol: str) -> list[tuple[str, float]]:
    """新浪的前复权因子 [(生效日, 因子)]，从新到旧。因子从生效日起用到下一个更新的生效日前一天，
    前复权价 = 不复权价 ÷ 因子（最新一段是 1）。"""
    text = await _get(session, QFQ.format(symbol), "新浪财经")
    data = json.loads(text[text.index("{"): text.rindex("}") + 1]).get("data") or []
    return sorted(((x["d"], float(x["f"])) for x in data), reverse=True) or [("1900-01-01", 1.0)]


def _factor(factors: list[tuple[str, float]], day: str) -> float:
    return next((f for d, f in factors if d <= day), 1.0)


def blocked(row: dict, name: str, nuclear: set[str]) -> bool:
    return (row.get("f265") in BLOCK_INDUSTRIES or row["f12"] in nuclear or "军工" in str(row.get("f103") or "")
            or bool(BLOCK_NAME.search(name)))


async def build(bank) -> None:
    session = bank.http()
    bank.status["kline"] = "正在拉沪深300 成分股"
    rows = await _clist(session, HS300, "f12,f13,f14,f26,f100,f103,f265")
    nuclear = {r["f12"] for r in await _clist(session, NUCLEAR, "f12")}
    bank.status["kline"] = "正在拉交易日历"
    save_json(bank.dir / "kline_calendar.json", await _klines(session, INDEX, "f51"))
    # 上市日（f26，YYYYMMDD）晚于这天的凑不出一段能出题的（MIN_AGE + 99 根，差不多一年半），不进股票池
    cutoff = int(time.strftime("%Y%m%d", time.localtime(time.time() - 540 * 86400)))
    entries = []
    for r in rows:
        if int(r.get("f26") or 0) > cutoff:
            continue
        # 东财的名字是全角、带空格的（「万  科Ａ」），科创板的带 -U、-W 后缀
        name = re.sub(r"-[A-Z]+$", "", unicodedata.normalize("NFKC", r["f14"]).replace(" ", ""))
        if blocked(r, name, nuclear):
            continue
        code = r["f12"]
        entries.append({
            "id": f"k-{code}", "group": code, "title": name, "names": [name], "answer": f"{name}（{code}）",
            "secid": f"{r['f13']}.{code}", "symbol": ("sh" if r["f13"] == 1 else "sz") + code,
            "industry": r.get("f100") or "", "hints": [],
        })
    bank.commit("kline", entries)


async def load(session: aiohttp.ClientSession, entry: dict, data_dir: Path) -> list[list]:
    """随机挑一段 WARM + SHOWN + AHEAD 根的日 K：中间没停牌（和交易日历一天不差），上市满 MIN_AGE 个交易日、
    START 以后，不含今天（盘中还没收完）。返回 [日期, 开, 收, 高, 低, 量]，价格按复权因子等比调整到这段的最后一天，
    这段里有分红送转也是连着的。"""
    cal = load_json(data_dir / "kline_calendar.json") or []
    pos = {d: i for i, d in enumerate(cal)}
    rows = [line.split(",") for line in await _klines(session, entry["secid"], "f51,f52,f53,f54,f55,f56")]
    at = [pos.get(r[0], -1) for r in rows]
    n, today = WARM + SHOWN + AHEAD, time.strftime("%Y-%m-%d")
    starts = [
        j for j in range(MIN_AGE, len(rows) - n + 1)
        if rows[j][0] >= START and rows[j + n - 1][0] < today and at[j] >= 0 and at[j + n - 1] - at[j] == n - 1
    ]
    if not starts:
        raise RuntimeError(f"{entry['title']} 没有能出题的一段")
    window = rows[(j := random.choice(starts)): j + n]
    factors = await _factors(session, entry["symbol"])
    end = _factor(factors, window[-1][0])
    bars = []
    for day, *nums in window:
        o, c, h, lo, vol = map(float, nums)
        k = end / _factor(factors, day)
        bars.append([day, round(o * k, 3), round(c * k, 3), round(h * k, 3), round(lo * k, 3), round(vol / k)])
    return bars


def pct(v: float) -> str:
    return f"{v:+.2f}%"


def outcome(entry: dict, bars: list[list]) -> dict:
    """揭晓用的：这段之后 AHEAD 个交易日的涨跌幅（保留两位，排名也按这个算）、答案卡的提示。
    最大浮盈、最大回撤按之后那段的最高价、最低价比看到的最后一根收盘。"""
    base, after = bars[WARM + SHOWN - 1][2], bars[WARM + SHOWN:]
    change = round((after[-1][2] / base - 1) * 100, 2)
    up = max(0.0, (max(b[3] for b in after) / base - 1) * 100)
    down = min(0.0, (min(b[4] for b in after) / base - 1) * 100)
    return {
        "change": change,
        # 东财的行业是申万二级，和一级同名的带「Ⅱ」
        "original": " · ".join(x for x in (entry["group"], (entry.get("industry") or "").rstrip("Ⅱ")) if x),
        "hints": [
            ["区间", f"{bars[WARM][0]} ~ {bars[-1][0]}"], ["看到", bars[WARM + SHOWN - 1][0]],
            [f"{AHEAD}日涨跌", pct(change)], ["走势", trend(change, up, down)],
            ["最大浮盈", pct(up)], ["最大回撤", pct(down)],
        ],
    }


def trend(change: float, up: float, down: float) -> str:
    """之后那段怎么走的，up / down 是最大浮盈、最大回撤（%）。"""
    if abs(change) < 2 and up < 4 and down > -4:
        return "横盘震荡"
    if change >= 15 or change <= -15:
        return "大涨" if change > 0 else "大跌"
    if change >= 0:
        return "先跌后涨" if down <= -3 else "冲高回落" if up - change >= 5 else "一路上涨"
    return "冲高回落" if up >= 3 else "探底回升" if change - down >= 5 else "一路下跌"


def question_hints() -> list[list[str]]:
    return [
        ["市场", "A股 · 沪深300 · 日K"], ["给看", f"{SHOWN} 个交易日"],
        ["猜", f"之后 {AHEAD} 个交易日的涨跌幅"], ["说法", "涨3、跌1.5、-2%、平"],
    ]


def _cn_number(s: str) -> float | None:
    """「三」「十五」「二十」「三点五」，最多两位整数。"""
    whole, _, frac = s.partition("点")
    if whole.count("十") > 1 or len(whole) > 3:
        return None
    if "十" in whole:
        tens, _, ones = whole.partition("十")
        if len(tens) > 1 or len(ones) > 1 or tens in ("零", "〇"):
            return None
        value = (CN_DIGITS[tens] if tens else 1) * 10 + (CN_DIGITS[ones] if ones else 0)
    elif len(whole) == 1:
        value = CN_DIGITS[whole]
    else:
        return None
    return float(f"{value}.{''.join(str(CN_DIGITS[c]) for c in frac)}") if frac else float(value)


def parse(text: str) -> float | None:
    """群友这句话猜的涨跌幅（%），不是在猜返回 None。"""
    text = re.sub(r"[\s。！!～~]+", "", text)
    if text in FLAT:
        return 0.0
    if text in PLUS_ONE:
        return None
    if not (m := GUESS.fullmatch(text)) or not (m["sign"] or m["unit"]):
        return None
    num = m["num"]
    value = 0.5 if num == "半" else float(num) if num[0].isdigit() else _cn_number(num)
    if value is None:
        return None
    if m["half"]:
        if num == "半" or "." in num or "点" in num:
            return None
        value += 0.5
    if m["sign"] in ("跌", "降", "-", "－", "−"):
        value = -value
    return round(value, 2) + 0.0 if -100 <= value <= 1000 else None  # + 0.0 把「跌0」的 -0.0 变成 0.0


def fmt(v: float) -> str:
    """猜的涨跌幅怎么念：涨3%、跌1.5%、平。"""
    return "平" if v == 0 else f"{'涨' if v > 0 else '跌'}{abs(v):g}%"
