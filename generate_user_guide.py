#!/usr/bin/env python3
"""
生成文件夹重组工具V7.0小白使用说明PDF
"""

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm, cm
from reportlab.lib.colors import HexColor, black, white
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, Image, ListFlowable, ListItem
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_JUSTIFY
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib import colors
import os

# 注册中文字体
pdfmetrics.registerFont(TTFont('SimHei', '/home/z/my-project/fonts/simhei.ttf'))

# 颜色定义
PRIMARY_COLOR = HexColor('#1a365d')
SECONDARY_COLOR = HexColor('#2c5282')
ACCENT_COLOR = HexColor('#3182ce')
LIGHT_BG = HexColor('#f7fafc')
WARNING_COLOR = HexColor('#c53030')
SUCCESS_COLOR = HexColor('#276749')

# 创建文档
doc = SimpleDocTemplate(
    '/home/z/my-project/download/小白使用说明V7.0.pdf',
    pagesize=A4,
    rightMargin=20*mm,
    leftMargin=20*mm,
    topMargin=20*mm,
    bottomMargin=20*mm
)

# 创建样式
styles = getSampleStyleSheet()

# 标题样式
title_style = ParagraphStyle(
    'Title',
    fontName='SimHei',
    fontSize=28,
    leading=36,
    alignment=TA_CENTER,
    textColor=PRIMARY_COLOR,
    spaceAfter=20*mm
)

# 副标题样式
subtitle_style = ParagraphStyle(
    'Subtitle',
    fontName='SimHei',
    fontSize=14,
    leading=20,
    alignment=TA_CENTER,
    textColor=SECONDARY_COLOR,
    spaceAfter=10*mm
)

# 一级标题样式
h1_style = ParagraphStyle(
    'Heading1',
    fontName='SimHei',
    fontSize=18,
    leading=26,
    textColor=PRIMARY_COLOR,
    spaceBefore=15*mm,
    spaceAfter=8*mm,
    borderPadding=5,
    borderColor=ACCENT_COLOR,
    borderWidth=0,
    leftIndent=0
)

# 二级标题样式
h2_style = ParagraphStyle(
    'Heading2',
    fontName='SimHei',
    fontSize=14,
    leading=20,
    textColor=SECONDARY_COLOR,
    spaceBefore=10*mm,
    spaceAfter=5*mm
)

# 正文样式
body_style = ParagraphStyle(
    'Body',
    fontName='SimHei',
    fontSize=11,
    leading=18,
    alignment=TA_JUSTIFY,
    spaceAfter=4*mm,
    firstLineIndent=22
)

# 提示框样式
tip_style = ParagraphStyle(
    'Tip',
    fontName='SimHei',
    fontSize=10,
    leading=16,
    textColor=HexColor('#2d3748'),
    backColor=HexColor('#e2e8f0'),
    borderPadding=10,
    spaceAfter=5*mm
)

# 警告样式
warning_style = ParagraphStyle(
    'Warning',
    fontName='SimHei',
    fontSize=10,
    leading=16,
    textColor=WARNING_COLOR,
    spaceAfter=5*mm
)

# 代码样式
code_style = ParagraphStyle(
    'Code',
    fontName='SimHei',
    fontSize=9,
    leading=14,
    backColor=HexColor('#edf2f7'),
    borderPadding=8,
    spaceAfter=4*mm,
    leftIndent=10,
    rightIndent=10
)

# 列表项样式
list_style = ParagraphStyle(
    'ListItem',
    fontName='SimHei',
    fontSize=11,
    leading=18,
    leftIndent=20,
    spaceAfter=2*mm
)

# 构建内容
story = []

# 封面
story.append(Spacer(1, 60*mm))
story.append(Paragraph('文件夹重组工具', title_style))
story.append(Paragraph('小白使用说明 V7.0', subtitle_style))
story.append(Spacer(1, 20*mm))
story.append(Paragraph('让文件整理变得简单', subtitle_style))
story.append(Spacer(1, 40*mm))

