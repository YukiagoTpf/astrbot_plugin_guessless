"""离线生成猜日语题；用 python3 -I 运行，原始词表不随插件发布。"""

import argparse
import csv
import gzip
import hashlib
import io
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

# 中文均为本项目原创；只在此白名单内挑词，避免直接发布外部释义和敏感词。
READINGS = """
概念 思考时用来概括事物的基本观念
概略 大致的内容或经过
配慮 对他人情况的体谅和考虑
妥協 双方让步后达成一致
把握 掌握整体情况
促進 推动某事更快发展
抑制 控制某种活动或趋势
維持 使现有状态继续保持
確保 设法保证拥有所需的东西
展望 对未来发展的看法
根拠 支持判断的依据
過程 事情发展的经过
過剰 超过所需的程度
適宜 根据情况采取合适的做法
適応 适应环境或条件
柔軟 柔和且能灵活变化
慎重 做事小心周到
着実 一步步稳妥推进
顕著 明显而引人注意
迅速 处理事情的速度快
円滑 进展顺畅
緩和 使紧张等程度减轻
緊密 关系密切
巧妙 做法灵巧高明
精巧 制作细致巧妙
繊細 细致且敏感
鮮明 清晰而鲜明
誠実 待人真诚可靠
律儀 遵守约定且认真有礼
几帳面 做事细致有条理
臆病 胆小怕事
曖昧 含糊不清
無邪気 单纯没有心机
孤独 缺少陪伴的孤单感
気配 通过细微迹象感到的动静
兆候 某事即将发生的迹象
余裕 时间等方面留有余地
余暇 工作之外可自由支配的时间
趣旨 说话或活动的主要用意
趣向 为增添趣味而作的安排
志向 朝着某个方向追求
嗜好 对饮食等事物的偏好
募集 公开招收所需人员等
応募 报名参加招募
採用 选用人员或做法
研修 为掌握工作技能而学习
講義 系统讲授某门知识
講演 向听众作专题演说
講座 针对某类知识开设的课程
課程 学习的课程安排
課題 需要研究或完成的任务
評価 对价值或表现作出判断
評判 人们对某事的评价
批評 分析优缺点并提出看法
比較 把多个对象放在一起看异同
統計 汇总和分析数量资料
傾向 事物呈现的发展方向
特徴 与其他事物不同的特征
特色 特有的风格或性质
特長 特别突出的优点
長所 优于其他方面的特点
短所 相对不足的方面
欠点 不够完善的地方
得意 擅长某事或感到自豪
苦労 为完成某事付出的辛劳
努力 为实现目标持续用力
工夫 为把事情做好而想办法
改善 使原有状况变好
改良 改进物品或方法
開発 开发新的产品或技术
発展 规模或程度逐渐扩大
発達 功能等逐渐成熟
普及 广泛传播和使用
応用 把知识用于实际场景
応援 为他人提供支持或鼓励
応対 接待并回应来访者
対応 根据情况采取行动
接客 接待顾客
接続 把两个东西连接起来
接近 距离逐渐缩短
接触 与某物碰到或发生联系
連絡 传递消息并保持联系
連携 多方互相配合
連想 由一件事想到另一件事
連続 相继发生而不中断
継続 保持同一活动进行
中断 进行中的事情暂时停下
完了 工作等全部结束
完成 按要求做好一件事或物品
達成 达到预定目标
実現 使设想成为现实
実施 按计划具体开展
実行 把想法付诸行动
実用 实际使用
実験 在一定条件下检验现象
観察 仔细看事物的状态
観測 用仪器等测量自然现象
測定 测出某项数值
計画 事先安排步骤和内容
企画 策划活动或产品
計算 根据数字进行运算
予算 事先估计的收支安排
費用 为某事支出的钱
消費 使用物品或金钱
節約 尽量减少不必要的使用
貯金 存起来的钱或存钱行为
預金 存放在金融机构的钱
利息 存借款项按比例产生的钱
利益 收入扣除成本后的所得
収入 得到的款项
支出 付出去的钱
出費 某件事花掉的钱
給与 工作得到的报酬
給料 按期领取的工作报酬
時給 每小时计算的报酬
通勤 往返住处和工作地点
通学 往返住处和学校
残業 正常工作时间以外继续工作
勤務 在单位承担工作
出勤 到工作地点上班
欠勤 未到岗位工作
休暇 获准不工作的假期
休憩 工作等中间短暂休息
休養 为恢复体力而休息
栄養 身体活动所需的养分
睡眠 睡觉的状态
疲労 身心感到疲倦
回復 恢复原来的状态
予防 事先采取措施避免问题
症状 生病时表现出的异常
診察 医生查看病情
診断 根据检查判断病情
治療 对疾病采取处理措施
看病 照顾生病的人
介護 照料生活有困难的人
衛生 保持环境等清洁健康
清潔 干净而没有污物
湿気 空气或物体中的潮湿感
乾燥 水分减少的状态
温暖 气候温和不冷
冷房 使室内降温的设备或措施
暖房 使室内升温的设备或措施
換気 让室内空气流通更新
設備 为某种用途配备的设施
装置 发挥特定功能的器械
機能 事物所起的作用
性能 器械等发挥功能的能力
構造 各部分的组成方式
組織 为共同目的组成的集体
管理 对工作或物品进行照管
整理 按条理收拾物品或信息
整備 使设备等处于可用状态
修理 修复损坏的东西
修正 修改不正确的部分
修復 修补到原有状态
点検 逐项查看有无问题
検査 按标准查验
検討 从多方面考虑
検証 核查说法是否成立
確認 核实情况是否属实
承認 认可某项请求或事实
了承 理解并接受相关情况
納得 理解后感到可以接受
承諾 同意他人的请求
拒否 明确表示不接受
断念 放弃原本打算做的事
決断 下定决心作出选择
判断 根据情况作出结论
推測 根据线索估计未知情况
推定 基于材料作近似判断
予測 事先估计将来的情况
予想 对将来情况的设想
期待 对将来的好结果怀有希望
信頼 相信并愿意依靠对方
信用 相信某人的诚实或能力
尊重 重视对方的意愿或价值
尊敬 敬重他人的品行或成就
感謝 对帮助表达谢意
感激 内心受到强烈触动
感動 因事物而产生深刻触动
共感 对他人的感受产生认同
共通 多个事物共同具有
共同 多方一起参与
協力 为同一目的互相帮助
協調 配合彼此的步调
調整 对各部分作适当安排
調和 各部分搭配协调
均衡 各方面保持平衡
平等 各方享有同等待遇
公平 不偏向任何一方
公正 按合理标准不偏不倚
率直 坦诚表达真实想法
素直 态度坦然且愿意听取意见
謙虚 不自满并尊重他人
寛容 能包容他人的不足
温厚 性情温和宽厚
冷静 不因情绪而慌乱
真剣 认真对待事情
真面目 态度端正且认真
熱心 对某事十分投入
積極 主动参与的态度
消極 不太主动的态度
客観 不依个人好恶观察事物
主観 从自身立场作出的看法
抽象 舍去细节概括共同性质
具体 清楚呈现实际内容
本質 事物最根本的性质
現象 能被观察到的外在表现
原則 一般应遵循的基本准则
例外 不符合通常规则的情况
条件 某事成立所需的前提
事情 相关背景和具体状况
事態 事情所处的状态
事実 实际存在或发生的事
実績 已经取得的实际成绩
成果 努力后得到的结果
効果 某种行为产生的作用
効率 投入与产出的关系
能率 单位时间内完成工作的程度
能力 完成某事的本领
才能 在某方面突出的天赋
技術 完成专门工作的技能
技能 通过训练掌握的本领
知識 学习和经验积累的认识
知恵 运用认识解决问题的智慧
教養 通过学习形成的文化素养
常識 社会普遍了解的基本认识
意識 对周围事物的觉察
認識 对事物性质的理解
印象 留在心里的总体感受
記憶 记住的信息或记住的能力
記録 将事情或数据留下来
記述 用文字写出事情的内容
表現 用语言等传达想法
表情 面部表现出的情绪
描写 用文字等刻画具体情景
表面 物体最外面的一层
裏面 物体的另一面
側面 事物的某个方面或旁边
範囲 某事涉及的界限
規模 事物整体的大小
規則 按照一定条理制定的要求
規律 集体生活应遵守的秩序
秩序 有条理且稳定的状态
順序 先后排列的次序
手順 做事情的具体步骤
順調 按预想平稳进行
困難 做起来不容易
面倒 费事或照料他人的事情
複雑 内部关系交错不简单
単純 组成或想法不复杂
簡潔 表达简明扼要
明確 清楚且确定
明白 清楚到没有疑问
確実 有把握且可靠
正確 与事实或标准一致
適切 切合具体情况
妥当 合理且合乎分寸
有効 能发挥预期作用
無効 不能产生应有的效力
有益 对某方面有帮助
貴重 难得且值得重视
豊富 数量或种类充足
豊作 农作物收成好
豊か 生活等充裕
不足 未达到需要的数量
欠乏 严重缺少所需的东西
余分 超出所需的部分
負担 需要承担的工作或费用
貢献 为整体带来积极作用
提供 把所需的东西拿出来供使用
供給 向需要方持续提供
需要 对商品或服务的需求
資源 可供利用的天然物质等
資金 开展某事所需的款项
資料 研究或说明问题所用的材料
素材 制作物品或作品所用的原料
材料 加工或判断所依据的东西
原料 制造产品最初使用的物质
品質 物品的性质和质量
質素 生活简单不铺张
素朴 自然而不加修饰
豪華 外观华丽且花费较多
優雅 举止等文雅从容
優秀 表现明显出色
優先 在次序上放在前面
優劣 好坏或高下的差别
独自 自己所独有的方式
独特 明显不同于其他事物
独立 不依赖其他事物而存在
独創 不模仿他人而自己创造
創造 创出新的事物
創作 制作文学艺术作品
想像 在心里描绘尚未亲见的事物
想定 预先设定某种情况
構想 对整体内容作初步设想
発想 思考时出现的新点子
提案 提出供讨论的想法
提携 双方合作开展业务
結束 多人紧密团结
結晶 物质形成规则排列的固体
結合 多个部分结合在一起
結論 讨论后得出的最终判断
要旨 一段话或文章的主要内容
要点 事情最关键的部分
概要 整体的大致内容
詳細 具体而细致的内容
総合 将多个方面合在一起考虑
総括 对整体情况作归纳
統一 使多个方面保持一致
調査 为了解情况收集材料
分析 将整体拆开研究各部分
分類 按照相同特征分组
分担 分别承担一部分工作
分配 按一定方式分别给予
分野 某一类活动的领域
領域 某种知识或活动涉及的范围
分量 物品的多少
割合 部分在整体中所占的比例
比率 两个数量之间的比例
比例 两个数值按同一规律变化
平均 总和除以个数所得的数值
標準 判断或测量使用的准则
基準 判断时作为依据的尺度
基礎 支撑事物的基本部分
基本 最根本的内容
基盤 支撑活动开展的基础
基調 整体贯穿的主要风格
背景 事情发生的环境和原因
背後 物体的后方
周辺 某个中心附近的范围
周囲 环绕四周的部分
循環 不断循环往复
周期 同种现象再次出现的间隔
持続 某种状态连续保持
永続 长久持续
長期 延续很久的时期
短期 延续较短的时期
臨時 根据临时需要安排
臨機 状况发生时及时应对
即座 当场立刻
即時 在同一时间马上进行
一律 对所有情况采用同一标准
一貫 从头到尾保持相同方针
一概 不区分情况而笼统处理
一括 把多件事合起来处理
一斉 大家在同一时间一起行动
一応 暂且做到一定程度
一層 程度更加深入
一段 一个阶段或一级
抜群 明显超过其他同类
抜本 从根本上解决
克服 经过努力战胜困难
達者 技能熟练或身体硬朗
熟練 经过反复练习而熟练
熟達 对某种技能非常精通
熟慮 反复而仔细地考虑
熟読 仔细反复地阅读
黙読 不发出声音地阅读
朗読 清楚地大声读出文章
閲覧 阅读查看书刊资料
検索 按条件查找信息
索引 帮助查找内容的目录
引用 使用他人的文字说明观点
参照 对照其他材料加以查看
参考 作为判断和学习的依据
見当 大致的方向或估计
見解 对事物形成的看法
見通し 对未来进展的预计
見積もり 预先计算的所需费用
手配 为事情进展预先安排
手際 处理事务的方法和熟练程度
手軽 方便而不费事
手間 做事情所需的时间和劳力
手頃 价格或大小等恰到好处
心地 接触某物时的感受
心得 对做事方式的理解和分寸
心掛け 平时注意坚持的态度
心構え 面对某事的思想准备
気兼ね 因顾及他人而拘束
気遣い 对他人的关照和体贴
気軽 不拘束且没有负担
気楽 心里没有太多压力
気分 当时的心情或身体感觉
気質 一个人较稳定的性情
気象 大气中发生的自然现象
気候 一地长期的天气特征
気圧 空气产生的压力
湿度 空气含水分的程度
密度 单位体积内的物质多少
濃度 溶液中某种成分的比例
角度 两条线等相交形成的张开程度
速度 单位时间内运动的快慢
頻度 某事在一定时间内出现的次数
振動 物体在一定位置附近往复运动
振幅 振动偏离中心的最大距离
波紋 水面受扰动后扩散的圈状波纹
波長 波动中重复位置之间的距离
透明 能透光并看清另一侧
鮮度 食物保持新鲜的程度
風味 食物特有的香气和味道
香辛料 为菜肴增添香味或辣味的调料
調味料 调整菜肴味道的食品原料
食材 用于做饭的食物材料
献立 一餐所安排的菜式
炊事 烧饭做菜的家务
洗剤 清除污垢的清洁用品
食器 盛放食物的器皿
家具 在室内使用的桌椅柜等物品
寝具 睡觉使用的被褥等用品
衣類 各种穿着用的物品
衣装 演出等特定场合所穿的服装
衣料 制作服装的材料或衣服
織物 纱线织成的布料
繊維 细长柔韧的丝状材料
絨毯 铺在地上的厚织物
座布団 坐时垫在身下的小软垫
浴衣 夏天穿的轻便和式衣服
足袋 大脚趾分开的传统袜子
襖 日式房间里可滑动的隔扇
障子 糊纸的透光格子门
戸棚 带门的储物柜
雨戸 防雨挡风的外层门板
雨具 雨天防淋湿的用品
蛇口 水管出水处的开关
栓 塞住孔口的零件
便箋 写信时用的纸
定規 画直线或测长度的工具
書籍 正式出版的图书
書類 办事时使用的文件材料
筆記 用笔把内容记录下来
筆者 文章的撰写者
執筆 提笔写文章
随筆 自由记录见闻和感想的文章
随時 根据需要随时安排
随分 程度比预想高很多
随所 各个地方
随伴 伴随另一件事出现
随意 按自己的意思安排
賃貸 收取费用后将物品租给别人
賃金 为劳动支付的报酬
賃貸借 出租与承租的关系
家賃 租住房屋支付的费用
敷金 租房时预交的担保款
礼金 租房时向房东支付的酬谢款
倉庫 储存货物的建筑
物置 存放杂物的地方
納屋 农家存放器具的附属建筑
軒先 房檐前面的空间
縁側 日式房间外缘的走廊平台
日陰 阳光照不到的地方
日向 阳光照得到的地方
木陰 树木遮出的阴凉处
紅葉 叶片在秋天变色的现象
落葉 叶子从树上落下
新緑 初夏新长出的绿色枝叶
若葉 刚长出的嫩叶
苗木 为移植栽培的小树
植木 栽种供观赏的树木
草花 开花的草本植物
雑草 非栽培而自行生长的草
芝生 成片种植的矮草地
花壇 专门种花的一块地
園芸 栽培花草蔬果的活动
栽培 人工种植并照料植物
収穫 收获成熟的农作物
果樹 结出可食用果实的树木
果汁 从水果中榨出的液体
果肉 水果可食用的柔软部分
穀物 稻麦等粮食作物
穀類 谷物这一类粮食
穀倉 储存粮食的仓库
穂 稻麦等顶端长籽实的部分
麦 大麦小麦等作物的统称
稲 稻谷植物
藁 稻麦脱粒后留下的茎
種子 可用于繁殖植物的颗粒
発芽 种子开始长出幼芽
開花 花朵绽开的过程
結実 植物结出果实
肥料 为植物补充养分的材料
土壌 供植物生长的地表土层
地盤 建筑物下面的地层基础
地形 地表高低起伏的形态
地質 地层和岩石的性质
岩石 自然形成的坚硬矿物集合体
鉱物 构成岩石的天然物质
砂利 用于铺路等的小碎石
砂浜 由沙子形成的海边滩地
海辺 海岸附近的地方
渓谷 山间被流水切出的谷地
山脈 连续成列的群山
山頂 山的最高处
山麓 山脚附近的地区
麓 山脚下的地方
丘陵 由连绵低丘组成的地形
平野 地势平坦开阔的陆地
盆地 四周较高中间较低的地形
高原 海拔较高且较平坦的地区
河川 大小河流的统称
河口 河流汇入海洋等的位置
水源 河流等水体的起始处
水面 水体最上方的表面
水滴 小颗粒状的水
水蒸気 水变成的气体
蒸発 液体转为气体的过程
蒸気 液体受热等形成的气体
凝固 液体转为固体的过程
凍結 液体因降温而结冰
霜 地表形成的白色冰晶
霧 靠近地面的细小水滴集合体
露 清晨等凝结在物体表面的水滴
雷 伴随闪电出现的响声
稲妻 天空中出现的闪电
夕立 夏日下午骤然下起的雨
梅雨 初夏持续阴雨的时节
晴天 天空晴朗的天气
快晴 几乎没有云的晴朗天气
曇天 被云层覆盖的天气
曇り 天空被云遮住的状态
降水 从大气落到地面的水分
降雨 下雨这一现象
降雪 下雪这一现象
積雪 地面上堆积的雪
雪解け 积雪融化的过程
雪崩 山坡积雪突然下滑的现象
雪原 被雪覆盖的原野
氷河 高山或高纬地区缓慢流动的冰体
氷山 海中漂浮的大型冰块
流氷 在水面漂动的冰块
北極 地球北端的区域
南極 地球南端的区域
赤道 地球中部假想的纬线
緯度 表示南北位置的角度
経度 表示东西位置的角度
海抜 以海面为基准的高度
標高 地点相对于基准面的高度
"""

