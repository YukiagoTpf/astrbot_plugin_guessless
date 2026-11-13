# AGENTS.md

AstrBot 群聊猜题插件（Guessless）。功能和用法见 [README](README.md) 与 [docs/](docs/)，这里只写改代码需要知道的事。

## 代码结构

```
main.py               插件入口 GuessPlugin：指令、一道题的生命周期（Round）、额度、排行榜和整点播报、暂停、重启收尾
metadata.yaml         AstrBot 插件元数据（插件市场也读它：short_desc、tags）
CHANGELOG.md          更新日志，插件市场的「更新日志」页显示它
logo.png              插件市场的图标，256×256
_conf_schema.json     插件配置项（AstrBot WebUI 读这个）
requirements.txt      运行依赖，AstrBot 加载插件时自动安装
pyproject.toml        开发工具配置（ruff）
core/
  bank.py             题库：各题型的构建（Bank._build_*）、定期刷新和重建判断（_settings / _stale）、出题（pick）
  entries.py          题目条目和提示（*_entry、country_basics、scrub），纯函数、不联网
  blind/              盲猜系列，一个题型一个模块（对比的列、一行怎么算、题库怎么建），见下文「盲猜系列」
    base.py           盲猜的底层：题型规格 Blind、对比表各格的算法（num、ratio、tiered、away……）、条目 entry
    __init__.py       BLIND：登记了哪些盲猜题型
    jpword.py         猜日语：quiz 文字题分支，不画对比表
    wordle.py         英文Wordle：本地词表、重复字母计分、自定义字母棋盘
    chengyu.py        猜成语：本地四字成语、重复字计分、自定义汉字棋盘、字形和提示防泄露
    enword.py         猜英文：quiz 分支，完整拼词判定及词形防泄露检查
  sources.py          外部数据源：Wikidata、中文维基浏览量、B 站、AniList（含猜声优的 staff 名单）、AnimeThemes、IGDB、Steam、PokeAPI、
                      Gundam Wiki、vtbs.moe；HttpError
  geomatch.py         猜地点匹配、猜日本地点匹配：按地区挑四国 / 四县（GROUPS）、出题、线索、认「BADC」这样的答案；
                      猜地点匹配是两国各两张（PAIRS、pick_pair、parse_pair，每人答一次）；街景用猜地点、猜日本地点的题库
  geo.py              地理：GeoNames、Wikidata 国家资料、日本市町村和中国地级行政区（含北上广深的区）、街景取景、Natural Earth 轮廓、盲猜地点的地区划分和方位，以及各种版本常量
  cards.py            卡片渲染（Pillow）：九宫格、街景三联、猜声优的一排角色、国家轮廓（答案卡带邻国底图）、世界地图、对比表、听歌进度条、
                      猜群友的头像棋盘、猜K线的 K 线图、整点排行榜长图
  members.py          猜群友：挑人、拉 QQ 头像、每段露哪几格、认群友说的是谁（Roster）
  audio.py            听歌猜番的音频：ffmpeg 解码、切片段
  answer.py           答案归一化和判定
  commands.py         认 @bot 说得不准的指令：去空格和客套、按关键词认题型、和指令比像不像；局中的「提示」；中文数字
  store.py            数据目录里 JSON 文件的读写
  hint_data.py        手写的特产、名胜、美食提示
  cn_data.py          手写的盲猜中国地点资料：城市线级、2025 年 GDP、拆成区出题的市（北上广深）
  jpword_data.py      手写的猜日语题库：题面、答案说法、线索和解析
  jpword_gen.json     JLPT / JMdict 派生的猜日语题库：来源、许可、读音、变形和中译日
  wordle_gen.json     ECDICT 派生的英文Wordle答案池和可猜词表（MIT，含来源和完整许可）
  chengyu_gen.json    chinese-xinhua / THUOCL 派生的成语答案池和可猜表（MIT，含来源、筛选统计和完整许可）
  enword_data.py      手写的猜英文题库：中文猜词、英文填空、反义近义词
  enword_gen.json     ECDICT 派生的猜英文题库：四六级和考研词汇，含来源和完整 MIT 许可
  sake_data.py        手写的盲猜日本酒资料：銘柄的简称、さけのわ风味标签的中文
  mecha_data.py       手写的 Gundam Wiki 资料中译（世界观、阵营、机型、机械设计）和机体外号
  vtuber_data.py      手写的猜Vtuber不出题名单（品牌号、搬运号、成人向、有争议的）
  teacher.py          猜老师：名单（Wikidata、日文维基的信息框）、FANZA 头像的地址、条目
  seiyuu.py           猜声优：名单（AniList 的 staff，按日文名对上 Bangumi 的人物）、能出的角色、条目、每道题挑哪四个角色、按什么先后露
  wow.py              魔兽世界：猜魔兽地图的地图资料（wago.tools 的 DB2 CSV）、截图（warcraft.wiki.gg）、条目；盲猜魔兽Boss的首领资料
  kline.py            猜K线：股票池（沪深300、日经225、标普500、纳指100）、出题时拉一段日 K 并复权（A 股按新浪的因子，日股美股用
                      Yahoo 的 adjclose）、揭晓的统计、认群友说的涨跌幅
  lines.py            所有发到群里的文案
docs/                 用户文档
tools/generate_enword.py  离线生成猜英文题库，读取本地 ECDICT CSV（不进插件包）
tools/generate_wordle.py  离线生成英文Wordle词表，读取本地 ECDICT CSV（不进插件包）
tools/generate_chengyu.py 离线生成猜成语题库，读取本地 chinese-xinhua JSON / THUOCL 词频（不进插件包）
```

