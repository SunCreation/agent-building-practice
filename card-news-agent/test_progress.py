"""Streaming transport checks using a real local child process, no model calls."""
import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import engines

class ProgressTests(unittest.IsolatedAsyncioTestCase):
    async def test_event_arrives_before_child_exits(self):
        with tempfile.TemporaryDirectory() as folder:
            seen = asyncio.Event()
            messages = []
            async def progress(message):
                messages.append(message)
                if '웹 검색 시작' in message:
                    seen.set()
            payload = {'type':'assistant','message':{'content':[{'type':'tool_use','id':'search1','name':'WebSearch','input':{'query':'card news design'}}]}}
            code = 'import time; print('+repr(json.dumps(payload))+',flush=True); time.sleep(0.4)'
            task = asyncio.create_task(engines._run([sys.executable,'-c',code,'stream-json'],Path(folder),'test',progress))
            await asyncio.wait_for(seen.wait(), 2)
            self.assertFalse(task.done())
            await task
            self.assertTrue(any('card news design' in m for m in messages))

    async def test_quiet_process_does_not_invent_activity(self):
        with tempfile.TemporaryDirectory() as folder:
            messages=[]
            async def progress(message): messages.append(message)
            with patch.object(engines,'HEARTBEAT_SECONDS',0.03):
                await engines._run([sys.executable,'-c','import time; time.sleep(.15)'],Path(folder),'test',progress)
            self.assertEqual(messages, ['AI 작업 프로세스를 시작했습니다.'])

    def test_private_content_is_not_displayed(self):
        mapper=engines._ProgressEvents()
        self.assertEqual(mapper.messages({'type':'assistant','message':{'content':[{'type':'thinking','thinking':'private'}]}}),[])
        event={'type':'assistant','message':{'content':[{'type':'tool_use','id':'a','name':'WebSearch','input':{'query':'Bearer private-credential'}}]}}
        self.assertNotIn('private-credential',' '.join(mapper.messages(event)))
        self.assertNotIn('token=',engines._safe_url('https://example.org/page?token=private'))

    def test_partial_public_json_is_summarized_and_thinking_excluded(self):
        mapper=engines._ProgressEvents()
        def delta(kind,text):
            return mapper.messages({'type':'stream_event','event':{'type':'content_block_delta','delta':{'type':kind,'text':text,'thinking':text}}})
        self.assertEqual(delta('thinking_delta','private reasoning'),[])
        self.assertEqual(delta('text_delta','{"headline": "주방에서'),[])
        self.assertEqual(delta('text_delta',' 확인할 세 가지"}'),['카드 제목 · 주방에서 확인할 세 가지'])
        self.assertEqual(mapper.text_summary(force=True),[])

    def test_public_prose_and_secret_redaction(self):
        mapper=engines._ProgressEvents()
        result=mapper.messages({'type':'assistant','message':{'content':[{'type':'text','text':'후보 두 개의 차이를 정리하고 있습니다.'}]}})
        self.assertEqual(result,['작성 내용 · 후보 두 개의 차이를 정리하고 있습니다.'])
        mapper.public_text='Bearer confidential-token'
        self.assertEqual(mapper.text_summary(force=True),[])
