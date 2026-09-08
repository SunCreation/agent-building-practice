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

    async def test_quiet_process_reports_wait_without_claiming_success(self):
        with tempfile.TemporaryDirectory() as folder:
            messages=[]
            async def progress(message): messages.append(message)
            with patch.object(engines,'HEARTBEAT_SECONDS',0.03):
                await engines._run([sys.executable,'-c','import time; time.sleep(.15)'],Path(folder),'test',progress)
            self.assertTrue(any('응답을 기다리는 중' in m for m in messages))

    def test_private_content_is_not_displayed(self):
        mapper=engines._ProgressEvents()
        self.assertEqual(mapper.messages({'type':'assistant','message':{'content':[{'type':'thinking','thinking':'private'},{'type':'text','text':'raw response'}]}}),[])
        event={'type':'assistant','message':{'content':[{'type':'tool_use','id':'a','name':'WebSearch','input':{'query':'Bearer private-credential'}}]}}
        self.assertNotIn('private-credential',' '.join(mapper.messages(event)))
        self.assertNotIn('token=',engines._safe_url('https://example.org/page?token=private'))