`main.py`、`metadata.yaml`、`_conf_schema.json`、`requirements.txt` 必须在根目录，AstrBot 只在那里找；其余代码放 `core/`。

## 验证改动

没有单元测试。每次改完至少：

```bash
uvx ruff check .  # 规则见 pyproject.toml
```

代码依赖 AstrBot 运行环境（`astrbot.api`、Pillow、aiohttp），要跑起来得在 AstrBot 里，比如官方 Docker 镜像。做法是把插件拷进容器的临时目录，用 `sys.path.insert` 导入后直接调函数：

```bash
docker cp . <容器>:/tmp/dev/astrbot_plugin_guessless
docker exec -w /AstrBot <容器> python3 -c '
import sys; sys.path.insert(0, "/tmp/dev")
from astrbot_plugin_guessless.core import answer
print(answer.is_correct("旷野之息", answer.candidates(["塞尔达传说 旷野之息"])))  # True
'
```

- 改了题库构建或提示：把线上数据目录里的缓存（`geo_*.json`、`country_shapes.json` 等）拷到临时目录，`Bank(临时目录, 配置)` 调对应的 `_build_*`（盲猜是 `BLIND[cat].build(bank)`），检查条目和提示。不要在线上数据目录里试。
- 改了提示内容（`core/hint_data.py`、`entries.scrub`）：重建地点、国家、日本地点题库后，检查每条提示里没有答案的中文名（`answer.norm` 后比较），也没有残留的 `□`。
- 改了卡片渲染：渲染样图存成 PNG 打开看，至少覆盖九宫格、宝可梦、街景，国家的出题卡和答案卡都要看，挑邻国多的（越南、德国）、被包围的（南非）、跨 180° 经线的（俄罗斯、斐济）。
- 改了听歌猜番：把 `audio.clip` 切出来的 WAV 存下来听，至少听一首 OP 和一首 ED，开头不能是静音。
- 改了 `main.py`：没有完整的事件环境，可以用 `GuessPlugin.__new__` 建对象、手动塞属性、假的 `context` 来测单个方法。

## 约定

