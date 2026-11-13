"""从本地 ECDICT CSV 确定性生成四六级和考研英文题；例句和解析为原创。

用法：python3 -I tools/generate_enword.py --ecdict INPUT.csv --license LICENSE --output core/enword_gen.json
只用标准库，不联网；原始数据请下载到仓库外的空目录。
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path

SOURCE = "https://github.com/skywind3000/ECDICT"
BASIC = {"cet4"}
CORE = {"cet4", "cet6", "ky"}
POS = {"n": "名词", "pl": "名词", "a": "形容词", "adj": "形容词", "adv": "副词", "v": "动词", "vt": "动词", "vi": "动词"}

# 释义题的常见六级词经人工审读分档，其余题型仍按考试标签分档。
DEFINITION_TIERS = {
    'abbreviation': False,
    'acre': True,
    'adoption': True,
    'aluminum': True,
    'ampere': True,
    'architecture': True,
    'archive': True,
    'astronaut': True,
    'atmosphere': True,
    'biology': True,
    'bronze': True,
    'carpenter': True,
    'cell': True,
    'centimetre': True,
    'champagne': True,
    'clause': True,
    'complexity': True,
    'conscience': True,
    'county': True,
    'crystal': True,
    'database': True,
    'decade': True,
    'diameter': True,
    'dividend': True,
    'drought': True,
    'element': True,
    'engineering': True,
    'evaporation': True,
    'factor': True,
    'fossil': True,
    'function': True,
    'fund': True,
    'gallon': True,
    'geology': True,
    'hardware': True,
    'hierarchy': True,
    'historian': True,
    'horizon': True,
    'inflation': True,
    'infrastructure': True,
    'interface': True,
    'interpreter': True,
    'kilowatt': True,
    'landlord': True,
    'lease': True,
    'librarian': True,
    'linen': True,
    'literature': True,
    'manuscript': True,
    'mechanics': True,
    'mercury': True,
    'millimeter': True,
    'molecule': True,
    'monopoly': True,
    'muscle': True,
    'musician': True,
    'nitrogen': True,
    'ohm': True,
    'orchestra': True,
    'ounce': True,
    'ozone': False,
    'paragraph': True,
    'parameter': True,
    'peninsula': True,
    'pension': True,
    'percentage': True,
    'photography': True,
    'physicist': True,
    'privacy': True,
    'protein': True,
    'quart': True,
    'resistance': True,
    'ripple': True,
    'saliva': True,
    'sensor': True,
    'skeleton': True,
    'sodium': True,
    'software': True,
    'spectrum': True,
    'spouse': True,
    'statistics': True,
    'subsidiary': True,
    'sulfur': True,
    'surgeon': True,
    'tide': True,
    'tyre': True,
    'variable': True,
    'verse': True,
    'villa': True,
    'volt': True,
    'volume': True,
    'wavelength': True,
}

# 人工选题清单只决定考点；释义、候选答案与词形仍从 CSV 取出并核对。
DEFINITIONS = """
abbreviation|缩写词
acre|英亩
acrobat|杂技演员
adoption|收养
aluminum|铝
ampere|安培
antenna|天线
apartment|公寓
architecture|建筑学
archive|档案馆
ash|灰烬
astronaut|宇航员
atmosphere|大气
auditorium|礼堂
backbone|脊椎
bacon|熏猪肉
barley|大麦
barometer|气压计
bead|珠子
bean|豆子
beetle|甲虫
bench|长椅子
berry|浆果
bibliography|参考书目
biology|生物学
blanket|毛毯
blueprint|蓝图
boiler|锅炉
bone|骨头
boot|长靴
branch|树枝
brick|砖块
bridle|马勒
bronze|青铜
broom|扫帚
buffalo|水牛
bulb|电灯泡
button|钮扣
cab|出租车
cabin|客舱
cabinet|橱柜
cable|电缆
cafe|咖啡馆
cage|笼
calculus|微积分学
calendar|日历
capacitor|电容器
cape|海角
carbohydrate|碳水化合物
carpenter|木匠
carpet|地毯
catalyst|催化剂
cell|细胞
centimetre|厘米
cereal|谷类食品
chain|链
champagne|香槟酒
chestnut|栗子
chord|和音
chronicle|编年史
clamp|夹钳
clause|子句
closet|壁橱
coefficient|系数
collar|颈圈
comb|头梳
compatibility|兼容性
complexity|复杂性
conscience|良心
container|集装箱
context|上下文
copper|铜
corn|玉蜀黍
counter|柜台
county|县
crab|螃蟹
cradle|摇篮
crop|农作物
crystal|水晶
cuckoo|杜鹃鸟
cupboard|餐具柜
cylinder|汽缸
database|数据库
decade|十年
decimal|小数
deer|鹿
desert|沙漠
diameter|直径
diamond|钻石
diploma|毕业证书
dividend|股利
dome|圆顶
donkey|驴子
drainage|排水
drought|干旱
dust|灰尘
eclipse|日食
ecosystem|生态系统
electrode|电极
element|元素
embroidery|刺绣
encyclopedia|百科全书
engine|引擎
engineering|工程学
envelope|信封
equator|赤道
evaporation|蒸发
factor|因数
feather|羽毛
fireplace|壁炉
flask|烧瓶
fossil|化石
function|函数
fund|基金
gallery|画廊
gallon|加仑
garage|车库
garlic|大蒜
geology|地质学
granite|花岗岩
graphite|石墨
greenhouse|温室
grocery|食品杂货店
habitat|栖息地
hammer|锤
hardware|硬件
harp|竖琴
heater|加热器
hedge|树篱
helicopter|直升机
hemisphere|半球
hierarchy|等级体系
hinge|铰链
historian|历史学家
honey|蜂蜜
horizon|地平线
inflation|通货膨胀
infrastructure|基础设施
insect|昆虫
insulator|绝缘体
interface|界面
interpreter|口译者
invoice|发票
jar|广口瓶
kilowatt|千瓦
ladder|梯
landlord|房东
lane|巷
lathe|车床
lease|租约
leather|皮革
lemon|柠檬
librarian|图书管理员
lily|百合
limestone|石灰石
linen|亚麻布
lip|唇
literature|文学
locomotive|火车头
loom|织布机
lumber|木材
lung|肺
magnet|磁铁
mammal|哺乳动物
manuscript|原稿
maple|枫
marble|大理石
mast|桅
mechanic|机械工
mechanics|力学
mercury|汞
microprocessor|微处理器
mill|磨坊
millimeter|毫米
molecule|分子
monopoly|垄断
mosaic|马赛克
mosquito|蚊子
motor|马达
muscle|肌肉
mushroom|蘑菇
musician|音乐家
nail|钉子
necklace|项链
needle|针
nest|巢
network|网络
nickel|镍
nitrogen|氮
oak|橡树
oar|桨
ohm|欧姆
olive|橄榄树
onion|洋葱
orchard|果园
orchestra|管弦乐队
organ|风琴
ounce|盎司
oven|烤箱
overpass|天桥
owl|猫头鹰
oyster|牡蛎
ozone|臭氧
paddle|划桨
pan|平锅
panorama|全景
pantry|食品室
paragraph|段落
parameter|参数
peach|桃子
peacock|孔雀
pebble|小圆石
pedal|踏板
peninsula|半岛
pension|养老金
pepper|胡椒
percentage|百分比
perch|河鲈
pest|害虫
photography|摄影术
physicist|物理学家
pillow|枕头
pine|松树
piston|活塞
plateau|高原
porcelain|瓷器
portrait|人像
poultry|家禽
prairie|大草原
primate|灵长目动物
prism|棱镜
privacy|隐私
projector|放映机
protein|蛋白质
pulley|滑轮
pumpkin|南瓜
quart|夸脱
quartz|石英
radiator|散热器
rainbow|彩虹
reef|暗礁
reptile|爬行动物
reservoir|水库
resistance|电阻
ripple|涟漪
roof|屋顶
ruby|红宝石
saliva|唾液
salmon|鲑鱼
sardine|沙丁鱼
seed|籽
sensor|传感器
shell|贝壳
shovel|铁铲
shrub|灌木
shutter|百叶窗
signal|信号
silicon|硅
skeleton|骨骼
skyscraper|摩天楼
slipper|拖鞋
snail|蜗牛
soccer|英式足球
sock|短袜
sodium|钠
software|软件
soil|土壤
spectrum|光谱
spider|蜘蛛
spouse|配偶
squirrel|松鼠
stadium|露天大型运动场
staple|订书钉
statistics|统计学
statue|雕像
stool|凳子
subsidiary|子公司
suitcase|手提箱
sulfur|硫
surgeon|外科医生
tanker|油船
thermometer|温度计
tide|潮汐
tuna|金枪鱼
tunnel|隧道
tyre|轮胎
uniform|制服
variable|变量
vase|花瓶
veil|面纱
velvet|天鹅绒
vent|通风孔
verse|诗句
villa|别墅
volcano|火山
volt|伏特
volume|音量
wagon|四轮马车
wallet|皮夹
wardrobe|衣橱
warehouse|货栈
wasp|黄蜂
waterfall|瀑布
wave|波
wavelength|波长
wedge|楔子
whistle|口哨
willow|柳树
windmill|风车
woodpecker|啄木鸟
wool|羊毛
workshop|车间
wrench|扳手
wrist|手腕
yacht|游艇
yeast|酵母
yolk|蛋黄
zinc|锌
""".strip().splitlines()

# 中文题面经审定后补足语境；反查仍使用上面的源义项，避免跳过歧义检查。
DEFINITION_LABELS = {
    'abbreviation': '缩写形式的统称',
    'adoption': '建立亲子关系的收养',
    'atmosphere': '包围星球的气体层',
    'bacon': '培根',
    'cereal': '早餐谷物食品',
    'chord': '和弦',
    'clause': '语法中的分句',
    'corn': '玉米',
    'drought': '持续缺雨的一段干旱',
    'element': '化学元素',
    'function': '数学中的函数',
    'ladder': '可攀爬的梯子',
    'manuscript': '作者手写的原稿',
    'mast': '桅杆',
    'mechanics': '物理学中的力学',
    'molecule': '化学中的分子',
    'paddle': '手持短桨',
    'pantry': '食品储藏室',
    'parameter': '数学或统计模型中的参数',
    'portrait': '人物肖像画',
    'prairie': '北美大草原',
    'resistance': '电阻，指物理量',
    'shutter': '相机快门',
    'stool': '无靠背单人凳',
    'wagon': '四轮运货马车',
    'windmill': '风力磨坊',
}

# 逐题审读补充常见同义说法，词典的中文措辞不同也应认可。
DEFINITION_ALIASES = {
    'apartment': ['flat'],
    'archive': ['archives'],
    'astronaut': ['cosmonaut', 'spaceman'],
    'auditorium': ['hall'],
    'backbone': ['spine'],
    'cab': ['taxi'],
    'cabinet': ['cupboard'],
    'cape': ['headland', 'promontory'],
    'carpet': ['rug', 'mat'],
    'centimetre': ['centimeter'],
    'encyclopedia': ['encyclopaedia'],
    'landlord': ['landlady'],
    'lane': ['alley'],
    'mercury': ['quicksilver'],
    'overpass': ['footbridge', 'flyover'],
    'pan': ['skillet'],
    'pantry': ['larder'],
    'pest': ['vermin'],
    'shovel': ['spade'],
    'shrub': ['bush'],
    'skeleton': ['bones'],
    'soccer': ['football'],
    'spouse': ['partner'],
    'verse': ['line'],
    'volume': ['loudness'],
    'wagon': ['waggon'],
    'wrench': ['spanner'],
}

# p 过去式，d 过去分词，s 复数，r 比较级，t 最高级，i 现在分词，3 第三人称单数。
# 同一个原词只选一种；规则变化限于双写、y 变 i、去 e。
MORPHOLOGY = [
    'arise:p', 'awake:p', 'bend:p', 'bind:p', 'bite:p', 'choose:p', 'cling:p',
    'creep:p', 'deal:p', 'dwell:p', 'feed:p', 'forbid:p', 'forgive:p', 'freeze:p',
    'grind:p', 'hide:p', 'kneel:p', 'lead:p', 'leap:p', 'lose:p', 'mislead:p',
    'misunderstand:p', 'overcome:p', 'overtake:p', 'rise:p', 'seek:p', 'shake:p', 'shrink:p',
    'slide:p', 'spin:p', 'swell:d', 'swing:p', 'undergo:p', 'undertake:p', 'undo:p',
    'uphold:p', 'weave:p', 'weep:p', 'win:p', 'withstand:p', 'burn:d', 'sew:d',
    'sow:d', 'spell:p', 'speed:p', 'tear:p', 'withdraw:p', 'analysis:s', 'appendix:s',
    'bacterium:s', 'bureau:s', 'crisis:s', 'criterion:s', 'curriculum:s', 'focus:s', 'formula:s',
    'hypothesis:s', 'index:s', 'leaf:s', 'loaf:s', 'ox:s', 'phenomenon:s', 'radius:s',
    'self:s', 'shelf:s', 'spectrum:s', 'stimulus:s', 'synthesis:s', 'thesis:s', 'wharf:s',
    'clumsy:r', 'costly:r', 'crazy:r', 'dim:r', 'fat:r', 'funny:r', 'lucky:r',
    'muddy:r', 'noisy:r', 'pretty:r', 'tidy:r', 'tiny:r', 'ugly:r', 'admit:p',
    'occur:p', 'refer:p', 'permit:p', 'prefer:p', 'reply:p', 'panic:p', 'picnic:p',
    'commit:p', 'control:p', 'equip:p', 'excel:p', 'expel:p', 'apply:p', 'comply:p',
    'notify:p',
]

# ECDICT exchange 只保留一种时，补常见英美异形；仍核对词典收录。
MORPH_VARIANTS = {
    'appendix:s': ['appendixes'],
    'bureau:s': ['bureaus'],
    'burn:d': ['burned'],
    'curriculum:s': ['curriculums'],
    'dwell:p': ['dwelled'],
    'focus:s': ['focuses'],
    'formula:s': ['formulas'],
    'index:s': ['indexes'],
    'kneel:p': ['kneeled'],
    'leap:p': ['leaped'],
    'radius:s': ['radiuses'],
    'sew:d': ['sewed'],
    'shrink:p': ['shrunk'],
    'sow:d': ['sowed'],
    'spectrum:s': ['spectrums'],
    'speed:p': ['speeded'],
    'spell:p': ['spelled'],
    'swell:d': ['swelled'],
    'wharf:s': ['wharfs'],
}

# 原词、目标词性、答案、中文限定；两边必须能在核心词表查到相应词性。
DERIVATIONS = """
weak|名词|weakness|弱点
honest|名词|honesty|诚实
safe|名词|safety|安全
responsible|名词|responsibility|责任
active|名词|activity|活动
curious|名词|curiosity|好奇心
popular|名词|popularity|受欢迎程度
refuse|名词|refusal|拒绝
approve|名词|approval|赞同
survive|名词|survival|生存
perform|名词|performance|表演
appear|名词|appearance|外貌
exist|名词|existence|存在
differ|名词|difference|差别
refer|名词|reference|参考
prefer|名词|preference|偏好
confident|名词|confidence|信心
patient|名词|patience|耐心
silent|名词|silence|寂静
convenient|名词|convenience|便利
independent|名词|independence|独立
accurate|名词|accuracy|准确性
frequent|名词|frequency|频率
nature|形容词|natural|自然的
centre|形容词|central|中央的
danger|形容词|dangerous|危险的
courage|形容词|courageous|勇敢的
ambition|形容词|ambitious|有抱负的
mystery|形容词|mysterious|神秘的
anxiety|形容词|anxious|焦虑的
enthusiasm|形容词|enthusiastic|热情的
sympathy|形容词|sympathetic|同情的
optimism|形容词|optimistic|乐观的
energy|形容词|energetic|精力充沛的
gentle|副词|gently|轻柔地
basic|副词|basically|基本上
automatic|副词|automatically|自动地
broad|动词|broaden|拓宽
analyse|名词|analysis|分析
assume|名词|assumption|假设
consume|名词|consumption|消耗
prescribe|名词|prescription|处方
subscribe|名词|subscription|订阅
describe|名词|description|描述
predict|名词|prediction|预测
restrict|名词|restriction|限制
instruct|名词|instruction|指示
construct|名词|construction|建造
produce|名词|production|生产
introduce|名词|introduction|介绍
reduce|名词|reduction|减少
induce|名词|induction|归纳
conclude|名词|conclusion|结论
extend|名词|extension|延伸
comprehend|名词|comprehension|理解
expand|名词|expansion|扩张
revise|名词|revision|修订
divide|名词|division|划分
collide|名词|collision|碰撞
persuade|名词|persuasion|说服
convert|名词|conversion|转变
express|名词|expression|表达
impress|名词|impression|印象
admit|名词|admission|承认
permit|名词|permission|许可
transmit|名词|transmission|传输
emit|名词|emission|排放
commit|名词|commitment|承诺
invest|名词|investment|投资
achieve|名词|achievement|成就
establish|名词|establishment|建立
improve|名词|improvement|改善
measure|名词|measurement|测量
arrange|名词|arrangement|安排
treat|名词|treatment|对待
require|名词|requirement|要求
retire|名词|retirement|退休
respond|名词|response|回应
maintain|名词|maintenance|维护
explain|名词|explanation|解释
pronounce|名词|pronunciation|发音
propose|名词|proposal|提议
dispose|名词|disposal|处理
remove|名词|removal|移除
deny|名词|denial|否认
depart|名词|departure|离开
recover|名词|recovery|恢复
discover|名词|discovery|发现
deliver|名词|delivery|递送
identify|名词|identification|识别
classify|名词|classification|分类
qualify|名词|qualification|资格
notify|名词|notification|通知
justify|名词|justification|正当理由
satisfy|名词|satisfaction|满意
apply|名词|application|申请
imply|名词|implication|含义
occupy|名词|occupation|职业
compete|名词|competition|竞争
compose|名词|composition|作文
repeat|名词|repetition|重复
recognize|名词|recognition|认出
perceive|名词|perception|感知
conceive|名词|conception|概念
absorb|名词|absorption|吸收
solve|名词|solution|解决办法
resolve|名词|resolution|决心
benefit|形容词|beneficial|有益的
finance|形容词|financial|财务的
economy|形容词|economic|与经济体系相关的
industry|形容词|industrial|工业的
commerce|形容词|commercial|商业的
profession|形容词|professional|职业的
origin|形容词|original|最初的
option|形容词|optional|可选择的
accident|形容词|accidental|意外的
occasion|形容词|occasional|偶尔的
emotion|形容词|emotional|情感的
intellect|形容词|intellectual|智力的
structure|形容词|structural|结构的
logic|形容词|logical|合乎逻辑的
psychology|形容词|psychological|心理的
geography|形容词|geographical,geographic|地理的
history|形容词|historical|历史的
theory|形容词|theoretical|理论的
technique|形容词|technical|技术的
mathematics|形容词|mathematical|数学的
""".strip().splitlines()

# 反义关系手工审定，语境用中文锁定，ECDICT 核对双方词性和中文义项。
ANTONYMS = """
awake|asleep|形容词|是否处于睡眠状态
artificial|natural|形容词|是否天然形成
certain|uncertain|形容词|是否确定
common|rare,uncommon,unusual|形容词|是否经常见到
dull|sharp|形容词|刀刃是否锋利
external|internal|形容词|物体内外
foolish|wise,sensible|形容词|判断是否明智
horizontal|vertical|形容词|线条方向
humble|proud,arrogant|形容词|态度谦逊与否
incomplete|complete|形容词|资料是否齐全
inferior|superior|形容词|品质高低
invisible|visible|形容词|能否被看见
legal|illegal,unlawful|形容词|是否符合法律
loose|tight|形容词|绳结松紧
major|minor|形容词|事情重要程度
maximum|minimum|名词|数值上限与下限
negative|positive|形容词|数值的正负
optimistic|pessimistic|形容词|对未来的预期
ordinary|extraordinary,unusual|形容词|是否不同寻常
permanent|temporary|形容词|持续时间是否长久
private|public|形容词|场所是否对大家开放
rough|smooth|形容词|表面是否平滑
rural|urban|形容词|乡村与城市
solid|liquid|形容词|物态的固态与液态
transparent|opaque|形容词|材料是否透光
valuable|worthless|形容词|是否有价值
voluntary|compulsory,mandatory|形容词|参加是否出于自愿
wild|tame,domesticated|形容词|动物是否被驯养
arrival|departure|名词|到达与离开
entrance|exit|名词|建筑物的进出口
advantage|disadvantage|名词|有利条件与不利条件
expand|contract,shrink|动词|体积的胀缩
export|import|动词|商品运入与运出
advance|retreat|动词|运动方向的前进与后退
abundant|scarce|形容词|资源充裕程度
abstract|concrete|形容词|概念抽象与具体
ambiguous|explicit,clear,unambiguous|形容词|说明是否清晰明确
approximate|exact,precise|形容词|数值是否精确
arrogant|modest,humble|形容词|对自己能力的态度
awkward|graceful|形容词|动作是否优雅
barren|fertile|形容词|土壤是否肥沃
collective|individual|形容词|活动以集体还是个人为单位
conscious|unconscious|形容词|是否清醒有知觉
deliberate|accidental,unintentional|形容词|失误是否出于有意
diverse|uniform|形容词|样式是否多样
dynamic|static|形容词|图像是否处于变化中
fragile|sturdy,robust,durable|形容词|物品是否结实耐用
initial|final|形容词|过程的最初与最后
intensive|extensive|形容词|农业经营的集约与粗放
objective|subjective|形容词|评价是否受个人感受影响
passive|active|形容词|学习态度被动与主动
reluctant|willing,eager|形容词|是否愿意参加
rigid|flexible|形容词|安排能否灵活调整
sophisticated|naive|形容词|想法成熟与天真
trivial|significant,important|形容词|问题重要程度
universal|particular,specific|形容词|适用范围普遍与特定
valid|invalid|形容词|票证是否有效
virtue|vice|名词|品行中的优点与缺点
profit|loss|名词|经营结果的盈利与亏损
attract|repel|动词|磁极相吸与相斥
ascend|descend|动词|运动方向上升与下降
complicate|simplify|动词|使问题复杂与简单
conceal|reveal,disclose|动词|信息隐藏与揭示
compress|expand|动词|体积压缩与扩张
enlarge|reduce,shrink|动词|尺寸放大与缩小
multiply|divide|动词|算术中的乘与除
retain|discard|动词|物品保留与丢弃
strengthen|weaken|动词|强度加强与减弱
widen|narrow|动词|道路拓宽与收窄
""".strip().splitlines()

# 句子原创；中文限定和语法共同约束可接受答案，避免无穷的近义填法。
CLOZES = """
accountant|名词|会计师|My aunt works as an （　） and checks company accounts.
architect|名词|建筑师|The （　） designed our new school building.
balcony|名词|阳台|We keep flower pots on our apartment's （　）.
barber,hairdresser|名词|理发师|The （　） cut my hair before the interview.
battery|名词|电池，单数|My clock needs a new （　）.
beard|名词|胡须|My grandfather has a long white （　） on his chin.
blanket|名词|毯子|She covered her knees with a warm （　）.
algebra|名词|代数学|In （　）, letters can stand for unknown numbers.
cabinet|名词|储物柜，单数|The cups are stored in a kitchen （　）.
calculator|名词|计算器|Use a （　） to multiply these large numbers.
calendar|名词|日历|Mark your birthday on the wall （　）.
carpenter|名词|木匠|The （　） made a wooden table for us.
fraction|名词|分数，单数|One half is a （　） with a numerator of one.
champion|名词|冠军|She won the final and became the tennis （　）.
compass|名词|指南针|A （　） can help us find north in the forest.
continent|名词|洲，大陆，单数|Asia is the largest （　） on Earth.
curtain|名词|窗帘，单数|Pull back the （　） to let sunlight in.
cushion,pillow|名词|靠垫，单数|Put a soft （　） behind your back on the sofa.
deadline|名词|截止日期|The （　） for handing in the project is Friday.
dentist|名词|牙医|The （　） checked my teeth this morning.
diameter|名词|直径|The （　） of this circle passes through its centre.
drawer|名词|抽屉，单数|I keep my socks in the bottom （　）.
geometry|名词|几何学|We study angles and shapes in （　）.
envelope|名词|信封|Put the letter inside an （　） before mailing it.
ferry,ferryboat|名词|渡船|We crossed the river by （　）.
rhythm,beat|名词|节奏|The drummer keeps a steady （　） throughout the song.
fountain|名词|喷泉|Water sprays from the （　） in the park.
garage|名词|车库|We park our car in the （　） beside the house.
helmet|名词|头盔|Wear a （　） to protect your head while cycling.
longitude|名词|经度|We measured how far east we were by checking our （　）.
leisure|名词|闲暇|She enjoys painting in her （　） time.
librarian|名词|图书管理员|Ask the （　） where to find books about birds.
microscope|名词|显微镜|We examined tiny cells through a （　）.
napkin,serviette|名词|餐巾，单数|He wiped his mouth with a paper （　）.
nursery,daycare|名词|托儿所|The parents leave their toddler at a （　） each morning.
orchard|名词|果园|We picked apples in the （　）.
oxygen|名词|氧气|Fish take （　） from water through their gills.
peninsula|名词|半岛|The map shows a （　） surrounded by sea on three sides.
fossil|名词|化石，单数|We found a （　） of an ancient shell in the rock.
recipe|名词|食谱，单数|Follow this （　） to bake a chocolate cake.
rectangle|名词|长方形|A （　） has four right angles and two pairs of equal sides.
refrigerator,fridge|名词|冰箱|Put the milk in the （　） to keep it cold.
reservation,booking|名词|预订|I made a hotel （　） for two nights.
sculpture|名词|雕塑，单数|The artist carved an abstract （　） from a block of marble.
shampoo|名词|洗发水|Use （　） to wash your hair.
skeleton|名词|骨骼，骨架|The museum displays the （　） of a huge dinosaur.
thermometer|名词|温度计|Use a （　） to measure the water temperature.
triangle|名词|三角形|A （　） has exactly three sides.
tuition|名词|学费|She saved money to pay her university （　） fees.
vacuum|名词|真空|Sound cannot travel through a perfect （　）.
vegetation|名词|植被|Thick （　） covers the hillside after the rainy season.
vowel|名词|元音字母，单数|The letter A is a （　）.
vitamin|名词|维生素，单数|This fruit contains a lot of （　） C.
waist|名词|腰|This belt fits comfortably around my （　）.
wrinkle,crease|名词|褶皱，单数|There is a small （　） in the shirt collar.
altitude|名词|海拔，高度|The plane reached an （　） of ten thousand metres.
astronomy|名词|天文学|My interest in stars led me to study （　）.
attorney,lawyer,solicitor,barrister,counsel|名词|律师|The （　） checked the contract for her.
auction|名词|拍卖，单数|The old painting was sold at an （　）.
biography|名词|传记，单数|I read a （　） about a famous painter's life.
budget|名词|预算|Our monthly （　） sets a limit on spending.
calorie|名词|卡路里，单数|A （　） is a unit used to measure food energy.
carbon|名词|碳|Both diamonds and graphite are made of （　）.
certificate|名词|证书，单数|She received a （　） after completing the course.
circumference|名词|周长|Measure the circle's （　） with a flexible tape.
clay|名词|黏土|The potter shaped a bowl from wet （　）.
collar|名词|领子|The shirt's （　） sits neatly around his neck.
colleague|名词|同事|My （　） and I work in the same office.
conjunction|名词|连词，单数|The word 'and' is a （　） joining two clauses.
curriculum|名词|课程体系|The new school （　） includes music and art.
decimal|名词|小数|Write three quarters as a （　）.
dialect|名词|方言|This local （　） sounds different from the standard language.
drought|名词|干旱|Months without rain caused a severe （　）.
economics|名词|经济学|She studies prices and markets in （　）.
editor|名词|编辑|The （　） checks every article before publication.
entrepreneur|名词|创业者|The young （　） started a business selling handmade bags.
equation|名词|方程式|Solve this （　） to find the value of x.
era|名词|时代，单数|The new invention marked the start of an （　）.
evaporation|名词|蒸发|Heat speeds up the （　） of water from the dish.
fatigue,exhaustion,tiredness,weariness|名词|疲劳|After walking all day, she felt great （　）.
formula|名词|公式|Use this （　） to calculate the area of a circle.
friction|名词|摩擦力|Oil reduces （　） between the moving metal parts.
gene|名词|基因，单数|A （　） carries instructions that can affect an inherited trait.
gesture|名词|手势，单数|She made a （　） with her hand to show the direction.
gravity|名词|重力|Objects fall toward Earth because of （　）.
habitat|名词|栖息地|The wetland is an important （　） for birds.
harbour,harbor,port|名词|港口|The fishing boat returned to the （　） before the storm.
hedge|名词|树篱，单数|A thick （　） of bushes borders the garden.
hemisphere|名词|半球，单数|The equator divides Earth into two halves, each called a （　）.
horizon|名词|地平线|The sun slowly disappeared below the （　）.
hydrogen|名词|氢|Water contains （　） and oxygen.
inertia|名词|惯性|Because of （　）, a rolling ball tends to keep moving.
latitude|名词|纬度|The equator has a （　） of zero degrees.
mammal|名词|哺乳动物|A whale is a （　） that feeds its young with milk.
metaphor|名词|隐喻，单数|Calling time a river is a （　）.
moisture,water|名词|水分|Plants absorb （　） from the soil through their roots.
molecule|名词|分子，单数|A water （　） contains two hydrogen atoms and one oxygen atom.
mortgage|名词|住房抵押贷款|We repay our home （　） every month.
optics|名词|光学|In （　）, we study how light behaves.
orbit|名词|轨道|The satellite follows an （　） around Earth.
parasite|名词|寄生生物，单数|A （　） lives on or inside another organism and obtains nutrients from it.
pedestrian|名词|行人|The （　） waited at the crossing before walking across.
pension|名词|养老金|She receives a monthly （　） after retirement.
perimeter|名词|周长|Add all four side lengths to find the rectangle's （　）.
pharmacy,chemist,drugstore|名词|药房|We collected the medicine from the （　）.
philosophy|名词|哲学|She studies questions about knowledge and existence in （　）.
preposition|名词|介词，单数|The word 'under' is a （　） showing position.
proportion,percentage,fraction|名词|比例|What （　） of the class chose music rather than art?
psychology|名词|心理学|In （　）, we study human thoughts and behaviour.
reptile|名词|爬行动物|A lizard is a （　） with dry, scaly skin.
saliva|名词|唾液|The mouth produces （　） to moisten food.
satellite|名词|卫星，单数|The moon is a natural （　） of Earth.
sociology|名词|社会学|In （　）, we study patterns of human social life.
spectrum|名词|光谱|A prism splits white light into a （　） of colours.
stimulus|名词|刺激，单数|The plant's growth toward light is a response to that （　）.
telescope|名词|望远镜|We looked at the moon through a （　）.
textile|名词|纺织品，单数|Cotton cloth is a common （　）.
velocity|名词|速度，含方向|In physics, （　） describes speed together with direction.
warehouse,storehouse,depot|名词|仓库|The goods are stored in a large （　） before delivery.
""".strip().splitlines()


def senses(row):
    """只取有明确词性的一般中文义项；领域行、英文和括注全部不进题面。"""
    result = defaultdict(set)
    text = row["translation"].replace(r"\n", "\n")
    for line in text.splitlines():
        matched = re.match(r"^(n|pl|a|adj|adv|vt|vi|v)\.\s*(.*)$", line)
        if not matched:
            continue
        pos, body = matched.groups()
        for sense in re.split(r"[,，;；]", body):
            sense = sense.strip()
            if re.fullmatch(r"[\u4e00-\u9fff]{1,9}", sense):
                result[POS[pos]].add(sense)
    return result


def load(path):
    rows = {}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not {"word", "translation", "tag", "exchange"} <= set(reader.fieldnames or ()):
            raise ValueError("ECDICT CSV 缺少必需字段")
        for row in reader:
            word = row["word"]
            if re.fullmatch(r"[a-z]+", word):
                if word in rows:
                    raise ValueError(f"ECDICT 单词重复：{word}")
                rows[word] = row
    return rows


def require(rows, word, pos=""):
    if word not in rows:
        raise ValueError(f"词不在 ECDICT 词表：{word}")
    if pos and pos not in {POS[p] for p in re.findall(r"(?:^|\\n)(n|pl|a|adj|adv|vt|vi|v)\.\s", rows[word]["translation"])}:
        raise ValueError(f"ECDICT 词性不符：{word} / {pos}")
    return rows[word]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ecdict", type=Path, required=True)
    parser.add_argument("--license", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    notice = args.license.read_text(encoding="utf-8")
    if "MIT License" not in notice or "Permission is hereby granted" not in notice:
        raise ValueError("需要 ECDICT 的完整 MIT LICENSE")
    rows = load(args.ecdict)
    reverse = defaultdict(set)
    for word, row in sorted(rows.items()):
        if CORE.intersection(row["tag"].split()) and len(word) >= 3:
            for pos, meanings in senses(row).items():
                for meaning in meanings:
                    reverse[pos, meaning].add(word)
    questions = []

    def add(key, tested, question, names, pos="", meaning="", example="", explanation="", main_tier=None):
        row = require(rows, tested)
        # 词形按原型分档，其他题按被考的目标词分档。
        if not CORE.intersection(row["tag"].split()):
            raise ValueError(f"被考词或其原型不在核心词表：{tested}")
        basic = bool(BASIC.intersection(row["tag"].split()))
        if main_tier is not None:
            basic = main_tier
        questions.append([key, basic, question, list(names), pos, meaning, example, explanation])

    for value in sorted(DEFINITIONS):
        word, meaning = value.split("|")
        row = require(rows, word, "名词")
        tags = set(row["tag"].split())
        if "zk" in tags:
            raise ValueError(f"释义题不能新增中考词：{word}")
        if word in DEFINITION_TIERS and not tags.intersection({"cet6", "ky"}):
            raise ValueError(f"替换释义题必须使用六级或考研词：{word}")
        names = sorted(reverse["名词", meaning])
        if word not in names or len(names) > 3:
            raise ValueError(f"释义不在源词条或超过三个核心词：{word} / {meaning} / {names}")
        names = list(dict.fromkeys([word, *[name for name in names if name != word], *DEFINITION_ALIASES.get(word, [])]))
        for name in names:
            require(rows, name, "名词")
        if len(names) > 3:
            raise ValueError(f"补齐常见答案后仍有歧义：{word} / {names}")
        label = DEFINITION_LABELS.get(word, meaning)
        add(f"def-{word}", word, f"根据中文释义写出一个英文单词：\n名词：{label}。", names,
            explanation=f"这里考查名词义项“{label}”，可写 {'、'.join(names)}。", main_tier=DEFINITION_TIERS.get(word))

    labels = {"p": "过去式", "d": "过去分词", "s": "复数", "r": "比较级", "t": "最高级", "i": "现在分词", "3": "第三人称单数形式"}
    seen_stems = set()
    for value in sorted(MORPHOLOGY):
        word, code = value.split(":")
        if word in seen_stems:
            raise ValueError(f"同一原词只能考一种词形：{word}")
        seen_stems.add(word)
        row = require(rows, word)
        exchange = dict(part.split(":", 1) for part in row["exchange"].split("/") if ":" in part)
        names = list(dict.fromkeys([*exchange.get(code, "").split(","), *MORPH_VARIANTS.get(value, [])]))
        for name in names:
            require(rows, name)
        if any(not re.fullmatch(r"[a-z]{3,}", name) or name == word for name in names):
            raise ValueError(f"无效或未变化的词形：{word}:{code} / {names}")
        label = labels[code]
        extra = "（表示播种）" if word == "sow" else ""
        add(f"form-{word}-{code}", word, f"写出「{word}」的{label}{extra}。", names,
            explanation=f"{word} 的{label}是 {' / '.join(names)}。")

    for value in sorted(DERIVATIONS):
        original, pos, answers, meaning = value.split("|")
        original_row = require(rows, original)
        if not CORE.intersection(original_row["tag"].split()):
            raise ValueError(f"原词不在四六级和考研范围：{original}")
        names = answers.split(",")
        for name in names:
            require(rows, name, pos)
        add(f"derive-{original}-{names[0]}", names[0],
            f"词性转换：写出「{original}」的{pos}形式。\n（{meaning}）", names,
            explanation=f"{original} 对应的{pos}形式是 {'、'.join(names)}，这里表示{meaning}。")

    for value in sorted(ANTONYMS):
        original, answers, pos, context = value.split("|")
        original_row = require(rows, original)
        if not CORE.intersection(original_row["tag"].split()):
            raise ValueError(f"原词不在四六级和考研范围：{original}")
        names = answers.split(",")
        for name in names:
            require(rows, name, pos)
        add(f"ant-{original}", names[0], f"写出「{original}」的反义词。\n（{pos}；{context}）", names,
            explanation=f"在{context}的语境下，{original} 的反义词可写 {'、'.join(names)}。")

    for value in sorted(CLOZES):
        answers, pos, meaning, sentence = value.split("|")
        names = answers.split(",")
        for name in names:
            require(rows, name, pos)
        if len(re.findall(r"[A-Za-z]+", sentence)) > 15:
            raise ValueError(f"填空句超过十五词：{sentence}")
        add(f"cloze-{names[0]}", names[0], f"用一个英文单词填空：\n{sentence}\n（{meaning}）", names, pos=pos,
            explanation=f"{'、'.join(names)} 在这里表示{meaning}，符合句中的用法。")

    questions.sort(key=lambda record: record[0])
    if len({q[0] for q in questions}) != len(questions):
        raise ValueError("生成题号重复")
    if len({q[2] for q in questions}) != len(questions):
        raise ValueError("生成题面重复")
    data = {"source": {"name": "ECDICT", "url": SOURCE,
                       "sha256": hashlib.sha256(args.ecdict.read_bytes()).hexdigest(),
                       "fields": ["translation", "tag", "exchange"], "scope": ["cet4", "cet6", "ky"],
                       "definition_exclude_tags": ["zk"],
                       "notes": "中文释义及词形取自 ECDICT；词性转换、反义关系经人工选择并以 ECDICT 核对；例句和解析原创。"},
            "license": {"spdx": "MIT", "notice": notice.strip(), "original_text": "例句和解析随本仓库按 MIT 提供。"},
            "questions": questions}
    encoded = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    if len(encoded.encode("utf-8")) > 1_000_000:
        raise ValueError("英文生成题库超过 1 MB")
    args.output.write_text(encoded, encoding="utf-8")
    print(f"生成 {len(questions)} 道；大众 {sum(q[1] for q in questions)}，小众 {sum(not q[1] for q in questions)}")


if __name__ == "__main__":
    main()
