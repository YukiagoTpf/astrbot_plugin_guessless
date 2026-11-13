"""从 ECDICT CSV 确定性生成本地 Wordle 词表。用 python3 -I 执行，不导入或执行源数据。"""

import argparse
import ast
import csv
import hashlib
import json
import re
from pathlib import Path

SOURCE = "https://github.com/skywind3000/ECDICT"
EXAMS = {"cet4", "cet6", "ky"}
WORD = re.compile(r"[a-z]{5}\Z")
PROPER = re.compile(r"人名|地名|男子名|女子名|姓氏|\[人名\]|\[地名\]|缩写|缩略|简写|简称|商标|(?:英国|美国|法国|德国|日本|中国|印度|意大利|希腊|罗马|苏联|俄国).*(?:人|语|市|省|国)|音译")
GUESS_PROPER = re.compile(r"品牌|公司名|乐队名|首都|港市|abbr\.|牌照相机|（[^）]*(?:城市|港口|公司|诗人|作家|名医)[^）]*）", re.I)
DEFINITION_BLOCK = re.compile(r"trade\s*mark|abbreviation|acronym|initialism|short(?:ened)? (?:form|for)", re.I)
DEFINITION_PROPER = re.compile(r"\b(?:capital|city|town|port|river|island|province|county|village|lake) (?:of|in|on|near)\b|"
                               r"\bsurname\b|\bgiven name\b|\b(?:poet|physician|writer|composer|painter)\b.*\b\d{3,4}", re.I)
# 这些源条目没有一致的专名标记，不能把品牌、作品和人名当作普通词。
PROPER_WORDS = {"akira", "asher", "bambi", "boyle", "conti", "cymru", "dolby", "galen", "golan", "hegel",
                "honda", "iliad", "lhasa", "lorca", "merck", "mobil", "nixon", "pusey", "roche", "sanyo",
                "sarum", "seiko", "sions"}
ABBREVIATIONS = {"auto", "biotech", "memo", "phone", "maths", "kilo", "xerox"}
QUALITY_BLOCK = {"equipe", "jodie", "reservior", "turkey", "uptodate", "vitamine"}
BAD_SPELLINGS = {"reservior", "uptodate"}
WORD_FIXES = {"CORE": "core"}  # 源词条是普通词及其变形，大小写误标；不批量小写化专名或缩写。
POS = {"n": "名词", "v": "动词", "a": "形容词", "j": "形容词", "r": "副词", "d": "副词",
       "p": "介词", "c": "连词", "i": "感叹词", "u": "代词", "m": "数词"}
TRANSLATION_POS = {"n": "名词", "v": "动词", "vt": "动词", "vi": "动词", "a": "形容词", "adj": "形容词",
                   "ad": "副词", "adv": "副词", "prep": "介词", "conj": "连词", "pron": "代词", "num": "数词",
                   "interj": "感叹词", "int": "感叹词", "art": "冠词", "aux": "助动词"}
# 修正源词典可确认的错释、偏义；不改源数据。
MEANINGS = {"stale": ("形容词", "不新鲜的；陈旧的"), "hasty": ("形容词", "匆忙的；轻率的"),
            "gender": ("名词", "性别；语法中的性"), "hamburger": ("名词", "汉堡包；牛肉饼"),
            "oriental": ("形容词", "东方的；亚洲的"), "agent": ("名词", "代理人；代理商；媒介"),
            "aggressive": ("形容词", "进取的；爱挑衅的"), "arrow": ("名词", "箭头；箭头记号"),
            "chancellor": ("名词", "大学校长"), "enlist": ("动词", "参与；谋取"),
            "expedition": ("名词", "探险队；探险"), "fence": ("名词 / 动词", "围墙；栅栏"),
            "fleet": ("名词", "车队；船队"), "marine": ("形容词", "海的；海产的；海底的"),
            "maiden": ("名词 / 形容词", "少女；未婚的；初次的"), "poll": ("名词 / 动词", "民意测验；轮询"),
            "queer": ("形容词", "奇怪的；不舒服的；可疑的"), "veteran": ("名词 / 形容词", "老手；经验丰富的"),
            "virgin": ("形容词", "初始的；纯的"), "tactic": ("名词", "策略"),
            "chief": ("形容词", "主要的；首位的"), "loose": ("形容词", "宽松的；松的；不牢固的"),
            "shoot": ("名词 / 动词", "芽；拍摄；射门")}