- 注释和文案用中文，注释短、讲为什么；对群发的话只写在 `core/lines.py`。
- 持久化数据写插件数据目录（`StarTools.get_data_dir`），用 `store.save_json`（先写临时文件再替换）。不要写进插件目录。
- 外部请求走 `Bank._get` / `_post` / `_request`（带重试）。报错用 `media.HttpError`，它不带 URL，因为有些 key 在 URL 参数里，不能进日志。
- 影响题库内容的规则改了，要改对应的版本常量，否则线上旧题库不会重建：

  | 改了什么 | 改哪个 |
  |---|---|
  | 题目条目格式、通用选题规则 | `bank.BANK_VERSION` |
  | `hint_data.py`、`entries.scrub`、地点和国家的提示规则、报距离用的位置（`geo.towns` / `centroid`、`places`） | `geo.HINTS_VERSION` |
  | 动画、游戏的提示规则（标签筛选、提示顺序） | `entries.WORK_HINTS_VERSION` |
  | 只有游戏才有的提示（平台：`PLATFORMS`、`MINOR_PLATFORMS`） | `entries.GAME_VERSION` |
  | 宝可梦条目格式（盲猜宝可梦的 `attrs`） | `entries.POKEMON_VERSION` |
  | 萝卜的选题、条目格式、`mecha_data.py` | `entries.MECHA_VERSION` |
  | Vtuber 的选题、条目格式、`vtuber_data.py` | `entries.VTUBER_VERSION` |
  | 猜K线的选股（`kline.BLOCK_*`、`NUCLEAR`、`JP_BLOCK_*`、`US_BLOCK_*`）、条目格式 | `kline.VERSION` |
  | 猜老师的选人（`MIN_DEBUT_*`、`NO_IMAGE`）、资料、提示、叫法、拼头像地址的规则 | `teacher.VERSION` |
  | 猜声优的选人、选角色（`BLOCK_WORKS`、`MIN_CHARS`）、资料、提示、叫法 | `seiyuu.VERSION` |
  | 猜魔兽地图的选地图（`CITIES`）、挑图规则（`BAD_NAME` / `BAD_CAT`、尺寸）、提示 | `wow.VERSION` |
  | 盲猜地点的条目格式、选题分档、地区划分（`geo.REGIONS`） | `blind/geodle.py` 的 `VERSION` |
  | 盲猜日本地点的选题、条目格式、提示规则（`LANDMARK_CLASS` / `LANDMARK_BLOCK`） | `blind/jpgeodle.py` 的 `VERSION` |
  | 盲猜中国地点的选题、条目格式、提示规则（`CN_SIGHT_BLOCK`、`geo.CN_PROVINCES`）、`cn_data.py` | `blind/cngeodle.py` 的 `VERSION` |
  | 盲猜日本酒的选题（`AREA_TOP`、`NO_ANSWER`）、条目格式、提示规则、`sake_data.py` | `blind/sakedle.py` 的 `VERSION` |
  | 盲猜魔兽Boss的选题（`NOT_ONE`、`MAIN_UNTIL`）、条目格式、提示、叫法规则 | `blind/bossdle.py` 的 `VERSION` |
  | 盲猜以撒道具的选题（`NO_ANSWER`、`MAIN_QUALITY`）、条目格式、提示、叫法规则 | `blind/isaacdle.py` 的 `VERSION` |
  | 猜日语的手写题库、答案说法、提示或选题规则（`jpword_data.py`） | `blind/jpword.py` 的 `VERSION` |
  | 猜英文的手写题库、答案说法、提示或选题规则（`enword_data.py`） | `blind/enword.py` 的 `VERSION` |
  | 英文Wordle的选题、词表、条目格式、提示规则 | `blind/wordle.py` 的 `VERSION` |
  | 猜成语的选题、词表、字形检查、条目格式、提示规则 | `blind/chengyu.py` 的 `VERSION` |
  | 听歌猜番的选曲规则（`sources.anime_themes`） | `bank.ANISONG_VERSION` |
  | 街景取景规则（全景缓存作废重找） | `geo.SAMPLING` |
  | 国界处理（`country_shapes.json` 重新生成） | `geo.OUTLINE_VERSION` |