# 版本信息表格
version_data = [
    ['版本', 'V7.0'],
    ['适用系统', 'Windows 7 及以上'],
    ['运行环境', 'Python 3.9 及以上'],
    ['更新日期', '2026年4月14日']
]

version_table = Table(version_data, colWidths=[60*mm, 80*mm])
version_table.setStyle(TableStyle([
    ('FONTNAME', (0, 0), (-1, -1), 'SimHei'),
    ('FONTSIZE', (0, 0), (-1, -1), 11),
    ('TEXTCOLOR', (0, 0), (0, -1), SECONDARY_COLOR),
    ('TEXTCOLOR', (1, 0), (1, -1), black),
    ('ALIGN', (0, 0), (0, -1), 'RIGHT'),
    ('ALIGN', (1, 0), (1, -1), 'LEFT'),
    ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
    ('TOPPADDING', (0, 0), (-1, -1), 8),
]))
story.append(version_table)

story.append(PageBreak())

# 目录
story.append(Paragraph('目录', h1_style))
story.append(Spacer(1, 5*mm))

toc_items = [
    '一、这个工具是做什么的？',
    '二、安装准备',
    '三、快速上手（3步搞定）',
    '四、常用功能详解',
    '五、常见问题解答',
    '六、安全提示'
]

for item in toc_items:
    story.append(Paragraph(item, list_style))

story.append(PageBreak())

# 第一章：工具介绍
story.append(Paragraph('一、这个工具是做什么的？', h1_style))

story.append(Paragraph(
    '你是否遇到过这样的情况：电脑里的文件夹乱成一团，想整理却不知从何下手？下载的文件散落各处，重复文件占满硬盘？这个工具就是专门帮你解决这些问题的！',
    body_style
))

story.append(Paragraph('它能帮你做什么？', h2_style))

features = [
    '【清理垃圾】自动识别并清理缓存文件夹（如 node_modules、__pycache__ 等）',
    '【删除重复】找出并删除重复文件，释放硬盘空间',
    '【整理结构】把深层嵌套的文件夹"拍扁"，让文件更容易找到',
    '【修复权限】解决"文件无法删除"、"需要管理员权限"等问题',
    '【安全可靠】所有操作都可以撤销，误删文件也能找回'
]

for f in features:
    story.append(Paragraph('• ' + f, list_style))

story.append(Spacer(1, 5*mm))
story.append(Paragraph(
    '最重要的是：这个工具默认只"预览"不"真干"，你必须明确确认才会真正执行操作，所以不用担心误操作！',
    tip_style
))

# 第二章：安装准备
story.append(Paragraph('二、安装准备', h1_style))

story.append(Paragraph('步骤1：检查 Python 是否已安装', h2_style))
story.append(Paragraph(
    '这个工具需要 Python 才能运行。请按以下步骤检查：',
    body_style
))

story.append(Paragraph('1. 按 Win + R 打开"运行"窗口', list_style))
story.append(Paragraph('2. 输入 cmd 并回车，打开命令提示符', list_style))
story.append(Paragraph('3. 输入 python --version 并回车', list_style))

story.append(Spacer(1, 3*mm))
story.append(Paragraph(
    '如果显示类似 "Python 3.9.x" 或 "Python 3.10.x" 的版本号，说明已安装成功。如果显示"不是内部或外部命令"，说明需要先安装 Python。',
    body_style
))

story.append(Paragraph('步骤2：安装 Python（如果还没装）', h2_style))
story.append(Paragraph('1. 访问 python.org 官网', list_style))
story.append(Paragraph('2. 下载 Python 3.9 或更高版本', list_style))
story.append(Paragraph('3. 安装时务必勾选 "Add Python to PATH" 选项', list_style))
story.append(Paragraph('4. 安装完成后重启命令提示符，再次检查版本', list_style))

