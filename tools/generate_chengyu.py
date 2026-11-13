"""离线生成四字成语题库；只解析本地数据，使用 python3 -I 运行。"""

import argparse
import ast
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

from zhconv import convert

XINHUA = "https://github.com/pwxcoo/chinese-xinhua"
THUOCL = "https://github.com/thunlp/THUOCL"
XINHUA_REV = "fe6d6c2e8baa82187f4c96bbe042e43f96c05666"
THUOCL_REV = "a30ce79d895d01ab5132a5c74c29703ff7efb4cc"
WORD = re.compile(r"[\u4e00-\u9fff]{4}")
# 已知错字及异写不进入可猜表；不把错误写法自动映射成正确答案。
BAD_SPELLINGS = {"一股作气", "一愁莫展", "一筹莫画", "世外桃园", "迫不急待", "按步就班", "再接再励", "默守成规",
                 "谈笑风声", "相形见拙", "趋之若骛", "穿流不息", "甘败下风", "不径而走", "滥芋充数"}
# 只有指向另一写法、没有独立释义的条目，或释义自报异写的，保守排除。
VARIANT = re.compile(r"(?:同|见|即|参见)[‘'\"“「]?([\u4e00-\u9fff]{4})[’'\"”」]|亦作|也作|又作|亦写作|也写作|同义|异体|讹|通[‘'\"“]|误作")
UNCLEAR = re.compile(r"不详|无解释|暂无|待考|语出|典出|详见|参见|见[‘'\"“]|同[‘'\"“]|亦同|犹言|犹[‘'\"“]|略同|也说|亦称|又称")
TONES = {char: str(tone) for tone, chars in enumerate(("āēīōūǖ", "áéíóúǘ", "ǎěǐǒǔǚ", "àèìòùǜ"), 1) for char in chars}
# 审读发现上游错释或残句，直接舍弃，不猜测修订原文。
BAD_MEANINGS = {"一口咬定", "一拍即合", "处心积虑", "治病救人", "深山老林", "痛定思痛", "药到病除",
                "宁缺毋滥", "隐隐约约", "害群之马", "永垂不朽", "细枝末节", "动荡不安", "众说纷纭"}
# 首轮按常用度审读的候选到词频 858；提高边界，不因新增排除项补入未审读的低频词。
MIN_FREQUENCY = 859


def tones(pinyin: str) -> str:
    parts = pinyin.lower().split()
    if len(parts) != 4 or any(not re.fullmatch(r"[a-züāáǎàēéěèīíǐìōóǒòūúǔùǖǘǚǜ]+", p) for p in parts):
        return ""
    values = [[TONES[c] for c in p if c in TONES] for p in parts]
    if any(len(v) != 1 for v in values):
        return ""  # 缺声调或多音标注不猜读音，也不给不可靠线索
    return " ".join(v[0] for v in values)


def generate(args):
    root = Path(__file__).resolve().parents[1]
    tree = ast.parse((root / "core/blind/chengyu.py").read_text(encoding="utf-8"))
    rules = {node.targets[0].id: ast.literal_eval(node.value) for node in tree.body
             if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
             and node.targets[0].id in {"NO_ANSWER", "ANSWER_BLOCK"}}
    blocks = {key: re.compile(value) for key, value in rules["ANSWER_BLOCK"].items()}
    raw = json.loads(args.idiom.read_text(encoding="utf-8"))
    frequencies = {}
    for line in args.frequency.read_text(encoding="utf-8").splitlines():
        word, frequency = line.split()
        frequencies[word] = max(frequencies.get(word, 0), int(frequency))
    counts, examples = Counter(), {}

    def reject(reason, word):
        counts[reason] += 1
        examples.setdefault(reason, [])
        if len(examples[reason]) < 5:
            examples[reason].append(word)

    records = {}
    for item in raw:
        word = convert(item["word"].strip(), "zh-hans")
        if not WORD.fullmatch(word):
            reject("非四字汉字", word)
        elif word in records:
            reject("重复词条", word)
        elif word in BAD_SPELLINGS or VARIANT.search(item["explanation"]):
            reject("异写或错字", word)
        else:
            records[word] = {**item, "explanation": convert(item["explanation"], "zh-hans")}
    guesses = sorted(records)
    answers = []
    for word, item in sorted(records.items(), key=lambda pair: (-frequencies.get(pair[0], 0), pair[0])):
        meaning = item["explanation"].strip()
        reason = next((key for key, pattern in blocks.items() if pattern.search(word + meaning)), "")
        if reason:
            reject(reason, word)
        elif word in rules["NO_ANSWER"]:
            reject("永久不出题", word)
        elif word in BAD_MEANINGS:
            reject("已知错释", word)
        elif (not 8 <= len(meaning) <= 110 or UNCLEAR.search(meaning)
              or re.search(r"[^\u4e00-\u9fff\u3000-\u303f，。；：、！？“”‘’（）《》〈〉…—\s]", meaning)):
            reject("释义不清或格式", word)
        elif not tones(item["pinyin"]):
            reject("读音不可靠", word)
        elif max(Counter(word).values()) > 2:
            reject("重复字过多", word)
        elif not all("\u4e00" <= c <= "\u9fff" and _common(c) for c in word):
            reject("生僻字", word)
        elif frequencies.get(word, 0) < MIN_FREQUENCY:
            reject("低频或无词频", word)
        elif len(answers) >= 2000:
            reject("常用度名额", word)
        else:
            answers.append([word, tones(item["pinyin"]), meaning])
    if len(answers) < 500:
        raise ValueError(f"合规常用成语不足：{len(answers)}")
    sources = []
    for name, url, revision, data, license_file, relative in (
        ("chinese-xinhua", XINHUA, XINHUA_REV, args.idiom, args.xinhua_license, "data/idiom.json"),
        ("THUOCL", THUOCL, THUOCL_REV, args.frequency, args.thuocl_license, "data/THUOCL_chengyu.txt"),
    ):
        license_text = license_file.read_text(encoding="utf-8")
        if "MIT License" not in license_text or "Permission is hereby granted" not in license_text:
            raise ValueError(f"{name} 缺少 MIT 许可全文")
        sources.append({"name": name, "url": f"{url}/blob/{revision}/{relative}", "revision": revision,
                        "sha256": hashlib.sha256(data.read_bytes()).hexdigest(), "license": "MIT",
                        "license_url": f"{url}/blob/{revision}/LICENSE", "license_text": license_text})
    counts.setdefault("重复字过多", 0)
    payload = {"source": sources, "license": "MIT", "license_text": [s["license_text"] for s in sources],
               "selection": f"按 THUOCL 词频降序、同频字序，词频至少 {MIN_FREQUENCY}，GB2312 汉字，最多 2000 道；全部大众题",
               "stats": {"raw": len(raw), "answers": len(answers), "guesses": len(guesses),
                         "excluded": dict(sorted(counts.items())), "examples": dict(sorted(examples.items()))},
               "answers": sorted(answers), "guesses": guesses}
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(payload["stats"], ensure_ascii=False, indent=1))


def _common(char):
    try:
        return len(char.encode("gb2312")) == 2
    except UnicodeEncodeError:
        return False


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--idiom", type=Path, required=True)
    parser.add_argument("--frequency", type=Path, required=True)
    parser.add_argument("--xinhua-license", type=Path, required=True)
    parser.add_argument("--thuocl-license", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1] / "core/chengyu_gen.json")
    generate(parser.parse_args())
