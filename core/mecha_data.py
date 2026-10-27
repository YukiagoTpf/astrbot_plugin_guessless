"""猜萝卜手写的数据：Gundam Wiki（英文）资料的中译，以及热门机体的各地译名和外号。

只收有把握的译法；对不上的阵营、机械设计显示「—」，不硬翻。
G 高达里「新香港」「新中国」这类以现实国家命名的阵营一律不翻、不出。
"""

# Gundam Wiki 的 era → (对比表里的短写, 提示里的全称)
ERAS = {
    "Universal Century": ("宇宙世纪", "宇宙世纪（U.C.）"),
    "Universal Century (GQ)": ("宇宙世纪", "宇宙世纪（U.C.）"),
    "Universal Century (Thunderbolt)": ("宇宙世纪", "宇宙世纪（U.C.）"),
    "Universal Century (Origin)": ("宇宙世纪", "宇宙世纪（U.C.）"),
    "Cosmic Era": ("C.E.", "宇宙纪元（C.E.）"),
    "Anno Domini": ("西历", "西历（A.D.）"),
    "After Colony": ("A.C.", "殖民纪元（A.C.）"),
    "Future Century": ("F.C.", "未来世纪（F.C.）"),
    "After War": ("A.W.", "战后纪元（A.W.）"),
    "Correct Century": ("正历", "正历（C.C.）"),
    "Advanced Generation": ("A.G.", "A.G. 纪元"),
    "Regild Century": ("R.C.", "复兴世纪（R.C.）"),
    "Post Disaster": ("P.D.", "灾后纪元（P.D.）"),
    "Ad Stella": ("A.S.", "星历（A.S.）"),
    "Build Fighters": ("创战者", "高达创战者（高达模型）"),
    "Build Divers": ("创形者", "高达创形者（高达模型）"),
}

# 机体的所属（operator 的第一个）→ 中文；括号里的作品标注（GQ、Origin）先去掉再查
FACTIONS = {
    "Earth Federation Forces": "地球联邦军", "Earth Federation Space Force": "地球联邦军",
    "Earth Federation": "地球联邦军", "Earth Federation Army": "地球联邦军",
    "Earth Sphere Federation Forces": "地球联邦军", "Earth Federation Forces (AGE)": "地球联邦军",
    "Principality of Zeon": "吉翁公国", "Zeon": "吉翁公国", "Zeon Remnants": "吉翁残党",
    "Autonomous Republic of Zeon": "吉翁共和国", "Republic of Zeon": "吉翁共和国",
    "Neo Zeon": "新吉翁", "Newborn Neo Zeon": "新吉翁", "Axis Zeon": "阿克西斯", "Axis": "阿克西斯",
    "Sleeves": "带袖的", "Titans": "提坦斯", "Anti Earth Union Group": "奥古", "Anti-Earth Union Group": "奥古",
    "AEUG": "奥古", "Karaba": "卡拉巴", "Londo Bell": "隆德·贝尔", "Mafty": "马夫提",
    "Crossbone Vanguard": "骷髅先锋", "Zanscare Empire": "赞斯卡尔帝国",
    "ZAFT": "扎夫特", "Earth Alliance": "地球联合", "Orb Union": "奥布", "Orb National Defense Force": "奥布",
    "Three Ships Alliance": "三舰同盟", "Clyne Faction": "克莱因派", "Serpent Tail": "蛇尾",
    "Celestial Being": "天上人", "Human Reform League": "人类革新联盟",
    "Union of Solar Energy and Free Nations": "联合", "Union": "联合", "Advanced European Union": "AEU",
    "A-Laws": "A-Laws", "Innovators (Group)": "革新者", "Innovators (Innovade Group)": "革新者",
    "Innovators": "革新者", "Team Trinity": "三位一体", "United Nations Forces": "联合国军",
    "United Nation Forces": "联合国军",
    "Gjallarhorn": "基亚拉霍恩", "Tekkadan": "铁华团", "Teiwaz": "泰瓦兹",
    "OZ": "OZ", "Romefeller Foundation": "罗姆斐拉财团", "White Fang": "白牙",
    "Dianna Counter": "迪亚娜亲卫队", "Militia": "民兵",
    "Shin Sei Development Corporation": "辛瑟开发公社", "Earth House": "地球寮",
    "Jeturk Heavy Machinery": "杰塔克重工", "Jeturk House": "杰塔克寮", "Grassley Defense Systems": "格拉斯利",
    "Peil Technologies": "佩尔科技", "Dawn of Fold": "弗尔德黎明",
    "Vagan": "维冈", "Capital Guard": "首都卫队", "Capital Army": "首都军",
}

