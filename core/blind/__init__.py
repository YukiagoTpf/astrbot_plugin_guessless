"""盲猜系列：不给图，群友每猜一个，回一行它和答案的对比，「提示」只加表里比不出来的线索。

一个题型一个模块，模块里定义 MODE = Blind(...)（见 base.Blind），再加进下面的 BLIND。开题和排行榜指令、出题、
判对错、对比表、答案卡、题库定期重建都按 Blind 接上，模块只管对比哪几列、一行怎么算（row）、题库怎么建（build）。
另外要在 core/lines.py 里写四句文案（{cat}_empty、card_note_{cat}、timeout_{cat}、board_empty_{cat}），
要单独的每题时长、每人次数就在 _conf_schema.json 加 {cat}_round_seconds、{cat}_max_guesses。
"""

from . import cngeodle, geodle, jpgeodle, mechadle, pokedle
from .base import Blind

# 题型 -> Blind，顺序就是指令列表和排行榜里的顺序，题库也按这个顺序建
BLIND: dict[str, Blind] = {m.MODE.cat: m.MODE for m in (pokedle, mechadle, geodle, jpgeodle, cngeodle)}
