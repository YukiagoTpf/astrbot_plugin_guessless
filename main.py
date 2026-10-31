import asyncio
import math
import random
import re
import time
from collections import deque
from dataclasses import dataclass, field

import astrbot.api.message_components as Comp
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star, StarTools

from .core import audio, cards, geo, members
from .core.answer import candidates, is_correct
from .core.bank import Bank
from .core.blind import BLIND
from .core.lines import LINES, say
from .core.store import load_json, save_json

# 题型 -> 开题指令，也是卡片标题；排行榜指令去掉开头的「猜」。盲猜系列（不给图，每猜一个回一行对比）见 core/blind
MODES = {
    "game": "猜游戏", "anime": "猜动画", "anisong": "听歌猜番", "pokemon": "猜宝可梦", "mecha": "猜萝卜",
    "vtuber": "猜Vtuber", "member": "猜群友", "geo": "猜地点", "country": "猜国家", "jpgeo": "猜日本地点",
    **{cat: b.command for cat, b in BLIND.items()},
}
BOARDS = {cat: name.removeprefix("猜") + "排行榜" for cat, name in MODES.items()}
OFFLINE = {"country"}  # 暂时下线的题型：开题和排行榜都只回一句，不出题、不播报
GRID = {"game", "anime", "pokemon", "mecha", "vtuber"}  # 九宫格逐格揭开的题型
# 按「说的是哪一个」判对错的题型（地点类之外）：机体名互相包含（「自由高达」和「强袭自由高达」），不能按一大段算对；
# Vtuber 的名字也常互相包含；群友按 @、QQ 号、名片认人
BY_KEY = {"mecha", "mechadle", "vtuber", "member"}
GEO_ALIASES = {"猜街景"}
JPGEO_ALIASES = {"猜日本街景"}
VTUBER_ALIASES = {"猜vtuber", "猜VTuber", "猜V", "猜v"}
VTUBER_BOARD_ALIASES = {"vtuber排行榜", "VTuber排行榜", "V排行榜"}
ALL_BOARDS_ALIASES = {"战斗力榜", "总排行榜"}  # 管理员提前发整点排行榜，指令本身是「排行榜」
COMMANDS = (
    set(MODES.values()) | set(BOARDS.values()) | {"暂停", "重新开始", "排行榜", "宵禁", "取消宵禁", "恢复宵禁"} | GEO_ALIASES | JPGEO_ALIASES
    | VTUBER_ALIASES | VTUBER_BOARD_ALIASES | ALL_BOARDS_ALIASES | {a for b in BLIND.values() for a in b.aliases}
)
# @bot 说「猜xx」「盲猜xx」「听歌猜xx」但没有这个题型时回一句
UNKNOWN_MODE = re.compile(r"^(?:盲猜|听歌猜|猜)\S{1,12}$")
AT_TEXT = re.compile(r"\s*@[^@]*?\(\d+\)\s*")  # message_str 里 @ 别人的部分：「 @名片(QQ号) 」
HOUR = 3600
BOARD_TOP = 5  # 整点排行榜每个题型列前几名


@dataclass
class Round:
    category: str
    umo: str
    entry: dict | None = None
    image: object = None
    reveal: object = None  # 答案卡用的图，没有就用 image
    hints: list = field(default_factory=list)  # 这道题按顺序给的提示，一般就是 entry["hints"]
    cands: set = field(default_factory=set)
    order: list = field(default_factory=list)  # 九宫格揭开顺序，开题时按图片排（cards.reveal_order）
    shown: int = 1
    deadline: float = 0.0
    last_hint: float = 0.0
    hint_wait_sent: float = -1.0  # 已对哪次揭开后的冷却发过「等一下」，每个冷却窗口只回一次
    timer: asyncio.Task | None = None
    paid: str = ""  # 这道题扣的是每小时额度（hourly）还是管理员加的题（bonus），没出成时退回去
    wrong: dict = field(default_factory=dict)  # 用户 -> 猜错过的答案（Bank.match 的键），每人最多 _limit 个
    # 有反馈的题型（地点类、盲猜）本题猜错过的：{"key", "title", "km" 或 "row"}，再猜一次不扣次数
    history: list = field(default_factory=list)
    pcm: bytes = b""  # 听歌猜番：解码好的音频，见 audio.decode
    peek: object = None  # 盲猜萝卜：对比表旁边放的一格设定图，整道题不变
    song: str = ""  # 听歌猜番：答案卡上写的歌名和歌手
    avatar: object = None  # 猜群友：头像（只在内存里）
    plan: list = field(default_factory=list)  # 猜群友：每段露哪几格，见 members.plan
    roster: object = None  # 猜群友：开题时的群成员，认人用，见 members.Roster

    def left(self) -> int:
        return max(0, round(self.deadline - time.monotonic()))

    @property
    def key(self) -> str:
        """答案在 Bank.match 里的键：地点类是国家 / 都道府县代码。"""
        return self.entry.get("group", self.entry["id"])


class GuessPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.data_dir = StarTools.get_data_dir("astrbot_plugin_guessless")
        self.bank = Bank(self.data_dir, config)
        self.rounds: dict[str, Round] = {}  # 群号 -> 进行中的题目，一个群同时只有一道
        self.recent = {cat: deque(maxlen=100) for cat in MODES}
        self.scores = load_json(self.data_dir / "scores.json") or {}
        self.paused: set[str] = set(load_json(self.data_dir / "paused.json") or [])
        # 群号 -> 最近一小时内开题的时间戳，所有猜题共用一个额度；存盘是为了重启不清零
        self.quota: dict[str, list[float]] = load_json(self.data_dir / "quota.json") or {}
        # 群号 -> 管理员额外放开的题数，每小时额度用完后再扣，不过期
        self.bonus: dict[str, int] = load_json(self.data_dir / "bonus.json") or {}
        # 群号 -> 管理员临时解禁宵禁到什么时候（时间戳），过了就照常宵禁
        self.curfew_lifts: dict[str, float] = load_json(self.data_dir / "curfew_lifts.json") or {}
        self._announcer: asyncio.Task | None = None
        # 群号 -> 发消息用的 unified_msg_origin，整点播报排行榜要用；群里有人说话就记下
        self.origins: dict[str, str] = load_json(self.data_dir / "origins.json") or {}
        # 群号 -> 上次播报时各题型的答对次数 {题型: {用户: 次数}}，算「这段时间新增」，没变就不播
        self.broadcasts: dict[str, dict] = load_json(self.data_dir / "broadcasts.json") or {}
        self._slot, self._due = self._board_slot(), set()
        self._broadcaster: asyncio.Task | None = None
        self.member_bags: dict[str, set] = {}  # 群号 -> 这一轮猜群友出过题的人，都出过了再清空

    async def initialize(self):
        self.bank.start()
        # 上次退出时还没结束的题（重启、停机时恰好有人在猜）：退额度，连上平台后公布答案
        if voided := load_json(self.data_dir / "rounds.json"):
            for gid, rec in voided.items():
                self._quota_refund(gid, rec.get("paid", ""))
            save_json(self.data_dir / "rounds.json", {})
            self._announcer = asyncio.create_task(self._announce_void(voided))
        self._broadcaster = asyncio.create_task(self._broadcast_loop())

    async def terminate(self):
        for rnd in self.rounds.values():
            if rnd.timer:
                rnd.timer.cancel()
        for task in (self._announcer, self._broadcaster):
            if task:
                task.cancel()
        await self.bank.close()

    @filter.command("猜游戏")
    async def guess_game(self, event: AstrMessageEvent):
        """开一道猜游戏（Metacritic 评分榜）"""
        async for r in self._start(event, "game"):
            yield r

    @filter.command("猜动画")
    async def guess_anime(self, event: AstrMessageEvent):
        """开一道猜动画（Bangumi 热门）"""
        async for r in self._start(event, "anime"):
            yield r

    @filter.command("听歌猜番")
    async def guess_anisong(self, event: AstrMessageEvent):
        """开一道听歌猜番（动画 OP / ED 片段）"""
        async for r in self._start(event, "anisong"):
            yield r

    @filter.command("猜宝可梦")
    async def guess_pokemon(self, event: AstrMessageEvent):
        """开一道猜宝可梦（全国图鉴）"""
        async for r in self._start(event, "pokemon"):
            yield r

    @filter.command("猜萝卜")
    async def guess_mecha(self, event: AstrMessageEvent):
        """开一道猜萝卜（高达和超级机器人大战的机体）"""
        async for r in self._start(event, "mecha"):
            yield r

    @filter.command("猜Vtuber", alias=VTUBER_ALIASES)
    async def guess_vtuber(self, event: AstrMessageEvent):
        """开一道猜Vtuber（B 站上国内外的虚拟主播）"""
        async for r in self._start(event, "vtuber"):
            yield r

    @filter.command("猜群友")
    async def guess_member(self, event: AstrMessageEvent):
        """开一道猜群友（看头像的一小块）"""
        async for r in self._start(event, "member"):
            yield r

    @filter.command("猜地点", alias=GEO_ALIASES)
    async def guess_geo(self, event: AstrMessageEvent):
        """开一道猜地点（Google 街景，猜国家）"""
        async for r in self._start(event, "geo"):
            yield r

    @filter.command("猜国家")
    async def guess_country(self, event: AstrMessageEvent):
        """猜国家（暂时下线）"""
        async for r in self._start(event, "country"):
            yield r

    @filter.command("猜日本地点", alias=JPGEO_ALIASES)
    async def guess_jpgeo(self, event: AstrMessageEvent):
        """开一道猜日本地点（日本街景，猜都道府县）"""
        async for r in self._start(event, "jpgeo"):
            yield r

    @filter.command("游戏排行榜")
    async def game_board(self, event: AstrMessageEvent):
        """猜游戏答对次数排行"""
        yield self._board(event, "game")

    @filter.command("动画排行榜")
    async def anime_board(self, event: AstrMessageEvent):
        """猜动画答对次数排行"""
        yield self._board(event, "anime")

    @filter.command("听歌猜番排行榜")
    async def anisong_board(self, event: AstrMessageEvent):
        """听歌猜番答对次数排行"""
        yield self._board(event, "anisong")

    @filter.command("宝可梦排行榜")
    async def pokemon_board(self, event: AstrMessageEvent):
        """猜宝可梦答对次数排行"""
        yield self._board(event, "pokemon")

    @filter.command("萝卜排行榜")
    async def mecha_board(self, event: AstrMessageEvent):
        """猜萝卜答对次数排行"""
        yield self._board(event, "mecha")

    @filter.command("Vtuber排行榜", alias=VTUBER_BOARD_ALIASES)
    async def vtuber_board(self, event: AstrMessageEvent):
        """猜Vtuber答对次数排行"""
        yield self._board(event, "vtuber")

    @filter.command("群友排行榜")
    async def member_board(self, event: AstrMessageEvent):
        """猜群友答对次数排行"""
        yield self._board(event, "member")

    @filter.command("地点排行榜")
    async def geo_board(self, event: AstrMessageEvent):
        """猜地点答对次数排行"""
        yield self._board(event, "geo")

    @filter.command("国家排行榜")
    async def country_board(self, event: AstrMessageEvent):
        """猜国家答对次数排行（暂时下线）"""
        yield self._board(event, "country")

    @filter.command("日本地点排行榜")
    async def jpgeo_board(self, event: AstrMessageEvent):
        """猜日本地点答对次数排行"""
        yield self._board(event, "jpgeo")

    @filter.command("排行榜", alias=ALL_BOARDS_ALIASES)
    async def all_boards(self, event: AstrMessageEvent):
        """管理员：马上发一张各题型的排行榜图，和整点播报的一样，下次整点从这次算新增"""
        gid = event.get_group_id()
        if not gid:
            return
        if not event.is_admin():
            yield event.plain_result(say("not_admin_board"))
            return
        if gid in self.paused:
            yield event.plain_result(say("paused"))
            return
        if not (card := self._all_boards(gid, say("board_now", time=time.strftime("%H:%M")))):
            yield event.plain_result(say("board_empty_all"))
            return
        self._mark_broadcast(gid)
        yield event.chain_result([Comp.Image.fromBase64(card)])

    @filter.regex(r"^(?:增加|加)\s*(\d+)\s*道?题$")
    async def add_quota(self, event: AstrMessageEvent):
        """管理员 @bot 发「增加 N 题」：本群额外放开 N 道题，每小时额度用完后再用"""
        gid = event.get_group_id()
        if not gid or not event.is_at_or_wake_command:
            return
        event.stop_event()
        if not event.is_admin():
            yield event.plain_result(say("not_admin_quota"))
            return
        n = int(re.search(r"\d+", event.message_str).group())
        if not 0 < n <= 100:
            yield event.plain_result(say("bonus_range"))
            return
        self.bonus[gid] = self.bonus.get(gid, 0) + n
        save_json(self.data_dir / "bonus.json", self.bonus)
        yield event.plain_result(say("bonus_added", n=n, left=self._quota_left(gid)))

    @filter.regex(r"^\s*(?:宵禁|取消宵禁|恢复宵禁|解禁\s*\d)")
    async def curfew_admin(self, event: AstrMessageEvent):
        """@bot 发「宵禁」看本群的宵禁时间；管理员还可以发「宵禁 00:00-07:00」改时间、「取消宵禁」、
        「宵禁解禁 N 小时」临时放开 N 小时（过了自动恢复）、「恢复宵禁」提前结束解禁。
        改时间直接改插件配置里的 group_curfews，WebUI 里看到的是同一份。"""
        gid = event.get_group_id()
        if not gid or not event.is_at_or_wake_command:
            return
        event.stop_event()
        text = re.sub(r"\s+", "", event.message_str)
        span = self._curfew_span(gid)
        if text == "宵禁":
            yield event.plain_result(say("curfew_show", start=span[0], end=span[1]) if span else say("curfew_none"))
            return
        if not event.is_admin():
            yield event.plain_result(say("not_admin_curfew"))
            return
        if m := re.fullmatch(r"(?:宵禁)?解禁(\d+)个?小时", text):
            hours = int(m[1])
            if not span:
                yield event.plain_result(say("curfew_none"))
            elif not 0 < hours <= 24:
                yield event.plain_result(say("curfew_lift_range"))
            else:
                self.curfew_lifts[gid] = time.time() + hours * HOUR
                save_json(self.data_dir / "curfew_lifts.json", self.curfew_lifts)
                until = time.strftime("%H:%M", time.localtime(self.curfew_lifts[gid]))
                yield event.plain_result(say("curfew_lifted", n=hours, until=until))
        elif text in ("恢复宵禁", "宵禁恢复"):
            lifted = self.curfew_lifts.pop(gid, 0) > time.time()
            save_json(self.data_dir / "curfew_lifts.json", self.curfew_lifts)
            yield event.plain_result(say("curfew_restored") if lifted and span else say("curfew_not_lifted"))
        elif text in ("取消宵禁", "宵禁关", "宵禁取消", "宵禁关闭"):
            self._set_curfew(gid, "")
            yield event.plain_result(say("curfew_off"))
        elif m := re.fullmatch(r"宵禁(\d{1,2})(?:[:：](\d{2}))?[-~～到](\d{1,2})(?:[:：](\d{2}))?", text):
            start, end = (int(m[1]), int(m[2] or 0)), (int(m[3]), int(m[4] or 0))
            if start == end or any(h > 24 or mm > 59 for h, mm in (start, end)):
                yield event.plain_result(say("curfew_usage"))
                return
            start, end = (f"{h % 24:02d}:{mm:02d}" for h, mm in (start, end))
            self._set_curfew(gid, f"{start}-{end}")
            yield event.plain_result(say("curfew_set", start=start, end=end))
        else:
            yield event.plain_result(say("curfew_usage"))

    def _set_curfew(self, gid: str, span: str) -> None:
        """改插件配置里本群的宵禁（span 空串是取消），存回配置文件；临时解禁一并作废。"""
        items = [x for x in self.config.get("group_curfews") or [] if not re.match(rf"\s*{gid}\s*[:：]", str(x))]
        self.config["group_curfews"] = [*items, f"{gid}:{span}"] if span else items
        self.config.save_config()
        if self.curfew_lifts.pop(gid, None):
            save_json(self.data_dir / "curfew_lifts.json", self.curfew_lifts)

    # 权限自己判断而不用 permission_type，好回自定义文案
    @filter.command("暂停")
    async def pause(self, event: AstrMessageEvent):
        """管理员：暂停本群猜题，直到「重新开始」"""
        gid = event.get_group_id()
        if not gid:
            return
        if not event.is_admin():
            yield event.plain_result(say("not_admin"))
            return
        self.paused.add(gid)
        save_json(self.data_dir / "paused.json", sorted(self.paused))
        text = say("pause")
        if (rnd := self.rounds.pop(gid, None)) and rnd.entry:
            rnd.timer.cancel()
            self._rounds_save()
            text += "\n" + say("pause_void", answer=rnd.entry["answer"])
        yield event.plain_result(text)

    @filter.command("重新开始")
    async def resume(self, event: AstrMessageEvent):
        """管理员：恢复本群猜题"""
        gid = event.get_group_id()
        if not gid:
            return
        if not event.is_admin():
            yield event.plain_result(say("not_admin"))
            return
        if gid not in self.paused:
            yield event.plain_result(say("not_paused"))
            return
        self.paused.discard(gid)
        save_json(self.data_dir / "paused.json", sorted(self.paused))
        yield event.plain_result(say("resume"))

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def on_group_message(self, event: AstrMessageEvent):
        """题目进行中处理「提示」和答案，不需要 @。另外记下群的 unified_msg_origin（整点播报用），
        @bot 说了不存在的「猜xx」回一句。"""
        gid = event.get_group_id()
        if not gid or event.get_sender_id() == event.get_self_id():
            return
        if self.origins.get(gid) != event.unified_msg_origin:
            self.origins[gid] = event.unified_msg_origin
            save_json(self.data_dir / "origins.json", self.origins)
        rnd = self.rounds.get(gid)
        text = event.message_str.strip()
        if not text or text.split()[0] in COMMANDS:
            return
        if not rnd or rnd.entry is None:
            if self._unknown_mode(event, text):
                yield event.plain_result(say("unknown_mode"))
                event.stop_event()
            return

        if text == "提示":
            now = time.monotonic()
            wait = rnd.last_hint + self.config.get("hint_cooldown_seconds", 5) - now
            if wait > 0:
                # 几个人同时喊：第一个揭开，其余的回一次「等一下」
                if rnd.hint_wait_sent != rnd.last_hint:
                    rnd.hint_wait_sent = rnd.last_hint
                    yield event.plain_result(say("hint_wait", wait=math.ceil(wait)))
            elif rnd.shown < self._steps(rnd):
                rnd.last_hint = now
                rnd.shown += 1
                for chain in self._question(rnd):
                    yield event.chain_result(chain)
            else:
                rnd.last_hint = now
                cat = rnd.category
                key = "hint_exhausted" if cat in GRID else "hint_exhausted_blind" if cat in BLIND else f"hint_exhausted_{cat}"
                yield event.plain_result(say(key if key in LINES else "hint_exhausted_clue", left=rnd.left()))
            event.stop_event()
            return

        uid, cat = event.get_sender_id(), rnd.category
        if cat == "member" and uid == rnd.key:
            return  # 被猜的群友本人说什么都不算
        wrong = rnd.wrong.setdefault(uid, set())
        limit = self._limit(cat)
        if limit and len(wrong) >= limit:
            return  # 次数用完了，说对了也不算
        key, item = self._match(rnd, event, text)
        # 地点类按「说的是哪个地方」判，「几内亚」不算「赤道几内亚」；萝卜同理；其他题型按答案的各种说法判
        if (key == rnd.key) if self.bank.places.get(cat) or cat in BY_KEY else is_correct(text, rnd.cands):
            chain = self._finish(gid, rnd, uid, event.get_sender_name())
            if chain:
                yield event.chain_result(chain)
            event.stop_event()
            return
        # 闲聊、说的不是任何一道题的答案，不算次数
        if key is None:
            if self._unknown_mode(event, text):
                yield event.plain_result(say("unknown_mode"))
                event.stop_event()
            return
        if seen := next((h for h in rnd.history if h["key"] == key), None):
            yield event.plain_result(self._repeat(rnd, seen))
            event.stop_event()
            return
        if key in wrong:
            return
        wrong.add(key)
        name = event.get_sender_name() or uid
        reply, card = self._feedback(rnd, key, item)
        if reply and limit and len(wrong) < limit:
            reply += "。" + say("guess_left", name=name, left=limit - len(wrong))
        if limit and len(wrong) >= limit:
            reply = "。".join(x for x in (reply, say("guess_used_up", name=name, limit=limit)) if x)
        if reply or card:
            yield event.chain_result([*([Comp.Plain(reply)] if reply else []), *card])
            event.stop_event()

    async def _start(self, event: AstrMessageEvent, cat: str):
        """开题。听歌猜番除了卡片还要另发一条语音，所以这里是生成器，可能产出两条消息。"""
        gid = event.get_group_id()
        if not gid:
            yield event.plain_result(say("group_only"))
            return
        if cat in OFFLINE:
            yield event.plain_result(say("offline"))
            return
        if gid in self.paused:
            yield event.plain_result(say("paused"))
            return
        if curfew := self._curfew(gid):
            yield event.plain_result(say("curfew", start=curfew[0], end=curfew[1]))
            return
        if cur := self.rounds.get(gid):
            if cur.entry is None:
                yield event.plain_result(say("preparing"))
            else:
                yield event.plain_result(say("busy", kind=MODES[cur.category], left=cur.left()))
            return
        if cat != "member" and not self.bank.ready(cat):
            yield event.plain_result(say("not_ready", kind=MODES[cat], status=self.bank.status[cat]))
            return
        if not (paid := self._quota_take(gid)):
            yield event.plain_result(self._quota_full(gid))
            return

        # 先占位再下载图片，防止下载期间被重复开题
        rnd = self.rounds[gid] = Round(cat, event.unified_msg_origin, paid=paid)
        try:
            found = await (self._prepare_member(rnd, event) if cat == "member" else self._prepare(rnd))
        except Exception as e:
            logger.error(f"[guess] picking {cat} failed: {e}")
            found = None
        if not found:
            self.rounds.pop(gid, None)
            self._quota_refund(gid, paid)  # 没出成题不算
            yield event.plain_result(say("pick_failed" if found is None else "member_none"))
            return
        if self.rounds.get(gid) is not rnd:  # 下载期间被暂停了
            yield event.plain_result(say("paused"))
            return

        # 盲猜要一个个推，两分钟不够；日本的市町村有一千七百多个、中国的地级行政区三百多个，单独设了更长的
        shared, default = ("blind_round_seconds", 300) if cat in BLIND else ("round_seconds", 120)
        seconds = self._setting(cat, "round_seconds", shared, default)
        rnd.cands = candidates(rnd.entry["names"])
        rnd.deadline = time.monotonic() + seconds
        rnd.timer = asyncio.create_task(self._expire(gid, rnd, seconds))
        self._rounds_save()
        for chain in self._question(rnd):
            yield event.chain_result(chain)

    async def _prepare(self, rnd: Round) -> bool:
        """抽题，备好题图、答案卡的图、提示（听歌猜番还有音频）。素材下载失败换一道，最多 3 次。"""
        cat = rnd.category
        for _ in range(3):
            entry = self.bank.pick(cat, self.recent[cat])
            try:
                await self._load(rnd, entry)
            except Exception as e:
                logger.warning(f"[guess] media of {entry['id']} failed: {e}")
                continue
            self.recent[cat].append(entry.get("group", entry["id"]))
            return True
        raise RuntimeError("连续 3 道题的素材都没下载下来")

    async def _prepare_member(self, rnd: Round, event: AstrMessageEvent) -> bool:
        """猜群友：现拉群成员，开题的人和 bot 不出；本轮出过的先不出，都出过了重来。
        挨个试头像，默认头像、纯色的跳过，试 members.TRIES 个都不行返回 False。"""
        bot = getattr(event, "bot", None)
        if bot is None:
            raise RuntimeError("猜群友只支持 OneBot（QQ）")
        gid = event.get_group_id()
        rows = await bot.call_action("get_group_member_list", group_id=int(gid))
        rows = [m for m in rows or [] if isinstance(m, dict) and m.get("user_id")]
        exclude = {str(event.get_self_id()), str(event.get_sender_id())}
        bag = self.member_bags.setdefault(gid, set())
        if not (pool := members.pick(rows, exclude | bag)):
            bag.clear()
            pool = members.pick(rows, exclude)
        session = self.bank.http()
        for m in pool[: members.TRIES]:
            uid = str(m["user_id"])
            if (face := await members.avatar(session, uid)) is None:
                continue
            name = members.display_name(m)
            joined = time.localtime(m["join_time"]) if m.get("join_time") else None
            rnd.hints = [["入群", f"{joined.tm_year} 年 {joined.tm_mon} 月"]] if joined else []
            rnd.avatar, rnd.plan, rnd.roster = face, members.plan(face), members.Roster(rows)
            rnd.reveal = cards.mosaic(face)
            rnd.entry = {"id": uid, "title": name, "answer": name, "names": [name], "hints": rnd.hints}
            bag.add(uid)
            return True
        return False

    async def _load(self, rnd: Round, entry: dict) -> None:
        cat = rnd.category
        rnd.hints, rnd.image, rnd.reveal = entry["hints"], None, None
        if b := BLIND.get(cat):
            # 不给图；「提示」只给表里比不出来的线索，一开始一条都没有
            rnd.hints, rnd.shown = b.hints(entry), 0
            if entry.get("images"):  # 盲猜地点没有图，答案卡用对比表，见 _finish
                rnd.reveal = cards.load(await self.bank.image(entry), contain=True)
            if b.peek:
                rnd.peek = cards.peek(rnd.reveal)
            rnd.image = b.table([], say(f"{cat}_empty"), rnd.peek)
        elif cat == "anisong":
            # OP 比 ED 好认，抽中的概率翻倍
            theme = random.choices(entry["themes"], [2 if t["type"] == "OP" else 1 for t in entry["themes"]])[0]
            rnd.pcm = await audio.decode(await self.bank.audio(theme["audio"]))
            label = f"{theme['type']}{theme['seq'] or ''}"
            rnd.hints = [["曲目", label], *entry["hints"]]
            rnd.song = " · ".join(x for x in (label, theme["song"], " / ".join(theme["artists"])) if x)
            rnd.reveal = cards.load(await self.bank.image(entry))
        elif entry.get("fit") == "panels":
            rnd.image = cards.load_panels(await self.bank.panels(entry))
        elif entry.get("fit") == "outline":
            rings, world, group = self.bank.outline(entry)
            rnd.image = cards.load_outline(rings)  # 出题不画邻国，答案卡再画
            rnd.reveal = cards.load_outline(rings, world, group)
        else:
            rnd.image = cards.load(await self.bank.image(entry), contain=entry.get("fit") == "contain")
            rnd.order = cards.reveal_order(rnd.image)
        rnd.entry = entry

    def _hourly_limit(self, gid: str) -> int:
        """本群每小时能开几道：group_hourly_limits 里写了「群号:题数」的按它，没写的用 hourly_limit。0 不限。"""
        for item in self.config.get("group_hourly_limits") or []:
            if (m := re.fullmatch(r"\s*(\d+)\s*[:：]\s*(\d+)\s*", str(item))) and m[1] == gid:
                return int(m[2])
        return self.config.get("hourly_limit", 10)

    def _curfew_span(self, gid: str) -> tuple[str, str] | None:
        """本群设的宵禁 (开始, 结束)，没设返回 None。group_curfews 里每行「群号:开始-结束」，分钟可以不写（0-7）。"""
        for item in self.config.get("group_curfews") or []:
            m = re.fullmatch(
                r"\s*(\d+)\s*[:：]\s*(\d{1,2})(?:[:：](\d{2}))?\s*[-~～]\s*(\d{1,2})(?:[:：](\d{2}))?\s*", str(item)
            )
            if m and m[1] == gid:
                return f"{int(m[2]) % 24:02d}:{int(m[3] or 0):02d}", f"{int(m[4]) % 24:02d}:{int(m[5] or 0):02d}"
        return None

    def _curfew(self, gid: str) -> tuple[str, str] | None:
        """本群现在在不在宵禁里：在就返回 (开始, 结束)，否则 None。开始晚于结束就是跨午夜（23:00-07:00）；
        管理员临时解禁（curfew_lifts）期间不算。"""
        if not (span := self._curfew_span(gid)) or self.curfew_lifts.get(gid, 0) > time.time():
            return None
        now = time.localtime()
        minute = now.tm_hour * 60 + now.tm_min
        start, end = (int(x[:2]) * 60 + int(x[3:]) for x in span)
        inside = start <= minute < end if start <= end else (minute >= start or minute < end)
        return span if inside else None

    @staticmethod
    def _hour_start(now: float) -> float:
        """now 所在整点的时间戳，按本地时间（AstrBot 的时区，Docker 里用 TZ 设）。"""
        t = time.localtime(now)
        return time.mktime((t.tm_year, t.tm_mon, t.tm_mday, t.tm_hour, 0, 0, 0, 0, -1))

    def _quota_window(self, gid: str) -> tuple[int, int, float]:
        """本群现在这段额度：(能开几道, 开了几道, 几秒后刷新)，能开 0 道是不限。
        整点刷新（默认）一个整点一段，能开每小时题数道；滚动刷新（rolling_quota）把一小时按题数等分，
        每段一道，这段没开的不留到下一段。开题时间存在 quota.json 里（只留这个整点以来的），重启不影响。"""
        now = time.time()
        limit, start, step = self._hourly_limit(gid), self._hour_start(now), HOUR
        self.quota[gid] = [t for t in self.quota.get(gid, []) if t >= start]
        if limit and self.config.get("rolling_quota", False):
            step = HOUR / limit
            start += (now - start) // step * step
            limit = 1
        return limit, sum(t >= start for t in self.quota[gid]), start + step - now

    def _quota_take(self, gid: str) -> str:
        """开题扣额度：先扣这段的额度，用完了扣管理员加的题，都没有返回空串。每小时额度为 0 不限。"""
        limit, used, _ = self._quota_window(gid)
        if not limit or used < limit:
            self.quota[gid].append(time.time())
            paid = "hourly"
        elif self.bonus.get(gid, 0) > 0:
            self.bonus[gid] -= 1
            paid = "bonus"
        else:
            return ""
        self._quota_save()
        return paid

    def _quota_refund(self, gid: str, paid: str) -> None:
        if paid == "hourly" and self.quota.get(gid):
            self.quota[gid].pop()
        elif paid == "bonus":
            self.bonus[gid] = self.bonus.get(gid, 0) + 1
        self._quota_save()

    def _quota_full(self, gid: str) -> str:
        """额度用完时回的话：整点刷新报离整点还有几分钟，滚动刷新报离下一段还有几分钟。"""
        limit, _, wait = self._quota_window(gid)
        wait = max(1, math.ceil(wait / 60))
        if self.config.get("rolling_quota", False):
            return say("quota_rolling", every=f"{60 / self._hourly_limit(gid):.3g}", wait=wait)
        return say("quota", limit=limit, wait=wait)

    def _quota_left(self, gid: str) -> int:
        """现在还能开几道（这段剩下的 + 管理员加的）。"""
        limit, used, _ = self._quota_window(gid)
        return max(0, limit - used) + self.bonus.get(gid, 0)

    def _rounds_save(self) -> None:
        """进行中的题只在内存里，另存一份答案和扣的额度，进程被重启打断时下次启动能收尾。"""
        save_json(self.data_dir / "rounds.json", {
            gid: {"umo": rnd.umo, "category": rnd.category, "answer": rnd.entry["answer"], "paid": rnd.paid}
            for gid, rnd in self.rounds.items() if rnd.entry
        })

    async def _announce_void(self, voided: dict) -> None:
        """刚启动时平台还没连上（NapCat 要过几秒才重连），发不出去就隔 5 秒再试，最多等 3 分钟。"""
        for _ in range(36):
            await asyncio.sleep(5)
            for gid, rec in list(voided.items()):
                text = say("restart_void", kind=MODES.get(rec["category"], ""), answer=rec["answer"])
                try:
                    if await self.context.send_message(rec["umo"], MessageChain([Comp.Plain(text)])):
                        del voided[gid]
                except Exception as e:
                    logger.debug(f"[guess] announcing voided round in {gid} failed, will retry: {e}")
            if not voided:
                return
        logger.warning(f"[guess] gave up announcing voided rounds in {sorted(voided)}")

    def _quota_save(self) -> None:
        save_json(self.data_dir / "quota.json", self.quota)
        save_json(self.data_dir / "bonus.json", self.bonus)

    def _steps(self, rnd: Round) -> int:
        """九宫格 9 格；听歌猜番按片段数；猜群友 4 段；街景、轮廓、盲猜宝可梦是整图直接给，「提示」只加文字线索。"""
        if rnd.category in GRID:
            return 9
        if rnd.category == "member":
            return len(members.STAGES)
        if rnd.category == "anisong":
            return len(audio.CLIPS)
        return len(rnd.hints)

    def _limit(self, cat: str) -> int:
        """每人每题最多猜几次，0 不限。盲猜萝卜、盲猜日本 / 中国地点要多猜几个才推得出来，单独设了。"""
        return self._setting(cat, "max_guesses", "max_guesses", 5)

    def _setting(self, cat: str, name: str, shared: str, default):
        """题型自己的设置 {cat}_{name}（_conf_schema.json 里写了才有），没有就用共用的 shared。"""
        return self.config.get(f"{cat}_{name}", self.config.get(shared, default))

    def _card(self, rnd: Round, image, status: str, hints: list) -> list:
        limit = self._limit(rnd.category)
        tries = say("tries", limit=limit) if limit else say("tries_free")
        note = f"card_note_{rnd.category}" if f"card_note_{rnd.category}" in LINES else "card_note"
        revealed = set(rnd.order[: rnd.shown]) if rnd.category in GRID else None
        card = cards.render(image, revealed, MODES[rnd.category], status, hints, say(note, tries=tries))
        return [Comp.Image.fromBase64(card)]

    def _question(self, rnd: Round) -> list[list]:
        """出题和提示发的消息：一张卡片，标题、进度和提示都画在图上；听歌猜番再加一条语音。"""
        image, status = rnd.image, f"{rnd.shown}/{self._steps(rnd)} · 剩 {rnd.left()} 秒"
        if rnd.category in BLIND:
            status = f"已猜 {len(rnd.history)} 次 · 剩 {rnd.left()} 秒"
        if rnd.category == "member":
            image = cards.mosaic(rnd.avatar, *rnd.plan[rnd.shown - 1])
            status = f"露出 {members.LABELS[rnd.shown - 1]} · 剩 {rnd.left()} 秒"
        if rnd.category == "anisong":
            sec = audio.CLIPS[rnd.shown - 1]
            image = cards.music(audio.CLIPS, rnd.shown, say("song_step", n=rnd.shown, sec=sec))
            voice = [Comp.Record.fromBase64(audio.clip(rnd.pcm, sec))]
            return [self._card(rnd, image, status, rnd.hints[: rnd.shown]), voice]
        return [self._card(rnd, image, status, rnd.hints[: rnd.shown])]

    def _match(self, rnd: Round, event: AstrMessageEvent, text: str) -> tuple[str | None, dict | None]:
        """这句话猜的是谁 / 哪个，同 Bank.match。猜群友按开题时的群成员认，@ 了谁就是猜谁。"""
        if rnd.category != "member":
            return self.bank.match(rnd.category, text, prefer=rnd.key)
        self_id = str(event.get_self_id())
        ats = [str(c.qq) for c in event.get_messages() if isinstance(c, Comp.At) and str(c.qq) not in (self_id, "all")]
        key = rnd.roster.find(AT_TEXT.sub(" ", text), ats, rnd.key)
        return key, ({} if key else None)

    def _unknown_mode(self, event: AstrMessageEvent, text: str) -> bool:
        """@bot 说了「猜xx」，但没有这个题型（有的话指令已经接走了）。暂停中的群不理。"""
        return (event.is_at_or_wake_command and bool(UNKNOWN_MODE.match(text))
                and event.get_group_id() not in self.paused)

    def _feedback(self, rnd: Round, key: str, item: dict) -> tuple[str, list]:
        """猜错时回什么，(文字, 卡片)：地点类报距离，猜国家再附一张按远近上色的世界地图；
        盲猜回对比表；其他题型不回。"""
        cat = rnd.category
        if (b := BLIND.get(cat)) and (attrs := item.get("attrs")):
            rnd.history.insert(0, {"key": key, "title": item["title"], "row": b.row(attrs, rnd.entry["attrs"])})
            rnd.image = b.table([(h["title"], h["row"]) for h in rnd.history], inset=rnd.peek)
            return say("blind_wrong", place=item["title"]), self._question(rnd)[0]
        spot = rnd.entry.get("spot")
        if "points" not in item or not spot:
            return "", []
        km = geo.distance(item["points"], spot)
        rnd.history.append({"key": key, "title": item["title"], "km": km})
        text = say("guess_far", place=item["title"], dist=say(f"dist_{cat}", km=km))
        return text, (self._globe(rnd) if cat == "country" else [])

    def _repeat(self, rnd: Round, seen: dict) -> str:
        if "km" in seen:
            return say("guess_repeat", place=seen["title"], dist=say(f"dist_{rnd.category}", km=seen["km"]))
        return say("guess_repeat_board", place=seen["title"])

    def _globe(self, rnd: Round) -> list:
        """猜国家猜错时的世界地图：猜过的国家按离答案的远近上色，下面列出来，近的在前。"""
        world, iso = self.bank.world_map()
        guesses = sorted(rnd.history, key=lambda h: h["km"])
        image = world.render({iso.get(h["key"]): cards.heat(h["km"]) for h in guesses}, inset=rnd.image)
        hints = [[h["title"], f"{h['km']} 公里", cards.heat(h["km"])] for h in guesses]
        return self._card(rnd, image, f"已猜 {len(guesses)} 个 · 剩 {rnd.left()} 秒", hints[:9])

    def _progress(self, rnd: Round) -> tuple[str, str]:
        """答对文案的档位和「用了多少」：九宫格按格数，听歌猜番按段数，盲猜按全群猜了几次，其他按线索数。"""
        if rnd.category in BLIND:
            n = len(rnd.history) + 1
            return ("correct_fast" if n <= 3 else "correct_slow" if n >= 10 else "correct"), f"{n} 次"
        n, steps = rnd.shown, self._steps(rnd)
        edge = max(1, round(steps * 2 / 9))  # 九宫格是 ≤ 2 格算快、≥ 7 格算慢，其他按比例
        tier = "correct_fast" if n <= edge else "correct_slow" if n > steps - edge else "correct"
        unit = {"anisong": "段", "member": "段提示"}.get(rnd.category, "格" if rnd.category in GRID else "条线索")
        return tier, f"{n} {unit}"

    def _finish(self, gid: str, rnd: Round, uid: str | None = None, name: str = "") -> list | None:
        if self.rounds.get(gid) is not rnd:
            return None  # 答对和超时撞在一起，已经结算过
        del self.rounds[gid]
        self._rounds_save()
        entry = rnd.entry
        answer = entry["answer"]
        if uid:
            rnd.timer.cancel()
            name = name or uid
            count = self._add_score(gid, rnd.category, uid, name)
            tier, progress = self._progress(rnd)
            text = say(tier, name=name, progress=progress) + "\n" + say("score", answer=answer, count=count)
        else:
            text = say(f"timeout_{rnd.category}") + "\n" + say("answer", answer=answer)
        # 答案卡：整图公开，标题写答案，右上角写原名；猜国家这时才画邻国，听歌猜番写上歌名；
        # 没有图的盲猜（盲猜地点）用对比表，最上面加一行答案自己的资料
        original = entry.get("original", "")
        hints = [["歌曲", rnd.song], *entry["hints"]] if rnd.song else entry["hints"]
        image = rnd.reveal or rnd.image
        if (b := BLIND.get(rnd.category)) and rnd.reveal is None:
            rows = [(h["title"], h["row"]) for h in rnd.history]
            image = b.table([(entry["title"], b.row(entry["attrs"], entry["attrs"])), *rows])
        card = cards.render(image, None, entry["title"], original if original != entry["title"] else "答案", hints)
        return [Comp.Plain(text), Comp.Image.fromBase64(card)]

    async def _expire(self, gid: str, rnd: Round, seconds: int):
        await asyncio.sleep(seconds)
        try:
            if chain := self._finish(gid, rnd):
                await self.context.send_message(rnd.umo, MessageChain(chain))
        except Exception as e:
            logger.error(f"[guess] sending timeout message failed: {e}")

    def _add_score(self, gid: str, cat: str, uid: str, name: str) -> int:
        rec = self.scores.setdefault(gid, {}).setdefault(cat, {}).setdefault(uid, {"name": name, "count": 0})
        rec["name"] = name
        rec["count"] += 1
        save_json(self.data_dir / "scores.json", self.scores)
        return rec["count"]

    def _board(self, event: AstrMessageEvent, cat: str):
        gid = event.get_group_id()
        if cat in OFFLINE:
            return event.plain_result(say("offline"))
        if gid in self.paused:
            return event.plain_result(say("paused"))
        board = self.scores.get(gid, {}).get(cat, {})
        if not board:
            return event.plain_result(say(f"board_empty_{cat}"))
        top = sorted(board.values(), key=lambda r: r["count"], reverse=True)[:10]
        lines = [say("board_title", kind=MODES[cat])]
        lines += [f"{i}. {r['name']}  {r['count']}" for i, r in enumerate(top, 1)]
        return event.plain_result("\n".join(lines))

    def _board_slot(self) -> str:
        """现在属于哪一段播报时间：按本地时间每 board_interval_hours 小时一段。"""
        hours = max(1, self.config.get("board_interval_hours", 1))
        now = time.localtime()
        return f"{now.tm_year}-{now.tm_yday}-{now.tm_hour // hours}"

    async def _broadcast_loop(self) -> None:
        """每分钟看一次：进了新的一段，就给每个有排行的群播一次；正在出题的群等这道题结束再播。"""
        while True:
            await asyncio.sleep(60)
            if not self.config.get("board_interval_hours", 1):
                continue
            if (slot := self._board_slot()) != self._slot:
                self._slot, self._due = slot, set(self.scores)
            for gid in list(self._due):
                if gid in self.rounds:
                    continue
                self._due.discard(gid)
                try:
                    await self._broadcast(gid)
                except Exception as e:
                    logger.error(f"[guess] broadcasting boards to {gid} failed: {e}")

    async def _broadcast(self, gid: str) -> None:
        """整点播报。没人新答对就不播（第一次播除外）。"""
        if gid in self.paused or not (origin := self.origins.get(gid)):
            return
        if self._snapshot(gid) == self.broadcasts.get(gid):
            return
        if not (card := self._all_boards(gid, say("board_broadcast", time=time.strftime("%H:00")))):
            return
        if await self.context.send_message(origin, MessageChain([Comp.Image.fromBase64(card)])):
            self._mark_broadcast(gid)

    def _snapshot(self, gid: str) -> dict:
        """本群现在各题型的答对次数 {题型: {用户: 次数}}。"""
        return {cat: {uid: rec["count"] for uid, rec in recs.items()} for cat, recs in self.scores.get(gid, {}).items()}

    def _mark_broadcast(self, gid: str) -> None:
        """记下这次播报时的答对次数，下次播报的「新增」从这里算。"""
        self.broadcasts[gid] = self._snapshot(gid)
        save_json(self.data_dir / "broadcasts.json", self.broadcasts)

    def _all_boards(self, gid: str, title: str) -> str | None:
        """一张图列出各题型的前几名，名字后面标上次播报以来新答对几次；一个人都没答对过返回 None。"""
        board = self.scores.get(gid, {})
        last = self.broadcasts.get(gid)
        sections = []
        for cat, name in MODES.items():
            if cat in OFFLINE or not board.get(cat):
                continue
            top = sorted(board[cat].items(), key=lambda kv: kv[1]["count"], reverse=True)[:BOARD_TOP]
            # 第一次播没有上次的数，不标新增
            before = (last or {}).get(cat, {})
            rows = [(rank, rec["name"], rec["count"], rec["count"] - before.get(uid, 0) if last else 0)
                    for rank, (uid, rec) in enumerate(top, 1)]
            sections.append((name.removeprefix("猜"), rows))
        return cards.leaderboard(title, sections) if sections else None


def _blind_commands(cat: str) -> None:
    """盲猜题型的开题和排行榜指令，按 BLIND 生成，加新题型不用改这里。AstrBot 按模块和函数名认处理函数，
    名字沿用以前手写的 guess_{cat}、{cat}_board，WebUI 里改过的指令设置不会丢。"""
    b = BLIND[cat]

    async def start(self, event: AstrMessageEvent):
        async for r in self._start(event, cat):
            yield r

    async def board(self, event: AstrMessageEvent):
        yield self._board(event, cat)

    for fn, name, doc, command, alias in (
        (start, f"guess_{cat}", f"开一道{b.command}（{b.desc}）", b.command, set(b.aliases) or None),
        (board, f"{cat}_board", f"{b.command}答对次数排行", BOARDS[cat], None),
    ):
        fn.__name__, fn.__qualname__, fn.__doc__ = name, f"GuessPlugin.{name}", doc
        setattr(GuessPlugin, name, filter.command(command, alias=alias)(fn))


for _cat in BLIND:
    _blind_commands(_cat)
