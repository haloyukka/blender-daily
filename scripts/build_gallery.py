"""
works/ 配下の作品フォルダを走査して、トップの README.md にサムネイル一覧を書き込む。

  python scripts/build_gallery.py

作品フォルダの決まりごと:
  works/YYYY-MM-DD_slug/
    ├─ README.md   … 1行目の「# タイトル」を一覧に使う
    ├─ *.py        … 再現用スクリプト
    └─ render.png  … サムネイル（無ければフォルダ内の最初の画像）

README.md の <!-- GALLERY:START --> と <!-- GALLERY:END --> の間だけを書き換える。
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKS = ROOT / "works"
README = ROOT / "README.md"
START, END = "<!-- GALLERY:START -->", "<!-- GALLERY:END -->"
COLUMNS = 3
THUMB_WIDTH = 240
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif")
FOLDER_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})_(.+)$")


def find_image(folder: Path):
    preferred = folder / "render.png"
    if preferred.exists():
        return preferred
    images = sorted(p for p in folder.iterdir() if p.suffix.lower() in IMAGE_EXTS)
    return images[0] if images else None


def read_title(folder: Path, fallback: str) -> str:
    readme = folder / "README.md"
    if readme.exists():
        for line in readme.read_text(encoding="utf-8").splitlines():
            if line.startswith("# "):
                return line[2:].strip()
    return fallback


def collect_works():
    works = []
    for folder in WORKS.iterdir() if WORKS.exists() else []:
        m = FOLDER_RE.match(folder.name)
        if not folder.is_dir() or not m:
            continue
        date, slug = m.groups()
        works.append({
            "date": date,
            "title": read_title(folder, slug),
            "dir": folder.relative_to(ROOT).as_posix(),
            "image": (lambda p: p.relative_to(ROOT).as_posix() if p else None)(find_image(folder)),
        })
    return sorted(works, key=lambda w: w["dir"], reverse=True)  # 新しい順


def render_table(works) -> str:
    if not works:
        return "_まだ作品はありません。_"
    cells = []
    for w in works:
        img = (f'<img src="{w["image"]}" width="{THUMB_WIDTH}" alt="{w["title"]}">'
               if w["image"] else "(no image)")
        cells.append(f'<a href="{w["dir"]}">{img}</a><br><sub>{w["date"]}</sub><br>'
                     f'<a href="{w["dir"]}">{w["title"]}</a>')
    rows = []
    for i in range(0, len(cells), COLUMNS):
        tds = "".join(f'<td align="center" valign="top">{c}</td>' for c in cells[i:i + COLUMNS])
        rows.append(f"<tr>{tds}</tr>")
    count = f"作品数: {len(works)}"
    return count + "\n\n<table>\n" + "\n".join(rows) + "\n</table>"


def main():
    text = README.read_text(encoding="utf-8")
    if START not in text or END not in text:
        raise SystemExit(f"README.md に {START} / {END} がありません")
    head, rest = text.split(START, 1)
    _, tail = rest.split(END, 1)
    new = f"{head}{START}\n{render_table(collect_works())}\n{END}{tail}"
    if new != text:
        README.write_text(new, encoding="utf-8")
        print("README.md を更新しました")
    else:
        print("変更なし")


if __name__ == "__main__":
    main()
