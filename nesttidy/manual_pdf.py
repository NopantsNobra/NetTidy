from __future__ import annotations

from pathlib import Path


MANUAL_LINES = [
    "理巢 NestTidy 简要操作手册",
    "",
    "1. 双击 start_nesttidy.bat 启动软件，浏览器会打开本地 Web UI。",
    "2. 在源目录中填写要整理的文件夹路径。",
    "3. 如果选择的是资源包父目录，使用批量整理；如果选择的是单个资源目录，使用单个资源整理。",
    "4. 先点击生成试运行计划。试运行不会修改磁盘。",
    "5. 在预览映射和异常项中检查整理结果。",
    "6. 需要安全复制时填写 copy 输出目录，再使用 copy 命令或 API 执行；源目录不会改变。",
    "7. move 会直接整理源目录，必须确认预览无误，并保留事务日志。",
    "8. 深度诊断可检查重复文件；报告只给建议，不自动删除。",
    "9. 权限修复默认关闭，只在明确勾选或命令行指定时执行。",
    "10. 报告会写入 理巢报告 目录，可查看 summary、mappings、issues 和 history.sqlite。",
]


def write_manual_pdf(path: str | Path) -> Path:
    out = Path(path).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    content = build_content_stream(MANUAL_LINES)
    objects: list[bytes] = []
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    objects.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>")
    objects.append(b"<< /Type /Font /Subtype /Type0 /BaseFont /STSong-Light /Encoding /UniGB-UCS2-H /DescendantFonts [6 0 R] >>")
    objects.append(f"<< /Length {len(content)} >>\nstream\n".encode("ascii") + content + b"\nendstream")
    objects.append(b"<< /Type /Font /Subtype /CIDFontType0 /BaseFont /STSong-Light /CIDSystemInfo << /Registry (Adobe) /Ordering (GB1) /Supplement 2 >> /FontDescriptor 7 0 R >>")
    objects.append(b"<< /Type /FontDescriptor /FontName /STSong-Light /Flags 4 /FontBBox [0 -200 1000 900] /ItalicAngle 0 /Ascent 880 /Descent -120 /CapHeight 700 /StemV 80 >>")
    write_pdf(out, objects)
    return out


def build_content_stream(lines: list[str]) -> bytes:
    chunks = ["BT", "/F1 15 Tf", "50 790 Td", "20 TL"]
    for idx, line in enumerate(lines):
        if idx:
            chunks.append("T*")
        chunks.append(f"<{line.encode('utf-16-be').hex().upper()}> Tj")
    chunks.append("ET")
    return "\n".join(chunks).encode("ascii")


def write_pdf(path: Path, objects: list[bytes]) -> None:
    offsets: list[int] = []
    data = bytearray(b"%PDF-1.4\n%\xE2\xE3\xCF\xD3\n")
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(data))
        data.extend(f"{index} 0 obj\n".encode("ascii"))
        data.extend(obj)
        data.extend(b"\nendobj\n")
    xref = len(data)
    data.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    data.extend(b"0000000000 65535 f \n")
    for offset in offsets:
        data.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    data.extend(f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode("ascii"))
    path.write_bytes(bytes(data))

