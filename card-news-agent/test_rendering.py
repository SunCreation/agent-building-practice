"""Renderer checks use Chromium and the actual Antigravity-generated lesson PNG."""
import asyncio
import copy
import os
import json
import shutil
from pathlib import Path
import tempfile
import unittest
import zipfile

from rendering import html_document, png_dimensions, render, validate_story


IMAGE = Path(os.environ.get("CARD_NEWS_TEST_IMAGE", str(Path(__file__).resolve().parent / "examples/ai-news/background.png")))
STORY = {
    "title": "AI 최신소식", "audience": "AI를 활용하는 기획자",
    "sources": [{"id": "s1", "title": "확인한 원문", "url": "https://example.com/news"}],
    "cards": [{"id": f"card-{i}", "headline": headline,
               "body": "새로운 기능은 누구에게 어떤 변화를 만들까요?\n발표 내용과 실제로 확인된 범위를 구분해서 살펴봅니다.",
               "source_ids": ["s1"], "image_prompt": "추상적인 기술 일러스트"}
              for i, headline in enumerate(["AI 소식, 내 일에 연결하기", "발표와 검증 사이", "확인할 변화 세 가지", "우리 팀에 적용한다면", "원문에서 한 번 더 확인하기"], 1)]
}


class RenderingTests(unittest.TestCase):
    def test_path_traversal_rejected(self):
        story = copy.deepcopy(STORY)
        story["cards"][0]["id"] = "../stolen"
        with self.assertRaises(ValueError):
            validate_story(story)

    def test_unknown_citation_rejected(self):
        story = copy.deepcopy(STORY)
        story["cards"][0]["source_ids"] = ["absent"]
        with self.assertRaises(ValueError):
            validate_story(story)

    def test_actual_png_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fake.png"
            path.write_text("Created image successfully")
            with self.assertRaises(ValueError):
                png_dimensions(path)

    def test_escaping(self):
        story = copy.deepcopy(STORY)
        story["cards"][0]["body"] = '<script>alert("x")</script>'
        doc = html_document(story, IMAGE)
        self.assertNotIn("<script>", doc)
        self.assertIn("&lt;script&gt;", doc)
        self.assertIn("data:image/png;base64,", doc)

    def test_render_and_unchanged_cards_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            first, second = Path(tmp) / "first", Path(tmp) / "second"
            source_image = Path(tmp) / "background.png"
            shutil.copyfile(IMAGE, source_image)
            provenance = {"tool": "agy", "prompt": "글자 없는 AI 배경", "created_at": "2026-09-08"}
            (Path(tmp) / "image-provenance.json").write_text(json.dumps(provenance))
            story = copy.deepcopy(STORY)
            story["researched_at"] = "2026-09-08T21:00:00+09:00"
            names = asyncio.run(render(story, first, source_image))
            self.assertEqual(json.loads((first / "image-provenance.json").read_text()), provenance)
            self.assertIn(story["researched_at"], (first / "sources.md").read_text())
            revised = copy.deepcopy(story)
            revised["cards"][1]["body"] = "이 카드의 본문만 수정했습니다."
            asyncio.run(render(revised, second, source_image))
            for card in STORY["cards"]:
                path = first / (card["id"] + ".png")
                self.assertEqual(png_dimensions(path), (1080, 1350))
                if card["id"] != "card-2":
                    self.assertEqual(path.read_bytes(), (second / path.name).read_bytes())
            self.assertNotEqual((first / "card-2.png").read_bytes(), (second / "card-2.png").read_bytes())
            with zipfile.ZipFile(first / "card-news.zip") as archive:
                self.assertEqual(set(archive.namelist()), set(names) - {"card-news.zip"})
                self.assertIsNone(archive.testzip())

    def test_text_overflow_rejected_before_export(self):
        story = copy.deepcopy(STORY)
        story["cards"][0]["body"] = "긴 본문을 조정하지 않고 넣으면 안 됩니다. " * 200
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "exceeds|overflow"):
                asyncio.run(render(story, tmp, IMAGE))
            self.assertFalse((Path(tmp) / "card-news.zip").exists())


if __name__ == "__main__":
    unittest.main()
