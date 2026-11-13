"""猜日语：手写文字题，借用盲猜的生命周期，不生成对比表。"""

import re

from .. import cards
from ..answer import norm
from ..jpword_data import QUESTIONS
from .base import Blind, entry

VERSION = "jpword-v1"  # 数据、答案说法、提示或选题规则改了就换
CLUES = ("考点", "释义", "例句", "首字")


async def build(bank) -> None:
    entries = []
    for key, main, question, names, topic, meaning, example, explanation in QUESTIONS:
        answers = {norm(n) for n in names}
        # 词义和读音题不能靠照抄原词答对，繁简转换后也不能相同。
        if key.startswith(("m", "r")):
            word = re.search(r"「([^」]+)」", question)
            if not word:
                raise ValueError(f"日语题 {key} 的题面缺少原词")
            if norm(word[1]) in answers:
                raise ValueError(f"日语题 {key} 的答案包含题面原词")
        item = entry("jpword", key, names[0], "", names[0], list(names),
                     [["考点", topic], ["释义", meaning], ["例句", example]], None, main)
        # 单字首字就是答案；短别名也可能藏在字数提示里，统一检查整条提示。
        item["hints"] = [h for h in item["hints"] if not (
            h[0] == "首字" and (min(map(len, answers)) <= 1 or any(n in norm("".join(h)) for n in answers))
        )]
        for hint in item["hints"]:
            if any(n in norm("".join(hint)) for n in answers):
                raise ValueError(f"日语题 {key} 的提示包含答案")
        item.update(question=question, explanation=explanation)
        entries.append(item)
    bank.commit("jpword", entries)


def quiz(item: dict, reveal: bool = False):
    return cards.quiz(item["question"], item["answer"] if reveal else "", item["explanation"] if reveal else "")


def row(guess: dict, answer: dict) -> list:
    return []


MODE = Blind(
    cat="jpword", command="猜日语", aliases=("猜日语语法", "猜日语单词"),
    desc="日语助词、词形、词义、读音和语法文字题", header="日语", columns=(), row=row,
    clues=CLUES, build=build, settings=(VERSION,), exact=True, quiz=quiz,
)
