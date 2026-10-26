# -*- coding: utf-8 -*-
"""生成《历史相似检索系统说明书》PDF 与 DOCX（面向普通用户的中文手册）。

一次运行同时产出：
    packaging/manual/历史相似检索系统说明书.pdf   （reportlab，A4，中文）
    packaging/manual/历史相似检索系统说明书.docx  （python-docx）

两份文件共用同一份内容模型 CONTENT，保证内容一致；PDF 侧显式注册系统自带的
SimHei 单体 TTF 以保证中文正常，DOCX 侧显式设置 w:eastAsia 中文字体。

用法：
    python packaging/make_manual.py
"""
from __future__ import annotations

import os
import sys

# ─────────────────────────────────────────────────────────────────────
# 常量
# ─────────────────────────────────────────────────────────────────────
VERSION = "v2.2.0"
DOC_DATE = "2026-10-26"
TITLE = "历史相似检索系统"
SUBTITLE = "用户说明书"
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "manual")
PDF_PATH = os.path.join(OUT_DIR, "历史相似检索系统说明书.pdf")
DOCX_PATH = os.path.join(OUT_DIR, "历史相似检索系统说明书.docx")

# 系统自带中文字体（单体 TTF，reportlab 直接注册即可）
FONT_CANDIDATES = [
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\simkai.ttf",
    r"C:\Windows\Fonts\Deng.ttf",
    # 构建机为 Linux 时使用文泉驿 TrueType 字体；.ttc 需要指定子字体索引
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
]
DOCX_FONT = "SimHei"          # Word 侧中文字体名（显示用）
DOCX_LATIN = "SimHei"