- 新配置项写进 `_conf_schema.json`（密钥加 `"secret": true`），并更新 `docs/configuration.md`。
- 加了题型，在 `core/commands.py` 的 `KEYWORDS` 里给它加关键词（盲猜的在 `BLIND_OF` 里对上同类的看图题型），不然 @bot 说得不准时认不出来。
- 文档：README 只放概要和索引，细节写进 `docs/` 对应的文件；行为改了要同步改文档。

## 盲猜系列

不给图、每猜一个回一行对比的题型。开题和排行榜指令、出题、判对错、对比表、答案卡、题库定期重建都按 `blind.Blind` 接上，
`main.py`、`bank.py` 里不写具体题型。加一个新的：

1. 在 `core/blind/` 新建模块（照着 `cngeodle.py` 写）：`COLUMNS`、`CLUES`、`row(猜的 attrs, 答案的 attrs)`，
   自己建题库的再写 `build(bank)`：用 `bank.http()`、`bank.views()` 查资料，条目用 `base.entry` 拼，
   建好调 `bank.commit(cat, entries, places=...)`（`places` 是所有能猜的，键和条目的 `group` 一样，每项要有 `attrs`）。
   借别的题库的（盲猜宝可梦）不写 `build`，在那个题库的条目里放 `attrs`，`Blind` 里写 `base`。
   最后定义 `MODE = Blind(...)`，规则版本放进 `settings`。
2. 加进 `blind/__init__.py` 的 `BLIND`。
3. `core/lines.py` 加四句：`{cat}_empty`、`card_note_{cat}`、`timeout_{cat}`、`board_empty_{cat}`。
4. 要和其他盲猜不一样的限时、次数，就在 `_conf_schema.json` 加 `{cat}_round_seconds`、`{cat}_max_guesses`，
   没有就用 `blind_round_seconds`、`max_guesses`。
5. 文档：README 的题型列表，`docs/usage.md`（指令、对比规则）、`docs/question-banks.md`（题库怎么来的）各加一节。

`Blind.board` 可选自定义棋盘，签名 `(entry, rows, total, reveal)`，其中 rows 是按时间排列的 `(title, row结果)`；
`Blind.fits(guess_attrs, answer_attrs)` 返回 False 时当闲聊；`Blind.rows=0` 默认无全群总次数上限，非零时读 `{cat}_rows`（1–10），
填满仍未猜中按超时结算，文案用 `{cat}_rows_used`。自定义棋盘猜错文案用 `{cat}_wrong`。
`Blind.default_guesses=None` 默认沿用共享次数，非空时作为该题型未加载配置的每人次数默认值；新字段都不影响现有题型。
英文Wordle 用这几个字段；词表由 `tools/generate_wordle.py` 确定性生成，只在线下运行，CSV 用 `python3 -I` 读取。

`Blind.quiz` 是不做对比的文字题分支（猜日语、猜英文）：传入 `(entry, reveal=False)` 画题面，`reveal=True` 画答案和解析。
条目用 `base.entry(..., attrs=None, ...)` 拼，不放 `attrs`，题面另存 `question`；`_load` 保存两张图并从零条提示开始，
猜错只计次、不回表，进度按已给的线索数算。仍按 BLIND 自动接指令、榜单和题库刷新；帮助里放「还有」。
猜英文在 `BY_KEY` 中按完整单词判对（`exact=True`），提示防泄露按英文词边界检查常见词形变化，不能按子串比较。
单字答案不能给首字提示，手写线索要检查所有 `names`（包括单假名）归一化后不出现在任何提示里。

AstrBot 按模块和函数名认指令的处理函数，盲猜的是 `main._blind_commands` 生成的 `guess_{cat}`、`{cat}_board`，
`cat` 定了就别改，改了 WebUI 里对这条指令的设置会丢。

## 红线

