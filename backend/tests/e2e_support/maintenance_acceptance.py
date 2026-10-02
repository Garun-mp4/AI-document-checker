"""End-to-end acceptance for maintenance against the isolated synthetic stack."""
from __future__ import annotations

import io
import os
import re
import subprocess
import time
import unittest
import zipfile
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[3]
E2E_PROJECT = os.environ.get("E2E_COMPOSE_PROJECT", "")
E2E_COMPOSE_FILE = os.environ.get("E2E_COMPOSE_FILE", str(ROOT / "compose.e2e.yml"))
E2E_BASE_URL = os.environ.get("AI_CHECKER_BASE_URL", "")
BASE = E2E_BASE_URL.rstrip("/") + "/api/v1"
COMPOSE = ["docker", "compose", "-p", E2E_PROJECT, "-f", E2E_COMPOSE_FILE]


class LocalDataMaintenanceAcceptance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not re.fullmatch(r"document-checker-e2e-[0-9a-f]{8}", E2E_PROJECT):
            raise RuntimeError("M15 acceptance requires a unique isolated E2E Compose project")
        if not re.fullmatch(r"https?://(?:127\.0\.0\.1|localhost):5175", E2E_BASE_URL):
            raise RuntimeError("M15 acceptance requires the dedicated local test port 5175")
        cls.client = httpx.Client(base_url=BASE, trust_env=False, timeout=60)
        cls.client.post("/__e2e/provider", json={"mode": "ready"}).raise_for_status()

    @classmethod
    def tearDownClass(cls):
        cls.client.close()

    def setUp(self):
        self.document_ids: list[str] = []

    def tearDown(self):
        for document_id in self.document_ids:
            try:
                self.client.delete(f"/documents/{document_id}")
            except httpx.HTTPError:
                pass

    def _wait_ready(self, document_id: str, timeout: float = 150) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            response = self.client.get(f"/documents/{document_id}")
            if response.status_code == 404:
                self.fail("Document disappeared while processing")
            response.raise_for_status()
            document = response.json()
            if document["status"] == "ready":
                return document
            if document["status"] == "failed":
                self.fail(f"Synthetic document processing failed: {document.get('error_message')}")
            time.sleep(0.25)
        self.fail("Timed out waiting for synthetic document processing")

    def _wait_new_job(self, document_id: str, previous_ids: set[str], timeout: float = 150) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            response = self.client.get(f"/documents/{document_id}/jobs")
            response.raise_for_status()
            new_jobs = [job for job in response.json() if job["id"] not in previous_ids]
            if new_jobs:
                job = new_jobs[0]
                if job["state"] == "failed":
                    self.fail(f"Synthetic processing job failed: {job}")
                if job["state"] == "succeeded":
                    return job
            time.sleep(0.25)
        self.fail("Timed out waiting for the new synthetic processing job")

    def _upload(self, filename: str, marker: str) -> str:
        content = f"M15 acceptance fixture. Marker: {marker}. Цель: проверить сохранность данных.\n".encode()
        response = self.client.post("/documents", files={"file": (filename, content, "text/plain")})
        response.raise_for_status()
        document_id = response.json()["id"]
        self.document_ids.append(document_id)
        self._wait_ready(document_id)
        return document_id

    def _send_chat_message(self, document_id: str, marker: str) -> str:
        chat_response = self.client.get(f"/documents/{document_id}/chat")
        chat_response.raise_for_status()
        chat_id = chat_response.json()["id"]
        response = self.client.post(f"/chats/{chat_id}/messages", json={"text": marker})
        response.raise_for_status()
        history = self.client.get(f"/chats/{chat_id}/messages")
        history.raise_for_status()
        self.assertTrue(any(item["role"] == "user" and item["content"] == marker for item in history.json()))
        return chat_id

    def _plan(self, action: str, document_ids: list[str] | None = None) -> dict:
        response = self.client.post("/maintenance/plans", json={
            "action": action,
            "document_ids": document_ids or [],
        })
        response.raise_for_status()
        return response.json()

    def _execute(self, plan: dict, confirmation: str | None = None) -> httpx.Response:
        return self.client.post("/maintenance/execute", json={
            "plan_id": plan["plan_id"],
            "confirmation": confirmation if confirmation is not None else plan["confirmation_phrase"],
        })

    def _compose(self, *args: str) -> str:
        result = subprocess.run(COMPOSE + list(args), cwd=ROOT, check=True, capture_output=True,
                                text=True, encoding="utf-8", errors="replace", timeout=90)
        return result.stdout

    def test_maintenance_preserves_user_data_and_diagnostics_are_content_free(self):
        seed = self.client.post("/__e2e/maintenance/seed")
        seed.raise_for_status()
        fixture_id = seed.json()["fixture_id"]
        auth_token = seed.json()["auth_token"]
        document_marker = f"M15_DOCUMENT_PRIVATE_SENTINEL_{fixture_id}"
        chat_marker = f"M15_CHAT_PRIVATE_SENTINEL_{fixture_id}"
        filename_marker = f"m15-private-filename-{fixture_id}.txt"
        document_id = self._upload(filename_marker, document_marker)
        chat_id = self._send_chat_message(document_id, chat_marker)

        diagnostics = self.client.get("/maintenance/diagnostics")
        diagnostics.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(diagnostics.content)) as archive:
            self.assertEqual(archive.namelist(), ["diagnostics.json"])
            diagnostic_bytes = archive.read("diagnostics.json")
        for private_value in (auth_token, document_marker, chat_marker, filename_marker):
            self.assertNotIn(private_value.encode(), diagnostic_bytes)
        self.assertIn(b"schema_version", diagnostic_bytes)
        self.assertIn(b"worker_container_healthcheck", diagnostic_bytes)

        temp_plan = self._plan("clear_temp")
        self.assertGreaterEqual(temp_plan["items"], 1)
        self.assertGreater(temp_plan["bytes"], 0)
        self.assertIn("atomic_write", temp_plan["contents"])
        temp_done = self._execute(temp_plan)
        temp_done.raise_for_status()
        self.assertEqual(temp_done.json()["outcome"], "complete")
        artifacts = self.client.get(f"/__e2e/maintenance/verify/{fixture_id}")
        artifacts.raise_for_status()
        self.assertTrue(artifacts.json()["temporary_removed"])
        self.assertEqual(self.client.get(f"/documents/{document_id}/file").content,
                         f"M15 acceptance fixture. Marker: {document_marker}. Цель: проверить сохранность данных.\n".encode())

        cache_plan = self._plan("clear_cache")
        self.assertGreater(cache_plan["items"], 0)
        self.assertIn("parser_cache_entries", cache_plan["contents"])
        self.assertIn("embedding_cache_entries", cache_plan["contents"])
        self.assertFalse(cache_plan["model_redownload_required"])
        self.assertIn("остаются на месте", cache_plan["scope"])
        cache_done = self._execute(cache_plan)
        cache_done.raise_for_status()
        self.assertEqual(cache_done.json()["outcome"], "complete")
        artifacts = self.client.get(f"/__e2e/maintenance/verify/{fixture_id}").json()
        self.assertTrue(artifacts["parser_cache_removed"])
        self.assertTrue(artifacts["embedding_cache_removed"])
        self.assertTrue(artifacts["authorization_preserved"])
        persisted = self.client.get(f"/chats/{chat_id}/messages")
        persisted.raise_for_status()
        self.assertTrue(any(item["content"] == chat_marker for item in persisted.json()))

        previous_job_ids = {job["id"] for job in self.client.get(f"/documents/{document_id}/jobs").json()}
        rebuild = self.client.post(f"/documents/{document_id}/markdown/rebuild")
        self.assertEqual(rebuild.status_code, 202, rebuild.text)
        rebuild.raise_for_status()
        self._wait_new_job(document_id, previous_job_ids)
        persisted = self.client.get(f"/chats/{chat_id}/messages")
        persisted.raise_for_status()
        self.assertTrue(any(item["content"] == chat_marker for item in persisted.json()))

        selected_plan = self._plan("delete_selected", [document_id])
        self.assertEqual(selected_plan["documents"][0]["filename"], filename_marker)
        self.assertGreaterEqual(selected_plan["records"]["messages"], 2)
        wrong_phrase = self._execute(selected_plan, "wrong confirmation")
        self.assertEqual(wrong_phrase.status_code, 400)
        self.assertEqual(self.client.get(f"/documents/{document_id}").status_code, 200)
        deleted = self._execute(selected_plan)
        deleted.raise_for_status()
        self.assertEqual(deleted.json()["deleted_documents"], 1)
        self.assertEqual(self.client.get(f"/documents/{document_id}").status_code, 404)

        second_id = self._upload(f"m15-final-library-{fixture_id}.txt", f"M15_FINAL_{fixture_id}")
        second_chat = self._send_chat_message(second_id, f"M15_FINAL_CHAT_{fixture_id}")
        queued_id = None
        worker_stopped = False
        try:
            # Keep one synthetic upload queued to prove library removal cancels associated work.
            self._compose("stop", "worker")
            worker_stopped = True
            queued_upload = self.client.post("/documents", files={
                "file": (f"m15-queued-{fixture_id}.txt", b"Queued maintenance fixture.", "text/plain")
            })
            queued_upload.raise_for_status()
            queued_id = queued_upload.json()["id"]
            self.document_ids.append(queued_id)
            job = self.client.get(f"/documents/{queued_id}/jobs")
            job.raise_for_status()
            self.assertEqual(job.json()[0]["state"], "queued")
        except Exception:
            if worker_stopped:
                self._compose("start", "worker")
                worker_stopped = False
            raise
        library_plan = self._plan("delete_all")
        self.assertGreaterEqual(library_plan["records"]["messages"], 2)
        self.assertIn(second_id, {item["id"] for item in library_plan["documents"]})
        self.assertIn(queued_id, {item["id"] for item in library_plan["documents"]})
        try:
            library_deleted = self._execute(library_plan)
            library_deleted.raise_for_status()
            self.assertGreaterEqual(library_deleted.json()["cancelled_jobs"], 1)
        finally:
            if worker_stopped:
                self._compose("start", "worker")
                worker_stopped = False
        summary = self.client.get("/maintenance/summary")
        summary.raise_for_status()
        self.assertEqual(summary.json()["counts"]["documents"], 0)
        self.assertEqual(summary.json()["counts"]["messages"], 0)
        empty_library_plan = self._plan("delete_all")
        self.assertEqual(empty_library_plan["items"], 0)
        empty_library_result = self._execute(empty_library_plan)
        empty_library_result.raise_for_status()
        self.assertEqual(empty_library_result.json()["deleted_documents"], 0)
        self.assertEqual(empty_library_result.json()["file_cleanup_errors"], 0)
        self.assertTrue(self.client.get(f"/__e2e/maintenance/verify/{fixture_id}").json()["authorization_preserved"])
        self.assertEqual(self.client.get(f"/chats/{second_chat}/messages").status_code, 404)


if __name__ == "__main__":
    unittest.main(verbosity=2)