# 每词两种，避开原手写题已考过的词形；词性必须与 JMdict 一致。
VERBS = """
似合う 相称
届く 送达
気付く 察觉
片付く 整理完毕
伝わる 传达开来
減る 减少
申し込む 申请报名
防ぐ 防止
認める 认可
与える 给予
応じる 回应
述べる 陈述
含める 包括在内
扱う 对待或处理
備える 配备
譲る 让给
諦める 放弃
迷う 犹豫或迷路
頼る 依靠
震える 发抖
進める 推进
迫る 迫近
補う 补足
見直す 重新检查
見送る 送别
受け入れる 接受
取り組む 着手处理
保つ 保持
遂げる 完成
伴う 伴随
踏まえる 依据
掲げる 提出目标或挂起
耐える 忍耐
整える 整理调整
見渡す 放眼望去
促す 促使
取り戻す 取回
引き起こす 引发
施す 实施措施
満たす 满足或装满
養う 培养
緩める 放松
申し出る 提出请求或帮助
受け止める 接住或正视
"""

ADJECTIVES = """
暑い 炎热
寒い 寒冷
忙しい 忙碌
楽しい 愉快
新しい 新
古い 旧
明るい 明亮
暗い 昏暗
広い 宽敞
狭い 狭窄
安い 便宜
高い 高或贵
長い 长
短い 短
重い 重
軽い 轻
便利 方便
元気 精神好
有名 有名
親切 热心
"""

