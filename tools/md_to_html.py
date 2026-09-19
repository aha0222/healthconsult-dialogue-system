"""极简 Markdown → HTML 转换，用于把结题报告渲染成可直接阅读/打印的网页。

只支持本项目文档用到的语法子集：标题、表格、粗体、行内代码、代码块、
引用、无序列表、分隔线。不做通用转换——需要完整功能请用 pandoc。

用法：
    python tools/md_to_html.py docs/personality_evaluation_report.md
    python tools/md_to_html.py <输入.md> [输出.html]
"""

import html
import re
import sys
from pathlib import Path

CSS = """
*{box-sizing:border-box}
body{font-family:-apple-system,"Microsoft YaHei",sans-serif;max-width:900px;
     margin:0 auto;padding:48px 24px 96px;line-height:1.85;color:#1d1d1f;background:#fff}
h1{font-size:28px;border-bottom:3px solid #0071e3;padding-bottom:12px;margin-top:0}
h2{font-size:22px;margin-top:44px;padding-left:12px;border-left:5px solid #0071e3}
h3{font-size:17px;margin-top:30px;color:#1d1d1f}
h4{font-size:15px;margin-top:22px;color:#3a3a3c}
table{border-collapse:collapse;width:100%;margin:18px 0;font-size:14px}
th{background:#f5f5f7;font-weight:600;text-align:left}
th,td{border:1px solid #d2d2d7;padding:9px 12px;vertical-align:top}
tr:nth-child(even) td{background:#fafafa}
code{background:#f5f5f7;padding:2px 6px;border-radius:4px;font-size:.9em;
     font-family:Consolas,"Courier New",monospace}
pre{background:#f5f5f7;padding:16px 18px;border-radius:8px;overflow-x:auto;line-height:1.6}
pre code{background:none;padding:0}
blockquote{border-left:4px solid #d2d2d7;margin:16px 0;padding:6px 18px;color:#3a3a3c;background:#fafafa}
hr{border:none;border-top:1px solid #e5e5ea;margin:36px 0}
ul{padding-left:24px}
li{margin:4px 0}
strong{color:#1d1d1f}
em{color:#6e6e73}
"""


def inline(text: str) -> str:
    text = html.escape(text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<!\*)\*([^*]+?)\*(?!\*)", r"<em>\1</em>", text)
    text = re.sub(r"`([^`]+?)`", r"<code>\1</code>", text)
    return text


def convert(md: str) -> str:
    lines = md.split("\n")
    out, i = [], 0
    while i < len(lines):
        line = lines[i]

        # 代码块
        if line.startswith("```"):
            lang = line[3:].strip()
            buf, i = [], i + 1
            while i < len(lines) and not lines[i].startswith("```"):
                buf.append(html.escape(lines[i]))
                i += 1
            cls = f' class="language-{lang}"' if lang else ""
            out.append(f"<pre><code{cls}>" + "\n".join(buf) + "</code></pre>")
            i += 1
            continue

        # 表格：当前行以 | 开头且下一行是分隔行
        if line.strip().startswith("|") and i + 1 < len(lines) and \
                re.match(r"^\s*\|[\s:\-|]+\|\s*$", lines[i + 1]):
            header = [c.strip() for c in line.strip().strip("|").split("|")]
            i += 2
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            t = ["<table><thead><tr>"]
            t += [f"<th>{inline(c)}</th>" for c in header]
            t.append("</tr></thead><tbody>")
            for r in rows:
                t.append("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>")
            t.append("</tbody></table>")
            out.append("".join(t))
            continue

        # 标题
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            lvl = len(m.group(1))
            out.append(f"<h{lvl}>{inline(m.group(2))}</h{lvl}>")
            i += 1
            continue

        # 分隔线
        if re.match(r"^-{3,}\s*$", line):
            out.append("<hr>")
            i += 1
            continue

        # 引用
        if line.startswith(">"):
            buf = []
            while i < len(lines) and lines[i].startswith(">"):
                buf.append(lines[i].lstrip("> ").rstrip())
                i += 1
            out.append("<blockquote>" + "<br>".join(inline(b) for b in buf) + "</blockquote>")
            continue

        # 无序列表
        if re.match(r"^\s*[-*]\s+", line):
            buf = []
            while i < len(lines) and re.match(r"^\s*[-*]\s+", lines[i]):
                buf.append(re.sub(r"^\s*[-*]\s+", "", lines[i]))
                i += 1
            out.append("<ul>" + "".join(f"<li>{inline(b)}</li>" for b in buf) + "</ul>")
            continue

        if not line.strip():
            i += 1
            continue

        out.append(f"<p>{inline(line)}</p>")
        i += 1

    return "\n".join(out)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    src = Path(sys.argv[1])
    if not src.exists():
        print(f"[错误] 找不到 {src}")
        sys.exit(1)
    dst = Path(sys.argv[2]) if len(sys.argv) > 2 else src.with_suffix(".html")

    body = convert(src.read_text(encoding="utf-8"))
    title = src.stem
    dst.write_text(
        f'<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">'
        f'<title>{html.escape(title)}</title><style>{CSS}</style></head>'
        f"<body>{body}</body></html>",
        encoding="utf-8",
    )
    print(f"已生成：{dst}")


if __name__ == "__main__":
    main()
