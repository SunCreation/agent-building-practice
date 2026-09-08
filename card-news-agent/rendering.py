"""Deterministic, offline 1080 × 1350 card rendering; never execute agent HTML."""
from __future__ import annotations

import base64
import hashlib
import html
import json
import re
import shutil
import struct
import zipfile
from pathlib import Path

from playwright.async_api import async_playwright


CSS = """
*{box-sizing:border-box}html,body{margin:0;background:#deddd7}
body{font-family:"Apple SD Gothic Neo","Noto Sans CJK KR","Malgun Gothic",sans-serif;color:#18254b}
.card{width:1080px;height:1350px;position:relative;background:#f6f3eb;overflow:hidden;padding:68px 76px}
header{display:flex;justify-content:space-between;font-size:21px;font-weight:800;letter-spacing:3px}
.mark{color:#2658de}.copy{position:absolute;left:76px;right:76px;top:175px;height:995px;display:flex;flex-direction:column;align-items:flex-start;overflow:hidden}
.eyebrow{font-size:24px;line-height:1.4;max-height:68px;overflow:hidden;letter-spacing:.5px;font-weight:600}
h1{font-size:64px;line-height:1.22;letter-spacing:-2px;word-break:keep-all;overflow-wrap:anywhere;margin:26px 0 26px;max-height:240px;overflow:hidden;flex-shrink:0;width:100%}
.rule{width:80px;height:8px;background:#f37b42;flex-shrink:0;margin-bottom:28px}
p{font-size:35px;line-height:1.65;letter-spacing:-.4px;word-break:keep-all;overflow-wrap:anywhere;white-space:pre-wrap;margin:0;width:100%;flex-shrink:0}
footer{position:absolute;left:76px;right:76px;bottom:60px;height:69px;border-top:1px solid currentColor;padding-top:18px;display:flex;justify-content:space-between;gap:20px;font-size:19px;line-height:1.35}
footer span{max-width:65%;overflow:hidden;overflow-wrap:anywhere}
footer span:last-child{text-align:right;max-width:30%}
.art{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;object-position:center bottom;opacity:1;z-index:0}
.card::after{content:"";position:absolute;inset:0;z-index:1;background:linear-gradient(to bottom,rgba(246,243,235,.97) 0%,rgba(246,243,235,.94) 48%,rgba(246,243,235,.85) 67%,rgba(246,243,235,.12) 87%,rgba(246,243,235,.05) 100%);pointer-events:none}
header,.copy,footer{z-index:2}header{position:relative}
footer{background:rgba(246,243,235,.95);padding:16px 18px;height:73px;bottom:40px;border:0;border-radius:8px}
.cover .copy{right:76px;height:820px}.cover h1{font-size:76px;max-height:292px}.cover p{max-width:860px;font-size:35px}
.deep{color:#fff9ed}.deep::after{background:linear-gradient(to bottom,rgba(14,29,58,.95) 0%,rgba(14,29,58,.91) 48%,rgba(14,29,58,.8) 67%,rgba(14,29,58,.12) 87%,rgba(14,29,58,.04) 100%)}
.deep .mark{color:#fff9ed}.deep .rule{background:#ffad75}.deep footer{background:rgba(14,29,58,.95)}
.split .copy{height:880px}.close .copy{height:880px}
.art{inset:auto 0 0;width:1080px;height:540px;object-fit:cover;object-position:center}
.card::after,.deep::after{display:none}
.copy,.cover .copy,.split .copy,.close .copy{top:130px;height:640px}
.eyebrow{font-size:21px;max-height:60px}
h1{font-size:58px;max-height:215px;margin:20px 0}.cover h1{font-size:64px;max-height:240px}
p,.cover p{font-size:30px;line-height:1.5;max-width:none}.rule{margin-bottom:20px;height:6px}
.deep{background:#102144}
@media print{.card{break-after:page}}@page{size:1080px 1350px;margin:0}
"""