DESIGNERS = {
    "Kunio Okawara": "大河原邦男", "Hajime Katoki": "Katoki", "Katoki Hajime": "Katoki",
    "Yutaka Izubuchi": "出渕裕", "Kanetake Ebikawa": "海老川兼武", "Kimitoshi Yamane": "山根公利",
    "Takayuki Yanase": "柳瀬敬之", "Hitoshi Fukuchi": "福地仁", "Kenji Teraoka": "寺冈贤司",
    "Ippei Gyoubu": "形部一平", "Kazumi Fujita": "藤田一己", "Junichi Akutsu": "阿久津润一",
    "Naohiro Washio": "鹫尾直广", "Kenki Fujioka": "藤冈建机", "Syd Mead": "席德·米德",
    "Junya Ishigaki": "石垣纯哉", "Mamoru Nagano": "永野护", "Makoto Kobayashi": "小林诚",
    "Mika Akitaka": "明贵美加", "Yasuo Ohtagaki": "太田垣康男", "Kazuhisa Kondo": "近藤和久",
    "Akira Yasuda": "安田朗", "Yasuhiro Moriki": "森木靖泰", "Mahiro Maeda": "前田真宏", "NAOKI": "NAOKI",
    "JNTHED": "JNTHED",
}

PRODUCTION = {
    "Prototype": "试作机", "Experimental": "试作机", "Mass Production": "量产机",
    "Mass Production General-Purpose": "量产机", "Limited Production": "少量生产", "Custom": "专用机",
}

