# 配置

AstrBot WebUI → 插件 → Guessless → 配置。所有配置都有默认值；改了影响题库内容的配置，一小时内会自动重建对应题库，重建期间旧题库照常出题。

## 配置项

| 配置 | 默认 | 说明 |
|---|---|---|
| `round_seconds` | 120 | 每道题限时（秒） |
| `jpgeodle_round_seconds` | 420 | 盲猜日本地点限时（秒），市町村太多，比其他盲猜多两分钟 |
| `cngeodle_round_seconds` | 420 | 盲猜中国地点限时（秒），能猜的有三百多个，同盲猜日本地点 |
| `sakedle_round_seconds` | 420 | 盲猜日本酒限时（秒），能猜的有三千多个銘柄 |
| `bossdle_round_seconds` | 420 | 盲猜魔兽Boss限时（秒），能猜的有一千多个首领 |
| `isaacdle_round_seconds` | 420 | 盲猜以撒道具限时（秒），能猜的有七百多个道具 |
| `blind_round_seconds` | 300 | 其他盲猜的限时（秒），要一个个推，比其他题型久 |
| `jpword_round_seconds` | 120 | 猜日语普通局限时（秒） |
| `jpword_max_guesses` | 3 | 猜日语普通局每人最多猜几次；0 不限 |
| `wordle_round_seconds` | 300 | 英文Wordle限时（秒） |
| `wordle_max_guesses` | 10 | 每人每题最多猜几次；0 不限，只认词表内的五字母完整单词，全群重复猜测不计次；仍受全群棋盘行数限制 |
| `wordle_rows` | 6 | 全群共用棋盘行数，1–10；超出范围按边界值处理，填满仍未猜中就公布答案 |
| `chengyu_round_seconds` | 300 | 猜成语限时（秒），独立于英文Wordle |
| `chengyu_max_guesses` | 10 | 猜成语每人每题最多猜错几次，0 不限；表外闲聊和全群重复猜测不扣次 |
| `chengyu_rows` | 10 | 猜成语全群共用棋盘行数，1–10；超出范围按边界值处理，填满仍未猜中按超时结算 |
| `enword_round_seconds` | 120 | 猜英文限时（秒） |
| `enword_max_guesses` | 3 | 猜英文每人最多猜几次；0 不限，完整说出题库内其他题的答案才计错一次 |
| `jpword_trigger_uids` | 空 | 每行一个用户 ID；替换名单里的人开允许的题型时，有概率换为仅开题人能答、能喊提示或求助的猜日语，不限时、不限次数。空列表关闭 |
| `jpword_trigger_modes` | `["any"]` | 可替换的题型键；`any` 或空列表表示所有可开题型，也可填 `["jpgeodle"]` 等限定范围。下线题型仍不开；直接开猜日语仍为普通限时局 |
| `jpword_trigger_rate` | 0.5 | 替换概率（0–1），默认 50%；0 关闭，1 每次触发。日语题库就绪才替换，暂停、宵禁、同群一题照旧。额度已满先拦；替换及求助换题不扣时段或加题额度。只有开题人答对才结束并记分，管理员可暂停作废；重启作废并公布当前题答案，免费局不退额度。直接开猜日语照常扣额度，管理员规则不变 |
| `hint_cooldown_seconds` | 5 | 两次「提示」的最短间隔（秒）；替换局两次求助也使用此冷却秒数，从换题完成或失败时重新计时，冷却内求助静默忽略；换题期间忽略所有局内消息，指令照常 |
| `max_guesses` | 5 | 每人每题最多猜几次，怎么算一次见[玩法](usage.md#猜的次数和距离)；0 为不限 |
| `mechadle_max_guesses` | 10 | 盲猜萝卜每人每题最多猜几次；0 为不限 |
| `jpgeodle_max_guesses` | 10 | 盲猜日本地点每人每题最多猜几次；0 为不限 |
| `cngeodle_max_guesses` | 10 | 盲猜中国地点每人每题最多猜几次；0 为不限 |
| `sakedle_max_guesses` | 10 | 盲猜日本酒每人每题最多猜几次；0 为不限 |
| `bossdle_max_guesses` | 10 | 盲猜魔兽Boss每人每题最多猜几次；0 为不限 |
| `isaacdle_max_guesses` | 10 | 盲猜以撒道具每人每题最多猜几次；0 为不限 |
| `geomatch_round_seconds` | 180 | 猜地点匹配限时（秒），到点一起公布 |
| `geomatch_max_guesses` | 3 | 现在不用：猜地点匹配每人只答一次。只有改回四国各一张的玩法（`geomatch.PAIRS` 去掉 geomatch）时才看它 |
| `jpgeomatch_round_seconds` | 180 | 猜日本地点匹配限时（秒），四张图要一张张对，比猜日本地点多一分钟 |
| `jpgeomatch_max_guesses` | 3 | 猜日本地点匹配每人每题最多猜几次；一共只有 24 种排法，猜错还报对了几个，给多了能硬试出来。0 为不限 |
| `kline_round_seconds` | 180 | 猜K线限时（秒）；凑满人数提前揭晓，到点没凑满按已经猜了的排名 |
| `kline_players` | 5 | 猜K线凑满几个人揭晓；每人只认第一句，按离实际涨跌幅的远近排名次，第一名记一次 |
| `hourly_limit` | 10 | 每个群每小时最多开几道题，所有题型共用，每个整点清零；0 为不限 |
| `group_hourly_limits` | 空 | 按群单独设每小时题数，每行一个「群号:题数」（如 `123456:3`），0 为不限；没写的群用 `hourly_limit` |
| `rolling_quota` | 关 | 滚动刷新每小时题数：一小时按题数等分，每段一道（每小时 6 道就是每 10 分钟一道），这段没开的作废；关着就是每个整点一次给满，见[玩法](usage.md#额度) |
| `group_curfews` | 空 | 按群设宵禁，每行一个「群号:开始-结束」（如 `123456:00:00-07:00`，跨午夜写 `23:00-07:00`）；这段时间不能开新题，进行中的照常答完。管理员在群里 @机器人 发「宵禁 00:00-07:00」「取消宵禁」改的也是这一项；临时解禁见[玩法](usage.md#额度) |
| `help_extra` | 空 | @机器人 说「帮助」时追加在猜题玩法后面的话（比如别的插件的用法），每行一条；写成「群号:文字」只在那个群加 |
| `board_interval_hours` | 1 | 每隔几小时在玩过的群里播报一张各题型排行榜的图，这段时间没人答对就不发；0 关闭，见[玩法](usage.md#整点排行榜) |
| `anime_pool_size` | 1000 | 动画题库大小 |
| `niche_ratio` | 0.2 | 每道题出小众题的概率，0.2 大约五题一道；0 只出大众题（声优用 `seiyuu_niche_ratio`）。游戏、宝可梦、萝卜、Vtuber、魔兽、老师、声优、盲猜地点、盲猜日本地点、盲猜日本酒、盲猜魔兽Boss、盲猜以撒道具、猜日语、猜英文分档，动画、地点、盲猜中国地点不分档。每个题型最近出过的 200 道（存在 `recent.json`，重启不清）先不出，这一档都出过了从最久没出的里挑；猜地点一国有几个全景，同一国挑最久没出过的那个（`recent_ids.json`） |
| `game_pool_size` | 500 | 游戏大众题数量 |
| `game_niche_size` | 300 | 游戏小众题数量 |
| `game_galgame_size` | 80 | 额外补充的全年龄视觉小说数量，0 关闭，上限 150；有低龄正式完整发行、中文名、非 NSFW 和至少 9 张已评安全截图才收，实际可能不足。按 VNDB 投票数选，前一半大众、其余小众；不占 MC 数量，无需 IGDB key。VNDB API 限非商业使用，规则及许可见[题库](question-banks.md#全年龄视觉小说) |
| `game_min_score` | 75 | 游戏最低 Metacritic 分 |
| `game_min_reviews` | 10 | 游戏最少媒体评测数 |
| `pokemon_main_gen` | 4 | 第 1 到第 N 世代的宝可梦算大众题 |
| `vtuber_pool_size` | 300 | Vtuber 题库大小，国内（主要说中文的）三分之二、国外三分之一，按 B 站粉丝数取 |
| `seiyuu_pool_size` | 500 | 声优题库大小，近 20 年还在接新角色的日本女声优按 Bangumi 收藏数取，前 100 位是热门（大众题）；能出的一共四百五十位上下 |
| `seiyuu_niche_ratio` | 0.5 | 猜声优出冷门（收藏数 100 名往后）的概率，0.5 是热门冷门各一半 |
| `refresh_days` | 7 | 题库定期刷新的间隔（天） |
| `igdb_client_id` / `igdb_client_secret` | 空 | 游戏截图、热度和别名，见下文 |
| `google_maps_key` | 空 | 猜地点、猜日本地点用的街景，见下文 |

各题库怎么用这些配置，见[题库](question-banks.md)。

## IGDB

不填时，Metacritic 游戏题图退回封面（常印着标题，揭开那一格基本就露馅了），也没有 IGDB 热度数据，这部分题库只按 MC 分取、不分大众 / 小众。

`game_galgame_size` 补充的视觉小说独立使用 VNDB 安全截图和投票数，不需要 IGDB key，仍分大众 / 小众。

1. 在 [dev.twitch.tv/console](https://dev.twitch.tv/console) 注册应用：OAuth 重定向填 `http://localhost`，类别选 Application Integration。
2. 把 Client ID 填进 `igdb_client_id`；在同一个应用里点「新密码」生成 secret，填进 `igdb_client_secret`。
3. 一小时内游戏题库自动重建。key 填错时构建中止、保留旧题库，日志里有「拒绝访问」。

## Google Maps

不填时不能猜地点和猜日本地点。

1. Google Cloud 建项目、绑结算账号，启用 Street View Static API。
2. 在「凭据」里建 API 密钥，填进 `google_maps_key`。

**街景图片按张计费**，每道题取 3 张（猜地点匹配、猜日本地点匹配 4 张），有每月免费额度。找全景用的是免费的元数据接口，建题库不花钱；只有出题时取图收费，图片不落盘（Google 条款也不允许长期缓存）。建议：

- 把 key 限制为只能调 Street View Static API；
- 在 Cloud 控制台的配额页给 Street View Static API 的每天请求数设上限，比如 300（约 100 道题）。

## 字体

卡片文字用 Noto Sans CJK（`/usr/share/fonts/opentype/noto`，AstrBot 官方 Docker 镜像自带）。找不到时退回 Pillow 默认字体，中文会显示成方块。

猜成语建库会检查 Regular、Bold 实际字形，缺字的成语不进本机题库，全部缺字会报错而不开放。它只读取本地词表，不需要 key 或外部接口；声调以数字显示，避免拼音字体兼容问题。

## ffmpeg

听歌猜番要用 ffmpeg 解码和切音频（AstrBot 官方 Docker 镜像自带）。找不到时这个题型不开放，`听歌猜番` 会回「题库还在搬砖（容器里没有 ffmpeg）」。语音发出去时由 NapCat 转成 QQ 的格式，NapCat 那边也要有 ffmpeg（官方镜像自带）。

## 数据目录

插件数据都在 AstrBot 的 `data/plugin_data/astrbot_plugin_guessless/` 下：

| 文件 | 内容 |
|---|---|
| `bank_*.json` | 各题型的题库 |
| `galgame_vndb.json` | VNDB 候选、正式低龄发行和经 NSFW 检查的 Bangumi 匹配，缓存 7 天，带来源和 ODbL 许可；不含截图文件 |
| `media_*.json`（含听歌猜番的 `media_anisong.json`）、`bgm_game_match.json`、`bgm_characters.json`、`bgm_mecha.json`、`gundam_wiki.json`、`geo_*.json`、`jp_panos.json`、`country_shapes.json` | 各数据源的查询缓存，删掉会在下次重建时重新查 |
| `images/` | 题图缓存 |
| `scores.json` | 排行榜 |
| `paused.json` | 暂停中的群 |
| `quota.json` / `bonus.json` | 每小时额度、管理员加的题 |
| `rounds.json` | 进行中的题及当前答案、实际扣的额度；替换局另存 `endless`、`solo` 和 `solo_name`，重启后作废收尾用 |
| `origins.json` / `broadcasts.json` | 整点播报用：往各群发消息的地址、上次播报时的答对次数 |