def validate_story(story: dict) -> None:
    for key in ("title", "audience"):
        if not isinstance(story.get(key), str) or not story[key].strip():
            raise ValueError(f"Missing {key}")
    cards = story.get("cards")
    if not isinstance(cards, list) or not 1 <= len(cards) <= 12:
        raise ValueError("Provide 1–12 cards")
    sources = story.get("sources")
    if not isinstance(sources, list):
        raise ValueError("sources must be a list")
    source_ids = set()
    for source in sources:
        if not all(isinstance(source.get(k), str) and source[k] for k in ("id", "title", "url")):
            raise ValueError("Each source requires id, title and url")
        if not re.match(r"^https?://[^\s]+$", source["url"]):
            raise ValueError("Source URLs must be HTTP(S)")
        if source["id"] in source_ids:
            raise ValueError("Duplicate source id")
        source_ids.add(source["id"])
    ids = set()
    for card in cards:
        cid = card.get("id", "")
        if not isinstance(cid, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", cid) or cid in ids:
            raise ValueError("Card ids must be unique safe filenames")
        ids.add(cid)
        for key in ("headline", "body"):
            if not isinstance(card.get(key), str) or not card[key].strip():
                raise ValueError(f"{cid}: missing {key}")
        refs = card.get("source_ids")
        if not isinstance(refs, list) or any(not isinstance(s, str) or s not in source_ids for s in refs):
            raise ValueError(f"{cid}: unknown source id")


def png_dimensions(path: Path) -> tuple[int, int]:
    """Check the actual file header, not the claimed extension."""
    data = path.read_bytes()
    if len(data) < 33 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        raise ValueError("Image must be an actual PNG")
    width, height = struct.unpack(">II", data[16:24])
    if not (64 <= width <= 8192 and 64 <= height <= 8192):
        raise ValueError("Image dimensions must be 64–8192 pixels")
    return width, height


def html_document(story: dict, image_path: Path) -> str:
    validate_story(story)
    paths = {c['id']: Path(image_path[c['id']]) for c in story['cards']} if isinstance(image_path, dict) else {c['id']: Path(image_path) for c in story['cards']}
    for file in paths.values(): png_dimensions(file)
    esc = html.escape
    cards = []
    styles = ["cover", "deep", "number", "split", "close"]
    for index, card in enumerate(story["cards"]):
        style = styles[index % len(styles)]
        image_uri = "data:image/png;base64," + base64.b64encode(paths[card["id"]].read_bytes()).decode("ascii")
        refs = " · ".join(card["source_ids"]) or "출처: 함께 제공된 자료 참고"
        cards.append(f'''<article class="card {style}" id="{esc(card['id'])}">
<header><span class="mark">TOPIC / BRIEF</span><span>{index + 1:02d} / {len(story['cards']):02d}</span></header>
<img class="art" src="{image_uri}" alt="내용 이해를 위한 생성 이미지">
<div class="copy"><div class="eyebrow" data-check>{esc(story['title'])}</div>
<h1 data-check>{esc(card['headline'])}</h1><div class="rule"></div><p data-check>{esc(card['body'])}</p></div>
<footer><span data-check>{esc(refs)}</span><span data-check>{esc(story['audience'])}</span></footer></article>''')
    return '<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=1080"><title>' + esc(story["title"]) + '</title><style>' + CSS + '</style></head><body>' + "".join(cards) + '</body></html>'


async def render(story: dict, directory: str | Path, image_path: str | Path) -> list[str]:
    """Render only after validating every card; reject overflowing text explicitly."""
    directory = Path(directory)
    image_path = {k: Path(v) for k,v in image_path.items()} if isinstance(image_path, dict) else Path(image_path)
    document = html_document(story, image_path)
    directory.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        try:
            page = await browser.new_page(viewport={"width": 1080, "height": 1350}, device_scale_factor=1)
            await page.route("**/*", lambda route: route.abort())
            await page.set_content(document)
            await page.evaluate("document.fonts.ready")
            # Header validation alone cannot establish that a PNG decodes.
            await page.evaluate("""async () => { for (const image of document.images) {
                await image.decode(); if (!image.naturalWidth) throw Error('Image could not decode');
            }}""")
            for card in story["cards"]:
                loc = page.locator("#" + card["id"])
                problems = await loc.evaluate("""e => {
                    const issues=[];
                    const art=e.querySelector('.art'), style=getComputedStyle(art), a=art.getBoundingClientRect(), r=e.getBoundingClientRect();
                    if(style.display==='none'||style.visibility==='hidden'||Number(style.opacity)<.9||a.width<r.width-1||a.height<539)
                        issues.push('generated image must cover the card');
                    for(const item of e.querySelectorAll('[data-check]')) {
                        if(item.scrollHeight > item.clientHeight + 1 || item.scrollWidth > item.clientWidth + 1)
                            issues.push(item.tagName + ' text overflow');
                    }
                    const copy=e.querySelector('.copy'), body=e.querySelector('p');
                    if(body.getBoundingClientRect().bottom > copy.getBoundingClientRect().bottom + 1)
                        issues.push('body exceeds copy area');
                    if(e.querySelector('footer').scrollHeight > e.querySelector('footer').clientHeight + 1)
                        issues.push('footer overflow');
                    return issues;
                }""")
                if problems:
                    raise ValueError(f"{card['id']}: {'; '.join(problems)}. Revise the copy before export.")
            for card in story["cards"]:
                await page.locator("#" + card["id"]).screenshot(path=str(directory / f"{card['id']}.png"), animations="disabled")
        finally:
            await browser.close()
    (directory / "cards.html").write_text(document, encoding="utf-8")
    (directory / "storyboard.json").write_text(json.dumps(story, ensure_ascii=False, indent=2), encoding="utf-8")
    sources = "# " + story["title"] + " — 출처\n\n" + "\n".join(f"- {s['id']}: {s['title']}\n  {s['url']}" for s in story["sources"])
    if story.get("researched_at"):
        sources += "\n\n조사 기준 시각: " + str(story["researched_at"]) + "\n"
    sources += "\n\n카드의 배경은 내용을 설명하기 위한 생성 이미지이며 실제 사건의 사진이 아닙니다.\n"
    (directory / "sources.md").write_text(sources, encoding="utf-8")
    files = [f"{c['id']}.png" for c in story["cards"]] + ["cards.html", "storyboard.json", "sources.md"]
    if isinstance(image_path, dict):
        records=[]
        for cid, image in image_path.items():
            filename=f"{cid}-image.png"
            shutil.copyfile(image, directory/filename); files.append(filename)
            provenance=image.parent/'image-provenance.json'
            records.append({'card_id':cid,'file':filename,'sha256':hashlib.sha256(image.read_bytes()).hexdigest(), 'generation':json.loads(provenance.read_text()) if provenance.is_file() else None})
        (directory/'image-provenance.json').write_text(json.dumps({'images':records},ensure_ascii=False,indent=2))
        files.append('image-provenance.json')
    else:
        provenance = image_path.parent / "image-provenance.json"
        if provenance.is_file():
            target = directory / "image-provenance.json"
            if provenance.resolve() != target.resolve(): shutil.copyfile(provenance, target)
            files.append("image-provenance.json")
    with zipfile.ZipFile(directory / "card-news.zip", "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for filename in files:
            # Fixed timestamps avoid meaningless archive changes on repeated runs.
            info = zipfile.ZipInfo(filename, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, (directory / filename).read_bytes())
    return files + ["card-news.zip"]
