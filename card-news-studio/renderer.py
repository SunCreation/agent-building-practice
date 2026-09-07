"""Render escaped editorial data, never arbitrary model-generated HTML."""
import html
import json
from pathlib import Path
from playwright.async_api import async_playwright


def html_document(story):
    esc = html.escape
    sources = {s["id"]: s["title"] for s in story["sources"]}
    cards = []
    for index, card in enumerate(story["cards"]):
        refs = " · ".join(f'{sid} {sources[sid]}' for sid in card["source_ids"])
        cards.append(f'''<article class="card" id="{esc(card['id'])}"><header><span>CARD / LAB</span><span>{index+1:02d} / {len(story['cards']):02d}</span></header><div class="eyebrow">{esc(story['title'])}</div><h1>{esc(card['headline'])}</h1><div class="rule"></div><p>{esc(card['body'])}</p><footer><span>{esc(refs)}</span><span>{esc(story['audience'])}</span></footer></article>''')
    return '''<!doctype html><html lang="ko"><meta charset="utf-8"><style>
*{box-sizing:border-box}body{margin:0;background:#eee;font-family:"Apple SD Gothic Neo","Malgun Gothic","Noto Sans CJK KR",sans-serif;color:#16332c}.card{width:1080px;height:1080px;position:relative;padding:76px 86px;background:#f5f1e7;page-break-after:always;overflow:hidden}.card:nth-child(even){background:#193e35;color:#f9f3df}header,footer{display:flex;justify-content:space-between;gap:30px}header{font-size:20px;letter-spacing:3px;font-weight:700}.eyebrow{overflow-wrap:anywhere;margin-top:115px;font-size:24px;opacity:.75}h1{font-size:68px;line-height:1.2;letter-spacing:-2px;margin:28px 0 30px;word-break:keep-all;overflow-wrap:anywhere;max-height:250px}.rule{height:8px;width:88px;background:#ed9a4e;margin:35px 0}p{font-size:34px;line-height:1.55;word-break:keep-all;overflow-wrap:anywhere;margin:0}footer{position:absolute;bottom:60px;left:86px;right:86px;font-size:19px;line-height:1.4;opacity:.8;border-top:1px solid currentColor;padding-top:22px}footer span{min-width:0;overflow-wrap:anywhere}footer span:first-child{max-width:65%}footer span:last-child{max-width:30%;text-align:right}@page{size:1080px 1080px;margin:0}@media print{body{background:none}.card{break-after:page}}
</style><title>Card News</title>''' + "".join(cards) + "</html>"


async def render(story, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    document = html_document(story)
    (directory / "cards.html").write_text(document, encoding="utf-8")
    (directory / "storyboard.json").write_text(json.dumps(story, ensure_ascii=False, indent=2), encoding="utf-8")
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        try:
            page = await browser.new_page(viewport={"width": 1080, "height": 1080}, device_scale_factor=1)
            await page.route("**/*", lambda route: route.abort())
            await page.set_content(document)
            await page.evaluate("document.fonts.ready")
            for card in story["cards"]:
                loc = page.locator("#" + card["id"])
                heading_overflows = await loc.locator("h1").evaluate("e => e.scrollHeight > e.clientHeight + 1")
                if heading_overflows:
                    raise ValueError(f"{card['id']}: headline exceeds its layout area; revise the headline before export")
                overlaps = await loc.evaluate("e => e.querySelector('p').getBoundingClientRect().bottom > e.querySelector('footer').getBoundingClientRect().top - 20")
                if overlaps:
                    raise ValueError(f"{card['id']}: text overlaps footer; shorten copy before export")
                await loc.screenshot(path=str(directory / f"{card['id']}.png"))
            await page.pdf(path=str(directory / "cards.pdf"), width="1080px", height="1080px", print_background=True)
        finally:
            await browser.close()
    return sorted(p.name for p in directory.iterdir() if p.is_file())
