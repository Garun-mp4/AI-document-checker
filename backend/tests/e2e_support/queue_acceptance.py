"""Destructive restart tests against synthetic E2E data only; never the user's stack."""
from __future__ import annotations

import concurrent.futures
import os
import subprocess
import time
import unittest
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[3]
COMPOSE = ['docker', 'compose', '-p', 'document-checker-e2e', '-f', str(ROOT / 'compose.e2e.yml')]
BASE = 'http://127.0.0.1:5174/api/v1'


def compose(*args):
    return subprocess.run(COMPOSE + list(args), cwd=ROOT, check=True, capture_output=True, text=True,
                          encoding='utf-8', errors='replace', timeout=90).stdout


class QueueAcceptance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = httpx.Client(base_url=BASE, trust_env=False, timeout=40)
        cls.client.post('/__e2e/provider', json={}).raise_for_status()
        # Run admission races with no other consumer claiming the controlled rows.
        compose('stop', 'worker')
        try:
            print(compose('exec', '-T', '-e', 'E2E_AUDIT_PROJECT=document-checker-e2e',
                          'api', 'python', '/test_support/queue_audit.py'), flush=True)
        finally:
            compose('start', 'worker')

    @classmethod
    def tearDownClass(cls):
        cls.client.post('/__e2e/provider', json={})
        cls.client.close()

    def setUp(self):
        self.ids = []
        self.client.post('/__e2e/provider', json={}).raise_for_status()

    def tearDown(self):
        self.client.post('/__e2e/provider', json={}).raise_for_status()
        compose('start', 'worker')
        for id in self.ids:
            self.client.delete(f'/documents/{id}').raise_for_status()

    def control(self, **options):
        self.client.post('/__e2e/provider', json=options).raise_for_status()

    def upload(self, name='m03.txt', content=None):
        if content is None:
            content = 'Цель — проверить очередь. Ответственный — Иван Пример. Срок — 2026 год.'.encode()
        response = self.client.post('/documents', files={'file': (name, content)})
        response.raise_for_status()
        id = response.json()['id']
        self.ids.append(id)
        return id

    def job(self, id):
        response = self.client.get(f'/documents/{id}/jobs')
        response.raise_for_status()
        return response.json()[0]

    def wait(self, predicate, seconds=150):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            result = predicate()
            if result:
                return result
            time.sleep(.2)
        self.fail('Timed out waiting for queue acceptance condition')

    def ready(self, id):
        def complete():
            job = self.job(id)
            if job['state'] == 'failed':
                self.fail(str(job))
            return job if job['state'] == 'succeeded' else None
        self.wait(complete)

    def test_01_concurrent_submissions_cancel_and_retry(self):
        self.control(hold_stage='extracting')
        id = self.upload()
        self.wait(lambda: self.job(id)['stage'] == 'extracting')
        active = self.job(id)
        self.assertIsNotNone(active['queued_at'])
        self.assertIsNotNone(active['started_at'])
        self.assertIsNotNone(active['stage_started_at'])
        self.assertGreaterEqual(active['queue_wait_seconds'], 0)
        self.assertGreaterEqual(active['stage_elapsed_seconds'], 0)
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            responses = list(pool.map(lambda _: self.client.post(f'/documents/{id}/retry'), range(8)))
        self.assertTrue(all(r.status_code == 202 for r in responses))
        self.assertEqual(len(self.client.get(f'/documents/{id}/jobs').json()), 1)
        old = self.job(id)
        self.client.post(f'/documents/{id}/cancel').raise_for_status()
        self.wait(lambda: self.job(id)['state'] == 'cancelled')
        self.control()
        self.client.post(f'/documents/{id}/retry').raise_for_status()
        self.ready(id)
        self.assertNotEqual(old['id'], self.job(id)['id'])
        self.assertEqual(self.client.get(f'/documents/{id}/file').content,
                         'Цель — проверить очередь. Ответственный — Иван Пример. Срок — 2026 год.'.encode())

    def test_01b_queued_jobs_report_stable_positions_and_wait_time(self):
        compose('stop', 'worker')
        first = self.upload()
        second = self.upload()
        first_job = self.job(first)
        second_job = self.job(second)
        self.assertEqual((first_job['state'], first_job['queue_position']), ('queued', 1))
        self.assertEqual((second_job['state'], second_job['queue_position']), ('queued', 2))
        self.assertIsNone(first_job['started_at'])
        self.assertIsNone(second_job['started_at'])
        self.assertGreaterEqual(first_job['queue_wait_seconds'], 0)
        self.assertGreaterEqual(second_job['queue_wait_seconds'], 0)
        compose('start', 'worker')
        self.ready(first)
        self.ready(second)

    def test_02_restart_partial_index_preserves_previous_version_chat_and_citations(self):
        id = self.upload()
        self.ready(id)
        document = self.client.get(f'/documents/{id}').json()
        insights = self.client.get(f'/documents/{id}/insights').json()
        chat = self.client.get(f'/documents/{id}/chat').json()
        reply = self.client.post(f"/chats/{chat['id']}/messages", json={'text': 'Кратко перескажи документ'})
        reply.raise_for_status()
        history = self.client.get(f"/chats/{chat['id']}/messages").json()
        self.control(hold_stage='indexing_checkpoint')
        self.client.post(f'/documents/{id}/markdown/rebuild').raise_for_status()
        self.wait(lambda: self.job(id)['stage'] == 'indexing_checkpoint')
        job = self.job(id)
        self.assertGreater(job['progress']['completed'], 0)
        self.assertEqual(self.client.get(f'/documents/{id}/insights').json(), insights)
        compose('kill', '-s', 'SIGKILL', 'worker')
        self.control()
        compose('start', 'worker')
        self.ready(id)
        current = self.client.get(f'/documents/{id}').json()
        self.assertGreater(current['active_version'], document['active_version'])
        self.assertEqual(self.job(id)['attempts'], 2)
        self.assertEqual(self.client.get(f"/chats/{chat['id']}/messages").json(), history)
        self.assertEqual(len(self.client.get(f'/documents/{id}/insights').json()), 7)
        chunks = self.client.get(f'/documents/{id}/chunks?limit=200').json()
        self.assertEqual(len(chunks), current['chunk_count'])
        self.assertEqual(len({c['ordinal'] for c in chunks}), len(chunks))
        self.assertTrue(all(c['locator']['processing_version'] == current['active_version'] for c in chunks))
        code = (
            "from pathlib import Path; "
            f"files=list(Path('/data/uploads').glob('{id}.v{job['version']}.*')); "
            "assert len(files)==2, files; assert not any('embedding.json' in p.name for p in files)"
        )
        compose('exec', '-T', 'api', 'python', '-c', code)

    def test_03_restart_ocr_preserves_original(self):
        content = (ROOT / 'frontend/e2e/fixtures/scan.pdf').read_bytes()
        self.control(hold_stage='ocr')
        id = self.upload('scan.pdf', content)
        self.wait(lambda: self.job(id)['stage'] == 'ocr')
        compose('kill', '-s', 'SIGKILL', 'worker')
        self.control()
        compose('start', 'worker')
        self.ready(id)
        self.assertEqual(self.job(id)['attempts'], 2)
        document = self.client.get(f'/documents/{id}').json()
        self.assertEqual(document['ocr_status'], 'ready')
        self.assertEqual(self.client.get(f'/documents/{id}/file').content, content)

    def test_04_interrupted_cloud_request_requires_explicit_analysis_retry(self):
        id = self.upload()
        self.ready(id)
        old = self.client.get(f'/documents/{id}/insights').json()
        original_chunks = self.client.get(f'/documents/{id}/chunks').json()
        calls = self.client.get('/__e2e/worker-control').json()['complete_calls']
        self.control(hold_complete=True)
        self.client.post(f'/documents/{id}/retry?operation=analysis').raise_for_status()
        self.wait(lambda: self.client.get('/__e2e/worker-control').json()['complete_calls'] == calls + 1)
        self.assertEqual(self.job(id)['stage'], 'analysis_request')
        compose('kill', '-s', 'SIGKILL', 'worker')
        self.control()
        compose('start', 'worker')
        self.wait(lambda: self.job(id)['state'] == 'failed')
        failed = self.job(id)
        self.assertEqual(failed['error_code'], 'analysis_interrupted')
        self.assertEqual(failed['attempts'], 1)
        self.assertEqual(self.client.get('/__e2e/worker-control').json()['complete_calls'], calls + 1)
        self.assertEqual(self.client.get(f'/documents/{id}/insights').json(), old)
        self.assertEqual(self.client.get(f'/documents/{id}/chunks').json(), original_chunks)
        self.client.post(f'/documents/{id}/retry?operation=analysis').raise_for_status()
        self.ready(id)
        self.assertEqual(self.client.get(f'/documents/{id}/chunks').json(), original_chunks)
        self.assertEqual(self.client.get('/__e2e/worker-control').json()['complete_calls'], calls + 2)

    def test_05_delete_running_job_cannot_resurrect_document(self):
        self.control(hold_stage='indexing_checkpoint')
        id = self.upload()
        self.wait(lambda: self.job(id)['stage'] == 'indexing_checkpoint')
        self.client.delete(f'/documents/{id}').raise_for_status()
        self.ids.remove(id)
        self.control()
        compose('restart', 'worker')
        time.sleep(7)
        self.assertEqual(self.client.get(f'/documents/{id}').status_code, 404)
        self.assertEqual(self.client.get(f'/documents/{id}/jobs').status_code, 404)
        code = f"from pathlib import Path; assert not any('{id}' in p.name for p in Path('/data/uploads').iterdir())"
        compose('exec', '-T', 'api', 'python', '-c', code)

    def test_06_failed_replacement_and_cancel_keep_ready_answers(self):
        id = self.upload()
        self.ready(id)
        current = self.client.get(f'/documents/{id}').json()
        old = self.client.get(f'/documents/{id}/insights').json()
        self.control(mode='unavailable')
        self.client.post(f'/documents/{id}/markdown/rebuild').raise_for_status()
        self.wait(lambda: self.job(id)['state'] == 'failed')
        self.assertEqual(self.job(id)['error_code'], 'model_unavailable')
        self.assertEqual(self.client.get(f'/documents/{id}/insights').json(), old)
        self.assertEqual(self.client.get(f'/documents/{id}').json()['active_version'], current['active_version'])
        self.control(hold_stage='extracting')
        self.client.post(f'/documents/{id}/retry?operation=process').raise_for_status()
        self.wait(lambda: self.job(id)['stage'] == 'extracting')
        self.client.post(f'/documents/{id}/cancel').raise_for_status()
        self.wait(lambda: self.job(id)['state'] == 'cancelled')
        self.assertEqual(self.client.get(f'/documents/{id}/insights').json(), old)
        self.assertEqual(self.client.get(f'/documents/{id}').json()['status'], 'ready')

    def test_07_cancel_releases_real_ocr_child_processes_and_temporary_files(self):
        import io

        from pypdf import PdfReader, PdfWriter
        original = PdfReader(ROOT / 'frontend/e2e/fixtures/scan.pdf')
        output = io.BytesIO()
        writer = PdfWriter()
        for _ in range(12):
            writer.add_page(original.pages[0])
        writer.write(output)
        id = self.upload('cancel-scan.pdf', output.getvalue())
        def children():
            code = ("import os,json; from pathlib import Path; "
                    "print(json.dumps([int(p.name) for p in Path('/proc').iterdir() "
                    "if p.name.isdigit() and int(p.name)!=os.getpid() and (p/'comm').exists() "
                    "and (p/'comm').read_text().strip() in ('tesseract','pdftoppm')]))")
            import json
            return json.loads(compose('exec', '-T', 'worker', 'python', '-c', code))
        self.wait(children, seconds=45)
        self.client.post(f'/documents/{id}/cancel').raise_for_status()
        self.wait(lambda: self.job(id)['state'] == 'cancelled', seconds=10)
        self.assertEqual(children(), [])
        compose('exec', '-T', 'worker', 'python', '-c',
                "from pathlib import Path; assert not [p for p in Path('/tmp').glob('document-worker-*') if p.is_dir()]")
        self.assertEqual(self.client.get(f'/documents/{id}/file').content, output.getvalue())

    def test_08_cancelled_checkpoint_releases_only_replacement_artifacts(self):
        id = self.upload()
        self.ready(id)
        old = self.client.get(f'/documents/{id}').json()
        original_chunks = self.client.get(f'/documents/{id}/chunks').json()
        self.control(hold_stage='indexing_checkpoint')
        self.client.post(f'/documents/{id}/markdown/rebuild').raise_for_status()
        self.wait(lambda: self.job(id)['stage'] == 'indexing_checkpoint')
        version = self.job(id)['version']
        self.client.post(f'/documents/{id}/cancel').raise_for_status()
        self.wait(lambda: self.job(id)['state'] == 'cancelled')
        self.assertEqual(self.client.get(f'/documents/{id}').json()['active_version'], old['active_version'])
        self.assertEqual(self.client.get(f'/documents/{id}/chunks').json(), original_chunks)
        self.assertEqual(self.client.get(f'/documents/{id}/markdown/download').status_code, 200)
        compose('exec', '-T', 'api', 'python', '-c',
                f"from pathlib import Path; assert not list(Path('/data/uploads').glob('{id}.v{version}.*'))")


if __name__ == '__main__':
    assert os.environ.get('E2E_AUDIT_PROJECT') == 'document-checker-e2e', 'Synthetic project authorization required'
    unittest.main(verbosity=2)