story.append(Paragraph(
    '【重要提示】安装时一定要勾选 "Add Python to PATH"，否则工具无法正常运行！',
    warning_style
))

# 第三章：快速上手
story.append(Paragraph('三、快速上手（3步搞定）', h1_style))

story.append(Paragraph('第1步：打开命令提示符', h2_style))
story.append(Paragraph('按 Win + R，输入 cmd，回车', body_style))

story.append(Paragraph('第2步：进入工具所在目录', h2_style))
story.append(Paragraph('假设你把工具放在 D 盘的 Tools 文件夹，输入：', body_style))
story.append(Paragraph('cd /d D:\\Tools', code_style))

story.append(Paragraph('第3步：运行工具', h2_style))
story.append(Paragraph('最简单的用法——先看看有什么问题：', body_style))
story.append(Paragraph('python 文件夹重组工具V7.0.py "D:\\我的文档" --diagnose', code_style))

story.append(Spacer(1, 3*mm))
story.append(Paragraph(
    '把 "D:\\我的文档" 换成你想整理的文件夹路径。程序会扫描并告诉你有哪些问题，但不会做任何修改。',
    body_style
))

# 第四章：常用功能详解
story.append(Paragraph('四、常用功能详解', h1_style))

story.append(Paragraph('功能1：诊断模式（推荐新手使用）', h2_style))
story.append(Paragraph(
    '诊断模式只会分析你的文件夹，告诉你有什么问题，绝对不会修改任何文件。这是最安全的使用方式！',
    body_style
))

story.append(Paragraph('python 文件夹重组工具V7.0.py "目标文件夹" --diagnose', code_style))

story.append(Paragraph('诊断报告会告诉你：', body_style))
story.append(Paragraph('• 有多少个缓存文件夹可以清理', list_style))
story.append(Paragraph('• 有多少重复文件占用了多少空间', list_style))
story.append(Paragraph('• 文件夹层级有多深', list_style))
story.append(Paragraph('• 有没有无法访问的文件', list_style))

story.append(Paragraph('功能2：预览模式', h2_style))
story.append(Paragraph(
    '预览模式比诊断模式更进一步，它会列出具体要执行的操作清单，但仍然不会真正执行。',
    body_style
))

story.append(Paragraph('python 文件夹重组工具V7.0.py "目标文件夹" --preview', code_style))

story.append(Paragraph('功能3：执行模式', h2_style))
story.append(Paragraph(
    '当你确认预览结果没问题后，可以加上 --execute 参数真正执行操作。程序会再次让你确认。',
    body_style
))

story.append(Paragraph('python 文件夹重组工具V7.0.py "目标文件夹" --execute', code_style))

story.append(Paragraph(
    '【安全机制】执行前程序会要求你输入 yes 确认。如果想跳过确认，可以加 -y 参数，但不推荐新手使用。',
    tip_style
))

story.append(Paragraph('功能4：层级扁平化', h2_style))
story.append(Paragraph(
    '如果你的文件夹嵌套太深（比如 "项目\\src\\components\\common\\utils\\helpers\\..."），可以用这个功能把文件提取到同一层。',
    body_style
))

story.append(Paragraph('python 文件夹重组工具V7.0.py "目标文件夹" --flatten --execute', code_style))

story.append(Paragraph('功能5：权限修复', h2_style))
story.append(Paragraph(
    '遇到"无法删除"、"需要权限"的问题？这个功能可以帮你修复文件权限。',
    body_style
))

story.append(Paragraph('python 文件夹重组工具V7.0.py "目标文件夹" --fix-attr all --execute', code_style))

# 第五章：常见问题
story.append(Paragraph('五、常见问题解答', h1_style))