# ─────────────────────────────────────────────────────────────────────
# 内容模型：block 都是元组 (kind, ...)
#   ("h1", 文本) ("h2", 文本) ("h3", 文本) ("p", 文本)
#   ("ul", [条目, ...]) ("tb", [表头...], [[行...], ...]) ("note", 文本)
# ─────────────────────────────────────────────────────────────────────
CONTENT = [
    # ═══════════════ 1 产品简介 ═══════════════
    ("h1", "1  产品简介"),
    ("p", "「历史相似检索系统」是一款安装在本机、单机单人使用的桌面工具。它的核心任务只有一句话："
          "在多年的历史气象资料里，帮你找出与「目标日期」天气形势最像的历史时刻，并据此估算降水概率，"
          "最后自动生成一批图表供你判断。"),
    ("p", "打个比方：把「目标日期某区域的大气状况」当成一份刚发下来的考卷，把历史上每一天的大气状况当成一位位考生。"
          "系统会把这张考卷和每一位考生逐一比对，挑出「答卷最像」的那几位历史考生；"
          "再看这几位历史考生当年那几天究竟下了多少雨，从而给出「今年这种形势，降水概率大概多大」的参考。"),
    ("h2", "1.1  它解决什么问题"),
    ("ul", [
        "在 TB 级的 ERA5 历史再分析资料里，快速找出与目标日期大气形势最相似的历史时刻，避免人工翻查。",
        "用找到的历史相似时刻，结合逐小时降水资料（GMCP），统计出降水概率产品（如 ≥50mm 强降水出现的比例）。",
        "把「相似的历史时刻」和「降水概率」画成直观的对比图与分布图，方便做预报、防汛、科研决策。",
    ]),
    ("h2", "1.2  适用对象"),
    ("p", "本系统面向不懂气象、也不懂编程的普通用户，例如气象业务与预报人员、防汛与水文人员、高校与科研人员等。"
          "使用时全部操作都在浏览器界面里点击完成，不需要敲命令、不需要写代码。"),
    ("h2", "1.3  主要功能一览"),
    ("tb", ["功能", "说明"], [
        ["数据源登记", "登记本机 ERA5 要素目录与 GMCP 降水目录，只登记不改动、不复制数据"],
        ["数据看板", "查看已登记的数据文件数量、覆盖的年份与月份、最近任务与操作"],
        ["历史相似检索", "按区域、时间域、要素与权重在历史资料中找出最相似的若干历史时刻"],
        ["组合策略", "用「交集组 + 直取组」把多个要素的相似结果合并成最终相似时刻集合"],
        ["降水概率产品", "对相似时刻的逐小时降水按 6 小时 / 24 小时窗口累加并统计概率"],
        ["数据预处理", "把 GMCP 逐小时降水累加为北京时间 24/12/6 小时产品，并可一键登记（V2.1.0 新增）"],
        ["GMCP 累计降水检索", "直接以累计降水场为目标变量检索相似历史日，产出平均/中位数/阈值概率产品（V2.1.0 新增）"],
        ["结果后处理", "结果页改 TopN 或自定义百分位即可重算产品图，无需重新检索；可分位数产品图与结果包导出（V2.2.0 新增）"],
        ["结果图表", "自动生成要素场对比图、直取要素场图、降水统计图、空间概率分布图、累计降水产品图"],
        ["历史结果回看", "随时回看过去任意一次任务的检索配置与产物"],
        ["操作记录", "完整记录数据源登记、检索、异常等操作，便于复现与追溯"],
    ]),
    ("note", "本系统只做「历史相似检索」，不改变、不订正原始气象资料；所有结论都来自历史样本的统计，"
             "是辅助判断的参考，不能替代专业预报。"),

    # ═══════════════ 2 运行环境要求 ═══════════════
    ("h1", "2  运行环境要求"),
    ("tb", ["项目", "要求"], [
        ["操作系统", "Windows 10 / Windows 11（64 位）"],
        ["中文字体", "系统自带中文字体（如 微软雅黑 Microsoft YaHei / 黑体 SimHei）；Windows 默认已具备"],
        ["数据磁盘", "ERA5 / GMCP 资料需位于本机可访问的磁盘、移动硬盘或已挂载的网络盘"],
        ["Python 环境", "无需安装，程序自带运行环境"],
        ["网络", "无需联网，全程在本机运行（访问地址为 127.0.0.1，即本机自己）"],
        ["显示环境", "任意现代浏览器（Edge / Chrome / Firefox 等）"],
    ]),
    ("h2", "2.1  关于中文字体"),
    ("p", "产品图上的地名、坐标轴、图注都会用到中文字体。Windows 系统默认自带中文字体，正常情况无需任何设置。"
          "如果发现图上的中文显示成一个个「方框」，说明系统缺少可用中文字体，"
          "请安装「微软雅黑」或「黑体」后重新检索出图（详见第 11 章常见问题）。"),
    ("h2", "2.2  关于数据磁盘"),
    ("p", "ERA5 与 GMCP 资料体量可达 TB 级，通常放在独立的资料盘（例如 I: 盘）。"
          "使用前请确认这些资料盘已经在本机可见、可读取；若是移动硬盘或网络盘，"
          "请先在资源管理器里确认盘符已经挂载并能打开，再回到系统的「数据源」页登记。"),

    # ═══════════════ 3 安装 ═══════════════
    ("h1", "3  安装"),
    ("p", "安装过程非常简单，全程按向导点击即可，且安装不需要管理员权限。"),
    ("ul", [
        "第 1 步：双击安装包（.exe 文件）启动安装向导。",
        "第 2 步：按向导提示逐步点击「下一步」。",
        "第 3 步：如需要，可修改安装位置；默认安装到当前用户目录，无需管理员权限。",
        "第 4 步：点击「安装」，等待进度走完。",
        "第 5 步：点击「完成」结束安装。",
    ]),
    ("h2", "3.1  安装位置与快捷方式"),
    ("p", "程序默认「绿色免管理员」安装到当前用户目录：%LOCALAPPDATA%\\Programs\\Histsim。"
          "安装完成后，会在「开始菜单」和「桌面」各生成两个快捷方式："),
    ("tb", ["快捷方式", "作用"], [
        ["历史相似检索系统", "双击启动本系统（在后台拉起服务并自动打开浏览器）"],
        ["停止历史相似检索系统", "双击完全停止后台服务（连检索用的子进程一并结束）"],
    ]),
    ("h2", "3.2  程序数据目录"),
    ("p", "程序运行产生的数据（元数据库、结果、缓存、运行日志）保存在另一个目录：%LOCALAPPDATA%\\Histsim。"
          "它和安装目录相互独立，卸载程序不会自动删除这里的数据（详见第 13 章与本手册附录）。"),
    ("note", "安装、卸载都只影响当前 Windows 用户，不需要管理员权限，也不会写入系统目录。"),

    # ═══════════════ 4 启动与停止 ═══════════════
    ("h1", "4  启动与停止"),
    ("h2", "4.1  启动"),
    ("ul", [
        "双击桌面上的「历史相似检索系统」快捷方式。",
        "系统会在后台拉起本地 Web 服务，并自动打开浏览器。",
        "首次启动会稍慢（需要初始化运行环境），请耐心等待浏览器页面出现。",
        "正常情况下浏览器会自动打开地址：http://127.0.0.1:8770/",
    ]),
    ("p", "如果浏览器没有自动打开，请手动打开浏览器，在地址栏输入 http://127.0.0.1:8770/ 后回车即可。"
          "如果提示打不开，可以稍等几秒再试，或用「停止」快捷方式停掉服务后重新启动。"),
    ("h2", "4.2  重复双击不会启动两份服务"),
    ("p", "如果服务已经在运行，再次双击「历史相似检索系统」快捷方式不会重复启动服务，"
          "只会再打开一个浏览器标签页指向同一个界面，可以放心重复点击。"),
    ("h2", "4.3  停止"),
    ("p", "需要停止时，双击桌面上的「停止历史相似检索系统」快捷方式即可。"
          "它会结束后台服务及其检索子进程并释放端口；已经完成的检索结果、数据源登记与切片缓存都会保留，"
          "下次启动后可继续使用。"),
    ("note", "日常使用后如果没有正在进行的检索，建议用「停止历史相似检索系统」把后台服务停掉，避免长期占用内存与端口。"),

    # ═══════════════ 5 第一次使用：登记数据源 ═══════════════
    ("h1", "5  第一次使用：登记数据源（最关键的一步）"),
    ("p", "系统本身不携带任何气象数据（ERA5 / GMCP 体积太大，不随安装包分发）。"
          "你必须先告诉系统「数据放在哪里」，它才能开始检索。这一步叫「登记数据源」。"),
    ("note", "系统只登记目录位置，不搬运、不复制、不预处理你的数据；检索时按文件命名规则直接读取原始文件。"),
    ("h2", "5.1  打开「数据源」页"),
    ("ul", [
        "在左侧导航栏点击「数据源」。",
        "页面顶部标题显示「数据源」，副标题为「登记 ERA5 要素目录与 GMCP 降水目录」。",
        "页面上有「登记数据源」表单，包含「数据源根目录」「扫描深度」「扫描并登记」按钮。",
    ]),
    ("h2", "5.2  填写数据源根目录"),
    ("p", "在「数据源根目录」输入框里填写你的数据所在目录。系统识别两种资料："),
    ("tb", ["资料类型", "目录与文件命名", "举例"], [
        ["ERA5 要素目录", "逐年逐日的单要素文件 era5.<要素>.<yyyymmdd>.nc",
         "era5.u_component_of_wind.20240427.nc"],
        ["GMCP 降水年目录", "按年份组织的逐小时文件 <yyyy>/GMCP_<yyyy>_<mm>_<dd>_<hh>.nc",
         "2020/GMCP_2020_04_27_06.nc"],
    ]),
    ("p", "其中 ERA5 的「要素」可为：u_component_of_wind（U 风）、v_component_of_wind（V 风）、"
          "temperature（温度）、specific_humidity（比湿）、relative_humidity（相对湿度）。"
          "一个目录里若同时含多个要素，系统会按要素分别登记。"),
    ("h2", "5.3  选择扫描深度"),
    ("p", "「扫描深度」决定从根目录往下找几层，可选项为："),
    ("tb", ["选项", "含义"], [
        ["仅本层", "只在根目录这一层查找数据文件"],
        ["含一层子目录", "在根目录及其下一级子目录中查找（默认）"],
        ["含两层子目录", "在根目录及往下两级子目录中查找"],
    ]),
    ("h2", "5.4  扫描并登记"),
    ("ul", [
        "点击「扫描并登记」按钮。",
        "系统开始扫描目录并统计覆盖情况；如果目录里有 20 万级文件，首次扫描需要一点时间，请耐心等待。",
        "扫描完成后，页面提示「扫描完成：登记 N 项」，下方「已登记」表格会列出每一项目录的类型、要素、文件数、年份、月份与状态。",
    ]),
    ("p", "同一种要素可以登记多个目录，检索时按登记顺序取第一个命中的文件（例如数据分了几个盘存放）。"
          "如需移除某项登记，点击该行右侧的「移除」按钮即可，移除只删除登记，不会删除磁盘上的文件。"),
    ("h2", "5.5  到「数据看板」页确认"),
    ("ul", [
        "点击左侧导航「数据看板」。",
        "顶部统计与四个关键指标会显示：ERA5 要素目录数、GMCP 降水目录数、数据文件总量、覆盖时间跨度。",
        "下方「数据覆盖矩阵」按「行 = 要素 × 年份、列 = 月份」展示，色块越深表示该月可用文件越多。",
        "确认覆盖的年份、月份与要素是否满足你的检索需求；不足时回到「数据源」页补充登记。",
    ]),
    ("h2", "5.6  数据预处理：生成北京时间累计降水产品（V2.1.0 新增）"),
    ("p", "GMCP 原始资料是「世界时逐小时」文件。V2.1.0 起，系统可以把它们累加成三组北京时间累计降水产品，"
          "并把产品目录像普通数据源一样登记后直接参与检索。"),
    ("tb", ["产品", "北京时间窗口", "文件名示例"], [
        ["24h（20–20）", "前一日 20 时 — 当日 20 时", "GMCP_24h_Rain_20000601_20_20LST.nc"],
        ["24h（08–08）", "当日 08 时 — 次日 08 时", "GMCP_24h_Rain_20000601_08_08LST.nc"],
        ["12h（08）", "前一日 20 时 — 当日 08 时", "GMCP_12h_Rain_20000601_08LST.nc"],
        ["12h（20）", "当日 08 时 — 20 时", "GMCP_12h_Rain_20000601_20LST.nc"],
        ["6h（02/08/14/20）", "20→02、02→08、08→14、14→20", "GMCP_6h_Rain_20000601_02LST.nc 等"],
    ]),
    ("ul", [
        "打开左侧「数据预处理」页，填写 GMCP 逐小时源目录（含 <yyyy>/GMCP_*.nc）与输出目录。",
        "选择起始/结束年份与产品分组（24h / 12h / 6h），点击「扫描源数据」可先核对文件数与网格。",
        "点击「开始预处理」后，页面实时显示进度；已存在的产品默认跳过，勾选「覆盖已有产品」才会重算。",
        "完成后点击「登记预处理产物」，系统会自动把 24h/12h/6h 各窗口登记为 gmcp_rain 数据源。",
    ]),
    ("note", "窗口内的取值为「窗口内各小时降水之和」，与需求给出的 UTC 时次区间逐条对齐；"
             "缺测的日期会跳过并计入「缺测未写」，不会用不完整的小时拼出产品。"),

    # ═══════════════ 6 界面总览 ═══════════════
    ("h1", "6  界面总览"),
    ("p", "主界面左侧是导航轨，共七个页面。顶部有一条向导条（数据预处理 → 数据源 → 检索配置 → 执行进程 → 结果产品），"
          "提示你当前处在整个流程的哪一步，点击向导条上的步骤可直接跳转。每个页面底部通常还有「下一步」按钮。"),
    ("tb", ["页面", "作用"], [
        ["数据看板", "数据源覆盖与系统概况：文件数、覆盖年份、覆盖矩阵、最近任务与操作"],
        ["数据源", "登记 ERA5 要素目录、GMCP 逐小时目录与累计降水产品目录，查看已登记项"],
        ["数据预处理", "把 GMCP 逐小时降水累加为北京时间 24/12/6 小时产品并登记（V2.1.0）"],
        ["检索配置", "设置区域、时间域、要素、权重、组合策略、累计降水目标与降水产品等全部检索参数"],
        ["执行进程", "查看当前检索任务的实时进度、分阶段步骤与日志，可取消任务"],
        ["结果产品", "查看相似时刻集合、累计降水产品、降水概率与图表，并回看历史任务"],
        ["操作记录", "检索任务历史与操作日志留痕，便于复现与追溯"],
    ]),
    ("note", "检索配置页的面板可以点击标题折叠/展开，折叠状态会被记住；下一次打开保持上次的样子。"),

    # ═══════════════ 7 检索配置详解 ═══════════════
    ("h1", "7  检索配置详解"),
    ("p", "「检索配置」页决定这次检索「在哪里找、找哪些时间、看哪些要素、怎么算相似、怎么组合、怎么出降水产品」。"
          "下面按页面上的分组逐项说明。所有参数都可以「选一个内置模板打底，再在其上覆盖任意项」。"),

    ("h2", "7.1  模板与目标"),
    ("p", "页面第一个面板是「模板与目标」："),
    ("ul", [
        "「内置模板」下拉可选择 plan_A（暖区降水检索）或 plan_B（锋面热动力检索）打底，也可选「自定义（不套用模板）」。",
        "「目标日期（UTC）」填入你要检索的那一天，格式 YYYY-MM-DD，例如 2024-04-27。目标日期是必填项。",
        "「配置名称」只是给这次配置起个名字，方便你在结果里辨认；改名不会影响计算结果。",
        "面板下方会实时提示「与模板默认值存在差异的项数」，帮你确认改了哪些参数。",
    ]),
    ("note", "选择模板后会载入该模板的全部默认值；你在界面上改动的字段只是「覆盖项」。"
             "判断某次检索到底用了什么参数，以结果里回放的「检索配置」为准。"),

    ("h2", "7.2  区域与时间域"),
    ("p", "这一组决定「检索区域」和「历史样本范围」。检索区域是一个以中心点为中心的矩形（正方形）。"),
    ("tb", ["参数", "含义", "默认值 / 取值"], [
        ["中心纬度", "检索矩形中心的纬度", "23.1652"],
        ["中心经度", "检索矩形中心的经度", "113.3405"],
        ["半边长（度）", "矩形半边长；约 1.0° ≈ 200km，2.5° ≈ 500km", "1.0"],
        ["降水统计点纬度", "统计「点降水」所用格点的纬度（可与中心解耦）", "23.1652"],
        ["降水统计点经度", "统计「点降水」所用格点的经度（可与中心解耦）", "113.3405"],
        ["剔除目标日 ± 天", "排除窗 exclude_days：剔除与目标日相差不超过 N 天的历史样本", "3"],
        ["起始年份 / 结束年份", "历史样本的年份范围（含两端）", "2000 / 2024"],
        ["样本选取方式", "决定历史样本怎么取（见下）", "calendar（日历全量）"],
        ["同期前后 x 天", "仅「历史同期」模式使用", "5（范围 0～60）"],
        ["月份", "参与取样的月份（可多选）；同期模式下被忽略", "4、5、6 月"],
        ["检索时次（UTC）", "取 ERA5 的哪个 UTC 时刻，可选 0/6/12/18，可多选", "0"],
    ]),
    ("h3", "7.2.1  目标日期与历史样本的关系"),
    ("p", "「目标日期」是你要研究的这一天；「历史样本」是系统用来比对的历史上的许多日子。"
          "系统把目标日期当成「考卷」，在历史样本里挑「最像的答卷」。目标日期本身不会作为历史样本参与排名。"),
    ("h3", "7.2.2  样本选取方式：calendar 与 same_period"),
    ("tb", ["方式", "含义"], [
        ["calendar（日历全量）", "在所选年份 × 所选月份 × 当月每一天中取样本（含目标所在年份）。适合按季节整月检索。"],
        ["same_period（历史同期）", "以目标日期的「月/日」为锚点，在每个年份取「前后 ± x 天」的样本，跳过目标年份，并忽略月份选择。"
         "适合「只想找往年在同一时节、前后几天里的相似个例」。"],
    ]),
    ("p", "注意：「检索时次」只影响从 ERA5 取哪些小时，不限制目标日期；目标日期的所有所选时次都会被读取。"),

    ("h2", "7.3  地图圈选检索区域"),
    ("p", "「地图圈选检索区域」面板里是一张广东省底图。你可以在图上按住鼠标拖拽出一个矩形，"
          "松手后系统会把矩形的中心经纬度和半边长自动回填到「区域与时间域」里。"
          "检索区域实际是正方形，边长取所画矩形的长边，图上画出的方框就是真正生效的范围。"),
    ("ul", [
        "「从配置回填」：按当前填写的经纬度和半边长，在地图上重画方框。",
        "「清除圈选」：把方框隐藏（已填写的数值保持不变）。",
    ]),
    ("note", "也可以在「区域与时间域」里直接手工输入经纬度和半边长，两种方式等效，以输入框里的数值为准。"),

    ("h2", "7.4  要素 × 层次 与 组合角色"),
    ("p", "这一组决定「用哪些气象要素、在哪些气压层上做比对」，并给每个「要素 × 层次」组合指定角色。"),
    ("ul", [
        "先在「要素」里勾选要用的气象要素，在「气压层（hPa）」里勾选气压层，系统据此生成一组「变量组合」卡片。",
        "点击某张变量卡片可循环切换角色：不参与 → 交集组 → 直取组 → 不参与……",
        "交集组（各自动 TopN 后取交集）：只有被交集组每个要素都排进各自前 N 名的历史日期，才算相似。",
        "直取组（各自 TopN 直接并入）：只要被直取组任一要素排进各自前 N 名，就直接算相似。",
        "「全部设为交集组」「全部清空角色」：批量为所有变量设置角色。",
        "「额外产出直取要素场图」复选框：勾选后，除了交集要素场图，还会为直取组单独出一张要素场图。",
    ]),
    ("p", "可选要素与单位：U 风（m/s）、V 风（m/s）、温度（K）、比湿（kg/kg）、相对湿度（%）。"
          "变量组合的键由「要素字母 + 层次」构成，例如 u925 表示 U 风 925hPa。"),

    ("h2", "7.5  距离权重与相似度"),
    ("p", "这一组决定「怎么把区域内的差异汇总成一个相似度指数」。相似度指数（SI）越小表示越相似。"),
    ("h3", "7.5.1  权重模式与半径"),
    ("tb", ["模式", "含义", "相关参数"], [
        ["zones（距离分区）", "按格点到区域中心的距离分档给权重，离中心越近权重越大", "距离分区（km, 权重；可分多段）"],
        ["gauss（高斯衰减）", "权重随距离按高斯曲线平滑衰减：w = exp(−(d/r0)²)", "高斯半径 r₀（km）"],
    ]),
    ("p", "默认分区为「50,0.7; 100,0.3」，含义是：距中心 ≤50km 的格点权重 0.7；50～100km 的权重 0.3；"
          "超过 100km 的沿用最后一档权重。高斯模式的默认半径 r₀ 为 50.0 km。"),
    ("h3", "7.5.2  特征处理"),
    ("tb", ["方式", "含义"], [
        ["none（不处理）", "保留原始量纲直接比对；不同要素量级差异大时，数值大的要素会主导相似度"],
        ["standardize（标准化）", "每个要素除以它在历史样本里的标准差，消除量级差异"],
        ["normalize（归一化）", "每个要素按历史样本的最小值、最大值缩放到 0～1"],
    ]),
    ("h3", "7.5.3  相似度度量（六种口径）"),
    ("tb", ["口径", "中文名 / 公式", "是否用距离权重"], [
        ["weighted_abs", "加权绝对差：Σ w·|Δx|", "是"],
        ["weighted_sq", "加权平方差：Σ w·Δx²", "是"],
        ["euclidean", "欧氏距离：√ΣΔx²", "否"],
        ["cosine", "余弦距离：1 − cos", "否"],
        ["correlation", "相关系数距离：1 − r", "否"],
        ["custom", "自定义公式（见下）", "由公式决定"],
    ]),
    ("p", "选择「自定义公式」时，下方会出现公式输入框。公式可以使用以下变量与函数（其余语法一律不允许）："),
    ("tb", ["变量 / 函数", "含义"], [
        ["d", "逐格点差（历史 − 目标，有符号）"],
        ["w", "距离权重（与格点同长度）"],
        ["n", "格点数"],
        ["abs / sqrt / exp / log / log1p", "绝对值、平方根、自然指数、自然对数、log(1+x)"],
        ["power / clip / minimum / maximum", "幂、截断、逐元素取小、逐元素取大"],
        ["sum / mean", "沿格点轴求和 / 求均值（使每个样本得到一个数值）"],
    ]),
    ("p", "公式示例：sqrt(sum(w*d*d))、sum(w*abs(d))、sqrt(mean(d*d))、sum(d)/n。"
          "公式必须能「归约为每个样本一个数值」，否则会被拒绝。"),
    ("h3", "7.5.4  排除窗 exclude_days"),
    ("p", "「剔除目标日 ± 天」用来排除与目标日期相差不超过 N 天的历史样本。"
          "例如设为 3，则目标日前后 3 天内的样本不会被当成「相似」——避免拿目标日临近的那几天冒充相似，"
          "让相似个例更有参考价值。默认值为 3。"),

    ("h2", "7.6  组合策略"),
    ("p", "这一组决定「交集组与直取组各自取前几名、是否启用部分交集」。"),
    ("tb", ["参数", "含义", "默认值"], [
        ["交集 TopN", "交集组每个要素各取前 N 名，再求交集", "50"],
        ["直取 TopN", "直取组每个要素各取前 N 名，直接并入最终集合", "5"],
        ["启用部分交集模式", "开启后，交集组内至少 k 个要素进入各自 TopN 即算入组", "关闭"],
        ["部分交集最少命中要素数 k", "部分交集模式下的命中门槛", "2"],
    ]),
    ("p", "交集与直取的含义：交集 = 「交集组各要素 TopN 的交集」，直取 = 「直取组各要素 TopN 的并集」，"
          "最终集合 = 交集 ∪ 直取。当候选样本远多于 TopN 时，严格交集很容易为空，"
          "可以适当放大 TopN，或启用部分交集。"),
    ("note", "若严格交集为空但确实存在「各要素都命中」的日期，系统会自动把 TopN 放宽到刚好能取到交集的最小值，"
             "并在结果里标注「已自动放宽」。"),

    ("h2", "7.7  降水概率产品"),
    ("p", "这一组决定降水统计的窗口与阈值。"),
    ("tb", ["参数", "含义", "默认值"], [
        ["6 小时窗口偏移", "相对检索时次的小时偏移，用于累加 6 小时降水量", "-2,-1,0,1,2,3"],
        ["24 小时窗口偏移", "24 小时窗口的小时偏移（当日 22UTC 起次日 21UTC）", "-2 … 21"],
        ["降水阈值", "概率统计的降水量分档阈值（mm）", "0.1, 50, 100"],
        ["同时产出 24 小时降水概率产品", "是否额外生成 24 小时产品", "按模板（plan_A 为否、plan_B 为是）"],
    ]),

    ("h2", "7.8  内置模板 plan_A 与 plan_B"),
    ("p", "系统内置两个模板，分别对应「暖区降水检索」和「锋面热动力检索」两种典型需求。"
          "它们不是两套功能，而是同一套参数的两组取值。对照如下："),
    ("tb", ["参数", "plan_A 暖区降水检索", "plan_B 锋面热动力检索"], [
        ["半边长", "1.0°（≈200km）", "2.5°（≈500km）"],
        ["年份范围", "2000–2024", "2000–2024"],
        ["月份", "4、5、6", "2、3、4、5、6"],
        ["检索时次", "0", "0、6、12、18"],
        ["要素 × 层次", "u925、v925、rh500（3 个）", "u/v/t/q × 975/925/850（12 个）"],
        ["特征处理", "none（不处理）", "standardize（标准化）"],
        ["相似度", "加权绝对差", "加权绝对差"],
        ["交集组", "u925、v925、rh500", "v975、v925、v850"],
        ["交集 TopN", "50", "60"],
        ["直取组", "（空）", "u/t/q 各 975/925/850（9 个）"],
        ["直取 TopN", "5", "5"],
        ["降水阈值", "0.1、50、100", "0.1、50"],
        ["生成 24 小时产品", "否", "是"],
    ]),
    ("h2", "7.9  GMCP 累计降水目标检索（V2.1.0 新增，第一优先）"),
    ("p", "除 ERA5 大气要素外，系统还可以直接把「预处理后的北京时间累计降水场」当作目标变量："
          "用目标日的 24/12/6 小时产品，在历史年份中找出累计降水场最相似的若干日期。"),
    ("ul", [
        "启用后，每个勾选的窗口独立检索 TopN（默认 24h 两套窗口同时启用）。",
        "相似日期的同一窗口累计降水场会统计：平均累计降水、中位数、≥0.1mm / ≥30mm / ≥50mm 概率（阈值可改）。",
        "只勾选累计降水、不勾选任何 ERA5 要素，即可做「累计降水单要素检索」。",
        "累计降水单要素检索同样并行：各窗口独立排名（多线程），产品文件直读使用多进程（V2.2.0 增强）。",
        "也可以与 ERA5 要素检索同时启用：两条路径各自出结果，累计降水产品优先展示。",
    ]),
    ("note", "累计降水检索依赖「数据预处理」页生成并登记的 gmcp_rain 数据源；若目标日或历史年份缺少产品，"
             "该窗口会被跳过并在结果页给出原因，不会影响其他窗口。"),

    # ═══════════════ 8 执行检索与进度 ═══════════════
    ("h1", "8  执行检索与进度"),
    ("p", "在「检索配置」页点击「开始检索」后，界面会自动跳到「执行进程」页，显示实时进度与分阶段步骤。"
          "整条流水线共八个环节（未启用的环节会自动跳过），进度条上依次点亮："),
    ("tb", ["序号", "阶段", "在做什么"], [
        ["1", "解析样本", "按配置扫描数据源覆盖，确定候选历史样本与区域网格"],
        ["2", "直读切片", "直接读取原始 ERA5 切片（多时次、多层次合并读，尽量少开文件）"],
        ["3", "特征处理", "把格点由近及远排序并按权重准备，必要时做标准化 / 归一化"],
        ["4", "相似度排名", "计算各要素的相似度指数并排名（含排除窗剔除）"],
        ["5", "累计降水检索", "以 GMCP 累计降水产品为目标变量，逐窗口检索 TopN 相似历史日"],
        ["6", "降水统计", "直读 GMCP 逐小时降水，按 6h / 24h 窗口对相似时刻累加"],
        ["7", "生成图表", "依次生成要素场对比图、直取要素场图、累计降水产品图、降水统计图、空间概率分布图"],
        ["8", "完成", "整轮检索结束，结果落盘，可到「结果产品」页查看"],
    ]),
    ("h2", "8.1  为什么第一次特别慢"),
    ("p", "第一次检索时，磁盘上还没有切片缓存，系统需要从原始文件（冷盘）逐个读取，速度明显较慢；"
          "读取过的切片会缓存下来，之后的检索命中缓存，速度会快很多。相同口径（配置指纹相同）的重复检索尤其快。"),
    ("h2", "8.2  取消检索"),
    ("p", "在「执行进程」页点击「取消任务」按钮即可取消当前检索，通常 1～3 秒内生效。"
          "取消后不会产出不完整的图表；已完成的历史检索结果不受影响。"),
    ("note", "「执行进程」页的进度每约 3 秒自动刷新；即使中途关掉浏览器标签页，任务仍在后台运行，"
             "重新打开页面还能继续查看进度。"),

    # ═══════════════ 9 结果产品解读 ═══════════════
    ("h1", "9  结果产品解读"),
    ("h2", "9.1  「交集日期」与「最终集合」"),
    ("ul", [
        "交集日期：被交集组的各个要素「同时」判定为相似的历史时刻（每个要素都排进了自己的前 N 名）。",
        "直取日期：被直取组任一要素排进前 N 名而直接入组的历史时刻。",
        "最终集合（final）= 交集 ∪ 直取；在结果表格里，交集日期排在前面，两组内部按日期升序。",
    ]),
    ("p", "「相似时刻明细」表里，每一行给出该日期在交集组各要素各自排名中的位次，以及「来源标注」"
          "（说明它是因为几个要素的交集入组，还是由哪些直取要素并入）。"),
    ("h2", "9.2  六类图表"),
    ("tb", ["图", "文件名", "内容"], [
        ["累计降水产品图", "precip_<窗口>.png（每个启用窗口一张）",
         "首格为目标日实况累计降水场，其后是相似日平均场、中位数场与 ≥各阈值的概率场（V2.2.0 增加目标日）"],
        ["累计降水分位数产品图", "precip_quantile_<窗口>.png（每个启用窗口一张）",
         "目标日 + P10/P25/P50/P75/P90 固定分位数 + 自定义百分位场，同色标便于横向比较（V2.2.0 新增）"],
        ["要素场对比图", "field_<时次>utc.png（每个时次一张）",
         "首行是目标日期，其后是相似集合里最靠前的 5 个日期；列是交集组要素；标题带名次与相似度指数 SI"],
        ["直取要素场图", "field_direct_<时次>utc.png（勾选时才生成）",
         "列是直取组要素；行取直取组第一个要素排名前若干名的日期与目标日"],
        ["降水统计图", "rain_bars.png",
         "各相似日期窗口累计降水量的柱状图（按降水量降序），并叠加阈值概率统计"],
        ["空间概率分布图", "spatial_<时次>utc.png / spatial_24h.png（每个时次一张）",
         "对相似集合里每个日期算出降水场后，沿日期维统计：平均降水场、达到最小阈值的出现概率、≥50mm 强降水概率"],
    ]),
    ("p", "文件名里的 <时次> 形如 00utc、06utc、12utc、18utc；24 小时产品固定为 spatial_24h.png。"
          "所有图都叠加了「广东省地市界 + 广东省轮廓（邻省淡化）」底图，虚线框标出的是真正的检索数据区。"),
    ("h2", "9.3  降水窗口口径"),
    ("tb", ["窗口", "覆盖范围"], [
        ["6 小时窗口", "锚定时次的「时次−2 ～ 时次+3」逐小时累加（相对该时次）"],
        ["24 小时窗口", "前一日 22UTC ～ 当日 21UTC，共 24 个小时逐小时累加"],
    ]),
    ("p", "降水阈值可以在「检索配置」的「降水概率产品」里修改；修改后概率统计与空间概率图都会按新阈值重新生成。"),
    ("note", "结果里的「降水概率」= 在相似历史时刻中，达到该阈值的比例；样本越多、越集中，参考价值越高。"),
    ("h2", "9.4  累计降水目标检索产品（V2.1.0 新增）"),
    ("p", "启用 GMCP 累计降水目标检索后，「结果产品」页会紧跟配置回放出现「GMCP 累计降水目标检索产品」面板。"
          "每个窗口一个标签页，页内给出："),
    ("ul", [
        "目标产品文件名与北京时间起止（例如前一日 20 时 — 当日 20 时）。",
        "目标日实况累计降水（统计点）与相似日平均、中位数、最大值。",
        "≥0.1mm / ≥30mm / ≥50mm 等阈值的概率（达到样本数 / 有效样本数）。",
        "固定分位数 P10 / P25 / P50 / P75 / P90 与自定义百分位的统计点数值（V2.2.0 新增）。",
        "相似历史日明细（名次、日期、相似度指数 SI、该日统计点累计降水）。",
    ]),
    ("note", "每个窗口的 TopN 相互独立；同一历史日期可能同时出现在多个窗口的相似列表里。"
             "产品图与表格使用同一批相似日期，数值口径一致。"),
    ("h2", "9.5  结果后处理与导出（V2.2.0 新增）"),
    ("p", "检索完成后，不需要重新检索即可在「结果产品」页调整累计降水产品口径并立即重算："),
    ("ul", [
        "产品 TopN：修改每个窗口参与统计的相似日数，点击「重算产品图」后直接按已保留的排名重算。"
        "可调上限由检索时保留的排名缓存决定（最多 300 名），界面会显示范围。",
        "自定义百分位 P：拖动滑块或输入 1～99 的数值，系统会实时重算分位数产品并刷新分位数产品图，"
        "无需重新计算相似度或重新读取历史产品。",
        "重算只更新产品统计与图表，不改变检索排名、配置指纹与最终相似时刻集合；结果会写回 result.json，"
        "历史回看时仍是重算后的口径。",
        "「导出 JSON」只下载结果数据；「导出结果包」下载 ZIP，内含 result.json、全部产品图（含分位数图）"
        "以及各窗口的相似排名 CSV，便于归档与二次分析。",
    ]),

    # ═══════════════ 10 历史结果回看 ═══════════════
    ("h1", "10  历史结果回看"),
    ("p", "「结果产品」页顶部的「选择任务」面板，可以随时找回过去任何一次检索的结果。"),
    ("ul", [
        "任务下拉列表把「本次会话已完成任务」与「数据库历史成功任务」合并后，统一按任务时间倒序排列（最新在前）。",
        "每个选项的标签依次是：可读时间、目标日期、配置名；进程重启后从落盘结果回看的任务会额外标注「· 历史」。",
        "选中某个任务（或点击「载入结果」）即自动载入；载入后首先呈现的是「检索配置（本次任务实际使用）」。",
        "该配置直接来自随结果落盘的 result.json，因此与检索当时完全一致，历史任务同样可以回放。",
        "点击「导出 JSON」可把当前结果导出为文件保存。",
    ]),
    ("note", "判断「某次检索到底用了什么参数」，看结果里回放的「检索配置」最可靠——它就是当时的真实配置。"),

    # ═══════════════ 11 常见问题 FAQ ═══════════════
    ("h1", "11  常见问题（FAQ）"),

    ("h3", "Q1  点击开始后，没有出要素场图 / 概率图"),
    ("tb", ["现象", "点了「开始检索」，进度跑到最后，却只有很少的图，甚至没有要素场图或概率图"],
     [["原因", "相似时刻集合为空（严格交集为空，即没有任何历史日期被交集组各要素同时命中）"],
      ["处理", "系统会自动放宽 TopN 再试；若仍为空，说明该配置下没有任何日期同时被各要素命中。"
               "可：①适当放大交集 TopN；②减少交集组要素数量；③放宽「剔除目标日 ± 天」排除窗；"
               "④改相似度口径或开启部分交集模式。"]]),

    ("h3", "Q2  产品图有「像素感」/「格子感」"),
    ("tb", ["现象", "要素场图、概率图上能看到明显的方块格子"],
     [["原因", "ERA5 区域切片本身是 0.25° 网格，检索区域越小、格点越少，格子就越明显；"
               "例如约 8°×8° 的区域只有 32×32 个格点，默认 2°×2° 区域更少。这是数据分辨率所致，不是软件缺陷"],
      ["处理", "适当扩大检索区域，或改用降水类产品（GMCP 为 0.1° 网格，更细腻）"]]),

    ("h3", "Q3  检索很慢"),
    ("tb", ["现象", "第一次检索要等很久"],
     [["原因", "首次读取的是冷盘、没有切片缓存；也可能是数据在较慢的移动硬盘 / 网络盘上"],
      ["处理", "耐心等待第一次完成；之后命中 cache 目录的切片缓存会快很多。相同配置的重复检索更快。"]]),

    ("h3", "Q4  端口 8770 / 8080 被占用"),
    ("tb", ["现象", "启动后地址不是 8770，或担心端口冲突"],
     [["原因", "默认端口被其他程序占用"],
      ["处理", "软件会自动顺延端口，无需手工处理；以浏览器实际打开的地址为准。停止服务会释放它占用的端口。"]]),

    ("h3", "Q5  图上的中文显示为方框"),
    ("tb", ["现象", "图上的中文地名、坐标轴文字变成一个个方框"],
     [["原因", "系统缺少可用的中文字体（如 微软雅黑 Microsoft YaHei / 黑体 SimHei）"],
      ["处理", "安装中文字体后重新检索出图。详见第 2 章「关于中文字体」。"]]),

    ("h3", "Q6  数据盘是移动硬盘 / 网络盘，未挂载"),
    ("tb", ["现象", "检索时报「找不到数据」或样本为空"],
     [["原因", "资料所在磁盘当前未挂载或不可访问（盘符变了、网络盘未连接）"],
      ["处理", "先挂载磁盘并能正常打开后，回到「数据源」页重新确认登记，再发起检索。"]]),

    ("h3", "Q7  如何彻底清除数据、如何卸载"),
    ("tb", ["现象", "想清空历史结果与缓存，或完全卸载程序"],
     [["处理", "清除数据：删除程序数据目录 %LOCALAPPDATA%\\Histsim 即可（含结果、缓存、日志与登记信息）；"
               "卸载程序：在「设置 → 应用」里卸载「历史相似检索系统」，或运行安装目录下的卸载程序。"],
      ["注意", "卸载程序不会自动删除 %LOCALAPPDATA%\\Histsim，需要清空时请手动删除该目录。"]]),

    # ═══════════════ 12 高级用法（命令行） ═══════════════
    ("h1", "12  高级用法（命令行，可选）"),
    ("p", "以下能力是给熟悉命令行的用户准备的可选项，普通用户日常不需要使用，全部功能都能在浏览器界面完成。"
          "安装后的程序本身接受两个参数：不带参数即为启动，「--stop」为停止、「--dashboard」为查看数据源概况。"),
    ("tb", ["命令", "作用", "适用场景"], [
        ["<程序> --stop", "停止后台服务（连检索子进程一并结束）", "与「停止历史相似检索系统」快捷方式等价"],
        ["<程序> --dashboard", "查看数据源与覆盖概况", "快速核对已登记的数据覆盖情况"],
        ["python -m histsim.cli serve", "后台常驻启动本地服务并等待就绪", "源码运行时从命令行启动"],
        ["python -m histsim.cli stop", "完全暂停服务（含 IO 子进程）", "源码运行时停止服务"],
        ["python -m histsim.cli register <root> --depth 1", "扫描并登记数据源目录", "批量登记数据源，免去逐页点击"],
        ["python -m histsim.cli dashboard", "显示数据源与覆盖概况", "命令行核对覆盖"],
        ["python -m histsim.cli preview --template plan_A --date 2024-04-27", "只做代价预估，不真正检索", "预估候选样本数、待读文件数与覆盖度"],
        ["python -m histsim.cli search --template plan_A --date 2024-04-27 --out 结果.json", "执行检索并把结果 JSON 导出", "批量检索或脚本化处理"],
    ]),
    ("note", "register / preview / search 属于源码运行时的命令行子命令；安装后的图形化程序本身主要提供启动、"
             "--stop 与 --dashboard 三个用法。"),

    # ═══════════════ 13 附录 ═══════════════
    ("h1", "13  附录"),

    ("h2", "13.1  程序数据目录结构"),
    ("p", "程序数据目录默认为 %LOCALAPPDATA%\\Histsim（源码运行时为项目下的 .histsim），结构如下："),
    ("tb", ["路径", "说明"], [
        ["index.db", "元数据库：数据源登记、覆盖聚合、检索任务历史、操作日志"],
        ["results/<任务ID>/", "每次检索的结果目录，目录名即任务 ID"],
        ["cache/<要素>/<年月>/*.npz", "ERA5 切片缓存，用于加速重复检索"],
        ["basemap/*.npz", "省界、地市界底图解析缓存"],
        ["run/server.json", "后台服务的运行记录（进程号、端口、启动时间）"],
        ["run/*.log", "服务运行日志"],
    ]),
    ("p", "任务 ID 的格式为 YYYYMMDD_HHMMSS_<6位十六进制>，前缀就是检索发起的时刻，因此结果目录可按时间辨认。"),

    ("h2", "13.2  结果目录里的文件名含义"),
    ("tb", ["文件名", "含义"], [
        ["result.json", "本次检索的完整结果（含检索配置 config），进程重启后可据此回看"],
        ["field_<时次>utc.png", "要素场对比图（每个时次一张，首行目标日、其后相似日）"],
        ["field_direct_<时次>utc.png", "直取要素场图（仅勾选且直取组非空时生成）"],
        ["rain_bars.png", "降水统计柱状图（相似日期降水量 + 阈值概率）"],
        ["spatial_<时次>utc.png", "空间概率分布图（每个时次一张：平均场 + 出现概率 + 强降水概率）"],
        ["spatial_24h.png", "空间概率分布图（24 小时，勾选生成 24h 产品时才有）"],
    ]),

    ("h2", "13.3  版本与技术支持"),
    ("tb", ["项目", "内容"], [
        ["产品名称", TITLE],
        ["版本号", VERSION],
        ["文档日期", DOC_DATE],
        ["技术支持", "如遇问题，请联系系统管理员或开发维护人员，并一并提供：版本号、任务 ID、"
                    "以及「操作记录」页中的操作日志，以便快速定位。"],
    ]),
]