- 仓库里不能有密钥、QQ 号、群号、本机路径等个人信息。key 一律从插件配置读。
- 题目、提示、文案不涉政：不放政治人物、议会和政府机关、战争、核相关的内容。不放露骨的色情：裸露、性行为的图和文字都不行（猜老师只用只拍脸的官方头像，三围、作品内容不写）。
- 猜老师只出确定成年后出道的（`teacher.MIN_DEBUT_AGE`，出道那年减出生那年满 19）和 2000 年以后出道的（`MIN_DEBUT_YEAR`，之前的头像是写真扫描，有露出的）；头像不合适的加进 `NO_IMAGE`，只能加不能删。
- 不要改动这些处理方式：`geo.EXCLUDE` 里的地区不出题；国界只用 Natural Earth 的 CHN 视角数据；邻国底图按 `FCLASS_CN` 合并（`geo.MERGED_FCLASS`）、`geo.SKIP_FEATURES` 不画。
- 提示里不能出现答案的国名、县名、首都名，也不能用「法式」「日元」这种拿国名首字的说法。
- 听歌猜番不用 AnimeThemes 标了 NSFW 的版本；音频只在出题时现下，不存盘。
- 萝卜的阵营不翻 G 高达里以现实国家命名的（新香港、新中国……），对比表和提示里显示「—」。
- 猜Vtuber不出 `vtuber_data.py` 里的账号；名单要扩只能往里加，不要删掉成人向和有争议的那几项。
- 猜群友的头像只在内存里出图，不存盘；答案只公布群名片 / 昵称，不发 QQ 号。
- 猜声优不出成人向（AniList 的 `isAdult`）、Ecchi 作品里的角色；舰船、国家拟人的作品（`seiyuu.BLOCK_WORKS`）不出，只能加不能删。
- 猜地点匹配不出以色列、巴勒斯坦（`geomatch.SKIP`，两个放一起对着认容易聊到政治上），只能加不能删。
- 盲猜中国地点不出、不认三沙市（`geo.CN_EXCLUDE`），港澳台不在里面；名胜过滤（`blind/cngeodle.py` 的 `CN_SIGHT_BLOCK`：红色景点、陵墓、领导人、战争……）只能加不能删。
- 盲猜日本地点不出、不认北方四岛上的村（`geo.disputed`），不出因核事故、核设施、美军基地出名的町村（`blind/jpgeodle.py` 的 `NO_ANSWER`，只能加不能删）；名胜过滤（`blind/jpgeodle.py` 的 `LANDMARK_BLOCK`：和平公园、原爆、靖国和护国神社、端岛、硫磺岛、领土争议的岛、皇室、陵墓、事件……）只能加不能删。
- 盲猜日本酒不出名字带「皇国」「尊皇」「旭日」「戦勝」「軍」这些的銘柄（`blind/sakedle.py` 的 `NO_ANSWER`），只能加不能删；用さけのわ的数据要注明出处（卡片角注、答案卡的「数据」），不能去掉。
- 盲猜以撒道具不出涉黄、擦边、自残自杀、毒品、战争的道具（`blind/isaacdle.py` 的 `NO_ANSWER`），只能加不能删。
- 猜K线不出军工、核相关的股票（`kline.BLOCK_INDUSTRIES`、`NUCLEAR`、`BLOCK_NAME`，日股的 `JP_BLOCK_SECTORS`、`JP_BLOCK_NAME`，美股的 `US_BLOCK_INDUSTRY`、`US_BLOCK_NAME`），只能加不能删。
- 英文Wordle的 `blind/wordle.py` 中，`NO_ANSWER` 只收本义明确涉政（政治、政党、政体、政府机关、领导人头衔）、战争 / 军事 / 武器、核、色情擦边、脏话、歧视性蔑称、毒品、自残自杀、暴力杀戮的词，只能加不能删；中性多义词不进永久名单，按 `ANSWER_BLOCK` 逐项过滤中文释义，展示时只使用合规义项。核相关按核武、核能、核电等具体词判断，军事也匹配具体词，不能因「核」「军」误伤果核、核实、冠军。王室头衔（king / queen / prince 等）不算红线，可以出题；不出题的词仍能作为猜测。
- 猜国家、盲猜以撒道具、猜群友暂时下线（`main.OFFLINE`）：开题和排行榜都只回「好危险，不踩啦」，也不进整点播报。