# 各地译名和外号，Bangumi 上没有的补上（键是 Gundam Wiki 的页面标题）
NICKNAMES = {
    "RX-78-2 Gundam": ["元祖高达", "初代高达", "元祖"],
    "MS-06F Zaku II": ["扎古", "扎古2", "绿扎古"],
    "MS-06S Char's Zaku II": ["夏亚专用扎古", "夏亚扎古", "红扎古", "指挥官用扎古"],
    "MS-07B Gouf": ["老虎", "古夫"],
    "MS-09 Dom": ["大魔", "德姆"],
    "MS-09R Rick Dom": ["加大魔", "里克德姆"],
    "MS-14A Gelgoog": ["勇士", "盖尔古格"],
    "MSN-02 Zeong": ["吉翁号", "基昂", "吉恩格"],
    "MSM-07 Z'Gok": ["魔蟹", "茲寇克"],
    "MS-18E Kämpfer": ["京宝梵", "肯普法"],
    "RX-75 Guntank": ["钢坦克"],
    "RX-77-2 Guncannon": ["钢加农"],
    "RGM-79 GM": ["吉姆"],
    "YMS-15 Gyan": ["吉恩", "强人"],
    "RX-78NT-1 Gundam NT-1": ["阿雷克斯", "Alex"],
    'RX-78GP01 Gundam "Zephyranthes"': ["GP01", "玉兰"],
    'RX-78GP02A Gundam "Physalis"': ["GP02", "酸浆果"],
    "RX-178 Gundam Mk-II": ["MK2", "高达MK2", "马克兔"],
    "MSZ-006 Zeta Gundam": ["Z高达", "Zeta"],
    "MSZ-010 ΖΖ Gundam": ["ZZ高达", "ZZ", "双Z"],
    "MSN-00100 Hyaku Shiki": ["百式"],
    "PMX-003 The-O": ["铁奥", "The O", "迪奥"],
    "AMX-004 Qubeley": ["卡碧尼", "丘贝雷"],
    "RX-93 ν Gundam": ["牛高达", "ν高达", "牛"],
    "RX-93-ν2 Hi-ν Gundam": ["海牛", "Hi-ν高达"],
    "MSN-04 Sazabi": ["沙扎比", "沙煲"],
    "RX-0 Unicorn Gundam": ["独角兽", "独角兽高达"],
    "MSN-06S Sinanju": ["新安洲", "新安州"],
    "NZ-666 Kshatriya": ["刹帝利", "克夏托利亚"],
    "RX-105 Ξ Gundam": ["Ξ高达", "克西高达", "柯西"],
    "RX-104FF Penelope": ["佩涅罗佩"],
    "F91 Gundam F91": ["F91", "高达F91"],
    "LM314V21 Victory 2 Gundam": ["V2高达", "V2"],
    "XM-X1 Crossbone Gundam X-1": ["海盗高达", "骷髅高达", "X1"],
    "GF13-017NJ Shining Gundam": ["闪光高达", "闪亮高达"],
    "GF13-017NJII God Gundam": ["神高达", "神之高达", "God高达"],
    "XXXG-01W Wing Gundam": ["飞翼高达", "飞翼"],
    "XXXG-00W0 Wing Gundam Zero": ["飞翼零式", "天使零式", "零式"],
    "GX-9900 Gundam X": ["高达X", "X高达"],
    "ZGMF-X10A Freedom Gundam": ["自由高达", "自由"],
    "ZGMF-X20A Strike Freedom Gundam": ["强袭自由", "突击自由", "攻击自由", "强自"],
    "ZGMF-X09A Justice Gundam": ["正义高达", "正义"],
    "ZGMF-X19A ∞ Justice Gundam": ["无限正义", "无限正义高达", "无正"],
    "ZGMF-X42S Destiny Gundam": ["命运高达", "命运"],
    "ZGMF-X666S Legend Gundam": ["天意高达", "天意"],
    "ZGMF-X56S Impulse Gundam": ["脉冲高达", "脉冲"],
    "GAT-X105 Strike Gundam": ["强袭高达", "突击高达", "攻击高达", "强袭"],
    "GN-001 Gundam Exia": ["能天使", "艾克夏"],
    "GN-0000 00 Gundam": ["00高达", "双零"],
    "GN-0000+GNR-010 00 Raiser": ["00强化型", "00Raiser", "00R"],
    "GNT-0000 00 Qan［T］": ["00Q", "量子型", "00量子型"],
    "ASW-G-08 Gundam Barbatos": ["巴巴托斯"],
    "ASW-G-08 Gundam Barbatos Lupus": ["天狼", "巴巴托斯天狼"],
    "ASW-G-08 Gundam Barbatos Lupus Rex": ["天狼帝", "巴巴托斯天狼帝"],
    "XVX-016 Gundam Aerial": ["风灵", "风灵高达"],
    "XGF-02 Gundam Lfrith": ["路布里斯"],
}

# 超级机器人大战里非高达机体的外号（键是 Bangumi 的日文名，比较时去掉空格和全角）
SRW_NICKNAMES = {
    "マジンガーZ": ["魔神Z", "铁甲万能侠"],
    "グレートマジンガー": ["大魔神", "大铁甲万能侠"],
    "グレンダイザー": ["古兰戴萨", "UFO魔神"],
    "マジンカイザー": ["魔神凯撒", "魔神皇帝"],
    "コン・バトラーV": ["超电磁机器人", "孔巴特拉V"],
    "ダイターン3": ["无敌钢人泰坦3", "大坦克3"],
}