# 具体名词的中文问法刻意避开中日同形；中文释义和提示为原创。
TRANSLATIONS = """
洗剤|清洁剂|清洗衣物或餐具时用来去污的用品
楽器|演奏器具|用来演奏旋律或节奏的物品
泉|泉水|地下水自然涌出地面形成的水体
列車|火车|在轨道上连成一列行驶的交通工具
停留所|公交站点|公交车沿线固定停靠供人上下车的位置
名刺|名片|印有姓名等信息、初次见面时交换的小卡片
乗客|乘车的人|搭乘交通工具出行的人
砂漠|沙漠|降水稀少且植被稀疏的广大地带
調味料|调味品|烹饪时用来调整味道的材料
倉庫|存货库房|集中存放货物的建筑
通帳|银行存折|记录存取款情况的小册子
乾電池|遥控器常用的一次性筒状电源|无需连接电线，可装入小型电器中使用
物置|杂物间|收放不常用物品的小屋或空间
踏切|铁路道口|铁路与道路平面交会的位置
温室|玻璃暖房|围护起来、适宜栽培植物的保温建筑
蛇口|水龙头|水管出水处控制水流的开关
風呂敷|包袱布|包裹并携带物品用的方形织物
水筒|随身水壶|出门时携带饮用水的容器
印鑑|印章|盖在文件上表示本人确认的小型工具
水田|稻田|灌水后种植稻谷的田地
制服|统一服装|学校或单位规定穿着的相同款式衣服
昆虫|六足小动物|身体分为头胸腹且有六条足的一类小动物
花壇|花圃|划出来专门种植花草的一块区域
貝殻|贝类的坚硬外壳|软体动物身体外面的坚硬保护层
化石|古生物遗存|古代生物遗体或痕迹保存在岩层中的遗存
三味線|三弦拨弦琴|一种用拨子演奏的传统三弦弦乐器
陶器|陶制器皿|用黏土烧成的器具
織物|纺织布料|用纱线纵横交织而成的材料
繊維|细丝状纺织原料|细长柔韧、能用于纺纱的丝状材料
衣装|演出服装|舞台表演等场合所穿的特定服饰
香辛料|香料和辛辣调料|用来为菜肴增添香气或辣味的材料
雨具|防雨用品|下雨时用来避免淋湿的各类用品
便箋|写信纸|专门用来书写信件的纸张
定規|直尺|画直线或测量长度的文具
座布団|坐垫|坐在地面时垫在身下的小软垫
戸棚|储物柜|带有柜门、分层存放物品的家具
"""


