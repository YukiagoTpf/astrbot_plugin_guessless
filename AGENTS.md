# AGENTS.md

AstrBot 群聊猜题插件（Guessless）。功能和用法见 [README](README.md) 与 [docs/](docs/)，这里只写改代码需要知道的事。

## 代码结构

| 文件 | 职责 |
|---|---|
| `main.py` | 插件入口 `GuessPlugin`：指令、一道题的生命周期（`Round`）、额度、排行榜、暂停、重启收尾 |
| `bank.py` | 题库：各题型的构建（`Bank._build_*`）、定期刷新和重建判断（`_settings` / `_stale`）、出题（`pick`）、题目条目和提示（`*_entry`、`country_basics`、`scrub`） |
| `media.py` | 外部数据源：Wikidata、B 站、AniList、IGDB、Steam、PokeAPI；`HttpError` |
| `geo.py` | 地理：GeoNames 城市、Wikidata 国家资料、街景取景（`StreetView`、`sample`）、Natural Earth 轮廓（`fetch_outlines`、`outline`）、各种版本常量 |
| `grid.py` | 卡片渲染（Pillow）：九宫格、街景三联、国家轮廓和邻国底图 |
| `answer.py` | 答案归一化和判定 |
| `hint_data.py` | 手写的特产、名胜、美食提示 |
| `lines.py` | 所有发到群里的文案 |
| `_conf_schema.json` | 插件配置项（AstrBot WebUI 读这个） |

## 验证改动

没有单元测试。每次改完至少：

```bash
uvx ruff check --isolated --select E9,F .
```

代码依赖 AstrBot 运行环境（`astrbot.api`、Pillow、aiohttp），要跑起来得在 AstrBot 里，比如官方 Docker 镜像。做法是把插件拷进容器的临时目录，用 `sys.path.insert` 导入后直接调函数：

```bash
docker cp . <容器>:/tmp/dev/astrbot_plugin_guessless
docker exec -w /AstrBot <容器> python3 -c '
import sys; sys.path.insert(0, "/tmp/dev")
from astrbot_plugin_guessless import grid, answer
print(answer.is_correct("旷野之息", answer.candidates(["塞尔达传说 旷野之息"])))  # True
'
```

- 改了题库构建或提示：把线上数据目录里的缓存（`geo_*.json`、`country_shapes.json` 等）拷到临时目录，`Bank(临时目录, 配置)` 调对应的 `_build_*`，检查条目和提示。不要在线上数据目录里试。
- 改了提示内容（`hint_data.py`、`scrub`）：重建地点、国家、日本地点题库后，检查每条提示里没有答案的中文名（`answer.norm` 后比较），也没有残留的 `□`。
- 改了卡片渲染：渲染样图存成 PNG 打开看，至少覆盖九宫格、宝可梦、街景，国家要看邻国多的（越南、德国）、被包围的（南非）、跨 180° 经线的（俄罗斯、斐济）。
- 改了 `main.py`：没有完整的事件环境，可以用 `GuessPlugin.__new__` 建对象、手动塞属性、假的 `context` 来测单个方法。

## 约定

- 注释和文案用中文，注释短、讲为什么；对群发的话只写在 `lines.py`。
- 持久化数据写插件数据目录（`StarTools.get_data_dir`），用 `bank.save_json`（先写临时文件再替换）。不要写进插件目录。
- 外部请求走 `Bank._get` / `_post` / `_request`（带重试）。报错用 `media.HttpError`，它不带 URL，因为有些 key 在 URL 参数里，不能进日志。
- 影响题库内容的规则改了，要改对应的版本常量，否则线上旧题库不会重建：

  | 改了什么 | 改哪个 |
  |---|---|
  | 题目条目格式、通用选题规则 | `bank.BANK_VERSION` |
  | `hint_data.py`、`scrub`、地点和国家的提示规则 | `geo.HINTS_VERSION` |
  | 街景取景规则（全景缓存作废重找） | `geo.SAMPLING` |
  | 国界处理（`country_shapes.json` 重新生成） | `geo.OUTLINE_VERSION` |

- 新配置项写进 `_conf_schema.json`（密钥加 `"secret": true`），并更新 `docs/configuration.md`。
- 文档：README 只放概要和索引，细节写进 `docs/` 对应的文件；行为改了要同步改文档。

## 红线

- 仓库里不能有密钥、QQ 号、群号、本机路径等个人信息。key 一律从插件配置读。
- 题目、提示、文案不涉政、不涉黄、不擦边：不放政治人物、议会和政府机关、战争、核相关的内容。
- 不要改动这些处理方式：`geo.EXCLUDE` 里的地区不出题；国界只用 Natural Earth 的 CHN 视角数据；邻国底图按 `FCLASS_CN` 合并（`geo.MERGED_FCLASS`）、`geo.SKIP_FEATURES` 不画。
- 提示里不能出现答案的国名、县名、首都名，也不能用「法式」「日元」这种拿国名首字的说法。

## 外部接口

- Google Street View Static API 的图片按张计费。构建题库只能用免费的元数据接口（`StreetView.find`），图片只在出题时取，不缓存。
- B 站搜索：先访问首页拿 `buvid3` cookie；3 秒一次，412 是被限流。
- AniList 约每分钟 30 次，用别名把多个查询合成一次请求。
- Wikidata SPARQL：用 `VALUES` 批量查；查询太重会 504，拆小或换查法。
- Metacritic 是非官方接口，apiKey 每次构建从游戏页面源码里现找（`Bank._mc_key`），不要写死在代码里。

## Git

- 直接提交到 `main`，不走 PR；只有仓库所有者能推，推送前先征得同意。`main` 禁止强推。
- 提交信息用英文：祈使句标题，正文写为什么改。
- 发版时更新 `metadata.yaml` 里的 `version`。
