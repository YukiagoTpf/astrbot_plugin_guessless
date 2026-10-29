# 配置

AstrBot WebUI → 插件 → Guessless → 配置。所有配置都有默认值；改了影响题库内容的配置，一小时内会自动重建对应题库，重建期间旧题库照常出题。

## 配置项

| 配置 | 默认 | 说明 |
|---|---|---|
| `round_seconds` | 120 | 每道题限时（秒） |
| `blind_round_seconds` | 300 | 各种盲猜的限时（秒），要一个个推，比其他题型久 |
| `hint_cooldown_seconds` | 5 | 两次「提示」的最短间隔（秒） |
| `max_guesses` | 5 | 每人每题最多猜几次，怎么算一次见[玩法](usage.md#猜的次数和距离)；0 为不限 |
| `mechadle_max_guesses` | 10 | 盲猜萝卜每人每题最多猜几次；0 为不限 |
| `hourly_limit` | 10 | 每个群每小时最多开几道题，所有题型共用，每个整点清零；0 为不限 |
| `group_hourly_limits` | 空 | 按群单独设每小时题数，每行一个「群号:题数」（如 `123456:3`），0 为不限；没写的群用 `hourly_limit` |
| `group_curfews` | 空 | 按群设宵禁，每行一个「群号:开始-结束」（如 `123456:00:00-07:00`，跨午夜写 `23:00-07:00`）；这段时间不能开新题，进行中的照常答完 |
| `board_interval_hours` | 1 | 每隔几小时在玩过的群里播报一张各题型排行榜的图，这段时间没人答对就不发；0 关闭，见[玩法](usage.md#整点排行榜) |
| `anime_pool_size` | 1000 | 动画题库大小 |
| `niche_ratio` | 0.2 | 每道题出小众题的概率，0.2 大约五题一道；0 只出大众题。游戏、宝可梦、萝卜、Vtuber、两种盲猜地点分档，动画、地点不分档 |
| `game_pool_size` | 500 | 游戏大众题数量 |
| `game_niche_size` | 300 | 游戏小众题数量 |
| `game_min_score` | 75 | 游戏最低 Metacritic 分 |
| `game_min_reviews` | 10 | 游戏最少媒体评测数 |
| `pokemon_main_gen` | 4 | 第 1 到第 N 世代的宝可梦算大众题 |
| `vtuber_pool_size` | 300 | Vtuber 题库大小，国内（主要说中文的）三分之二、国外三分之一，按 B 站粉丝数取 |
| `refresh_days` | 7 | 题库定期刷新的间隔（天） |
| `igdb_client_id` / `igdb_client_secret` | 空 | 游戏截图、热度和别名，见下文 |
| `google_maps_key` | 空 | 猜地点、猜日本地点用的街景，见下文 |

各题库怎么用这些配置，见[题库](question-banks.md)。

## IGDB

不填时，游戏题图退回 Metacritic 封面（常印着标题，揭开那一格基本就露馅了），也没有热度数据，题库只按 MC 分取、不分大众 / 小众。

1. 在 [dev.twitch.tv/console](https://dev.twitch.tv/console) 注册应用：OAuth 重定向填 `http://localhost`，类别选 Application Integration。
2. 把 Client ID 填进 `igdb_client_id`；在同一个应用里点「新密码」生成 secret，填进 `igdb_client_secret`。
3. 一小时内游戏题库自动重建。key 填错时构建中止、保留旧题库，日志里有「拒绝访问」。

## Google Maps

不填时不能猜地点和猜日本地点。

1. Google Cloud 建项目、绑结算账号，启用 Street View Static API。
2. 在「凭据」里建 API 密钥，填进 `google_maps_key`。

**街景图片按张计费**，每道题取 3 张，有每月免费额度。找全景用的是免费的元数据接口，建题库不花钱；只有出题时取图收费，图片不落盘（Google 条款也不允许长期缓存）。建议：

- 把 key 限制为只能调 Street View Static API；
- 在 Cloud 控制台的配额页给 Street View Static API 的每天请求数设上限，比如 300（约 100 道题）。

## 字体

卡片文字用 Noto Sans CJK（`/usr/share/fonts/opentype/noto`，AstrBot 官方 Docker 镜像自带）。找不到时退回 Pillow 默认字体，中文会显示成方块。

## ffmpeg

听歌猜番要用 ffmpeg 解码和切音频（AstrBot 官方 Docker 镜像自带）。找不到时这个题型不开放，`听歌猜番` 会回「题库还在搬砖（容器里没有 ffmpeg）」。语音发出去时由 NapCat 转成 QQ 的格式，NapCat 那边也要有 ffmpeg（官方镜像自带）。

## 数据目录

插件数据都在 AstrBot 的 `data/plugin_data/astrbot_plugin_guessless/` 下：

| 文件 | 内容 |
|---|---|
| `bank_*.json` | 各题型的题库 |
| `media_*.json`（含听歌猜番的 `media_anisong.json`）、`bgm_game_match.json`、`bgm_characters.json`、`bgm_mecha.json`、`gundam_wiki.json`、`geo_*.json`、`jp_panos.json`、`country_shapes.json` | 各数据源的查询缓存，删掉会在下次重建时重新查 |
| `images/` | 题图缓存 |
| `scores.json` | 排行榜 |
| `paused.json` | 暂停中的群 |
| `quota.json` / `bonus.json` | 每小时额度、管理员加的题 |
| `rounds.json` | 进行中的题，重启后收尾用 |
| `origins.json` / `broadcasts.json` | 整点播报用：往各群发消息的地址、上次播报时的答对次数 |