# ─────────────────────────────────────────────────────────────────────
# PDF（reportlab）
# ─────────────────────────────────────────────────────────────────────
def _pick_font() -> str:
    for p in FONT_CANDIDATES:
        if os.path.isfile(p):
            return p
    raise FileNotFoundError(
        "未找到可用的中文字体，请确认以下字体之一存在:\n  " + "\n  ".join(FONT_CANDIDATES))


def build_pdf() -> str:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import (BaseDocTemplate, Frame, KeepTogether, PageTemplate,
                                    Paragraph, Spacer, Table, TableStyle)
    from reportlab.platypus.tableofcontents import TableOfContents

    font_path = _pick_font()
    sub = 0 if font_path.lower().endswith(".ttc") else None
    pdfmetrics.registerFont(TTFont("CN", font_path, subfontIndex=sub))
    pdfmetrics.registerFont(TTFont("CN-Bold", font_path, subfontIndex=sub))
    pdfmetrics.registerFontFamily("CN", normal="CN", bold="CN-Bold",
                                  italic="CN", boldItalic="CN-Bold")

    INK = colors.HexColor("#1A1D22")
    ACCENT = colors.HexColor("#C2703A")
    HEAD_BG = colors.HexColor("#F0E3D2")
    LINE = colors.HexColor("#C9B79C")
    NOTE_BG = colors.HexColor("#FFF4E5")
    NOTE_LINE = colors.HexColor("#E0A800")

    doc_w = A4[0] - 2 * 46

    S = {
        "cover_title": ParagraphStyle("cover_title", fontName="CN", fontSize=30,
                                      leading=40, alignment=1, textColor=INK),
        "cover_sub": ParagraphStyle("cover_sub", fontName="CN", fontSize=16,
                                    leading=24, alignment=1, textColor=ACCENT),
        "cover_meta": ParagraphStyle("cover_meta", fontName="CN", fontSize=12,
                                     leading=22, alignment=1, textColor=INK),
        "toc_title": ParagraphStyle("toc_title", fontName="CN", fontSize=18,
                                    leading=26, spaceAfter=10, textColor=INK),
        "H1": ParagraphStyle("H1", fontName="CN-Bold", fontSize=16, leading=24,
                             spaceBefore=16, spaceAfter=8, textColor=ACCENT),
        "H2": ParagraphStyle("H2", fontName="CN-Bold", fontSize=13, leading=20,
                             spaceBefore=11, spaceAfter=5, textColor=INK),
        "H3": ParagraphStyle("H3", fontName="CN-Bold", fontSize=11.5, leading=18,
                             spaceBefore=8, spaceAfter=3, textColor=ACCENT),
        "body": ParagraphStyle("body", fontName="CN", fontSize=10.5, leading=17.5,
                               spaceAfter=5, textColor=INK, alignment=4),
        "bullet": ParagraphStyle("bullet", fontName="CN", fontSize=10.5, leading=17,
                                 leftIndent=14, bulletIndent=2, spaceAfter=3,
                                 textColor=INK),
        "note": ParagraphStyle("note", fontName="CN", fontSize=10, leading=16,
                               textColor=colors.HexColor("#6B4A12")),
        "th": ParagraphStyle("th", fontName="CN-Bold", fontSize=9.5, leading=14,
                             textColor=INK),
        "td": ParagraphStyle("td", fontName="CN", fontSize=9.5, leading=14,
                             textColor=INK),
    }

    def note_box(text):
        t = Table([[Paragraph(text, S["note"])]], colWidths=[doc_w])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), NOTE_BG),
            ("BOX", (0, 0), (-1, -1), 0.8, NOTE_LINE),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ]))
        return t

    def make_table(header, rows):
        data = [[Paragraph(str(c), S["th"]) for c in header]]
        for r in rows:
            data.append([Paragraph(str(c), S["td"]) for c in r])
        ncol = len(header)
        w = doc_w / ncol
        t = Table(data, colWidths=[w] * ncol, repeatRows=1)
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), HEAD_BG),
            ("GRID", (0, 0), (-1, -1), 0.5, LINE),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        return t

    # ── 页面装饰：页码 ──────────────────────────────────────────────
    def decorate(canvas, document):
        if document.page < 2:
            return
        canvas.saveState()
        canvas.setFont("CN", 8.5)
        canvas.setFillColor(colors.HexColor("#888888"))
        canvas.drawCentredString(A4[0] / 2.0, 15 * mm, f"第 {document.page} 页")
        canvas.setStrokeColor(colors.HexColor("#DDDDDD"))
        canvas.setLineWidth(0.4)
        canvas.line(46, 18 * mm, A4[0] - 46, 18 * mm)
        canvas.restoreState()

    # ── 目录：两遍构建自动回填页码 ──────────────────────────────────
    class ManualDoc(BaseDocTemplate):
        def afterFlowable(self, flowable):
            if isinstance(flowable, Paragraph):
                name = flowable.style.name
                if name == "H1":
                    self.notify("TOCEntry", (0, flowable.getPlainText(), self.page))
                elif name == "H2":
                    self.notify("TOCEntry", (1, flowable.getPlainText(), self.page))

    story = []

    # 封面
    story.append(Spacer(1, 150))
    story.append(Paragraph(TITLE, S["cover_title"]))
    story.append(Spacer(1, 10))
    story.append(Paragraph(SUBTITLE, S["cover_sub"]))
    story.append(Spacer(1, 90))
    story.append(Paragraph(f"版本号：{VERSION}", S["cover_meta"]))
    story.append(Paragraph(f"文档日期：{DOC_DATE}", S["cover_meta"]))
    story.append(Spacer(1, 40))
    story.append(Paragraph("（本手册面向普通用户，讲解安装、启动、数据源登记、检索配置与结果解读）",
                           S["cover_meta"]))
    story.append(Spacer(1, 40))
    from reportlab.platypus import PageBreak
    story.append(PageBreak())

    # 目录
    story.append(Paragraph("目录", S["toc_title"]))
    toc = TableOfContents()
    toc.levelStyles = [
        ParagraphStyle("toc0", fontName="CN-Bold", fontSize=11, leading=20,
                       leftIndent=0, textColor=INK),
        ParagraphStyle("toc1", fontName="CN", fontSize=10, leading=17,
                       leftIndent=18, textColor=INK),
    ]
    story.append(toc)
    story.append(PageBreak())

    # 正文
    for block in CONTENT:
        kind = block[0]
        if kind in ("h1", "h2", "h3"):
            story.append(Paragraph(block[1], S[kind.upper()]))
        elif kind == "p":
            story.append(Paragraph(block[1], S["body"]))
        elif kind == "ul":
            for it in block[1]:
                story.append(Paragraph(it, S["bullet"], bulletText="•"))
            story.append(Spacer(1, 3))
        elif kind == "tb":
            story.append(Spacer(1, 2))
            story.append(make_table(block[1], block[2]))
            story.append(Spacer(1, 7))
        elif kind == "note":
            story.append(Spacer(1, 3))
            story.append(note_box(block[1]))
            story.append(Spacer(1, 7))

    frame = Frame(46, 20 * mm, doc_w, A4[1] - 20 * mm - 50, id="body")
    doc = ManualDoc(PDF_PATH, pagesize=A4, leftMargin=46, rightMargin=46,
                    topMargin=50, bottomMargin=20 * mm,
                    title=f"{TITLE} 用户说明书", author=TITLE)
    doc.addPageTemplates([PageTemplate(id="all", frames=[frame], onPage=decorate)])
    doc.multiBuild(story)
    return PDF_PATH


