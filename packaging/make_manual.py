#!/usr/bin/env python3
"""把 docs/使用说明.md 生成**自包含**的 HTML（2026-10-03）。

为什么要 HTML 而不是直接发 .md：客户是"不懂编程的同事"——.md 双击打开
是带记号的天书，HTML 双击进浏览器、图文排版正常。自包含 = 截图按 base64
内嵌、CSS 内联，**一个文件走天下**（zip 会被转发，图片不能丢）。

产物两处用：
  * 随包：zip 里与应用并列放一份（macOS）或放进程序文件夹（Windows），
    双击即读；同时打进 app 包内，将来「帮助 → 使用说明」直接打开它。
  * 仓库：docs/使用说明.html 提交（GitHub 上也能点开）。

用法：python packaging/make_manual.py
"""
import base64
import re
import sys
from pathlib import Path

import markdown

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SRC = ROOT / "docs" / "使用说明.md"
OUT = ROOT / "docs" / "使用说明.html"

TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>XRD Toolkit 使用说明</title>
<style>
  body {{ max-width: 820px; margin: 2.2rem auto; padding: 0 1.1rem;
         font-family: -apple-system, "PingFang SC", "Microsoft YaHei",
                      "Segoe UI", sans-serif; line-height: 1.75;
         color: #1c2733; }}
  h1 {{ font-size: 1.7rem; border-bottom: 2px solid #e3e8ee;
        padding-bottom: .5rem; }}
  h2 {{ font-size: 1.25rem; margin-top: 2.2rem; color: #11324f; }}
  img {{ max-width: 100%; border: 1px solid #dde3ea; border-radius: 6px; }}
  code, pre {{ font-family: Menlo, Consolas, monospace; font-size: .92em; }}
  code {{ background: #f2f5f8; padding: .1em .35em; border-radius: 4px; }}
  table {{ border-collapse: collapse; width: 100%; }}
  th, td {{ border: 1px solid #dde3ea; padding: .45rem .6rem;
            text-align: left; }}
  th {{ background: #f6f8fa; }}
  blockquote {{ margin: 0; padding: .4rem 1rem; color: #5b6b7b;
                border-left: 4px solid #cbd5e0; background: #fafbfc; }}
  hr {{ border: none; border-top: 1px solid #e3e8ee; margin: 2.5rem 0; }}
</style>
</head>
<body>
{body}
</body>
</html>
"""


def inline_images(html: str, base: Path) -> str:
    """把 <img src="相对路径"> 换成 base64 内嵌（自包含的关键一步）。

    注意 markdown 的输出是 `<img alt="…" src="…" />`——**src 不一定紧跟
    在 `<img` 后面**（第一版按 `<img src=` 匹配，一张图都没换到，
    自包含变成了空壳还默默"成功"，现在加了一道数量断言兜底）。
    """
    def repl(m):
        head, src = m.group(1), m.group(2)
        path = (base / src).resolve()
        if not path.is_file():
            raise SystemExit(f"！图片找不到：{path}")
        data = base64.b64encode(path.read_bytes()).decode("ascii")
        return f'{head}src="data:image/png;base64,{data}"'
    return re.sub(r'(<img\b[^>]*?)src="([^"]+)"', repl, html)


def main() -> int:
    text = SRC.read_text(encoding="utf-8")
    n_src = len(re.findall(r"!\[[^\]]*\]\(", text))
    body = markdown.markdown(text, extensions=["tables", "fenced_code"])
    body = inline_images(body, SRC.parent)
    n_hit = body.count("data:image/png")
    if n_src != n_hit:            # 自包含是硬要求，数量对不上就当场报错
        raise SystemExit(f"！内嵌数量不符：源 {n_src} 张、内嵌 {n_hit} 张")
    OUT.write_text(TEMPLATE.format(body=body), encoding="utf-8")
    kb = OUT.stat().st_size // 1024
    print(f"✓ {OUT}（自包含 {n_hit} 张截图，{kb} KB）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
