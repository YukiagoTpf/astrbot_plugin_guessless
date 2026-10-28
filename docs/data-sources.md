# 数据来源

仓库里不带任何图片和题库，全部在运行时从下面这些地方拉取，版权归原作者和各数据源所有。

| 来源 | 用途 | 说明 |
|---|---|---|
| [Bangumi](https://bgm.tv) API | 动画榜单、中文名、用户标签、制作信息、角色表（主角声优）、机体（中文名、出场作品） | 免 key |
| Metacritic | 游戏评分榜、无 IGDB 时的封面 | 网页前端用的非官方接口，目前不用 key；改版了可能失效 |
| [IGDB](https://www.igdb.com) | 游戏截图、热度、别名、开发商、去重 | 需要 Twitch 开发者应用，见[配置](configuration.md#igdb) |
| Steam | IGDB 上没有截图时的游戏截图 | 商店公开接口 |
| 哔哩哔哩 | 动画分集封面 | 网页搜索接口，非官方；要先访问首页拿 cookie，搜太快会被限流 |
| [AniList](https://anilist.co) | 动画横幅、罗马音和英文名；听歌猜番补 ID | GraphQL，约每分钟 30 次 |
| [AnimeThemes](https://animethemes.moe) | 听歌猜番的 OP / ED 音频、歌名和歌手 | 免 key，约每分钟 90 次；音频出题时现下、不存盘 |
| [Gundam Wiki](https://gundam.fandom.com) | 高达机体的设定图、世界观、阵营、机型、机械设计、全高 | Fandom 上的 MediaWiki API，免 key；文字 CC BY-SA |
| [vtbs.moe](https://vtbs.moe) | 猜Vtuber的名单（VTuber Database：各语言的名字、所属团体）和 B 站账号数据（粉丝数、大航海人数、头像） | 社区维护的公开接口，免 key；头像从 B 站图床下载 |
| QQ 头像、群成员列表 | 猜群友：群成员（名片、昵称、入群时间、最近发言时间）走 OneBot 的 `get_group_member_list`，头像走 QQ 公开头像接口 `q1.qlogo.cn` | 都在开题时现拉，头像只在内存里出图，不存盘 |
| [Wikidata](https://www.wikidata.org) | 译名、国家资料（大洲、行驶方向、货币、首都、人口、面积） | SPARQL，CC0 |
| [PokeAPI](https://pokeapi.co) | 宝可梦资料；立绘取自 GitHub 上的 PokeAPI/sprites | GraphQL，免 key |
| [GeoNames](https://www.geonames.org) | 城市坐标、一级行政区、国家主要语言 | CC BY 4.0 |
| [Natural Earth](https://www.naturalearthdata.com) | 国家轮廓、邻国底图和世界地图（10m，CHN 视角版本） | 公有领域 |
| Google Street View Static API | 街景图片；找全景用免费的元数据接口 | 图片按张计费，遵守 Google Maps Platform 条款，图片不落盘 |
