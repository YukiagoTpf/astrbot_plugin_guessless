# AGENTS.md

AstrBot 群聊猜题插件（Guessless）。功能和用法见 [README](README.md) 与 [docs/](docs/)，这里只写改代码需要知道的事。

## 代码结构

```
main.py               插件入口 GuessPlugin：指令、一道题的生命周期（Round）、额度、排行榜和整点播报、暂停、重启收尾
metadata.yaml         AstrBot 插件元数据（插件市场也读它：short_desc、tags）
CHANGELOG.md          更新日志，插件市场的「更新日志」页显示它
_conf_schema.json     插件配置项（AstrBot WebUI 读这个）
requirements.txt      运行依赖，AstrBot 加载插件时自动安装
pyproject.toml        开发工具配置（ruff）
core/
  bank.py             题库：各题型的构建（Bank._build_*）、定期刷新和重建判断（_settings / _stale）、出题（pick）
  entries.py          题目条目和提示（*_entry、country_basics、scrub），纯函数、不联网
  sources.py          外部数据源：Wikidata、中文维基浏览量、B 站、AniList、AnimeThemes、IGDB、Steam、PokeAPI、Gundam Wiki、vtbs.moe；HttpError
  geo.py              地理：GeoNames、Wikidata 国家资料、日本市町村和中国地级行政区、街景取景、Natural Earth 轮廓、盲猜地点的地区划分和方位，以及各种版本常量
  cards.py            卡片渲染（Pillow）：九宫格、街景三联、国家轮廓（答案卡带邻国底图）、世界地图、对比表、听歌进度条、
                      猜群友的头像棋盘、整点排行榜长图
  members.py          猜群友：挑人、拉 QQ 头像、每段露哪几格、认群友说的是谁（Roster）
  audio.py            听歌猜番的音频：ffmpeg 解码、切片段
  answer.py           答案归一化和判定
  store.py            数据目录里 JSON 文件的读写
  hint_data.py        手写的特产、名胜、美食提示
  mecha_data.py       手写的 Gundam Wiki 资料中译（世界观、阵营、机型、机械设计）和机体外号
  vtuber_data.py      手写的猜Vtuber不出题名单（品牌号、搬运号、成人向、有争议的）
  lines.py            所有发到群里的文案
docs/                 用户文档
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

- 改了题库构建或提示：把线上数据目录里的缓存（`geo_*.json`、`country_shapes.json` 等）拷到临时目录，`Bank(临时目录, 配置)` 调对应的 `_build_*`，检查条目和提示。不要在线上数据目录里试。
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
  | 宝可梦条目格式（盲猜宝可梦的 `attrs`） | `entries.POKEMON_VERSION` |
  | 萝卜的选题、条目格式、`mecha_data.py` | `entries.MECHA_VERSION` |
  | Vtuber 的选题、条目格式、`vtuber_data.py` | `entries.VTUBER_VERSION` |
  | 盲猜地点的条目格式、选题分档、地区划分（`geo.REGIONS`） | `entries.GEODLE_VERSION` |
  | 盲猜日本地点的选题、条目格式、提示规则（`LANDMARK_CLASS` / `LANDMARK_BLOCK`） | `entries.JPGEODLE_VERSION` |
  | 盲猜中国地点的选题、条目格式、提示规则（`CN_SIGHT_BLOCK`、`geo.CN_PROVINCES`） | `entries.CNGEODLE_VERSION` |
  | 听歌猜番的选曲规则（`sources.anime_themes`） | `bank.ANISONG_VERSION` |
  | 街景取景规则（全景缓存作废重找） | `geo.SAMPLING` |
  | 国界处理（`country_shapes.json` 重新生成） | `geo.OUTLINE_VERSION` |

- 新配置项写进 `_conf_schema.json`（密钥加 `"secret": true`），并更新 `docs/configuration.md`。
- 文档：README 只放概要和索引，细节写进 `docs/` 对应的文件；行为改了要同步改文档。

## 红线

- 仓库里不能有密钥、QQ 号、群号、本机路径等个人信息。key 一律从插件配置读。
- 题目、提示、文案不涉政、不涉黄、不擦边：不放政治人物、议会和政府机关、战争、核相关的内容。
- 不要改动这些处理方式：`geo.EXCLUDE` 里的地区不出题；国界只用 Natural Earth 的 CHN 视角数据；邻国底图按 `FCLASS_CN` 合并（`geo.MERGED_FCLASS`）、`geo.SKIP_FEATURES` 不画。
- 提示里不能出现答案的国名、县名、首都名，也不能用「法式」「日元」这种拿国名首字的说法。
- 听歌猜番不用 AnimeThemes 标了 NSFW 的版本；音频只在出题时现下，不存盘。
- 萝卜的阵营不翻 G 高达里以现实国家命名的（新香港、新中国……），对比表和提示里显示「—」。
- 猜Vtuber不出 `vtuber_data.py` 里的账号；名单要扩只能往里加，不要删掉成人向和有争议的那几项。
- 猜群友的头像只在内存里出图，不存盘；答案只公布群名片 / 昵称，不发 QQ 号。
- 盲猜中国地点不出、不认三沙市（`geo.CN_EXCLUDE`），港澳台不在里面；名胜过滤（`entries.CN_SIGHT_BLOCK`：红色景点、陵墓、领导人、战争……）只能加不能删。
- 盲猜日本地点不出、不认北方四岛上的村（`geo.disputed`）；名胜过滤（`entries.LANDMARK_BLOCK`：和平公园、原爆、靖国和护国神社、端岛、领土争议的岛、皇室、陵墓、事件……）只能加不能删。
- 猜国家暂时下线（`main.OFFLINE`）：开题和排行榜都只回「好危险，不踩啦」，也不进整点播报。

## 外部接口

- Google Street View Static API 的图片按张计费。构建题库只能用免费的元数据接口（`StreetView.find`），图片只在出题时取，不缓存。
- B 站搜索：先访问首页拿 `buvid3` cookie；3 秒一次，412 是被限流。
- AniList 约每分钟 30 次，用别名把多个查询合成一次请求。
- Wikidata SPARQL：用 `VALUES` 批量查；查询太重会 504，拆小或换查法。在查询里用 `COALESCE` 挑中文标签、顺着 `P131+` 往上找都会超时，标签取回来在 Python 里挑，日本市町村的所属县看团体代码（P429），中国地级行政区的所属省份看行政区划代码（P442）。
- 中文维基浏览量：Wikimedia REST 的逐条接口并发几个就 429（让等 100 秒），只能挨个查；量大的用 MediaWiki API 的 `prop=pageviews`（一次 50 条，最近 60 天）。UA 要带联系方式（`sources.WIKI_UA`）。
- Metacritic 评分榜是网页前端的非官方接口，目前不用 key；以后要是又要 key 了，从配置读，不要写死在代码里。

## Git

- `main` 由仓库规则集保护：任何人都不能强推或删除；改动要走 PR 并经 1 人批准，只有仓库所有者（admin）可以绕过、直接推。
- 替所有者干活时直接提交到 `main`，推送前先征得同意；其他人 fork 后提 PR。
- 提交信息用英文：祈使句标题，正文写为什么改。
- 发版时更新 `metadata.yaml` 里的 `version`，在 `CHANGELOG.md` 里记一笔（写给用户看的，按版本倒序）。
- 插件市场在 AstrBot Cloud（https://cloud.astrbot.app/publish）：从 GitHub 仓库解析 `metadata.yaml` 提交，每个版本都要重新提交审核，版本号用过就不能再用。`AGENTS.md`、`CLAUDE.md` 等开发文件用 `.gitattributes` 的 `export-ignore` 排除出插件包。
