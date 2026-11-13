"""猜成语：本地四字成语表、标准重复字计分、全群共用棋盘。"""

import json
import re
from pathlib import Path

from .. import answer, cards
from .base import Blind, entry
from .wordle import row

VERSION = "chengyu-v1"  # 选题、词表、条目或提示改了就换
CLUES = ("声调", "释义")
# 本义明确不适合出题的成语，只能加不能删；可猜表仍保留它们。
NO_ANSWER = {
    "一夫当关", "一统天下", "万寿无疆", "三从四德", "三纲五常", "不共戴天", "不臣之心", "东亚病夫",
    "为国捐躯", "为虎作伥", "举世混浊", "九五之尊", "亡国之音", "人尽可夫", "人头畜鸣", "人面兽心",
    "以暴易暴", "伐罪吊民", "伤风败俗", "作奸犯科", "倒行逆施", "傻头傻脑", "兔死狗烹", "兴师动众",
    "兵临城下", "兵不厌诈", "兵荒马乱", "决一死战", "刀光剑影", "刀山火海", "刻薄寡恩", "剖腹藏珠",
    "助纣为虐", "十恶不赦", "南征北战", "卖国求荣", "厚颜无耻", "口诛笔伐", "含血喷人", "周公吐哺",
    "唯命是从", "四面楚歌", "国泰民安", "国破家亡", "声色犬马", "天诛地灭", "奴颜婢膝", "好色之徒",
    "妻妾成群", "威武不屈", "官官相护", "富国强兵", "寻花问柳", "尸位素餐", "尸横遍野", "屠龙之技",
    "崇洋媚外", "开国功臣", "引颈受戮", "弱肉强食", "强奸民意", "忠君爱国", "忠贞不渝", "性命交关",
    "恶贯满盈", "惨无人道", "戎马倥偬", "揭竿而起", "改朝换代", "斩尽杀绝", "斩草除根", "昏庸无道",
    "替天行道", "有勇无谋", "杀一儆百", "杀人如麻", "杀人放火", "杀气腾腾", "杀身成仁", "枪林弹雨",
    "横尸遍野", "歃血为盟", "残兵败将", "民不聊生", "水性杨花", "沉鱼落雁", "沐猴而冠", "流血漂杵",
    "淫词艳曲", "满门抄斩", "烽火连天", "焚书坑儒", "煮豆燃萁", "狗仗人势", "狗彘不如", "狼心狗肺",
    "王侯将相", "生灵涂炭", "男尊女卑", "画地为牢", "神机妙算", "神通广大", "穷兵黩武", "红杏出墙",
    "罄竹难书", "肝脑涂地", "胡言乱语", "花天酒地", "花容月貌", "草菅人命", "莫逆之交", "装聋作哑",
    "血债累累", "血口喷人", "血流成河", "血肉横飞", "衣冠禽兽", "诛心之论", "谋财害命", "贼眉鼠眼",
    "赤胆忠心", "赤身裸体", "赴汤蹈火", "轻生重义", "辱国丧师", "道貌岸然", "金戈铁马", "长治久安",
    "阿谀奉承", "除暴安良", "隔岸观火", "鞠躬尽瘁", "风流韵事", "风花雪月", "马革裹尸", "鬼使神差",
    "鬼哭狼嚎", "鬼斧神工", "鱼肉百姓", "鸦片战争", "龙凤呈祥", "龙袍加身",
    "两败俱伤", "你死我活", "再接再厉", "前仆后继", "劈头盖脸", "同归于尽", "含苞待放", "四脚朝天",
    "壮士断腕", "大好河山", "大打出手", "大张旗鼓", "安营扎寨", "开疆拓土", "必争之地", "所向披靡",
    "打击报复", "拳打脚踢", "死去活来", "气壮山河", "滥用职权", "独断专行", "痛不欲生", "竭泽而渔",
    "能上能下", "自投罗网", "落荒而逃", "贪赃枉法", "遍体鳞伤", "里应外合", "霸王别姬", "颠沛流离", "高高在上",
}
# 按类别过滤完整释义，宁可少出，也不把历史本义藏在现代比喻义后面。
ANSWER_BLOCK = {
    "色情": r"淫|色情|风月|床笫|男女|女色|美色|妓|嫖|姘|调情|通奸|性欲|肉欲|艳情|偷情|私情|贞节|贞操|夫妇|夫妻",
    "政治": r"政|帝|皇|君|臣|王侯|王朝|王位|朝廷|官|统治|权谋|谋权|篡|革命|民族|领导人|运动|封建|专制|国事|国家|国运|国势|国民|亡国|爱国|报国|奸雄|奸贼|忠诚|忠心|忠贞|忠义|忠节|忠烈|干部|职权|职务|民主|领导者|反动派|剥削|祖国|人民|疆土|领土|要职|诸侯",
    "军事": r"兵|战争|战斗|作战|战场|战役|军事|军|征战|征伐|讨伐|杀伐|武器|刀剑|枪炮|弓箭|弩|烽火|敌人|敌方|敌我|敌国|攻城|城池|盔甲|甲胄|阵地|阵势|打仗|交战|战胜|战败|战国|楚汉|三国|核武|核能|核电|原子弹|部队|驻扎|敌对|战略|战乱|败仗|进攻|攻打|攻取|攻占|防守|防御|抗击|溃败|箭靶|放箭|射箭|攻击|反击",
    "暴力": r"血|杀|屠|戮|刑|斩|剐|剖|尸|暴力|暴虐|残暴|残忍|凶残|凶恶|凶狠|凶猛|凶暴|虐待|伤害|残害|摧残|丧命|送命|毙|仇恨|报仇|复仇|仇敌|仇人|自残|自杀|寻死|轻生|毒品|吸毒|鸦片|大麻|海洛因|送死|断腕|殴打|打架|打斗|拳打|脚踢|受伤|创伤|鳞伤|相斗",
    "迷信": r"迷信|鬼|神灵|神仙|神佛|神明|神怪|神力|神话|神奇|神异|神秘|佛|菩萨|罗汉|观音|道教|道家|道士|宗教|法术|魔法|妖|仙人|仙境|仙女|仙山|地狱|冥|阴间|天命|天意|天子|上天|苍天|转世|轮回|因果报应|报应|占卜|算命|风水|魂|魄|祭祀|祈祷|保佑|吉祥|祥瑞|瑞应|龙凤|升天|成仙|和尚|尼姑|念经|天理|天道|天赐|天助|五行|八卦",
    "歧视辱骂": r"歧视|辱|骂|詈|蔑|讥|讽|嘲|贱|卑鄙|无耻|丑恶|恶毒|恶人|坏人|小人|败类|奴|婢|妇女|女子|女人|男人|男性|女性|妇人|残疾|残废|聋|哑|瞎|瘸|蠢|愚笨|愚蠢|呆傻|痴呆|下流|粗俗|粗野|低级趣味|野蛮|蛮夷|夷狄|胡人|胡说|少数民族",
}


