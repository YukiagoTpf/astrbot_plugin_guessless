"""英文 Wordle：ECDICT 本地词表、标准重复字母计分、全群共用棋盘。"""

import json
import re
from collections import Counter
from pathlib import Path

from .. import cards
from .base import Blind, entry
from .enword import leaks

VERSION = "wordle-v1"  # 选题、词表、条目格式或提示规则改了就换
CLUES = ("词性", "释义")  # 容器字体缺少常用 IPA 字形，不给音标
# 只放本义明确踩红线的词，只能加不能删；中性多义词按中文释义过滤，王室头衔不在红线内。
NO_ANSWER = {
    "ammunition", "anarchism", "anarchy", "arsenal", "artillery", "assassinate", "assault", "bastard",
    "battalion", "battle", "bayonet", "bitch", "bloody", "bomb", "brothel", "bullet", "cannabis", "cannon", "capitalism",
    "cock", "cocaine", "combat", "communism", "communist", "congress", "constitution", "coup", "cunt", "dagger", "damn",
    "democracy", "democrat", "dictator", "dildo", "election", "embassy", "erotic", "fascism", "fascist", "federal", "fuck",
    "gallows", "genocide", "gook", "government", "grenade", "guerilla", "guerrilla", "gunfire", "gunman",
    "gunpowder", "heroin", "hitler", "holocaust", "homicide", "incest", "jihad", "kill", "killer", "marijuana",
    "massacre", "military", "militia", "ministry", "missile", "monarchy", "morphine", "murder", "naked", "nazi",
    "neutron", "nigger", "nipple", "nuclear", "nudity", "opium", "orgasm", "penis", "pistol", "platoon",
    "plutonium", "political", "politician", "politics", "porn", "porno", "president", "prostitute", "racism",
    "racist", "rape", "regime", "regiment", "republic", "republican", "rifle", "saber", "sabre", "senate",
    "sexy", "shit", "slut", "socialism", "socialist", "soldier", "soviet", "spear", "sperm", "strangler",
    "striptease", "suicide", "sword", "terrorism", "terrorist", "torpedo", "torture", "troop", "uranium",
    "vagina", "warfare", "warrior", "weapon", "whore",
    "colonel", "commander", "corps", "empire", "lieutenant", "murderer", "navy", "negro", "obscene",
    "senator", "sexual", "siege", "slaughter", "submarine", "traitor", "treason", "tyranny", "tyrant",
}
# 逐个中文义项过滤；中性词可以使用合规义项，不因未展示的另一义永久禁用。
ANSWER_BLOCK = (
    r"政治|政权|政府|政党|议会|议员|国会|总统|首相|总理|内阁|帝国|君主制|王朝|殖民|共产|社会主义|资本主义|"
    r"民主|独裁|法西斯|纳粹|革命|叛乱|战争|战斗|作战|战场|战役|军事|军队|军人|军用|海军|陆军|空军|士兵|"
    r"武器|枪|炮|弹药|炸弹|导弹|轰炸|战舰|战机|侵略|俘虏|核武|核能|核弹|核电|核反应|核子|核裂变|核聚变|"
    r"原子弹|原子能|中子弹|铀|钚|色情|性交|性欲|性行为|肉欲|"
    r"性器|生殖|阴茎|阴道|乳房|乳头|裸体|裸露|淫|嫖|妓|强奸|卖春|脏话|粗俗|粗话|骂人|歧视|种族|"
    r"毒品|大麻|海洛因|可卡因|鸦片|吗啡|自杀|自残|杀死|杀害|谋杀|屠杀|处死|绞死|"
    r"军队|军人|军用|军官|军团|军营|军备|军火|军械|军舰|军机|军工|军令|军法|军衔|军中|军旗|军医|"
    r"将军|参军|从军|正规军|常备军|后备军|敌军|新兵|水兵|阅兵|传令兵|勤务兵|司令|指挥官|中尉|副官|"
    r"部队|兵种|工兵|士兵|征兵|战俘|火药|坦克|鱼雷|水雷|地雷|叛国|恐怖分子|选举|投票|选民|州长|总督|部长|"
    r"领事|大使|共和党|赤裸|脱衣|精液|迷幻药|吸毒|扼杀|侮辱性|贬称|"
    r"射击|射伤|弹丸|弹盒|箭杆|长矛|短矛|(?:^|[；，,])矛(?:$|[；，,])|奸污|绞刑|刺杀|"
    r"内战|会战|交战|战士|战利品|战壕|舰队|要塞|好战|元帅|老兵|"
    r"议院|宪法|罂粟|暴力|杀戮|杀人|凶杀|凶手|杀手|嗜杀|血腥|虐待|死刑|毒杀|杀戳|"
    r"元首|专制|暴政|暴君|资产阶级|自由主义者|自由主义的|封建制度|当政|"
    r"竞选|执政官|大臣|公使|市长|联邦|谋反|猥亵|情妇|反应堆"
)


def row(guess: dict, answer: dict) -> list[list[str]]:
    """先扣绿格，再从左到右分配剩余字母；同一字母不会凭空多出黄格。"""
    word, target = guess["word"], answer["word"]
    if len(word) != len(target):
        return []
    states = ["hit" if a == b else "miss" for a, b in zip(word, target)]
    remaining = Counter(b for b, state in zip(target, states) if state != "hit")
    for i, char in enumerate(word):
        if states[i] != "hit" and remaining[char]:
            states[i] = "near"
            remaining[char] -= 1
    return [[char.upper(), state, ""] for char, state in zip(word, states)]


def fits(guess: dict, answer: dict) -> bool:
    return len(guess["word"]) == len(answer["word"])


def board(item: dict, rows: list, total: int, reveal: bool = False):
    return cards.wordle(len(item["attrs"]["word"]), rows, total, item["answer"] if reveal else "")


async def build(bank) -> None:
    data = json.loads((Path(__file__).resolve().parents[1] / "wordle_gen.json").read_text(encoding="utf-8"))
    if not data.get("source") or not data.get("license"):
        raise ValueError("Wordle 词表缺少来源或许可")
    words = data["guesses"]
    if not isinstance(words, list) or any(not isinstance(w, str) or not re.fullmatch("[a-z]{4,10}", w) for w in words):
        raise ValueError("Wordle 可猜词必须是 4–10 位小写字母")
    if words != sorted(set(words)):
        raise ValueError("Wordle 可猜词表必须排序且不重复")
    places = {w: {"title": w, "names": [w], "attrs": {"word": w}} for w in words}
    entries, seen = [], set()
    for word, main, pos, meaning in data["answers"]:
        if (word not in places or word in seen or word in NO_ANSWER or re.search(ANSWER_BLOCK, meaning)
                or not isinstance(main, bool) or not re.search(r"[\u4e00-\u9fff]", meaning)):
            raise ValueError(f"Wordle 答案无效或被排除：{word}")
        seen.add(word)
        hints = [[key, value] for key, value in (("词性", pos), ("释义", meaning)) if value and not leaks(value, [word])]
        item = entry("wordle", word, word, "", word, [word], [], {"word": word}, main)
        # 英文提示按词边界过滤，不能沿用通用的子串过滤和自动首字提示。
        item["hints"] = hints
        entries.append(item)
    bank.commit("wordle", entries, places=places)


MODE = Blind(
    cat="wordle", command="英文Wordle", aliases=("猜Wordle", "Wordle", "wordle"),
    desc="全群共用字母棋盘，绿对位、黄错位、灰没有", header="英文", columns=(), clues=CLUES,
    row=row, build=build, settings=(VERSION,), exact=True, board=board, fits=fits, rows=6, default_guesses=3,
)
