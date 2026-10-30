"""盲猜的底层：题型的规格（Blind）、对比表的一格怎么算、一个地方一道题的条目怎么拼。各题型模块用这些拼出自己的
row 和题库，开题、判对错、出表、答案卡、题库定期重建由 main.py 和 bank.py 按 Blind 接上。"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .. import cards
from ..entries import finish_entry

if TYPE_CHECKING:
    from ..bank import Bank


@dataclass(frozen=True)
class Blind:
    """一个盲猜题型。cat 是题型键，这些都按它找：题库文件 bank_{cat}.json、分数、文案（core/lines.py 里的
    {cat}_empty、card_note_{cat}、timeout_{cat}、board_empty_{cat}）、配置项（{cat}_round_seconds、
    {cat}_max_guesses，_conf_schema.json 里没有就用共用的 blind_round_seconds、max_guesses）。"""

    cat: str
    command: str  # 开题指令，也是卡片标题；排行榜指令是「{command}排行榜」
    desc: str  # 指令说明括号里的那句，AstrBot 的指令列表里显示
    header: str  # 对比表第一列的表头
    columns: tuple[str, ...]  # 对比表其余各列
    clues: tuple[str, ...]  # 「提示」按这个顺序给条目 hints 里的线索，只放表里比不出来的
    row: Callable[[dict, dict], list]  # (猜的 attrs, 答案的 attrs) -> 对比表的一行，按 columns，每格见 num
    build: Callable[["Bank"], Awaitable[None]] | None = None  # 建题库，建好调 bank.commit；None 就借 base 的
    base: str = ""  # 借哪个题库里带 attrs 的条目（盲猜宝可梦借猜宝可梦的），不单独建
    settings: tuple = ()  # 影响题库内容的规则版本，变了就重建（Bank._stale）
    exact: bool = False  # 只认完整说出的名字（Bank.match）：能猜的上千个，按一段算会把闲聊当成猜
    peek: bool = False  # 对比表旁边放一格题图，整道题不变、不揭开（盲猜萝卜的资料列多，只看表太难）
    aliases: tuple[str, ...] = ()  # 开题指令的别名

    def table(self, rows: list, empty: str = "", inset=None):
        """对比表，rows 是 [(猜的名字, row 的结果), ...]，新猜的在上面。"""
        return cards.compare_table(self.header, self.columns, rows, empty, inset)

    def hints(self, entry: dict) -> list[list]:
        """这道题「提示」按顺序给的线索：按 clues 排，没有的跳过。"""
        found = {k: v for k, v in entry["hints"]}
        return [[k, found[k]] for k in self.clues if found.get(k)]


# 对比表的一格是 [显示的值, 结果, 箭头]。结果：hit（一致）、near（接近）、miss（不符）、none（没资料）；
# 箭头指答案更大（↑）还是更小（↓），方位格是从猜的地方看答案在哪个方向

def blank() -> list[str]:
    return ["—", "none", ""]


def num(x, y, text: str, close: bool = False) -> list[str]:
    return [text, "hit", ""] if x == y else [text, "near" if close else "miss", "↑" if y > x else "↓"]


def ratio(x: float, y: float, text: str) -> list[str]:
    """人口、面积跨好几个数量级，按倍数比：大的不到小的 1.5 倍算接近。"""
    if not x or not y:
        return blank()
    return num(x, y, text, max(x, y) <= min(x, y) * 1.5)


def short(n: float, unit: str = "") -> str:
    """表格里放得下的数，两位有效数字：14亿、1.3亿、5100万、1.1万、800。"""
    n = float(f"{n:.2g}")
    for size, name in ((1e8, "亿"), (1e4, "万")):
        if n >= size:
            return f"{n / size:g}{name}{unit}"
    return f"{n:g}{unit}"


def same(mine, theirs) -> list[str]:
    """一样绿、不一样红；猜的这个没资料写「—」。"""
    if not mine:
        return blank()
    return [mine, "hit" if mine == theirs else "miss", ""]


def tiered(mine: str, theirs: str, close: bool) -> list[str]:
    """分层的归属（地区、省份……）：一样绿；不一样但 close（同一个大洲、大区）黄；都不沾红。没资料写「—」。"""
    if not mine:
        return blank()
    return [mine, "hit", ""] if mine == theirs else [mine, "near" if close else "miss", ""]


def yes_no(mine: bool | None, theirs: bool | None) -> list[str]:
    """是不是首都、县厅、省会：一样绿、不一样红；None 是不适用（直辖市没有省会不省会），写「—」。"""
    if mine is None:
        return blank()
    return ["是" if mine else "否", "hit" if mine == theirs else "miss", ""]


# 地点类的方位格：答案自己那一行（答案卡上）、接壤、相距多远（near_km 以内标黄）
def here() -> list[str]:
    return ["就是这里", "hit", ""]


def touching(way: str) -> list[str]:
    return ["接壤", "near", way]


def away(km: int, near_km: int, way: str) -> list[str]:
    return [f"{km} 公里", "near" if km <= near_km else "miss", way]


def size(guess: dict, answer: dict) -> list[list[str]]:
    """地点类的人口、面积两格，箭头指答案更多（↑）还是更少（↓）。"""
    return [
        ratio(guess["pop"], answer["pop"], short(guess["pop"])),
        ratio(guess["area"], answer["area"], short(guess["area"], "km²")),
    ]


def entry(prefix: str, key: str, title: str, original: str, answer: str, names: list[str], hints: list,
          attrs: dict, main: bool) -> dict:
    """一个地方一道题、不要图的条目。id 是「{prefix}-{key}」，group 是 key（Bank.match 认的键，同 places 的键）；
    hints 是 [[名, 值], ...]，main 是不是大众题。"""
    return finish_entry({
        "id": f"{prefix}-{key}",
        "group": key,
        "title": title,
        "original": original,
        "answer": answer,
        "names": names,
        "tier": "main" if main else "niche",
        "hints": hints,
        "attrs": attrs,
    })