def pairs(text):
    return dict(line.split(maxsplit=1) for line in text.strip().splitlines())


def load_jlpt(paths):
    levels = {}
    for path in paths:
        with path.open(encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                level_match = re.fullmatch(r"n([1-5])", path.stem.lower())
                if level_match:
                    word = row["expression"]
                    level = int(level_match[1])
                    levels[word] = max(level, levels.get(word, 0))
    return levels


def load_jmdict(path, wanted):
    raw = gzip.decompress(path.read_bytes()) if path.suffix == ".gz" else path.read_bytes()
    # 不解析外部实体和 DTD；把词性实体保留为代码，避免执行不可信实体展开。
    raw = re.sub(rb"<!DOCTYPE[^\[]*\[.*?\]>", b"", raw, flags=re.S)
    raw = re.sub(rb"&([\w-]+);", lambda m: m[0] if m[1] in (b"amp", b"lt", b"gt", b"quot", b"apos") else m[1], raw)
    result = {word: {"common": set(), "readings": set(), "pos": set()} for word in wanted}
    for _, element in ET.iterparse(io.BytesIO(raw), events=("end",)):
        if element.tag != "entry":
            continue
        words = {e.text for e in element.findall("k_ele/keb")} & wanted
        for word in words:
            item = result[word]
            for reading in element.findall("r_ele"):
                restrictions = [e.text for e in reading.findall("re_restr")]
                if reading.find("re_nokanji") is not None or restrictions and word not in restrictions:
                    continue
                kana = reading.findtext("reb")
                item["readings"].add(kana)
                if any(e.text in {"news1", "ichi1", "spec1", "gai1"} for e in reading.findall("re_pri")):
                    item["common"].add(kana)
            for sense in element.findall("sense"):
                restrictions = [e.text for e in sense.findall("stagk")]
                if not restrictions or word in restrictions:
                    item["pos"].update(e.text for e in sense.findall("pos"))
        element.clear()
    return result


def katakana(text):
    return "".join(chr(ord(c) + 96) if "ぁ" <= c <= "ゖ" else c for c in text)


FORMS = ("ます形", "て形", "た形", "ない形", "可能形", "意志形", "受身形", "使役形", "ば形", "使役受身形")
GODAN = dict(zip("うくぐすつぬぶむる", ("わいうえお", "かきくけこ", "がぎぐげご", "さしすせそ", "たちつてと", "なにぬねの", "ばびぶべぼ", "まみむめも", "らりるれろ")))


def conjugate(word, kind, form):
    stem, end = word[:-1], word[-1]
    if kind == "v1":
        suffix = ("ます", "て", "た", "ない", "られる", "よう", "られる", "させる", "れば", "させられる")[FORMS.index(form)]
        return stem + suffix, f"一段动词去る加{suffix}。"
    a, i, _, e, o = GODAN[end]
    if form in ("て形", "た形"):
        suffix = {"う": "って", "つ": "って", "る": "って", "む": "んで", "ぶ": "んで", "ぬ": "んで", "く": "いて", "ぐ": "いで", "す": "して"}[end]
        if word in ("行く", "いく"):
            suffix = "って"
        if form == "た形":
            suffix = suffix[:-1] + ("だ" if suffix.endswith("で") else "た")
        return stem + suffix, f"五段动词以{end}结尾，{form}将词尾换成{suffix}。"
    suffix = {"ます形": i + "ます", "ない形": a + "ない", "可能形": e + "る", "意志形": o + "う", "受身形": a + "れる", "使役形": a + "せる", "ば形": e + "ば", "使役受身形": a + "せられる"}[form]
    return stem + suffix, f"五段动词将词尾{end}换成{suffix}。"


def unique_reading(data, word):
    common = data[word]["common"]
    if len(common) != 1:
        raise ValueError(f"{word} 的常用读音不唯一：{sorted(common)}")
    return next(iter(common))


def generate(levels, data):
    questions, rejected = [], []
    readings = pairs(READINGS)
    # 原题 r01-r16 不重复考同一个词。
    original = {"山", "川", "駅", "学校", "図書館", "毎朝", "今日", "明日", "昨日", "大人", "一人", "二人", "手紙", "牛乳", "荷物", "季節"}
    eligible = {1: [], 2: [], 3: []}
    for word, meaning in sorted(readings.items()):
        if word in original:
            continue
        if levels.get(word) not in eligible or len(word) < 2 or len(data[word]["common"]) != 1:
            rejected.append(word)
            continue
        eligible[levels[word]].append((word, meaning))
    common_n1 = {"一律", "中断", "了承", "介護", "企画", "優先", "共感", "出費", "分担", "分配", "勤務", "原則", "品質", "対応", "応募", "成果", "手配", "手順", "採用", "提供"}
    for level, count in ((3, 25), (2, 70), (1, 51)):
        pool = sorted(eligible[level], key=lambda pair: (pair[0] not in common_n1, pair[0]))
        if len(pool) < count:
            raise ValueError(f"N{level} 读音题不足：{len(pool)}")
        for word, meaning in pool[:count]:
            kana = unique_reading(data, word)
            questions.append([f"r-{word}", level != 1 or word in common_n1, f"写出「{word}」的平假名读音。", [kana, katakana(kana)], f"读音·N{level}", meaning, "", f"{word}读{kana}。"])
    if len(questions) != 146:
        raise ValueError(f"读音题不足：{len(questions)}")
    verbs = pairs(VERBS)
    for index, (word, meaning) in enumerate(sorted(verbs.items())):
        if word not in levels:
            raise ValueError(f"JLPT 中没有 {word}")
        kana = unique_reading(data, word)
        kinds = {p for p in data[word]["pos"] if p == "v1" or p.startswith("v5")}
        if len(kinds) != 1:
            raise ValueError(f"{word} 的动词分类不唯一：{kinds}")
        kind = next(iter(kinds))
        safe_forms = {
            "似合う": ("ます形", "ば形"), "届く": ("た形", "ない形"),
            "気付く": ("ば形", "ない形"), "片付く": ("た形", "て形"),
            "伝わる": ("ます形", "ない形"), "減る": ("て形", "ば形"),
            "迷う": ("て形", "た形"), "震える": ("ます形", "ない形"),
            "迫る": ("ます形", "た形"), "伴う": ("て形", "ない形"),
            "耐える": ("可能形", "ば形"),
            "遂げる": ("可能形", "意志形"), "諦める": ("ない形", "た形"),
        }
        for form in safe_forms.get(word, (FORMS[index % 10], FORMS[(index + 4) % 10])):
            written, rule = conjugate(word, kind, form)
            spoken, _ = conjugate(kana, kind, form)
            names = list(dict.fromkeys([written, spoken]))
            # 五段的使役受身常省略せら中的せ，す结尾除外；两种标准说法都接受。
            if form == "使役受身形" and kind != "v1" and not word.endswith("す"):
                names += [written.replace("せられる", "される"), spoken.replace("せられる", "される")]
                rule += "非す结尾的五段动词也接受常用缩约形。"
            questions.append([f"f-{word}-{form}", levels[word] != 1, f"写出「{word}」的{form}。\n使用标准形式，不省略「ら」。" if kind == "v1" and form == "可能形" else f"写出「{word}」的{form}。", names, f"动词{form}·N{levels[word]}", f"原词表示：{meaning}。", "", rule])
    # 八道补足形容词活用；各种形各两道，避免某种活用过多。
    adj_forms = ("过去形", "否定形", "て形", "副词形")
    selected_adj = ("暑い", "寒い", "忙しい", "楽しい", "便利", "元気", "有名", "親切")
    for index, word in enumerate(selected_adj):
        if word not in levels:
            raise ValueError(f"JLPT 中没有 {word}")
        kana = unique_reading(data, word)
        na = index >= 4
        expected = "adj-na" if na else "adj-i"
        if expected not in data[word]["pos"]:
            raise ValueError(f"{word} 缺少词性 {expected}")
        form = adj_forms[index % 4]
        suffix = (("だった", "ではない", "で", "に") if na else ("かった", "くない", "くて", "く"))[index % 4]
        answers = [(word if na else word[:-1]) + suffix, (kana if na else kana[:-1]) + suffix]
        if na and form == "否定形":
            answers += [word + "じゃない", kana + "じゃない"]
        rule = f"な形容词词干加{suffix}。" if na else f"い形容词去掉末尾い，加{suffix}。"
        questions.append([f"f-{word}-{form}", True, f"写出「{word + 'だ' if na else word}」的普通{form}。", answers, f"形容词{form}·N{levels[word]}", f"原词表示：{pairs(ADJECTIVES)[word]}。", "", rule])
    translations = []
    for line in sorted(TRANSLATIONS.strip().splitlines()):
        word, chinese, hint = line.split("|")
        if word not in levels:
            rejected.append(word)
            continue
        kana = unique_reading(data, word)
        # 同形词只要求假名，不能把题面的汉字收入答案。
        names = [word, kana]
        if any(name in chinese for name in names):
            rejected.append(word)
            continue
        translations.append([f"c-{word}", levels[word] != 1, f"把中文译成日语：{chinese}。", names, f"中译日·具体名词·N{levels[word]}", hint, "", f"这里可用{word}（{kana}）。"])
    if len(translations) < 30:
        raise ValueError(f"中译日不足：{len(translations)}")
    questions.extend(translations[:30])
    print(f"生成 {len(questions)} 道；筛掉非词表、单汉字、多常用读音或同形词：{'、'.join(sorted(set(rejected)))}")
    return sorted(questions, key=lambda q: q[0])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jlpt", nargs="+", type=Path, required=True, help="下载好的 N5 至 N1 CSV")
    parser.add_argument("--jmdict", type=Path, required=True, help="JMdict_e XML 或 .gz")
    parser.add_argument("--jlpt-license", type=Path, required=True, help="open-anki-jlpt-decks 的 LICENSE")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    wanted = set(pairs(READINGS)) | set(pairs(VERBS)) | set(pairs(ADJECTIVES)) | {line.split("|")[0] for line in TRANSLATIONS.strip().splitlines()}
    data = {
        "source": [
            {"name": "Jonathan Waller (tanos.co.uk) JLPT lists; open-anki-jlpt-decks", "url": "https://github.com/jamsinclair/open-anki-jlpt-decks", "original": "https://www.tanos.co.uk/jlpt/", "license": "CC BY (tanos); MIT (open-anki-jlpt-decks)"},
            {"name": "JMdict — James William BREEN and EDRDG", "url": "https://www.edrdg.org/jmdict/j_jmdict.html", "license": "CC BY-SA 4.0", "terms": "https://www.edrdg.org/edrdg/licence.html"},
        ],
        "license": "CC BY-SA 4.0 — https://creativecommons.org/licenses/by-sa/4.0/; JMdict-derived readings and parts of speech; Chinese clues, questions and conjugation explanations authored for Guessless.",
        "notices": {"open-anki-jlpt-decks": args.jlpt_license.read_text(encoding="utf-8")},
        "inputs": {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted([*args.jlpt, args.jmdict, args.jlpt_license])},
        "questions": generate(load_jlpt(args.jlpt), load_jmdict(args.jmdict, wanted)),
    }
    args.output.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
