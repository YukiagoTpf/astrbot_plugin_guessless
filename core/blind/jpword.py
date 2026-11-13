"""猜日语：N3–N1 文字题，借用盲猜的生命周期，不生成对比表。"""

import json
import re
from pathlib import Path

from .. import cards
from ..answer import norm
from ..jpword_data import QUESTIONS
from .base import Blind, entry

VERSION = "jpword-v2"  # 数据、答案说法、提示或选题规则改了就换
CLUES = ("考点", "释义", "例句", "首字")


async def build(bank) -> None:
    generated = json.loads((Path(__file__).resolve().parents[1] / "jpword_gen.json").read_text(encoding="utf-8"))
    if (not isinstance(generated, dict) or not generated.get("source") or not generated.get("license")
            or not isinstance(generated.get("questions"), list)):
        raise ValueError("日语生成题库缺少来源、许可或题目列表")
    entries = []
    keys, questions, signatures = set(), set(), set()
    for raw in [*QUESTIONS, *generated["questions"]]:
        if not isinstance(raw, (list, tuple)) or len(raw) != 8:
            raise ValueError("日语题字段应有八项")
        key, main, question, names, topic, meaning, example, explanation = raw
        if not isinstance(key, str) or not re.fullmatch(r"[prfghmc][\wー-]+", key):
            raise ValueError(f"日语题号不合法：{key}")
        if key in keys:
            raise ValueError(f"日语题号重复：{key}")
        keys.add(key)
        if not isinstance(main, bool) or not all(isinstance(s, str) for s in (question, topic, meaning, example, explanation)):
            raise ValueError(f"日语题 {key} 的字段类型不合法")
        if not question.strip() or not explanation.strip() or not isinstance(names, (list, tuple)) or not names:
            raise ValueError(f"日语题 {key} 缺少题面、答案或解析")
        for name in names:
            japanese = isinstance(name, str) and bool(re.fullmatch(r"[ぁ-ゖァ-ヺ一-鿿々ー]+", name))
            legacy = re.fullmatch(r"r(?:0[1-9]|1[0-6])", key) and isinstance(name, str) and re.fullmatch(r"[A-Za-zāīūēō]+", name)
            if not japanese and not legacy:
                raise ValueError(f"日语题 {key} 的答案字符不合法")
            if key.startswith("r") and not legacy and not re.fullmatch(r"[ぁ-ゖァ-ヺー]+", name):
                raise ValueError(f"日语题 {key} 的读音答案必须是假名")
            if key.startswith("m") and not re.fullmatch(r"[一-鿿々]+", name):
                raise ValueError(f"日语题 {key} 的词义答案必须是中文")
            if key.startswith(("f", "h")) and not re.search(r"[ぁ-ゖァ-ヺ]", name):
                raise ValueError(f"日语题 {key} 的变形或敬语答案必须包含完整词尾")
            if len(name) == 1 and re.fullmatch(r"[ぁ-ゖァ-ヺ]", name) and not key.startswith("p"):
                raise ValueError(f"日语题 {key} 的单假名答案只能用于助词题")
        answers = {norm(n) for n in names}
        if not all(answers):
            raise ValueError(f"日语题 {key} 的答案归一化后为空")
        words = re.findall(r'''「([^」]+)」|"([^"]+)"|“([^”]+)”|'([^']+)' ''', question, re.X)
        originals = tuple(norm(next(filter(None, word))) for word in words)
        # 词义、读音和活用题不能靠照抄原词答对，繁简转换后也不能相同。
        if key.startswith(("m", "r", "f", "c")):
            if key.startswith(("m", "r", "f")) and not originals:
                raise ValueError(f"日语题 {key} 的题面缺少原词")
            if any(word in answers for word in originals):
                raise ValueError(f"日语题 {key} 的答案包含题面原词")
        # 选择题必须展示候选；只允许答案出现在明确标出的选项行中。
        body = "\n".join(line for line in question.splitlines() if not (key.startswith("g") and line.startswith("选项：")))
        if any(len(n) >= 2 and n in norm(body) for n in answers):
            raise ValueError(f"日语题 {key} 的题面包含答案")
        if key.startswith("g"):
            option_lines = [line.removeprefix("选项：") for line in question.splitlines() if line.startswith("选项：")]
            options = option_lines[0].split(" ／ ") if len(option_lines) == 1 else []
            if len(options) != 3 or len({norm(s) for s in options}) != 3 or not answers & {norm(s) for s in options}:
                raise ValueError(f"日语题 {key} 的三个选项或正确选项不合法")
        # 同题面和同考点、答案、原词都视为重复，不能靠换题号重复扩容。
        signature = (key[0], norm(topic.rsplit("·N", 1)[0]), tuple(sorted(answers)), originals)
        if norm(question) in questions or signature in signatures:
            raise ValueError(f"日语题 {key} 重复")
        questions.add(norm(question))
        signatures.add(signature)
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
    desc="N3–N1 日语助词、词形、词义、读音、敬语和语法文字题", header="日语", columns=(), row=row,
    clues=CLUES, build=build, settings=(VERSION,), exact=True, quiz=quiz,
)
