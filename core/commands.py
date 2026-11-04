"""认 @bot 说的是哪条指令。AstrBot 只认原样注册的写法，群友记不全、说不准的（「猜 k 线」「来把盲猜日本」「K线榜」
「宝可梦排名」「猜宝克梦」）在这里兜底：先压掉空格、全角、大小写和「来个」「吧」这类客套，再按关键词认题型；
还认不出就和所有指令比像不像，很像的直接当它，有点像的给建议。纯函数，不联网。
"""

import difflib
import re
import unicodedata

# 题型的关键词，按从具体到宽泛排，先认到的算：「听歌猜番」里有「番」要先认成听歌，「猜日本地点」里有「地点」要先认成日本
KEYWORDS = (
    ("sakedle", ("日本酒", "清酒", "sake", "酒")),  # 要在日本前面：「日本酒」里有「日本」
    ("wow", ("魔兽世界", "魔兽", "wow", "艾泽拉斯", "图寻")),
    ("anisong", ("听歌", "猜歌", "番曲", "歌曲", "听op", "听ed", "歌")),
    ("kline", ("k线", "股票", "股市", "a股", "炒股", "大盘")),
    ("jpgeodle", ("市町村",)),
    ("jpgeo", ("日本", "都道府县")),
    ("cngeodle", ("中国", "城市", "地级市")),
    ("member", ("群友", "头像")),
    ("vtuber", ("vtuber", "vup", "虚拟主播", "主播")),
    ("pokemon", ("宝可梦", "神奇宝贝", "口袋妖怪", "宠物小精灵", "精灵", "pokemon")),
    ("mecha", ("萝卜", "高达", "敢达", "机体", "机甲", "机器人", "gundam")),
    ("anime", ("动画", "动漫", "番剧", "新番", "番", "anime")),
    ("game", ("游戏", "game")),
    ("geo", ("街景", "地点", "地理", "国家")),
)
BLIND_OF = {"pokemon": "pokedle", "mecha": "mechadle", "geo": "geodle", "jpgeo": "jpgeodle", "jpgeodle": "jpgeodle",
            "cngeodle": "cngeodle", "sakedle": "sakedle"}
ONLY_BLIND = {"jpgeodle", "cngeodle", "sakedle"}  # 只有盲猜的，没说「盲」也是它
GEO_WORDS = ("地点", "地方", "地区", "地名", "街景")  # 认成地点类以后这些不算多余（「盲猜日本的地点」「日本街景」）
BOARD = re.compile(r"排行榜|排行|排名|榜单|战斗力|战力|榜")
# 不认 help，AstrBot 自带。长的放前面，免得「有什么玩法」只认掉「有什么玩」
HELP = re.compile(r"有什么玩法|有啥玩法|有什么功能|有啥功能|有什么玩|有啥玩|怎么玩|帮助|菜单|指令|命令|玩法")
# 认完题型、排行榜之后，剩下的只能是这些：开题的动词、量词、语气词。长的放前面
FILLER = re.compile(
    r"猜题|开始|开一|帮我|给我|我要|我想|看看|一下|一把|一局|一道|一个|一轮|全部|所有|本群|总|"
    r"猜|盲|请|想|再|来|开|玩|整|搞|出|看|查|一|个|道|把|局|题|下|轮|次|吧|呗|呀|啊|嘛|哈|了|的|呢|捏|[!?~。，,.]"
)
TAIL = re.compile(r"(?:一下|下|吧|呗|呀|啊|嘛|哈|了|呢|捏|[!?~。，,.])+$")
PAUSE = re.compile(r"(?:暂停|停止|关闭|关掉|停掉)(?:猜题|出题|游戏)?|(?:猜题|出题)(?:暂停|停止|关闭|关掉|停了|关了)|别猜|不猜")  # 末尾的「了」已经当语气词去掉了
RESUME = re.compile(r"(?:重新开始|恢复|继续|开启|打开|重开)(?:猜题|出题|游戏)?|(?:猜题|出题)(?:恢复|继续|开始|开启|重开)")
# 局中喊提示：「提示」「给个提示」「再来点提示」「提示一下」
HINT = re.compile(r"(?:再|多)?(?:给|来|要)?(?:个|点|一点|一个|条|一条)?(?:提示|线索)(?:一下|下|吧|呗|啊|呀|嘛)*|提示提示|hint")
CN_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def squash(text: str) -> str:
    """去掉空白、全角转半角、不分大小写：「猜 k 线」「猜Ｋ线」都是「猜k线」。"""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text)).casefold()


def cn_number(s: str) -> float | None:
    """「三」「十五」「二十」「三点五」，最多两位整数；不是中文数字返回 None。"""
    whole, _, frac = s.partition("点")
    if not whole or any(c not in CN_DIGITS and c != "十" for c in whole) or any(c not in CN_DIGITS for c in frac):
        return None
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


def number(s: str) -> int | None:
    """阿拉伯数字或者中文数字（到九十九）。"""
    if s.isdigit():
        return int(s)
    value = cn_number(s)
    return int(value) if value is not None and value == int(value) else None


def _empty(s: str) -> bool:
    return not FILLER.sub("", s)


def intent(text: str) -> tuple[str, str] | None:
    """按关键词认，返回 (动作, 题型)：("start", 题型) 开题，("board", 题型) 这一类的排行榜，("boards", "") 总排行榜，
    ("help", "") 帮助，("pause", "") / ("resume", "") 暂停、恢复。认不出返回 None。
    认出来以后剩下的只能是客套话（FILLER），多出别的字不算：「番茄」不是猜动画，「高达好帅」不是猜萝卜。"""
    s = squash(text)
    bare = TAIL.sub("", s)
    if PAUSE.fullmatch(bare):
        return "pause", ""
    if RESUME.fullmatch(bare):
        return "resume", ""
    if HELP.search(s) and _empty(HELP.sub("", s)):
        return "help", ""
    kind = "board" if BOARD.search(s) else "start"
    blind = "盲" in s
    s = BOARD.sub("", s)
    for cat, words in KEYWORDS:
        if not any(w in s for w in words):
            continue
        rest = s
        for w in sorted({*words, *(GEO_WORDS if cat in BLIND_OF else ())}, key=len, reverse=True):
            rest = rest.replace(w, "")
        if not _empty(rest):
            return None
        if blind or cat in ONLY_BLIND:
            if cat not in BLIND_OF:
                return None  # 「盲猜游戏」：没有这个玩法
            cat = BLIND_OF[cat]
        return kind, cat
    if kind == "board" and _empty(s):
        return "boards", ""
    return None


def closest(text: str, names: dict[str, tuple[str, str]]) -> tuple[tuple[str, str] | None, list[str]]:
    """和所有指令的说法（names：说法 -> 动作）比像不像（difflib）。很像（≥ 0.75，且比第二像的高出 0.1）的返回它的动作，
    比如打错一个字的「猜宝克梦」；有点像（≥ 0.5）的返回最多三个说法当建议。"""
    s = squash(text)
    scored = sorted(((difflib.SequenceMatcher(None, s, squash(n)).ratio(), n) for n in names), reverse=True)
    if not scored:
        return None, []
    (best, name), second = scored[0], scored[1][0] if len(scored) > 1 else 0
    if best >= 0.75 and best - second >= 0.1:
        return names[name], []
    seen, out = set(), []
    for score, n in scored:
        if score < 0.5 or len(out) == 3:
            break
        if names[n] not in seen:  # 同一条指令的几个说法只建议一个
            seen.add(names[n])
            out.append(n)
    return None, out