# ─────────────────────────────────────────────────────────────────────
# DOCX（python-docx）
# ─────────────────────────────────────────────────────────────────────
def build_docx() -> str:
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Pt, RGBColor, Inches

    INK = RGBColor(0x1A, 0x1D, 0x22)
    ACCENT = RGBColor(0xC2, 0x70, 0x3A)

    def set_font(run, size=None, bold=None, color=None):
        run.font.name = DOCX_LATIN
        if size:
            run.font.size = Pt(size)
        if bold is not None:
            run.font.bold = bold
        if color is not None:
            run.font.color.rgb = color
        rpr = run._element.get_or_add_rPr()
        rfonts = rpr.find(qn("w:rFonts"))
        if rfonts is None:
            rfonts = OxmlElement("w:rFonts")
            rpr.append(rfonts)
        rfonts.set(qn("w:eastAsia"), DOCX_FONT)
        rfonts.set(qn("w:ascii"), DOCX_LATIN)
        rfonts.set(qn("w:hAnsi"), DOCX_LATIN)

    def shade(cell, fill):
        tcPr = cell._tc.get_or_add_tcPr()
        shd = OxmlElement("w:shd")
        shd.set(qn("w:val"), "clear")
        shd.set(qn("w:color"), "auto")
        shd.set(qn("w:fill"), fill)
        tcPr.append(shd)

    def add_para(text, size=10.5, bold=False, color=INK, align=None,
                 space_after=4, indent=None):
        p = doc.add_paragraph()
        if align is not None:
            p.alignment = align
        p.paragraph_format.space_after = Pt(space_after)
        p.paragraph_format.space_before = Pt(0)
        if indent:
            p.paragraph_format.left_indent = Inches(indent)
        r = p.add_run(text)
        set_font(r, size=size, bold=bold, color=color)
        return p

    def add_heading(text, level):
        if level == 1:
            p = add_para(text, size=16, bold=True, color=ACCENT, space_after=8)
            p.paragraph_format.space_before = Pt(14)
        elif level == 2:
            p = add_para(text, size=13, bold=True, color=INK, space_after=5)
            p.paragraph_format.space_before = Pt(10)
        else:
            p = add_para(text, size=11.5, bold=True, color=ACCENT, space_after=3)
            p.paragraph_format.space_before = Pt(7)
        return p

    def add_bullets(items):
        for it in items:
            p = doc.add_paragraph(style="List Bullet")
            p.paragraph_format.space_after = Pt(2)
            r = p.add_run(it)
            set_font(r, size=10.5)

    def add_note(text):
        t = doc.add_table(rows=1, cols=1)
        t.style = "Table Grid"
        cell = t.cell(0, 0)
        shade(cell, "FFF4E5")
        cell.paragraphs[0].text = ""
        r = cell.paragraphs[0].add_run(text)
        set_font(r, size=10, color=RGBColor(0x6B, 0x4A, 0x12))
        doc.add_paragraph().paragraph_format.space_after = Pt(2)

    def add_table(header, rows):
        t = doc.add_table(rows=1, cols=len(header))
        t.style = "Table Grid"
        hdr = t.rows[0].cells
        for i, c in enumerate(header):
            hdr[i].text = ""
            r = hdr[i].paragraphs[0].add_run(str(c))
            set_font(r, size=9.5, bold=True)
            shade(hdr[i], "F0E3D2")
        for row in rows:
            cells = t.add_row().cells
            for i, c in enumerate(row):
                cells[i].text = ""
                r = cells[i].paragraphs[0].add_run(str(c))
                set_font(r, size=9.5)
        doc.add_paragraph().paragraph_format.space_after = Pt(2)

    doc = Document()
    # 默认样式也设成中文字体，避免新增段落落到默认西文字体
    normal = doc.styles["Normal"]
    normal.font.name = DOCX_LATIN
    normal.font.size = Pt(10.5)
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), DOCX_FONT)

    # 封面
    for _ in range(5):
        doc.add_paragraph()
    add_para(TITLE, size=30, bold=True, align=WD_ALIGN_PARAGRAPH.CENTER, space_after=10)
    add_para(SUBTITLE, size=16, bold=True, color=ACCENT,
             align=WD_ALIGN_PARAGRAPH.CENTER, space_after=30)
    for _ in range(4):
        doc.add_paragraph()
    add_para(f"版本号：{VERSION}", size=12, align=WD_ALIGN_PARAGRAPH.CENTER, space_after=6)
    add_para(f"文档日期：{DOC_DATE}", size=12, align=WD_ALIGN_PARAGRAPH.CENTER, space_after=6)
    add_para("（本手册面向普通用户，讲解安装、启动、数据源登记、检索配置与结果解读）",
             size=11, align=WD_ALIGN_PARAGRAPH.CENTER, space_after=6)
    doc.add_page_break()

    # 目录（列出全部一、二级标题，便于对照）
    add_para("目录", size=18, bold=True, space_after=8)
    for block in CONTENT:
        if block[0] == "h1":
            add_para(block[1], size=11, bold=True, space_after=2)
        elif block[0] == "h2":
            p = add_para(block[1], size=10, space_after=1)
            p.paragraph_format.left_indent = Inches(0.25)
    doc.add_page_break()

    # 正文
    for block in CONTENT:
        kind = block[0]
        if kind == "h1":
            add_heading(block[1], 1)
        elif kind == "h2":
            add_heading(block[1], 2)
        elif kind == "h3":
            add_heading(block[1], 3)
        elif kind == "p":
            add_para(block[1], size=10.5)
        elif kind == "ul":
            add_bullets(block[1])
        elif kind == "tb":
            add_table(block[1], block[2])
        elif kind == "note":
            add_note(block[1])

    doc.save(DOCX_PATH)
    return DOCX_PATH


def verify_pdf() -> None:
    """抽取首页文本，确认 PDF 中文不是黑块（能读到中文标题即通过）。"""
    try:
        from pypdf import PdfReader
    except ImportError:
        print("  [跳过 PDF 校验] 未安装 pypdf")
        return
    reader = PdfReader(PDF_PATH)
    text = "\n".join((p.extract_text() or "") for p in reader.pages)
    assert "历史相似检索系统" in text, "PDF 文本中未读到中文标题，字体可能未生效"
    assert VERSION in text, "PDF 文本中未读到版本号"
    print(f"  PDF 页数：{len(reader.pages)}")
    print(f"  PDF 文本抽取校验通过（可读到中文标题「历史相似检索系统」与版本 {VERSION}）")


def main() -> int:
    os.makedirs(OUT_DIR, exist_ok=True)
    print("生成 PDF…")
    build_pdf()
    print("生成 DOCX…")
    build_docx()
    print("校验…")
    verify_pdf()
    for path in (PDF_PATH, DOCX_PATH):
        size = os.path.getsize(path)
        print(f"  {path}  ({size / 1024:.1f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())