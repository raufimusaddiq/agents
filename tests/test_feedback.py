from module_patch import patch_shared
"""Transcript streaming and attention detection, using isolated fixture data."""
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import server
from adapters.base import Ask, Event
from adapters.opencode import OpencodeAdapter
from adapters.codex import _parse_codex


class FeedbackTests(unittest.TestCase):
    def setUp(self):
        notifier = patch_shared(server, "notify", return_value=False)
        notifier.__enter__()
        self.addCleanup(notifier.__exit__, None, None, None)

    def test_opencode_streamed_part_updates_without_duplicates_or_skipped_timestamps(self):
        with tempfile.TemporaryDirectory() as folder:
            db = str(Path(folder) / 'fixture.sqlite')
            con = sqlite3.connect(db)
            con.executescript('''
                CREATE TABLE message(id TEXT PRIMARY KEY, data TEXT);
                CREATE TABLE part(id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT,
                                  time_created INTEGER, time_updated INTEGER, data TEXT);
            ''')
            con.execute('INSERT INTO message VALUES (?, ?)', ('m1', json.dumps({'role': 'assistant'})))
            def insert(pid, time, text):
                con.execute('INSERT INTO part VALUES (?, ?, ?, ?, ?, ?)',
                            (pid, 'm1', 's1', time, time, json.dumps({'type': 'text', 'text': text})))
                con.commit()
            insert('p1', 100, 'First')
            state = server.State()
            adapter = OpencodeAdapter()
            with patch('adapters.opencode.DB_PATH', db):
                first = adapter.events('s1', state)
                self.assertEqual([event.text for event in first], ['First'])
                self.assertEqual(adapter.events('s1', state), [])
                # New part at exactly the previous high-water timestamp.
                insert('p2', 100, 'Equal timestamp')
                self.assertEqual([event.text for event in adapter.events('s1', state)], ['Equal timestamp'])
                insert('p3', 101, 'Next millisecond')
                self.assertEqual([event.text for event in adapter.events('s1', state)], ['Next millisecond'])
                # Streaming updates the existing record, not its creation time.
                con.execute('UPDATE part SET time_updated=?, data=? WHERE id=?',
                            (102, json.dumps({'type': 'text', 'text': 'First completed'}), 'p1'))
                con.commit()
                revised = adapter.events('s1', state)
                self.assertEqual([event.text for event in revised], ['First completed'])
                self.assertEqual(revised[0].event_id, first[0].event_id)
                self.assertEqual(adapter.events('s1', state), [])
            con.close()

    def test_polling_replaces_streamed_message_in_place(self):
        state = server.State()
        state.agents['w1:p1'] = {'pane': 'w1:p1', 'kind': 'test', 'session_id': 's1'}
        batches = [[Event(kind='reply', text='Draft', event_id='p1')],
                   [Event(kind='reply', text='Complete', event_id='p1')]]
        class Adapter:
            def events(self, *_):
                return batches.pop(0)
        with patch_shared(server, 'STATE', state), patch_shared(server, 'get_adapter', return_value=Adapter()):
            server.poll_transcripts()
            server.poll_transcripts()
        self.assertEqual(len(state.events['w1:p1']), 1)
        self.assertEqual(state.events['w1:p1'][0]['text'], 'Complete')

    def test_every_harness_gets_raw_attention_fallback(self):
        state = server.State()
        state.agents['w1:p1'] = {'pane': 'w1:p1', 'kind': 'opencode', 'agent_status': 'idle'}
        with patch_shared(server, 'STATE', state), patch_shared(server, 'read_screen', return_value=['Sign in: enter the code']):
            server.read_screens()
        self.assertTrue(state.agents['w1:p1']['needs_user'])
        self.assertEqual(state.agents['w1:p1']['ask']['kind'], 'raw')
        with patch_shared(server, 'STATE', state), patch_shared(server, 'read_screen', return_value=['Ready']):
            server.read_screens()
        self.assertFalse(state.agents['w1:p1']['needs_user'])
        self.assertIsNone(state.agents['w1:p1']['ask'])

    def test_codex_input_does_not_flag_historical_login_text(self):
        screen = ["Verified login and press enter handling", "", "› Ask Codex to do anything", "GPT-6.1-Sol low", "← for agents · ? for shortcuts"]
        self.assertFalse(server._screen_needs_user(screen, "working", "codex"))
        self.assertTrue(server._screen_needs_user(["Sign in: enter the code"], "idle", "codex"))

    def test_permission_prompt_needs_attention(self):
        state = server.State()
        state.agents['w1:p1'] = {'pane': 'w1:p1', 'kind': 'test', 'agent_status': 'working'}
        class Adapter:
            supports = {'prompts': True}
            def session_status(self, _):
                return {}
            def parse_prompt(self, _):
                return Ask(kind='permission', question='Allow this command?')
        with patch_shared(server, 'STATE', state), patch_shared(server, 'get_adapter', return_value=Adapter()), patch_shared(server, 'read_screen', return_value=[]):
            server.read_screens()
        self.assertTrue(state.agents['w1:p1']['needs_user'])

    def test_feedback_ignores_poll_time_and_tracks_revisions(self):
        state = server.State()
        state.agents['w1:p1'] = {'pane': 'w1:p1', 'last_seen': 1}
        with patch_shared(server, 'STATE', state):
            before = server.feedback_signature()
            state.agents['w1:p1']['last_seen'] = 2
            self.assertEqual(server.feedback_signature(), before)
            state.feedback_revision += 1
            self.assertNotEqual(server.feedback_signature(), before)


