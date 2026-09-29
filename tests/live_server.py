"""Candidate server using live herdr reads and temporary board state.

For audit use only. Does not replace the installed service or change its config.
"""
import argparse
from pathlib import Path
import sys
import tempfile
import threading
import urllib.parse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server

parser = argparse.ArgumentParser()
parser.add_argument('--port', type=int, default=8793)
parser.add_argument('--dist', type=Path, default=server.ROOT / 'web' / 'dist')
args = parser.parse_args()
if not (args.dist / 'index.html').is_file():
    parser.error('build the frontend first, or supply --dist')
server.load_config()
server.config()['port'] = args.port
server.config()['auth']['password_hash'] = server.hash_password('board-test-pw')
server.config()['notifications']['webhook'] = {'kind': 'none'}
server.config()['remote'] = {'mode': 'none', 'hostnames': [], 'company_managed': False}
with tempfile.TemporaryDirectory(prefix='agent-board-live-audit-') as folder:
    root = Path(folder)
    (root / 'web').mkdir()
    (root / 'web' / 'dist').symlink_to(args.dist.resolve(), target_is_directory=True)
    server.ROOT = root
    server.ROSTER_PATH = root / 'roster.json'
    server.AUDIT_PATH = root / 'audit.jsonl'
    server.SECRETS_PATH = root / 'secrets.json'
    server.STATE_DIR = root / 'state'
    server.STATE_DIR.mkdir()
    threading.Thread(target=server.poll_loop, daemon=True).start()
    original_build_board = server.build_board
    def read_only_board():
        return {**original_build_board(), "read_only": True}
    server.build_board = read_only_board
    class ReadOnlyHandler(server.Handler):
        def _dispatch_post(self):
            if urllib.parse.urlparse(self.path).path not in {'/api/login','/api/logout','/api/diff','/api/menu'}:
                self._json(403, {"error":"read_only_preview"})
                return
            super()._dispatch_post()
        def do_GET(self):
            if urllib.parse.urlparse(self.path).path == '/ws/terminal':
                self._json(403, {"error":"read_only_preview"})
                return
            super().do_GET()
    httpd = server.http.server.ThreadingHTTPServer(('127.0.0.1', args.port), ReadOnlyHandler)
    print(f'Candidate board: http://127.0.0.1:{args.port}', flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
