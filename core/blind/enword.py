"""猜英文：四六级和考研词汇，完整拼词，提示按英文单词边界检查泄露。"""

import json
import re
import unicodedata
from collections import Counter
from pathlib import Path

from .. import cards
from ..answer import norm
from ..enword_data import QUESTIONS
from .base import Blind, entry

VERSION = "enword-v3"  # 数据、答案说法、提示或选题规则改了就换
CLUES = ("词性", "释义", "例句", "首字母", "字母数")


def forms(word: str) -> set[str]:
    """保守生成常见屈折变化，供提示过滤；不影响答题时认可的 names。"""
    word = word.lower()
    out = {word, *(word + suffix for suffix in ("s", "es", "ed", "ing", "er", "est", "ly"))}
    if word.endswith("e"):
        out.update((word + "d", word + "r", word + "st", word[:-1] + "ing", word[:-1] + "ly"))
    if len(word) > 1 and word.endswith("y") and word[-2] not in "aeiou":
        out.update(word[:-1] + suffix for suffix in ("ies", "ied", "ier", "iest", "ily"))
    if word.endswith("ie"):
        out.add(word[:-2] + "ying")
    if len(word) >= 3 and word[-1] not in "aeiouwxy" and word[-2] in "aeiou" and word[-3] not in "aeiou":
        out.update(word + word[-1] + suffix for suffix in ("ed", "ing", "er", "est"))
    return out


def leaks(text: str, names: list | tuple) -> bool:
    """只比完整英文词，art 不会误伤 start；全角字母也先统一。"""
    words = set(re.findall(r"[a-z]+", unicodedata.normalize("NFKC", text).casefold()))
    return any(words & forms(name) for name in names)


async def build(bank) -> None:
    try:
        data = json.loads((Path(__file__).resolve().parents[1] / "enword_gen.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("英文生成题库无法读取或不是有效 JSON") from exc
    if not isinstance(data, dict) or not data.get("source") or not data.get("license") or not isinstance(data.get("questions"), list):
        raise ValueError("英文生成题库缺少来源、许可或题目列表")
    entries = []
    keys, questions, fingerprints = set(), set(), set()
    primary = Counter()
    by_kind = {}
    for generated, record in [(False, record) for record in QUESTIONS] + [(True, record) for record in data["questions"]]:
        if not isinstance(record, (list, tuple)) or len(record) != 8:
            raise ValueError("英文题必须有八个字段")
        key, main, question, names, pos, meaning, example, explanation = record
        if (not isinstance(key, str) or not key or not isinstance(main, bool)
                or not all(isinstance(value, str) for value in (question, pos, meaning, example, explanation))
                or not question.strip() or not explanation.strip()):
            raise ValueError(f"英文题 {key} 的题号、分档、题面或解析无效")
        if key in keys:
            raise ValueError(f"英文题号重复：{key}")
        keys.add(key)
        if (not isinstance(names, (list, tuple)) or not names
                or any(not isinstance(name, str) or not re.fullmatch(r"[A-Za-z]{3,}", name) for name in names)):
            raise ValueError(f"英文题 {key} 的答案必须是至少三个纯英文字母")
        if len({norm(name) for name in names}) != len(names):
            raise ValueError(f"英文题 {key} 的可接受答案重复")
        kind = next((kind for marker, kind in (("中文释义", "definition"), ("填空", "cloze"),
                    ("反义词", "antonym"), ("近义词", "synonym"), ("词性转换", "derivation")) if marker in question), "form")
        normalized = norm(question)
        if normalized in questions:
            raise ValueError(f"英文题 {key} 的题面重复")
        questions.add(normalized)
        quoted = re.findall(r"""「([^」]+)」|"([^"]+)"|'([^']+)'|“([^”]+)”|‘([^’]+)’""", question)
        if any(norm(word) == norm(name) for group in quoted for word in group if word for name in names):
            raise ValueError(f"英文题 {key} 的答案包含题面原词")
        original = tuple(norm(word) for group in quoted for word in group if word)
        fingerprint = (kind, tuple(sorted(norm(name) for name in names)), original)
        if fingerprint in fingerprints:
            raise ValueError(f"英文题 {key} 的题型、答案和原词重复")
        fingerprints.add(fingerprint)
        answer = norm(names[0])
        # 仅允许原有手写题之间的同型主答案重复；新增题不能沿用这个例外。
        same_kind = (kind, answer)
        if same_kind in by_kind and (generated or by_kind[same_kind]):
            raise ValueError(f"英文题 {key} 的同型主答案重复：{answer}")
        by_kind[same_kind] = generated
        primary[answer] += 1
        if primary[answer] > 3:
            raise ValueError(f"英文主答案超过三次：{answer}")
        hints = [["词性", pos], ["释义", meaning], ["例句", example]]
        # 必须先查原始线索，否则 finish_entry 静默删除会掩盖题库错误。
        if any(leaks(value, names) for _, value in hints) or leaks(question, names):
            raise ValueError(f"英文题 {key} 的题面或提示包含答案及变形")
        item = entry("enword", key, names[0], "", names[0], list(names), hints, None, main)
        # 通用过滤按子串，会误删 art 题里的 start；英文已按词边界检查，只借自动首字母。
        item["hints"] = [hint for hint in hints if hint[1]] + [item["hints"][-1]]
        lengths = sorted({len(name) for name in names})
        item["hints"].append(["字母数", " / ".join(map(str, lengths))])
        if any(leaks(" ".join(hint), names) for hint in item["hints"]):
            raise ValueError(f"英文题 {key} 的提示包含答案及变形")
        item.update(question=question, explanation=explanation)
        entries.append(item)
    bank.commit("enword", entries)


def quiz(item: dict, reveal: bool = False):
    return cards.quiz(item["question"], item["answer"] if reveal else "", item["explanation"] if reveal else "")


def row(guess: dict, answer: dict) -> list:
    return []


MODE = Blind(
    cat="enword", command="猜英文", aliases=("猜英文单词", "猜单词"),
    desc="四六级和考研词汇：释义、词形、词性转换、填空和反义近义词，完整拼词", header="英文", columns=(), row=row,
    clues=CLUES, build=build, settings=(VERSION,), exact=True, quiz=quiz,
)