class NotificationTests(unittest.TestCase):
    def test_connected_page_counts_as_open(self):
        state = server.State()
        state.subscribe()
        with patch_shared(server,'STATE',state), patch_shared(server,'_last_page_seen',0):
            self.assertTrue(server.page_recently_open())

    def test_suppressed_page_notification_does_not_consume_webhook_limit(self):
        state = server.State()
        with patch_shared(server,'STATE',state), patch_shared(server,'page_recently_open',return_value=True), patch_shared(server,'_send_webhook') as send:
            self.assertFalse(server.notify('title','body','w1:p1'))
        self.assertNotIn('w1:p1',state.notified)
        send.assert_not_called()

    def test_waiting_agent_pushes_once_without_transcript_content(self):
        state = server.State()
        state.agents['w1:p1'] = {'pane':'w1:p1','kind':'opencode','agent_status':'blocked','name':'fixture'}
        with patch_shared(server,'STATE',state), patch_shared(server,'read_screen',return_value=['private login code']), patch_shared(server,'notify',return_value=True) as send:
            server.read_screens()
            server.read_screens()
        self.assertEqual(send.call_count,1)
        self.assertNotIn('private login code',str(send.call_args))


class CodexFeedbackTests(unittest.TestCase):
    def test_live_custom_exec_format_extracts_commands(self):
        code = 'text(await tools.exec_command({cmd:"python3 -m unittest discover",max_output_tokens:1000}));'
        events = _parse_codex({'type':'response_item','payload':{'type':'custom_tool_call','name':'exec','input':code}})
        self.assertEqual([(event.kind,event.command) for event in events], [('bash','python3 -m unittest discover')])

    def test_custom_exec_does_not_invent_commands_from_quoted_examples(self):
        code = 'text("Example: exec_command({cmd: \'git push\'})"); // exec_command({cmd:"git commit"})'
        events = _parse_codex({'type':'response_item','payload':{'type':'custom_tool_call','name':'exec','input':code}})
        self.assertEqual([event.kind for event in events],['tool'])
        self.assertFalse(any(event.command for event in events))

    def test_raw_patch_records_changed_files(self):
        patch_text = '*** Begin Patch\n*** Update File: web/src/main.tsx\n@@\n-old\n+new\n*** End Patch'
        events = _parse_codex({'type':'response_item','payload':{'type':'custom_tool_call','name':'apply_patch','input':patch_text}})
        self.assertEqual([(event.kind,event.path) for event in events],[('edit','web/src/main.tsx')])


class WorkflowTests(unittest.TestCase):
    def test_stage_and_alerts_track_test_commands_between_commits(self):
        state = server.State()
        state.agents['w1:p1'] = {'pane':'w1:p1'}
        events = [
            {'kind':'edit','path':'first.py'},
            {'kind':'bash','command':'python3 -m unittest discover'},
            {'kind':'bash','command':'git commit -m first','ts':'first'},
            {'kind':'edit','path':'second.py'},
            {'kind':'bash','command':'git commit -m second','ts':'second'},
        ]
        state.events['w1:p1'] = events
        with patch_shared(server,'STATE',state):
            server._detect_alerts()
        self.assertEqual([alert['key'] for alert in state.alerts], ['w1:p1:second:code_no_test'])
        stages = server.compute_stations({},events[:2],None)
        self.assertIn(('Tests','done'),stages['stations'])

    def test_docs_only_commit_does_not_claim_code_changed(self):
        state = server.State()
        state.agents['w1:p1'] = {'pane':'w1:p1'}
        state.events['w1:p1'] = [{'kind':'edit','path':'README.md'}, {'kind':'bash','command':'git add . && git commit -m docs'}]
        with patch_shared(server,'STATE',state):
            server._detect_alerts()
        self.assertEqual(state.alerts,[])

    def test_quoted_and_heredoc_source_is_not_git_execution(self):
        self.assertEqual(server.classify_git('echo "git push origin main"'),[])
        self.assertEqual(server.classify_git("python3 - <<'PY'\nprint('git commit -am test')\nPY"),[])
        self.assertFalse(server.has_test_action([{'kind':'bash','command':'echo "npm test"'}]))
        self.assertIn('commit',server.classify_git('git status\ngit commit -m actual'))

    def test_stage_all_survives_amend_and_includes_dot(self):
        self.assertIn('stage_all',server.classify_git('git add .'))
        self.assertIn('stage_all',server.classify_git('git add -A && git commit --amend'))
        self.assertNotIn('stage_all',server.classify_git('git commit --amend'))


if __name__ == '__main__':
    unittest.main()