def hints(record, blocked):
    """取完整短义项，避免在半个汉字词或括号中间硬截断。"""
    text = record["translation"].replace("\\n", "；")
    positions = list(dict.fromkeys(TRANSLATION_POS[p] for p in re.findall(r"\b([a-z]+)\.", text)
                                   if p in TRANSLATION_POS))
    if not positions:
        positions = list(dict.fromkeys(POS[p] for p in re.findall(r"(?:^|/)([a-z]):", record["pos"]) if p in POS))
    # 只用主释义，不把后面的专业旧义当成日常词义。
    meaning = re.sub(r"\[[^]]*\]|\([^)]*\)|（[^）]*）|\b[a-z]+\.", "", text.split("；")[0])
    parts = []
    for part in re.split(r"[,;，；]", meaning):
        part = part.strip()
        if not re.search(r"[\u4e00-\u9fff]", part) or blocked.search(part):
            continue
        if len("；".join([*parts, part])) <= 32:
            parts.append(part)
        if len(parts) == 3:
            break
    pos, meaning = MEANINGS.get(record["word"], (" / ".join(positions[:2]), "；".join(parts)))
    return pos, "；".join(part for part in meaning.split("；") if not blocked.search(part))


def rank(record):
    return min((int(record[k]) for k in ("bnc", "frq") if record[k].isdigit() and int(record[k]) > 0), default=999999)


def inflections(record):
    """只读取词典明确列出的屈折形式，不自行拼接词尾。"""
    for part in record["exchange"].split("/"):
        kind, _, value = part.partition(":")
        if kind in {"p", "d", "i", "3", "r", "t", "s"}:
            yield from value.split(",")


def is_inflected(record):
    """原形指向自身时保留，不能把 dress、glass 之类按结尾误当复数。"""
    for part in record["exchange"].split("/"):
        kind, _, value = part.partition(":")
        if kind == "0" and value and any(w != record["word"] for w in value.split(",")):
            return True
    return False


def ordinary(record):
    """专名、缩写和已知错拼不进入可猜词，普通词的屈折形式仍可猜。"""
    # 普通词可能和人名同形，英文定义全是专名时才剔除。
    definitions = [s for s in record["definition"].replace("\\n", "\n").splitlines() if s.strip()]
    return (record["word"] not in ABBREVIATIONS | QUALITY_BLOCK | BAD_SPELLINGS | PROPER_WORDS
            and not PROPER.search(record["translation"])
            and not GUESS_PROPER.search(record["translation"])
            and not DEFINITION_BLOCK.search(record["definition"])
            and not (definitions and all(DEFINITION_PROPER.search(s) for s in definitions)))


def generate(csv_path: Path, license_path: Path, output: Path):
    root = Path(__file__).resolve().parents[1]
    tree = ast.parse((root / "core/blind/wordle.py").read_text(encoding="utf-8"))
    rules = {node.targets[0].id: ast.literal_eval(node.value) for node in tree.body
             if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
             and node.targets[0].id in {"NO_ANSWER", "ANSWER_BLOCK"}}
    blocked = re.compile(rules["ANSWER_BLOCK"])
    records = {}
    with csv_path.open(encoding="utf-8-sig", newline="") as stream:
        for record in csv.DictReader(stream):
            word = record["word"] = WORD_FIXES.get(record["word"], record["word"])
            if re.fullmatch(r"[a-z]{1,10}", word):
                records.setdefault(word, record)
    seeds = sorted((w for w, r in records.items() if ordinary(r) and (r["tag"] or rank(r) < 999999)),
                   key=lambda w: (rank(records[w]), w))
    guesses = set()
    for word in seeds:
        if WORD.fullmatch(word):
            guesses.add(word)
        guesses.update(w for w in inflections(records[word]) if WORD.fullmatch(w)
                       and (w not in records or ordinary(records[w])))
    answers = []
    for word, record in sorted(records.items()):
        tags = set(record["tag"].split())
        translation = record["translation"].replace("\\n", "；")
        if (not WORD.fullmatch(word) or not tags & EXAMS or is_inflected(record) or word in rules["NO_ANSWER"]
                or not ordinary(record)
                or not re.search(r"[\u4e00-\u9fff]", translation)):
            continue
        pos, meaning = hints(record, blocked)
        if not pos or not meaning:
            continue
        main = bool(tags & {"cet4", "cet6"}) and rank(record) <= 10000
        answers.append([word, main, pos, meaning])
        guesses.add(word)
    # 源词典的已知错拼也不能当作合法猜测。
    guesses.difference_update(BAD_SPELLINGS)
    payload = {"source": SOURCE, "license": "MIT", "license_text": license_path.read_text(encoding="utf-8"),
               "source_sha256": hashlib.sha256(csv_path.read_bytes()).hexdigest(),
               "answers": answers, "guesses": sorted(guesses)}
    encoded = (json.dumps(payload, ensure_ascii=False, indent=1) + "\n").encode("utf-8")
    if len(encoded) > 1500000 or len(guesses) < 5000:
        raise ValueError(f"词表大小不合要求：{len(encoded)} bytes / {len(guesses)} words")
    output.write_bytes(encoded)
    print(f"answers={len(answers)} main={sum(a[1] for a in answers)} guesses={len(guesses)} bytes={len(encoded)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path)
    parser.add_argument("--license", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1] / "core/wordle_gen.json")
    args = parser.parse_args()
    generate(args.csv, args.license, args.output)
