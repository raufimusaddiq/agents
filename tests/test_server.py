from module_patch import patch_shared
"""Isolated HTTP regression tests; no herdr, credentials, or git mutations."""
import http.client
import json
from pathlib import Path
import tempfile
import socket
import struct
import subprocess
from types import SimpleNamespace
from adapters.base import Ask, Option
import threading
import unittest
from unittest.mock import patch
import server


class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        server.load_config()
        cls.httpd = server.http.server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        cls.port = cls.httpd.server_port
        server.config()['port'] = cls.port
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join()

    def request(self, path, body=None, headers=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=3)
        conn.request('POST' if body is not None else 'GET', path, body, headers or {})
        response = conn.getresponse()
        status, data = response.status, response.read()
        conn.close()
        return status, data

    def test_invalid_login_bodies(self):
        for body in ['[]', 'null', '"text"', '1', '{', b'\xff']:
            with self.subTest(body=body):
                status, data = self.request('/api/login', body)
                self.assertEqual(status, 400)
                self.assertIn('error', json.loads(data))

    def test_oversized_body_rejected_before_read(self):
        status, _ = self.request('/api/login', '{}', {'Content-Length': '1000001'})
        self.assertEqual(status, 413)

    def test_login_rejects_foreign_origin(self):
        status, _ = self.request('/api/login', '{}', {'Origin': 'https://evil.example'})
        self.assertEqual(status, 403)

    def test_origin_requires_exact_scheme_and_authority(self):
        host = f'127.0.0.1:{self.port}'
        self.assertTrue(server.origin_allowed(host, f'http://{host}'))
        self.assertFalse(server.origin_allowed(host, 'http://127.0.0.1:1'))
        self.assertFalse(server.origin_allowed(host, f'ftp://{host}'))

    def test_websocket_rejects_unmasked_and_oversized_frames(self):
        for header in [b"\x82\x01", b"\x82\xff" + struct.pack("!Q", 1_000_001)]:
            with self.subTest(header=header):
                left, right = socket.socketpair()
                try:
                    left.settimeout(1)
                    right.sendall(header)
                    with self.assertRaises(ConnectionError):
                        server._ws_recv(left)
                finally:
                    left.close()
                    right.close()

    def test_logout_revokes_and_expires_browser_cookie(self):
        token = server.SESSIONS.create()
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=3)
        conn.request('POST', '/api/logout', headers={'Cookie':f'board_session={token}'})
        response = conn.getresponse()
        self.assertEqual(response.status,200)
        self.assertIn('Max-Age=0', response.getheader('Set-Cookie'))
        self.assertEqual(response.getheader('Cache-Control'),'no-store')
        response.read()
        conn.close()
        self.assertFalse(server.SESSIONS.valid(token))

    def test_invalid_terminal_dimensions_return_json(self):
        with patch.object(server.Handler,'_auth',return_value='user'):
            status,data = self.request('/ws/terminal?pane=w1:p1&cols=bad', headers={'Upgrade':'websocket','Sec-WebSocket-Key':'dGhlIHNhbXBsZSBub25jZQ=='})
        self.assertEqual(status,400)
        self.assertEqual(json.loads(data)['error'],'bad_dimensions')

    def test_invalid_key_values_are_rejected(self):
        with patch.object(server.Handler, '_auth', return_value='user'):
            for keys in [[{}], [[]], [None], [1], 'enter', ['enter'] * 101]:
                status, _ = self.request('/api/keys', json.dumps({'pane':'w1:p1','keys':keys}))
                self.assertEqual(status, 400)

    def test_manual_removal_refuses_a_running_agent_checkout(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(server.Handler,'_auth',return_value='user'), patch.dict(server.STATE.agents,{'w1:p1':{'cwd':str(Path(folder)/'nested')}}), patch_shared(server,'_remove_worktree_by_path') as remove:
                status,_ = self.request('/api/worktree_remove',json.dumps({'path':folder}))
            self.assertEqual(status,409)
            remove.assert_not_called()

    def test_manual_removal_refuses_when_git_cannot_verify(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(server.Handler, '_auth', return_value='user'), \
                 patch_shared(server, 'git', return_value=(1, '')), \
                 patch_shared(server, '_remove_worktree_by_path') as remove:
                status, _ = self.request('/api/worktree_remove', json.dumps({'path':folder}))
                self.assertEqual(status, 409)
                remove.assert_not_called()

    def test_stale_prompt_never_sends_an_answer(self):
        ask = Ask(question='New question', options=[Option(number=1,label='Allow')])
        adapter = SimpleNamespace(supports={'prompts': True}, parse_prompt=lambda _: ask, plan_answer=lambda *_: self.fail('must not plan an answer'))
        with patch.object(server.Handler, '_auth', return_value='user'), \
             patch.object(server.Handler, '_body_screen', return_value=[]), \
             patch_shared(server, 'get_adapter', return_value=adapter), \
             patch.dict(server.STATE.agents, {'w1:p1':{'kind':'test'}}):
            expected = ask.to_json()
            expected['question'] = 'Old question'
            status, data = self.request('/api/answer', json.dumps({'pane':'w1:p1','answer':{'option':1},'expected_prompt':expected}))
            self.assertEqual(status, 409)
            self.assertEqual(json.loads(data)['error'], 'prompt_changed')

    def test_static_traversal_and_symlink(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            base = root / 'web' / 'dist'
            base.mkdir(parents=True)
            (base / 'index.html').write_text('board')
            sibling = root / 'web' / 'dist-private'
            sibling.mkdir()
            (sibling / 'secret.txt').write_text('SECRET')
            (base / 'assets').mkdir()
            (base / 'assets' / 'secret.txt').symlink_to(sibling / 'secret.txt')
            with patch_shared(server, 'ROOT', root):
                for path in ['/assets/../../dist-private/secret.txt', '/assets/secret.txt']:
                    status, data = self.request(path)
                    self.assertEqual(status, 404)
                    self.assertNotIn(b'SECRET', data)
                self.assertEqual(self.request('/')[0], 200)


class GitTests(unittest.TestCase):
    def test_clean_linked_checkout_removes_without_deleting_branch(self):
        with tempfile.TemporaryDirectory() as folder:
            repo = Path(folder) / 'repo'
            checkout = Path(folder) / 'checkout'
            repo.mkdir()
            def git(*args):
                return subprocess.run(['git','-C',str(repo),*args], capture_output=True,text=True,check=True).stdout
            git('init','-b','main')
            git('config','user.email','audit@example.invalid')
            git('config','user.name','Audit')
            (repo / 'file.txt').write_text('base')
            git('add','file.txt')
            git('commit','-m','base')
            git('worktree','add','-b','fixture',str(checkout))
            self.assertEqual(server.worktree_removal_blocker(str(checkout)),'')
            server._remove_worktree_by_path(str(checkout))
            self.assertFalse(checkout.exists())
            self.assertIn('fixture',git('branch','--list','fixture'))

    def test_real_remote_refs_detect_unpushed_commits_without_network(self):
        with tempfile.TemporaryDirectory() as folder:
            def git(*args):
                return subprocess.run(['git','-C',folder,*args],capture_output=True,text=True,check=True).stdout
            git('init','-b','main')
            git('config','user.email','audit@example.invalid')
            git('config','user.name','Audit')
            file = Path(folder) / 'file.txt'
            file.write_text('base')
            git('add','file.txt')
            git('commit','-m','base')
            git('remote','add','origin','https://example.invalid/fixture.git')
            git('update-ref','refs/remotes/origin/main','HEAD')
            self.assertEqual(server.worktree_removal_blocker(folder),'')
            file.write_text('local change')
            git('commit','-am','local')
            self.assertEqual(server.worktree_removal_blocker(folder),'unpushed commits')

    def test_special_paths_and_commit_subjects(self):
        with tempfile.TemporaryDirectory() as folder:
            def git(*args):
                result = subprocess.run(['git', '-C', folder, *args], capture_output=True, text=True, check=True)
                return result.stdout
            git('init')
            git('config', 'user.email', 'audit@example.invalid')
            git('config', 'user.name', 'Audit')
            old = 'old\tfile.txt'
            new = 'new\tfile.txt'
            (Path(folder) / old).write_text('original\n')
            git('add', '--', old)
            git('commit', '-m', 'Keep | literal pipe in subject')
            git('mv', '--', old, new)
            untracked = 'new\nfile.txt'
            (Path(folder) / untracked).write_text('new\n')
            status = server.git_status(folder)
            paths = [file['path'] for file in status['files']]
            self.assertCountEqual(paths, [new, untracked])
            self.assertEqual(status['commits'][0]['subject'], 'Keep | literal pipe in subject')
            self.assertEqual(server.worktree_removal_blocker(folder), 'uncommitted changes')


class SafetyTests(unittest.TestCase):
    def test_terminal_session_closes_when_auth_is_revoked(self):
        left,right = socket.socketpair()
        session = server.TerminalSession(left,'w1:p1',80,24,authorized=lambda:False)
        try:
            with patch.object(session,'_spawn'), patch.object(session,'_pty_to_ws'):
                session.run()
            self.assertTrue(session._stop.is_set())
            self.assertEqual(right.recv(2),b'\x88\x00')
        finally:
            left.close()
            right.close()

    def test_machine_token_storage_is_atomic_and_private(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'secrets.json'
            sessions = server.Sessions()
            sessions.machine_tokens['fixture'] = 'hashed-value'
            with patch_shared(server,'SECRETS_PATH',path), patch_shared(server,'SESSIONS',sessions):
                server._save_machine_tokens()
            self.assertEqual(json.loads(path.read_text()),{'machine_tokens':{'fixture':'hashed-value'}})
            self.assertEqual(path.stat().st_mode & 0o777,0o600)
            self.assertEqual(len(list(Path(folder).iterdir())),1)

    def test_sse_queue_coalesces_changes(self):
        queue = server._Queue()
        for _ in range(10_000):
            queue.put('changed')
        self.assertEqual(len(queue.items), 1)
        self.assertEqual(queue.get(), 'changed')
        self.assertIsNone(queue.get(timeout=0))

    def test_removal_guard(self):
        cases = [
            ([(1, '')], 'cannot verify worktree status'),
            ([(0, ' M file')], 'uncommitted changes'),
            ([(0, ''), (1, '')], 'cannot verify remotes'),
            ([(0, ''), (0, 'origin'), (1, '')], 'cannot verify unpushed commits'),
            ([(0, ''), (0, 'origin'), (0, 'invalid')], 'cannot verify unpushed commits'),
            ([(0, ''), (0, 'origin'), (0, '2')], 'unpushed commits'),
            ([(0, ''), (0, 'origin'), (0, '0')], ''),
            ([(0, ''), (0, '')], ''),
        ]
        for results, expected in cases:
            with self.subTest(results=results), patch_shared(server, 'git', side_effect=results):
                self.assertEqual(server.worktree_removal_blocker('/unused'), expected)

    def test_folder_picker_refuses_sibling_with_same_prefix(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder) / 'home'
            sibling = Path(folder) / 'home-private'
            home.mkdir()
            sibling.mkdir()
            with patch.object(server.os.path, 'expanduser', side_effect=lambda path: str(home) if path == '~' else path):
                self.assertEqual(server.list_folders(str(sibling))['path'], str(home))

    def test_prompt_identity_ignores_cursor_but_tracks_choices(self):
        ask = Ask(question='Allow?', options=[Option(number=1,label='Yes')]).to_json()
        before = server.prompt_identity(ask)
        ask['raw'] = 'different cursor'
        ask['options'][0]['selected'] = True
        self.assertEqual(server.prompt_identity(ask), before)
        ask['options'][0]['label'] = 'No'
        self.assertNotEqual(server.prompt_identity(ask), before)


if __name__ == '__main__':
    unittest.main()
