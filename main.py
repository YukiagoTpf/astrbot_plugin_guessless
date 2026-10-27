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

from .core import audio, cards, geo
from .core.answer import candidates, is_correct
from .core.bank import Bank
from .core.entries import POKEDLE_CLUES, pokedle_row
from .core.lines import LINES, say
from .core.store import load_json, save_json

# 题型 -> 开题指令，也是卡片标题；排行榜指令去掉开头的「猜」
MODES = {
    "game": "猜游戏", "anime": "猜动画", "anisong": "听歌猜番", "pokemon": "猜宝可梦", "pokedle": "盲猜宝可梦",
    "geo": "猜地点", "country": "猜国家", "jpgeo": "猜日本地点",
}
BOARDS = {cat: name.removeprefix("猜") + "排行榜" for cat, name in MODES.items()}
GRID = {"game", "anime", "pokemon"}  # 九宫格逐格揭开的题型
GEO_ALIASES = {"猜街景"}
JPGEO_ALIASES = {"猜日本街景"}
COMMANDS = set(MODES.values()) | set(BOARDS.values()) | {"暂停", "重新开始"} | GEO_ALIASES | JPGEO_ALIASES
HOUR = 3600


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
    wrong: dict = field(default_factory=dict)  # 用户 -> 猜错过的答案（Bank.match 的键），每人最多 max_guesses 个
    # 有反馈的题型（地点类、盲猜宝可梦）本题猜错过的：{"key", "title", "km" 或 "row"}，再猜一次不扣次数
    history: list = field(default_factory=list)
    pcm: bytes = b""  # 听歌猜番：解码好的音频，见 audio.decode
    song: str = ""  # 听歌猜番：答案卡上写的歌名和歌手

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
        self._announcer: asyncio.Task | None = None

    async def initialize(self):
        self.bank.start()
        # 上次退出时还没结束的题（重启、停机时恰好有人在猜）：退额度，连上平台后公布答案
        if voided := load_json(self.data_dir / "rounds.json"):
            for gid, rec in voided.items():
                self._quota_refund(gid, rec.get("paid", ""))
            save_json(self.data_dir / "rounds.json", {})
            self._announcer = asyncio.create_task(self._announce_void(voided))

    async def terminate(self):
        for rnd in self.rounds.values():
            if rnd.timer:
                rnd.timer.cancel()
        if self._announcer:
            self._announcer.cancel()
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

    @filter.command("盲猜宝可梦")
    async def guess_pokedle(self, event: AstrMessageEvent):
        """开一道盲猜宝可梦（不给图，猜一只看属性对比）"""
        async for r in self._start(event, "pokedle"):
            yield r

    @filter.command("猜地点", alias=GEO_ALIASES)
    async def guess_geo(self, event: AstrMessageEvent):
        """开一道猜地点（Google 街景，猜国家）"""
        async for r in self._start(event, "geo"):
            yield r

    @filter.command("猜国家")
    async def guess_country(self, event: AstrMessageEvent):
        """开一道猜国家（看国家轮廓）"""
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

    @filter.command("盲猜宝可梦排行榜")
    async def pokedle_board(self, event: AstrMessageEvent):
        """盲猜宝可梦答对次数排行"""
        yield self._board(event, "pokedle")

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
                for chain in self._question(rnd):
                    yield event.chain_result(chain)
            else:
                rnd.last_hint = now
                cat = rnd.category
                key = "hint_exhausted" if cat in GRID else f"hint_exhausted_{cat}"
                yield event.plain_result(say(key if key in LINES else "hint_exhausted_clue", left=rnd.left()))
            event.stop_event()
            return

        uid, cat = event.get_sender_id(), rnd.category
        wrong = rnd.wrong.setdefault(uid, set())
        limit = self.config.get("max_guesses", 5)
        if limit and len(wrong) >= limit:
            return  # 次数用完了，说对了也不算
        key, item = self.bank.match(cat, text, prefer=rnd.key)
        # 地点类按「说的是哪个地方」判，「几内亚」不算「赤道几内亚」；其他题型按答案的各种说法判
        if (key == rnd.key) if self.bank.places[cat] else is_correct(text, rnd.cands):
            chain = self._finish(gid, rnd, uid, event.get_sender_name())
            if chain:
                yield event.chain_result(chain)
            event.stop_event()
            return
        # 闲聊、说的不是任何一道题的答案，不算次数
        if key is None:
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
        if gid in self.paused:
            yield event.plain_result(say("paused"))
            return
        if cur := self.rounds.get(gid):
            if cur.entry is None:
                yield event.plain_result(say("preparing"))
            else:
                yield event.plain_result(say("busy", kind=MODES[cur.category], left=cur.left()))
            return
        if not self.bank.ready(cat):
            yield event.plain_result(say("not_ready", kind=MODES[cat], status=self.bank.status[cat]))
            return
        if not (paid := self._quota_take(gid)):
            yield event.plain_result(say("quota", limit=self.config.get("hourly_limit", 10), wait=self._quota_wait(gid)))
            return

        # 先占位再下载图片，防止下载期间被重复开题
        rnd = self.rounds[gid] = Round(cat, event.unified_msg_origin, paid=paid)
        try:
            await self._prepare(rnd)
        except Exception as e:
            self.rounds.pop(gid, None)
            self._quota_refund(gid, paid)  # 没出成题不算
            logger.error(f"[guess] picking {cat} failed: {e}")
            yield event.plain_result(say("pick_failed"))
            return
        if self.rounds.get(gid) is not rnd:  # 下载期间被暂停了
            yield event.plain_result(say("paused"))
            return

        seconds = self.config.get("round_seconds", 120)
        rnd.cands = candidates(rnd.entry["names"])
        rnd.deadline = time.monotonic() + seconds
        rnd.timer = asyncio.create_task(self._expire(gid, rnd, seconds))
        self._rounds_save()
        for chain in self._question(rnd):
            yield event.chain_result(chain)

    async def _prepare(self, rnd: Round) -> None:
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
            return
        raise RuntimeError("连续 3 道题的素材都没下载下来")

    async def _load(self, rnd: Round, entry: dict) -> None:
        cat = rnd.category
        rnd.hints, rnd.image, rnd.reveal = entry["hints"], None, None
        if cat == "pokedle":
            # 不给图；「提示」只给表里比不出来的线索，一开始一条都没有
            clues = {k: v for k, v in entry["hints"]}
            rnd.hints = [[k, clues[k]] for k in POKEDLE_CLUES if clues.get(k)]
            rnd.shown = 0
            rnd.image = cards.pokedle_table([], say("pokedle_empty"))
            rnd.reveal = cards.load(await self.bank.image(entry), contain=True)
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
        """九宫格 9 格；听歌猜番按片段数；街景、轮廓、盲猜宝可梦是整图直接给，「提示」只加文字线索。"""
        if rnd.category in GRID:
            return 9
        if rnd.category == "anisong":
            return len(audio.CLIPS)
        return len(rnd.hints)

    def _card(self, rnd: Round, image, status: str, hints: list) -> list:
        limit = self.config.get("max_guesses", 5)
        tries = say("tries", limit=limit) if limit else say("tries_free")
        note = f"card_note_{rnd.category}" if f"card_note_{rnd.category}" in LINES else "card_note"
        revealed = set(rnd.order[: rnd.shown]) if rnd.category in GRID else None
        card = cards.render(image, revealed, MODES[rnd.category], status, hints, say(note, tries=tries))
        return [Comp.Image.fromBase64(card)]

    def _question(self, rnd: Round) -> list[list]:
        """出题和提示发的消息：一张卡片，标题、进度和提示都画在图上；听歌猜番再加一条语音。"""
        image, status = rnd.image, f"{rnd.shown}/{self._steps(rnd)} · 剩 {rnd.left()} 秒"
        if rnd.category == "pokedle":
            status = f"已猜 {len(rnd.history)} 次 · 剩 {rnd.left()} 秒"
        if rnd.category == "anisong":
            sec = audio.CLIPS[rnd.shown - 1]
            image = cards.music(audio.CLIPS, rnd.shown, say("song_step", n=rnd.shown, sec=sec))
            voice = [Comp.Record.fromBase64(audio.clip(rnd.pcm, sec))]
            return [self._card(rnd, image, status, rnd.hints[: rnd.shown]), voice]
        return [self._card(rnd, image, status, rnd.hints[: rnd.shown])]

    def _feedback(self, rnd: Round, key: str, item: dict) -> tuple[str, list]:
        """猜错时回什么，(文字, 卡片)：地点类报距离，猜国家再附一张按远近上色的世界地图；
        盲猜宝可梦回对比表；其他题型不回。"""
        cat = rnd.category
        if cat == "pokedle" and (attrs := item.get("attrs")):
            rnd.history.insert(0, {"key": key, "title": item["title"], "row": pokedle_row(attrs, rnd.entry["attrs"])})
            rnd.image = cards.pokedle_table([(h["title"], h["row"]) for h in rnd.history])
            return say("pokedle_wrong", place=item["title"]), self._question(rnd)[0]
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
        """答对文案的档位和「用了多少」：九宫格按格数，听歌猜番按段数，盲猜宝可梦按全群猜了几次，其他按线索数。"""
        if rnd.category == "pokedle":
            n = len(rnd.history) + 1
            return ("correct_fast" if n <= 3 else "correct_slow" if n >= 10 else "correct"), f"{n} 次"
        n, steps = rnd.shown, self._steps(rnd)
        edge = max(1, round(steps * 2 / 9))  # 九宫格是 ≤ 2 格算快、≥ 7 格算慢，其他按比例
        tier = "correct_fast" if n <= edge else "correct_slow" if n > steps - edge else "correct"
        unit = "格" if rnd.category in GRID else "段" if rnd.category == "anisong" else "条线索"
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
        # 答案卡：整图公开，标题写答案，右上角写原名；猜国家这时才画邻国，听歌猜番写上歌名
        original = entry.get("original", "")
        hints = [["歌曲", rnd.song], *entry["hints"]] if rnd.song else entry["hints"]
        card = cards.render(
            rnd.reveal or rnd.image, None, entry["title"], original if original != entry["title"] else "答案", hints
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
        lines = [say("board_title", kind=MODES[cat])]
        lines += [f"{i}. {r['name']}  {r['count']}" for i, r in enumerate(top, 1)]
        return event.plain_result("\n".join(lines))