def fits(guess: dict, target: dict) -> bool:
    return bool(re.fullmatch(r"[\u4e00-\u9fff]{4}", guess.get("word", "")))


def board(item: dict, rows: list, total: int, reveal: bool = False):
    return cards.chengyu(rows, total, item["answer"] if reveal else "")


def hints(word: str, tones: str, meaning: str) -> list[list[str]]:
    """连名目也不能泄露单字；不用自动首字提示，也不展示历史出处和例句。"""
    result = []
    for key, value in (("声调", tones), ("释义", meaning)):
        if set(key) & set(word):
            continue
        value = "".join("○" if char in word else char for char in value)
        if value and not set(answer.norm(key + value)) & set(answer.norm(word)):
            result.append([key, value])
    assert all(not set(key + value) & set(word)
               and not set(answer.norm(key + value)) & set(answer.norm(word))
               for key, value in result), f"成语提示泄露：{word}"
    return result


async def build(bank) -> None:
    data = json.loads((Path(__file__).resolve().parents[1] / "chengyu_gen.json").read_text(encoding="utf-8"))
    if not data.get("source") or not data.get("license") or not data.get("license_text"):
        raise ValueError("成语词表缺少来源或许可")
    words = data["guesses"]
    if (not isinstance(words, list) or any(not isinstance(w, str) or not fits({"word": w}, {}) for w in words)
            or words != sorted(set(words))):
        raise ValueError("成语可猜表必须为排序去重的四字成语")
    # 字体发生变化时也不能出缺字的题；只比较缺字掩码，不依赖额外字体解析包。
    supported = set("".join(words))
    for bold in (False, True):
        face = cards.font(32, bold=bold)
        missing = bytes(face.getmask("\U0010ffff"))
        supported = {c for c in supported if bytes(face.getmask(c)) != missing}
    places = {w: {"title": w, "names": [w], "attrs": {"word": w}} for w in words if set(w) <= supported}
    entries, seen = [], set()
    for word, tones, meaning in data["answers"]:
        if (word not in words or word in seen or word in NO_ANSWER or not meaning
                or any(re.search(pattern, word + meaning) for pattern in ANSWER_BLOCK.values())
                or not re.fullmatch(r"[1-4](?: [1-4]){3}", tones)):
            raise ValueError(f"成语答案无效或被排除：{word}")
        seen.add(word)
        if word not in places:
            continue
        item = entry("chengyu", word, word, "", word, [word], [], {"word": word}, True)
        item["hints"] = hints(word, tones, meaning)
        entries.append(item)
    if not entries:
        raise ValueError("成语答案为空，请检查中文字体和词表")
    bank.commit("chengyu", entries, places=places)


MODE = Blind(
    cat="chengyu", command="猜成语", aliases=("中文Wordle", "成语Wordle"),
    desc="全群共用汉字棋盘，绿对位、黄错位、灰没有", header="成语", columns=(), clues=CLUES,
    row=row, build=build, settings=(VERSION,), exact=True, board=board, fits=fits, rows=10, default_guesses=10,
)
