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

from . import grid
from .answer import candidates, is_correct
from .bank import Bank, load_json, save_json
from .lines import LINES, say

LABELS = {"game": "游戏", "anime": "动画", "pokemon": "宝可梦", "geo": "地点", "country": "国家", "jpgeo": "日本地点"}
GEO_ALIASES = {"猜街景"}
JPGEO_ALIASES = {"猜日本街景"}
COMMANDS = (
    {f"猜{k}" for k in LABELS.values()} | {f"{k}排行榜" for k in LABELS.values()}
    | {"暂停", "重新开始"} | GEO_ALIASES | JPGEO_ALIASES
)
HOUR = 3600


@dataclass
class Round:
    category: str
    umo: str
    entry: dict | None = None
    image: object = None
    cands: set = field(default_factory=set)
    order: list = field(default_factory=lambda: random.sample(range(9), 9))
    shown: int = 1
    deadline: float = 0.0
    last_hint: float = 0.0
    hint_wait_sent: float = -1.0  # 已对哪次揭开后的冷却发过「等一下」，每个冷却窗口只回一次
    timer: asyncio.Task | None = None
    paid: str = ""  # 这道题扣的是每小时额度（hourly）还是管理员加的题（bonus），没出成时退回去

    def left(self) -> int:
        return max(0, round(self.deadline - time.monotonic()))


class GuessPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.data_dir = StarTools.get_data_dir("astrbot_plugin_guessless")
        self.bank = Bank(self.data_dir, config)
        self.rounds: dict[str, Round] = {}  # 群号 -> 进行中的题目，一个群同时只有一道
        self.recent = {cat: deque(maxlen=100) for cat in LABELS}
        self.scores = load_json(self.data_dir / "scores.json") or {}
        self.paused: set[str] = set(load_json(self.data_dir / "paused.json") or [])
        # 群号 -> 最近一小时内开题的时间戳，所有猜题共用一个额度；存盘是为了重启不清零
        self.quota: dict[str, list[float]] = load_json(self.data_dir / "quota.json") or {}
        # 群号 -> 管理员额外放开的题数，每小时额度用完后再扣，不过期
        self.bonus: dict[str, int] = load_json(self.data_dir / "bonus.json") or {}

    async def initialize(self):
        self.bank.start()

    async def terminate(self):
        for rnd in self.rounds.values():
            if rnd.timer:
                rnd.timer.cancel()
        await self.bank.close()

    @filter.command("猜游戏")
    async def guess_game(self, event: AstrMessageEvent):
        """开一道猜游戏（Metacritic 评分榜）"""
        yield await self._start(event, "game")

    @filter.command("猜动画")
    async def guess_anime(self, event: AstrMessageEvent):
        """开一道猜动画（Bangumi 热门）"""
        yield await self._start(event, "anime")

    @filter.command("猜宝可梦")
    async def guess_pokemon(self, event: AstrMessageEvent):
        """开一道猜宝可梦（全国图鉴）"""
        yield await self._start(event, "pokemon")

    @filter.command("猜地点", alias=GEO_ALIASES)
    async def guess_geo(self, event: AstrMessageEvent):
        """开一道猜地点（Google 街景，猜国家）"""
        yield await self._start(event, "geo")

    @filter.command("猜国家")
    async def guess_country(self, event: AstrMessageEvent):
        """开一道猜国家（看国家轮廓）"""
        yield await self._start(event, "country")

    @filter.command("猜日本地点", alias=JPGEO_ALIASES)
    async def guess_jpgeo(self, event: AstrMessageEvent):
        """开一道猜日本地点（日本街景，猜都道府县）"""
        yield await self._start(event, "jpgeo")

    @filter.command("游戏排行榜")
    async def game_board(self, event: AstrMessageEvent):
        """猜游戏答对次数排行"""
        yield self._board(event, "game")

    @filter.command("动画排行榜")
    async def anime_board(self, event: AstrMessageEvent):
        """猜动画答对次数排行"""
        yield self._board(event, "anime")

    @filter.command("宝可梦排行榜")
    async def pokemon_board(self, event: AstrMessageEvent):
        """猜宝可梦答对次数排行"""
        yield self._board(event, "pokemon")

    @filter.command("地点排行榜")
    async def geo_board(self, event: AstrMessageEvent):
        """猜地点答对次数排行"""
        yield self._board(event, "geo")

    @filter.command("国家排行榜")
    async def country_board(self, event: AstrMessageEvent):
        """猜国家答对次数排行"""
        yield self._board(event, "country")

    @filter.command("日本地点排行榜")
    async def jpgeo_board(self, event: AstrMessageEvent):
        """猜日本地点答对次数排行"""
        yield self._board(event, "jpgeo")

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
        """题目进行中处理「提示」和答案，不需要 @。"""
        gid = event.get_group_id()
        rnd = self.rounds.get(gid)
        if not rnd or rnd.entry is None or event.get_sender_id() == event.get_self_id():
            return
        text = event.message_str.strip()
        if not text or text in COMMANDS:
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
                yield event.chain_result(self._question(rnd))
            else:
                rnd.last_hint = now
                key = "hint_exhausted" if self._grid(rnd) else "hint_exhausted_clue"
                yield event.plain_result(say(key, left=rnd.left()))
            event.stop_event()
            return

        if is_correct(text, rnd.cands):
            chain = self._finish(gid, rnd, event.get_sender_id(), event.get_sender_name())
            if chain:
                yield event.chain_result(chain)
            event.stop_event()

    async def _start(self, event: AstrMessageEvent, cat: str):
        gid = event.get_group_id()
        if not gid:
            return event.plain_result(say("group_only"))
        if gid in self.paused:
            return event.plain_result(say("paused"))
        if cur := self.rounds.get(gid):
            if cur.entry is None:
                return event.plain_result(say("preparing"))
            return event.plain_result(say("busy", kind=LABELS[cur.category], left=cur.left()))
        if not self.bank.ready(cat):
            return event.plain_result(say("not_ready", kind=LABELS[cat], status=self.bank.status[cat]))
        if not (paid := self._quota_take(gid)):
            return event.plain_result(say("quota", limit=self.config.get("hourly_limit", 10), wait=self._quota_wait(gid)))

        # 先占位再下载图片，防止下载期间被重复开题
        rnd = self.rounds[gid] = Round(cat, event.unified_msg_origin, paid=paid)
        try:
            rnd.entry, rnd.image = await self._pick(cat)
        except Exception as e:
            self.rounds.pop(gid, None)
            self._quota_refund(gid, paid)  # 没出成题不算
            logger.error(f"[guess] picking {cat} failed: {e}")
            return event.plain_result(say("pick_failed"))
        if self.rounds.get(gid) is not rnd:  # 下载期间被暂停了
            return event.plain_result(say("paused"))

        seconds = self.config.get("round_seconds", 120)
        rnd.order = grid.reveal_order(rnd.image)
        rnd.cands = candidates(rnd.entry["names"])
        rnd.deadline = time.monotonic() + seconds
        rnd.timer = asyncio.create_task(self._expire(gid, rnd, seconds))
        return event.chain_result(self._question(rnd))

    async def _pick(self, cat: str):
        for _ in range(3):
            entry = self.bank.pick(cat, self.recent[cat])
            try:
                if entry.get("fit") == "panels":
                    image = grid.load_panels(await self.bank.panels(entry))
                elif entry.get("fit") == "outline":
                    image = grid.load_outline(self.bank.outline(entry))
                else:
                    image = grid.load(await self.bank.image(entry), contain=entry.get("fit") == "contain")
            except Exception as e:
                logger.warning(f"[guess] image of {entry['id']} failed: {e}")
                continue
            self.recent[cat].append(entry.get("group", entry["id"]))
            return entry, image
        raise RuntimeError("连续 3 张图片下载失败")

    def _quota_used(self, gid: str) -> list[float]:
        now = time.time()
        used = self.quota[gid] = [t for t in self.quota.get(gid, []) if now - t < HOUR]
        return used

    def _quota_take(self, gid: str) -> str:
        """开题扣额度：先扣每小时额度，用完了扣管理员加的题，都没有返回空串。hourly_limit 为 0 不限。"""
        limit = self.config.get("hourly_limit", 10)
        used = self._quota_used(gid)
        if not limit or len(used) < limit:
            used.append(time.time())
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

    def _quota_wait(self, gid: str) -> int:
        """每小时额度最早空出一道还要几分钟。"""
        limit = self.config.get("hourly_limit", 10)
        used = self._quota_used(gid)
        if not limit or len(used) < limit:
            return 0
        return max(1, math.ceil((used[-limit] + HOUR - time.time()) / 60))

    def _quota_left(self, gid: str) -> int:
        """现在还能开几道（这小时剩下的 + 管理员加的）。"""
        limit = self.config.get("hourly_limit", 10)
        return max(0, limit - len(self._quota_used(gid))) + self.bonus.get(gid, 0)

    def _quota_save(self) -> None:
        save_json(self.data_dir / "quota.json", self.quota)
        save_json(self.data_dir / "bonus.json", self.bonus)

    @staticmethod
    def _grid(rnd: Round) -> bool:
        """九宫格逐格揭开的玩法；街景、轮廓是整图直接给，「提示」只加文字线索。"""
        return rnd.entry.get("fit") not in ("panels", "outline")

    def _steps(self, rnd: Round) -> int:
        return 9 if self._grid(rnd) else len(rnd.entry["hints"])

    def _question(self, rnd: Round) -> list:
        """出题和提示只发一张卡片，标题、进度和提示都画在图上。"""
        image = grid.render(
            rnd.image,
            set(rnd.order[: rnd.shown]),
            f"猜{LABELS[rnd.category]}",
            f"{rnd.shown}/{self._steps(rnd)} · 剩 {rnd.left()} 秒",
            rnd.entry["hints"][: rnd.shown],
            say(f"card_note_{rnd.category}" if f"card_note_{rnd.category}" in LINES else "card_note"),
        )
        return [Comp.Image.fromBase64(image)]

    def _finish(self, gid: str, rnd: Round, uid: str | None = None, name: str = "") -> list | None:
        if self.rounds.get(gid) is not rnd:
            return None  # 答对和超时撞在一起，已经结算过
        del self.rounds[gid]
        entry = rnd.entry
        answer = entry["answer"]
        if uid:
            rnd.timer.cancel()
            name = name or uid
            count = self._add_score(gid, rnd.category, uid, name)
            tier = "correct_fast" if rnd.shown <= 2 else "correct_slow" if rnd.shown >= 7 else "correct"
            progress = f"{rnd.shown} 格" if self._grid(rnd) else f"{rnd.shown} 条线索"
            text = say(tier, name=name, progress=progress) + "\n" + say("score", answer=answer, count=count)
        else:
            text = say(f"timeout_{rnd.category}") + "\n" + say("answer", answer=answer)
        # 答案卡：整图公开，标题写答案，右上角写原名
        original = entry.get("original", "")
        card = grid.render(
            rnd.image, None, entry["title"], original if original != entry["title"] else "答案", entry["hints"]
        )
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
        if gid in self.paused:
            return event.plain_result(say("paused"))
        board = self.scores.get(gid, {}).get(cat, {})
        if not board:
            return event.plain_result(say(f"board_empty_{cat}"))
        top = sorted(board.values(), key=lambda r: r["count"], reverse=True)[:10]
        lines = [say("board_title", kind=LABELS[cat])]
        lines += [f"{i}. {r['name']}  {r['count']}" for i, r in enumerate(top, 1)]
        return event.plain_result("\n".join(lines))