## 外部接口

- Google Street View Static API 的图片按张计费。构建题库只能用免费的元数据接口（`StreetView.find`），图片只在出题时取，不缓存。
- B 站搜索：先访问首页拿 `buvid3` cookie；3 秒一次，412 是被限流。
- AniList 约每分钟 30 次，用别名把多个查询合成一次请求。分页最多翻到第 5000 条（`perPage` 50 就是 100 页），再往后报 400。
- Bangumi 的 `/v0/search/persons` 按名字搜人物，`filter.career` 填 `seiyu` 只搜声优；结果里带信息框和收藏人数，不用再查详情。
- Wikidata SPARQL：用 `VALUES` 批量查；查询太重会 504，拆小或换查法。在查询里用 `COALESCE` 挑中文标签、顺着 `P131+` 往上找都会超时，标签取回来在 Python 里挑，日本市町村的所属县看团体代码（P429），中国地级行政区的所属省份看行政区划代码（P442）。
- 中文维基浏览量：Wikimedia REST 的逐条接口并发几个就 429（让等 100 秒），只能挨个查；量大的用 MediaWiki API 的 `prop=pageviews`（一次 50 条，最近 60 天）。UA 要带联系方式（`sources.WIKI_UA`）。
- warcraft.wiki.gg（猜魔兽地图）、wago.tools（猜魔兽地图、盲猜魔兽Boss）：`api.php` 要用机器人式的 UA（`sources.WIKI_UA`），浏览器 UA 会被 Cloudflare 拦成 403；一秒一个请求。wago.tools 的 DB2 CSV 要带正式服的 `build`（`/api/builds/latest` 的 `wow.version`），不带给的是测试服。
- 东方财富行情（猜K线）：板块成分股 `clist` 一页最多 100 只；日 K 每行是「日期,开,收,高,低,量」，收盘在第二个。它和腾讯的前复权是把分红直接减掉，老价格会成负数，只拉不复权的（`fqt=0`），按新浪 `qfq.js` 的因子等比调整。返回的 Content-Type 不是 JSON，用 `json.loads(await r.text())`。
- Yahoo Finance（猜K线的日股、美股）：UA 带 Chrome 字样会被限流成 429，用 `kline.YAHOO_UA`（光一个 `Mozilla/5.0`）；日经官网反过来要浏览器 UA，维基要 `sources.WIKI_UA`。
- bindingofisaacrebirth.wiki.gg（盲猜以撒道具）：Cargo 查询一页 500 行，一秒一页；不带 UA 会被拦，用 `sources.WIKI_UA`。
- Metacritic 评分榜是网页前端的非官方接口，目前不用 key；以后要是又要 key 了，从配置读，不要写死在代码里。

## Git

- `main` 由仓库规则集保护：任何人都不能强推或删除；改动要走 PR 并经 1 人批准，只有仓库所有者（admin）可以绕过、直接推。
- 替所有者干活时直接提交到 `main`，推送前先征得同意；其他人 fork 后提 PR。
- 提交信息用英文：祈使句标题，正文写为什么改。
- 发版时更新 `metadata.yaml` 里的 `version`，在 `CHANGELOG.md` 里记一笔（写给用户看的，按版本倒序）。
- 插件市场在 AstrBot Cloud（https://cloud.astrbot.app/publish）：从 GitHub 仓库解析 `metadata.yaml` 提交，每个版本都要重新提交审核，版本号用过就不能再用。`AGENTS.md`、`CLAUDE.md` 等开发文件用 `.gitattributes` 的 `export-ignore` 排除出插件包。
