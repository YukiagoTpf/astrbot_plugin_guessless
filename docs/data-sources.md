# 数据来源

仓库里不带任何图片和题库，全部在运行时从下面这些地方拉取，版权归原作者和各数据源所有。

| 来源 | 用途 | 说明 |
|---|---|---|
| [Bangumi](https://bgm.tv) API | 动画榜单、中文名、游戏中文名 | 免 key |
| Metacritic | 游戏评分榜、无 IGDB 时的封面 | 网页前端用的非官方接口，要带网页里内嵌的 apiKey，每次构建从游戏页面源码里现找；改版了可能失效 |
| [IGDB](https://www.igdb.com) | 游戏截图、热度、别名、去重 | 需要 Twitch 开发者应用，见[配置](configuration.md#igdb) |
| Steam | IGDB 上没有截图时的游戏截图 | 商店公开接口 |
| 哔哩哔哩 | 动画分集封面 | 网页搜索接口，非官方；要先访问首页拿 cookie，搜太快会被限流 |
| [AniList](https://anilist.co) | 动画横幅、罗马音和英文名 | GraphQL，约每分钟 30 次 |
| [Wikidata](https://www.wikidata.org) | 译名、国家资料（大洲、行驶方向、货币、首都、人口、面积） | SPARQL，CC0 |
| [PokeAPI](https://pokeapi.co) | 宝可梦资料；立绘取自 GitHub 上的 PokeAPI/sprites | GraphQL，免 key |
| [GeoNames](https://www.geonames.org) | 城市坐标、一级行政区、国家主要语言 | CC BY 4.0 |
| [Natural Earth](https://www.naturalearthdata.com) | 国家轮廓和邻国底图（10m，CHN 视角版本） | 公有领域 |
| Google Street View Static API | 街景图片；找全景用免费的元数据接口 | 图片按张计费，遵守 Google Maps Platform 条款，图片不落盘 |
