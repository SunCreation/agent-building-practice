"""Offline checks: fabricated engine events are not live-model evidence."""
import asyncio
import copy
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ["STUDIO_WORKSPACE"] = tempfile.mkdtemp(prefix="studio-test-")
from fastapi.testclient import TestClient
import app
from engines import opencode_event, run_process
from storyboard import SAMPLE_SOURCE, demo_story, replace_card, validate_story
from renderer import html_document


class StudioTests(unittest.TestCase):
    def setUp(self):
        self.project = {"topic": "도서 교환", "audience": "이웃", "card_count": 4, "sources": [{"id": "s1", "title": "가상 행사", "text": SAMPLE_SOURCE}]}
        self.story = demo_story(self.project)

    def test_revision_preserves_other_cards(self):
        changed = copy.deepcopy(self.story["cards"][2])
        changed["headline"] = "선택 카드 변경"
        new = replace_card(self.story, changed, "c3")
        self.assertEqual(new["cards"][:2], self.story["cards"][:2])
        self.assertEqual(new["cards"][3], self.story["cards"][3])
        self.assertNotEqual(new["cards"][2], self.story["cards"][2])
        changed["source_ids"] = ["made-up"]
        with self.assertRaises(ValueError): replace_card(self.story, changed, "c3")

    def test_variable_count_and_escape(self):
        self.project["card_count"] = 3
        self.story["cards"] = self.story["cards"][:3]
        validate_story(self.story, self.project)
        self.story["cards"][0]["body"] = '<script>alert("x")</script>'
        self.assertNotIn('<script>', html_document(self.story))

    def test_fabricated_events(self):
        self.assertEqual(opencode_event({"type": "text", "sessionID": "ses_fixture", "part": {"text": "hello"}}), ("ses_fixture", "hello", "text"))
        with self.assertRaises(RuntimeError): opencode_event({"type": "error", "error": {"message": "fixture"}})

    def test_http_boundary(self):
        with TestClient(app.app, base_url="http://localhost") as client:
            data = {"topic":"도서 교환","audience":"이웃","source_text":SAMPLE_SOURCE}
            self.assertEqual(client.post('/api/projects', json=data).status_code,403)
            self.assertEqual(client.post('/api/projects',json=data,headers={"X-Studio-Request":"1","Origin":"https://evil.example"}).status_code,403)
            r=client.post('/api/projects',json=data,headers={"X-Studio-Request":"1"})
            self.assertEqual(r.status_code,200)
            pid=r.json()['id']
            self.assertEqual(client.get(f'/api/projects/{pid}/files/secret.txt').status_code,404)
            self.assertEqual(client.post(f'/api/projects/{pid}/jobs',json={"action":"revise","card_id":"c1","instruction":"hi"},headers={"X-Studio-Request":"1"}).status_code,400)
            app.save(app.directory(pid) / "storyboard.json", self.story)
            self.assertEqual(client.post(f'/api/projects/{pid}/jobs',json={"action":"generate"},headers={"X-Studio-Request":"1"}).status_code,409)

    def test_queued_cancellation(self):
        async def check():
            p = await app.create(app.ProjectInput(topic="예제", audience="학생", source_text=SAMPLE_SOURCE))
            j = await app.start(p["id"], app.JobInput(action="generate"))
            result = await app.cancel(j["id"])
            self.assertEqual(result["status"], "cancelled")
        asyncio.run(check())

    def test_token_required_for_tailnet_host(self):
        with patch.dict(os.environ, {"STUDIO_TOKEN": "test-only-token"}):
            with TestClient(app.app, base_url="https://studio.example.ts.net") as client:
                self.assertEqual(client.get('/api/projects').status_code, 401)
                self.assertEqual(client.get('/api/projects', headers={"Authorization": "Bearer wrong"}).status_code, 401)
                self.assertEqual(client.get('/api/projects', headers={"Authorization": "Bearer test-only-token"}).status_code, 200)

    def test_actual_process_cancellation(self):
        async def check():
            with tempfile.TemporaryDirectory() as tmp:
                marker=Path(tmp)/"pid.txt"
                code="import os,time,pathlib;pathlib.Path('pid.txt').write_text(str(os.getpid()));time.sleep(60)"
                task=asyncio.create_task(run_process([sys.executable,"-c",code],tmp,os.environ.copy(),lambda _:None,opencode_event))
                for _ in range(100):
                    if marker.exists():break
                    await asyncio.sleep(.01)
                pid=int(marker.read_text(encoding="utf-8"))
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):await task
                with self.assertRaises(ProcessLookupError):os.kill(pid,0)
        if os.name != 'nt':asyncio.run(check())


if __name__=='__main__':unittest.main()
