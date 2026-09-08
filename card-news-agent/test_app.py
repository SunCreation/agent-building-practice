"""API state/guard unit tests. Engine operations are replaced; no model calls."""
import asyncio
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import ANY, AsyncMock, patch

from fastapi.testclient import TestClient
import app as studio

HEADERS = {"x-studio-request": "1"}


async def waiting_operation(project):
    await asyncio.sleep(3600)


class AppTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = patch.object(studio, "WORKSPACE", Path(self.tmp.name))
        self.tasks = patch.object(studio, "TASKS", {})
        self.operations = patch.dict(studio.OPERATIONS, {key: waiting_operation for key in studio.OPERATIONS})
        self.workspace.start(); self.tasks.start(); self.operations.start()
        self.client = TestClient(studio.app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.operations.stop(); self.tasks.stop(); self.workspace.stop()
        self.tmp.cleanup()

    def project(self, status="idle", **updates):
        response = self.client.post("/api/projects", json={"topic": "AI 최신소식"}, headers=HEADERS)
        self.assertEqual(response.status_code, 200)
        project = response.json()
        project.update(status=status, **updates)
        studio.save(project)
        return project

    def post(self, project, suffix, payload=None):
        return self.client.post(f"/api/projects/{project['id']}/{suffix}", json=payload, headers=HEADERS)

    def test_progress_is_persisted_before_agent_finishes(self):
        project = self.project("running", stage="plan")
        async def streaming_ask(*args, on_progress=None, **kwargs):
            await on_progress("웹 검색 시작: 카드뉴스")
            saved = studio.load(project["id"])
            self.assertEqual(saved["status"], "running")
            self.assertEqual(saved["events"][-1]["message"], "웹 검색 시작: 카드뉴스")
            return {"session_id": "test-session", "text": '{"ok": true}'}
        with patch.object(studio, "ask", streaming_ask):
            result = asyncio.run(studio.agent(project, "test"))
        self.assertEqual(result, {"ok": True})

    def test_started_stage_has_its_own_clock(self):
        project = self.project()
        response = self.post(project, "research", {})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["stage_started_at"])

    def test_cross_origin_and_nonlocal_host_rejected(self):
        self.assertEqual(self.client.get("/api/projects", headers={"origin": "https://evil.example"}).status_code, 403)
        self.assertEqual(self.client.get("/api/projects", headers={"host": "evil.example"}).status_code, 403)
        self.assertEqual(self.client.get("/api/projects", headers={"origin": "http://testserver"}).status_code, 200)

    def test_mutation_requires_header(self):
        self.assertEqual(self.client.post("/api/projects", json={"topic": "AI"}).status_code, 403)
        self.assertEqual(self.client.post("/api/projects", json={"topic": "AI"}, headers={"x-studio-request": "0"}).status_code, 403)

    def test_selection_duplicate_and_unknown_rejected(self):
        project = self.project("awaiting_selection", candidates=[{"id": "n1"}, {"id": "n2"}])
        for values in (["n1", "n1"], ["missing"], []):
            self.assertEqual(self.post(project, "selection", {"candidate_ids": values}).status_code, 422)
        self.assertEqual(studio.load(project["id"])["status"], "awaiting_selection")
        self.assertEqual(len(studio.TASKS), 0)

    def test_stale_answer_does_not_start_work(self):
        project = self.project("waiting_for_user", question={"id": "current", "options": []})
        result = self.post(project, "answers", {"question_id": "old", "answer": "기획자"})
        self.assertEqual(result.status_code, 409)
        self.assertEqual(len(studio.TASKS), 0)

    def test_duplicate_answer_does_not_duplicate_job(self):
        project = self.project("waiting_for_user", question={"id": "current", "options": []})
        payload = {"question_id": "current", "answer": "기획자"}
        self.assertEqual(self.post(project, "answers", payload).status_code, 200)
        first_task = studio.TASKS[project["id"]]
        self.assertEqual(self.post(project, "answers", payload).status_code, 409)
        self.assertIs(studio.TASKS[project["id"]], first_task)
        self.assertEqual(studio.load(project["id"])["answer"], "기획자")

    def test_zip_requires_approval_of_current_version(self):
        project = self.project("preview", version=1)
        directory = studio.path(project["id"]) / "v1"
        directory.mkdir(); (directory / "card-news.zip").write_bytes(b"test artifact")
        url = f"/api/projects/{project['id']}/files/v1/card-news.zip"
        self.assertEqual(self.client.get(url).status_code, 409)
        self.assertEqual(self.post(project, "finalize").status_code, 200)
        self.assertEqual(self.client.get(url).status_code, 200)
        current = studio.load(project["id"])
        current.update(status="preview", version=2, final_approved=False)
        studio.save(current)
        self.assertEqual(self.client.get(url).status_code, 409)

    def test_restart_preserves_waiting_and_marks_running_interrupted(self):
        waiting = self.project("waiting_for_user", question={"id": "persistent", "options": [{"label": "A"}]})
        running = self.project("running", stage="research")
        with TestClient(studio.app) as restarted:
            saved = restarted.get(f"/api/projects/{waiting['id']}").json()
            self.assertEqual(saved["status"], "waiting_for_user")
            self.assertEqual(saved["question"]["id"], "persistent")
            saved = restarted.get(f"/api/projects/{running['id']}").json()
            self.assertEqual(saved["status"], "interrupted")
            self.assertEqual(saved["stage"], "research")

    def test_cancel_running_operation_and_retry(self):
        project = self.project()
        self.assertEqual(self.post(project, "research").status_code, 200)
        self.assertEqual(self.post(project, "cancel").status_code, 200)
        self.assertEqual(studio.load(project["id"])["status"], "canceled")
        self.assertTrue(studio.TASKS[project["id"]].done())
        self.assertEqual(self.post(project, "retry").status_code, 200)
        self.assertEqual(studio.load(project["id"])["status"], "running")

    def test_card_images_follow_content_and_reuse_verified_outputs(self):
        cards=[{'id':f'card-{i}','headline':f'주제 {i}','body':f'구체적인 내용 {i}'} for i in range(1,3)]
        project=self.project('running',story={'cards':cards})
        async def fake_generate(prompt,directory,**kwargs):
            directory.mkdir(parents=True,exist_ok=True)
            image=directory/'background.png';image.write_bytes(prompt.encode())
            (directory/'image-provenance.json').write_text(json.dumps({'generated':True,'sha256':hashlib.sha256(image.read_bytes()).hexdigest()}))
            return image
        with patch.object(studio,'generate_image',side_effect=fake_generate) as generate, patch.object(studio,'make_artifacts',new_callable=AsyncMock):
            asyncio.run(studio.produce(project))
            self.assertEqual(generate.await_count,2)
            self.assertIn('구체적인 내용 1',generate.await_args_list[0].args[0])
            asyncio.run(studio.produce(project))
            self.assertEqual(generate.await_count,2)
            project['story']['cards'][0]['body']='수정된 내용'
            asyncio.run(studio.produce(project))
            self.assertEqual(generate.await_count,3)
        self.assertNotEqual(project['card_images']['card-1']['path'],project['card_images']['card-2']['path'])

    def test_overflow_repair_only_changes_failed_card(self):
        story = {"title": "AI 소식", "audience": "기획자", "sources": [{"id": "s1", "title": "원문", "url": "https://example.com/news"}], "cards": [
            {"id": f"card-{i}", "headline": "확인한 소식", "body": "확인된 사실과 조건을 살펴봅니다.", "source_ids": ["s1"], "image_prompt": "배경"}
            for i in range(1, 6)]}
        project = self.project("running", story=copy.deepcopy(story))
        image = studio.path(project["id"]) / "background.png"
        fixed = {"id": "changed", "headline": "짧은 제목", "body": "조건을 확인하세요.", "source_ids": ["wrong"], "image_prompt": "changed"}
        with patch.object(studio, "render", new_callable=AsyncMock, side_effect=[ValueError("card-1: body exceeds copy area"), ["card-1.png", "card-news.zip"]]) as renderer, patch.object(studio, "agent", new_callable=AsyncMock, return_value=fixed) as agent:
            asyncio.run(studio.make_artifacts(project, image))
            self.assertEqual(renderer.await_count, 2)
            agent.assert_awaited_once()
        self.assertEqual(project["story"]["cards"][1:], story["cards"][1:])
        self.assertEqual(project["story"]["cards"][0]["body"], "조건을 확인하세요.")
        for field in ("id", "source_ids", "image_prompt"):
            self.assertEqual(project["story"]["cards"][0][field], story["cards"][0][field])
        self.assertEqual(project["status"], "preview")
        self.assertEqual(project["version"], 1)
        self.assertFalse(project["final_approved"])

    def test_plan_feedback_blocks_blank_duplicate_and_approval(self):
        project = self.project("awaiting_approval", story={"title": "original"})
        self.assertEqual(self.post(project, "plan-revisions", {"instruction": "   "}).status_code, 422)
        self.assertEqual(self.post(project, "plan-revisions", {"instruction": "첫 카드에 독자의 고민을 담아 줘"}).status_code, 200)
        self.assertEqual(self.post(project, "approve").status_code, 409)
        self.assertEqual(self.post(project, "plan-revisions", {"instruction": "again"}).status_code, 409)
        self.assertEqual(studio.load(project["id"])["story"]["title"], "original")

    def test_replan_preserves_old_story_until_valid_and_requires_new_approval(self):
        story = {"title":"test", "audience":"독자", "hook_candidates":["이 차이가 궁금한가요?","다른 후보"], "selected_hook":"이 차이가 궁금한가요?", "hook_reason":"독자의 고민과 연결", "sources":[{"id":"s1","title":"source","url":"https://example.com"}], "cards":[{"id":f"card-{i}","headline":"이 차이가 궁금한가요?" if i == 1 else "설명", "body":"근거 설명", "source_ids":["s1"]} for i in range(1,6)]}
        project = self.project("running", stage="replan", story=copy.deepcopy(story), answer="독자", research={}, plan_feedback="후킹 보완")
        brief = {"candidates":[{"headline": h, "reader_interest":"관심", "curiosity":"궁금증", "evidence":"근거", "payoff":"해소", "risk":"과장 없음"} for h in story["hook_candidates"]], "selected_hook":story["selected_hook"], "selection_reason":"관심과 연결"}
        invalid = copy.deepcopy(story); invalid["cards"][0]["headline"] = "발표 요약"
        with patch.object(studio,"agent",new_callable=AsyncMock,side_effect=[brief, invalid]):
            with self.assertRaises(ValueError): asyncio.run(studio.plan(project))
        self.assertEqual(project["story"], story)
        with patch.object(studio,"agent",new_callable=AsyncMock,side_effect=[brief, copy.deepcopy(story)]) as planner, patch.object(studio,"generate_image",new_callable=AsyncMock) as generate:
            asyncio.run(studio.plan(project))
            generate.assert_not_awaited()
            self.assertEqual(planner.await_count, 2)
            self.assertIn("첫 카드 후킹만 먼저", planner.await_args_list[0].args[1])
            self.assertIn(brief["selected_hook"], planner.await_args_list[1].args[1])
        self.assertEqual(project["status"], "awaiting_approval")
        self.assertEqual(project["plan_history"][0]["story"], story)

    def test_invalid_transition_and_missing_project(self):
        project = self.project()
        self.assertEqual(self.post(project, "approve").status_code, 409)
        self.assertEqual(self.post(project, "retry").status_code, 409)
        self.assertEqual(self.client.get("/api/projects/not-a-project").status_code, 404)


if __name__ == "__main__":
    unittest.main()