qa_data = [
    ['问题', '解决方法'],
    ['提示"找不到 Python"', '确保已安装 Python 且勾选了 "Add to PATH"'],
    ['提示"权限不足"', '右键点击命令提示符，选择"以管理员身份运行"'],
    ['扫描速度很慢', '文件夹文件太多，耐心等待。可以加 --threads 8 提高速度'],
    ['误删了文件怎么办', '在目标文件夹下的 .restructure_trash 目录里找回'],
    ['想撤销操作怎么办', '运行生成的 rollback.py 回滚脚本'],
    ['能整理 C 盘吗', '不能。程序禁止操作系统目录，防止误删系统文件'],
]

qa_table = Table(qa_data, colWidths=[55*mm, 85*mm])
qa_table.setStyle(TableStyle([
    ('FONTNAME', (0, 0), (-1, -1), 'SimHei'),
    ('FONTSIZE', (0, 0), (-1, -1), 10),
    ('BACKGROUND', (0, 0), (-1, 0), PRIMARY_COLOR),
    ('TEXTCOLOR', (0, 0), (-1, 0), white),
    ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
    ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ('GRID', (0, 0), (-1, -1), 0.5, HexColor('#cbd5e0')),
    ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
    ('TOPPADDING', (0, 0), (-1, -1), 8),
    ('LEFTPADDING', (0, 0), (-1, -1), 8),
    ('ROWBACKGROUNDS', (0, 1), (-1, -1), [white, LIGHT_BG]),
]))
story.append(qa_table)

# 第六章：安全提示
story.append(Paragraph('六、安全提示', h1_style))

story.append(Paragraph(
    '虽然这个工具有很多安全机制，但整理文件毕竟是"动真格"的操作，请务必注意以下几点：',
    body_style
))

safety_tips = [
    '【先备份】重要文件先备份到其他地方，以防万一',
    '【先预览】永远先用 --diagnose 或 --preview 看看结果',
    '【看清楚】执行前仔细阅读操作清单，确认没有误判',
    '【别急】不要加 -y 跳过确认，让程序多问一句更安全',
    '【别贪心】一次只整理一个文件夹，不要一次处理太多',
    '【保留日志】操作完成后保留 .restructure_log 目录，方便追溯'
]

for tip in safety_tips:
    story.append(Paragraph('• ' + tip, list_style))

story.append(Spacer(1, 10*mm))

# 快速参考卡片
story.append(Paragraph('快速参考卡片', h2_style))

cmd_data = [
    ['命令', '作用'],
    ['--diagnose', '仅诊断，不修改任何文件（最安全）'],
    ['--preview', '预览操作清单，不执行'],
    ['--execute', '执行操作（需确认）'],
    ['--flatten', '扁平化文件夹层级'],
    ['--fix-attr all', '修复所有权限问题'],
    ['--threads 8', '使用8个线程加速'],
    ['--help', '查看帮助信息'],
]

cmd_table = Table(cmd_data, colWidths=[45*mm, 95*mm])
cmd_table.setStyle(TableStyle([
    ('FONTNAME', (0, 0), (-1, -1), 'SimHei'),
    ('FONTSIZE', (0, 0), (-1, -1), 10),
    ('BACKGROUND', (0, 0), (-1, 0), ACCENT_COLOR),
    ('TEXTCOLOR', (0, 0), (-1, 0), white),
    ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
    ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ('GRID', (0, 0), (-1, -1), 0.5, HexColor('#cbd5e0')),
    ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
    ('TOPPADDING', (0, 0), (-1, -1), 8),
    ('LEFTPADDING', (0, 0), (-1, -1), 8),
    ('ROWBACKGROUNDS', (0, 1), (-1, -1), [white, LIGHT_BG]),
]))
story.append(cmd_table)

story.append(Spacer(1, 15*mm))
story.append(Paragraph(
    '祝你使用愉快！如有问题，请参考完整的产品手册或查看代码注释。',
    ParagraphStyle('Footer', fontName='SimHei', fontSize=10, alignment=TA_CENTER, textColor=SECONDARY_COLOR)
))

# 生成PDF
doc.build(story)
print('PDF生成成功：/home/z/my-project/download/小白使用说明V7.0.pdf')
