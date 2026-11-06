"""Exact shared frontend rules in a persistent, bounded local Node worker."""
from __future__ import annotations
import atexit
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import threading
from datetime import datetime

LEVEL_RULES = ('level1', 'level2', 'level3')

class LevelEvaluator:
    def __init__(self):
        self.lock = threading.Lock()
        self.process = None
        self.answers = queue.Queue()
        self.serial = 0
        self.contexts = {}
        self.status = {'state': 'warming', 'reason': 'Awaiting two valid scanner publications'}
        atexit.register(self.close)

    def close(self):
        child, self.process = self.process, None
        if child is not None:
            try:
                child.terminate()
                child.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                try: child.kill()
                except OSError: pass

    def _start(self):
        node = shutil.which('node')
        if not node:
            raise RuntimeError('Node runtime unavailable; level entries paused')
        answers = self.answers = queue.Queue()
        child = subprocess.Popen([node, str(Path(__file__).resolve().parents[1] / 'scripts' / 'bot_confirmation_worker.mjs')],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding='utf-8', bufsize=1,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.process = child
        def read():
            try:
                for line in child.stdout:
                    try: answers.put(json.loads(line))
                    except ValueError: continue
            finally: answers.put({'error': 'Level worker stopped'})
        threading.Thread(target=read, name='bot-level-reader', daemon=True).start()

    def evaluate(self, board, payload, now, bear=False):
        self.contexts = {}
        if os.environ.get('AGX_BOT_LEVEL_STRATEGIES', '1') == '0':
            self.status = {'state': 'disabled', 'reason': 'Level strategies disabled'}
            return {}
        publication = payload.get('scanCompletedAt') or payload.get('generatedAt')
        if not publication:
            return {}
        try:
            published = datetime.fromisoformat(str(publication).replace('Z', '+00:00'))
            if published.tzinfo is None or not 0 <= (now-published).total_seconds() <= 120:
                return {}
        except (ValueError, TypeError):
            return {}
        with self.lock:
            try:
                if self.process is None or self.process.poll() is not None: self._start()
                self.serial += 1
                request = {'id': self.serial, 'board': board, 'now': now.isoformat(),
                           'publicationAt': publication, 'rows': [{**r, 'direction': 'bear'} if bear and isinstance(r, dict) else r
                                                                   for r in list(payload.get('rows') or []) + list(payload.get('rest') or [])]}
                self.process.stdin.write(json.dumps(request, allow_nan=False) + '\n')
                self.process.stdin.flush()
                answer = self.answers.get(timeout=5)
                if answer.get('id') != self.serial or answer.get('error'):
                    raise RuntimeError('Level worker did not return a valid response')
                self.status = {'state': 'ready', 'publicationAt': publication,
                               'reason': 'Exact scanner levels and 23 Setup types; MOM optional'}
                self.contexts = answer.get('contexts', {})
                return answer['signals']
            except Exception as exc:
                self.close()
                self.status = {'state': 'unavailable', 'reason': type(exc).__name__ + ': level entries paused; existing rules continue'}
                return {}
