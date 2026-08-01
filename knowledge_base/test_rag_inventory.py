from __future__ import annotations

from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from audit.models import ACTION_TENANT_RAG_CONFIGURED, AuditEvent
from knowledge_base.models import (
    TenantRagConfiguration,
    TenantRagDocumentChunk,
    TenantRagDriveFileManifest,
    TenantRagDriveTextStaging,
)
from knowledge_base.rag.chunking import RagChunkingError, build_deterministic_chunks, load_chunk_config
from knowledge_base.rag.google_drive_inventory import (
    GOOGLE_DRIVE_FOLDER_MIME,
    GOOGLE_DRIVE_SHORTCUT_MIME,
    GoogleDriveAuthenticationError,
    GoogleDriveApiError,
    GoogleDriveConfigurationError,
    GoogleDriveInventoryService,
    GoogleDrivePermissionError,
    InventorySummary,
    build_google_drive_readonly_service,
    compute_text_sha256,
    normalize_text_for_rag,
)
from tenants.models import Tenant


class _FakeRequest:
    def __init__(self, payload=None, error=None):
        self.payload = payload or {}
        self.error = error

    def execute(self):
        if self.error:
            raise self.error
        return self.payload


class _FakeFilesResource:
    def __init__(self, *, folder_payload, children_pages, get_error=None, export_payloads=None, export_errors=None):
        self.folder_payload = folder_payload
        self.children_pages = children_pages
        self.get_error = get_error
        self.get_media_called = False
        self.export_media_called = False
        self.export_payloads = export_payloads or {}
        self.export_errors = export_errors or {}
        self.export_calls = []

    def get(self, **_kwargs):
        return _FakeRequest(payload=self.folder_payload, error=self.get_error)

    def list(self, **kwargs):
        q = kwargs["q"]
        parent_id = q.split("'")[1]
        token = kwargs.get("pageToken")
        payload = self.children_pages[parent_id][token]
        return _FakeRequest(payload=payload)

    def get_media(self, **_kwargs):  # pragma: no cover - guard API
        self.get_media_called = True
        raise AssertionError("Inventory phase must not download file content.")

    def export_media(self, **_kwargs):  # pragma: no cover - guard API
        self.export_media_called = True
        raise AssertionError("Inventory phase must not export Google Docs.")

    def export(self, **kwargs):
        file_id = kwargs["fileId"]
        self.export_calls.append((file_id, kwargs.get("mimeType")))
        if file_id in self.export_errors:
            return _FakeRequest(error=self.export_errors[file_id])
        return _FakeRequest(payload=self.export_payloads[file_id])


class _FakeDriveService:
    def __init__(self, files_resource):
        self._files_resource = files_resource

    def files(self):
        return self._files_resource


class _HttpLikeError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.resp = type("Resp", (), {"status": status})()


class ConfigureTenantRagCommandTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Granimarmores Pitondo", slug="granimarmores-pitondo")

    def test_configure_tenant_rag_is_idempotent(self):
        output = StringIO()
        args = [
            "configure_tenant_rag",
            "--tenant",
            self.tenant.slug,
            "--approved-folder-id",
            "1Wbo-Vwj01NiWlYS_F1XWoN7umP9DZ6Jm",
            "--enable-sync",
        ]
        call_command(*args, stdout=output)
        call_command(*args, stdout=output)

        config = TenantRagConfiguration.objects.get(tenant=self.tenant)
        self.assertTrue(config.sync_enabled)
        self.assertEqual(config.approved_folder_id, "1Wbo-Vwj01NiWlYS_F1XWoN7umP9DZ6Jm")
        self.assertEqual(TenantRagConfiguration.objects.filter(tenant=self.tenant).count(), 1)
        self.assertTrue(
            AuditEvent.objects.filter(
                action=ACTION_TENANT_RAG_CONFIGURED,
                tenant=self.tenant,
            ).exists()
        )

    def test_configure_tenant_rag_rejects_unknown_tenant(self):
        with self.assertRaises(CommandError):
            call_command(
                "configure_tenant_rag",
                "--tenant",
                "inexistente",
                "--approved-folder-id",
                "1Wbo-Vwj01NiWlYS_F1XWoN7umP9DZ6Jm",
                "--enable-sync",
            )

    def test_configure_tenant_rag_rejects_folder_url(self):
        with self.assertRaises(CommandError):
            call_command(
                "configure_tenant_rag",
                "--tenant",
                self.tenant.slug,
                "--approved-folder-id",
                "https://drive.google.com/drive/folders/1Wbo-Vwj01NiWlYS_F1XWoN7umP9DZ6Jm",
                "--enable-sync",
            )

    def test_configure_tenant_rag_sets_and_clears_min_similarity_score(self):
        call_command(
            "configure_tenant_rag",
            "--tenant",
            self.tenant.slug,
            "--approved-folder-id",
            "1Wbo-Vwj01NiWlYS_F1XWoN7umP9DZ6Jm",
            "--min-similarity-score",
            "0.35",
        )
        config = TenantRagConfiguration.objects.get(tenant=self.tenant)
        self.assertEqual(config.min_similarity_score, 0.35)

        # Works without folder-id when configuration already exists.
        call_command(
            "configure_tenant_rag",
            "--tenant",
            self.tenant.slug,
            "--clear-min-similarity-score",
        )
        config.refresh_from_db()
        self.assertIsNone(config.min_similarity_score)

    def test_configure_tenant_rag_rejects_invalid_min_similarity_score(self):
        with self.assertRaises(CommandError):
            call_command(
                "configure_tenant_rag",
                "--tenant",
                self.tenant.slug,
                "--approved-folder-id",
                "1Wbo-Vwj01NiWlYS_F1XWoN7umP9DZ6Jm",
                "--min-similarity-score",
                "2",
            )

    def test_model_rejects_out_of_range_tenant_threshold(self):
        config = TenantRagConfiguration.objects.create(
            tenant=self.tenant,
            approved_folder_id="1Wbo-Vwj01NiWlYS_F1XWoN7umP9DZ6Jm",
            sync_enabled=True,
        )
        config.min_similarity_score = -0.5
        with self.assertRaises(ValidationError):
            config.save()


class GoogleDriveClientConfigurationTests(SimpleTestCase):
    @override_settings(LIVIA_GOOGLE_SERVICE_ACCOUNT_FILE="")
    def test_build_service_requires_credential_setting(self):
        with self.assertRaises(GoogleDriveConfigurationError):
            build_google_drive_readonly_service()

    @override_settings(LIVIA_GOOGLE_SERVICE_ACCOUNT_FILE="/tmp/inexistent-livia-google-sa.json")
    def test_build_service_requires_existing_file(self):
        with self.assertRaises(GoogleDriveConfigurationError):
            build_google_drive_readonly_service()


class GoogleDriveInventoryServiceTests(SimpleTestCase):
    def test_inventory_handles_pagination_recursion_and_shortcuts(self):
        approved_folder_id = "1Wbo-Vwj01NiWlYS_F1XWoN7umP9DZ6Jm"
        children_pages = {
            approved_folder_id: {
                None: {
                    "files": [
                        {"id": "sub-folder", "name": "Sub", "mimeType": GOOGLE_DRIVE_FOLDER_MIME},
                        {"id": "f-2", "name": "B-file.pdf", "mimeType": "application/pdf", "size": "200", "modifiedTime": "2026-07-29T10:00:00Z"},
                    ],
                    "nextPageToken": "page-2",
                },
                "page-2": {
                    "files": [
                        {
                            "id": "shortcut-1",
                            "name": "atalho externo",
                            "mimeType": GOOGLE_DRIVE_SHORTCUT_MIME,
                            "shortcutDetails": {"targetId": "outside", "targetMimeType": GOOGLE_DRIVE_FOLDER_MIME},
                        }
                    ],
                    "nextPageToken": None,
                },
            },
            "sub-folder": {
                None: {
                    "files": [
                        {"id": "f-1", "name": "A-file.pdf", "mimeType": "application/pdf", "size": "100", "modifiedTime": "2026-07-28T10:00:00Z"}
                    ],
                    "nextPageToken": None,
                }
            },
        }
        files_resource = _FakeFilesResource(
            folder_payload={"id": approved_folder_id, "name": "Root", "mimeType": GOOGLE_DRIVE_FOLDER_MIME, "trashed": False},
            children_pages=children_pages,
        )
        service = GoogleDriveInventoryService(_FakeDriveService(files_resource))

        with self.assertLogs("knowledge_base.rag.google_drive_inventory", level="INFO") as logs:
            summary = service.inventory_approved_folder(approved_folder_id)

        self.assertEqual(summary.traversed_folders, 2)
        self.assertEqual(summary.blocked_shortcuts, 1)
        self.assertEqual(summary.scanned_items, 4)
        self.assertEqual([item.name for item in summary.files], ["A-file.pdf", "B-file.pdf"])
        self.assertFalse(files_resource.get_media_called)
        self.assertFalse(files_resource.export_media_called)
        self.assertIn("google_drive_inventory_completed", " ".join(logs.output))
        self.assertNotIn("private_key", " ".join(logs.output))

    def test_inventory_denies_unreachable_folder(self):
        files_resource = _FakeFilesResource(
            folder_payload={},
            children_pages={"root": {None: {"files": [], "nextPageToken": None}}},
            get_error=_HttpLikeError(404, "not found"),
        )
        service = GoogleDriveInventoryService(_FakeDriveService(files_resource))
        with self.assertRaises(GoogleDrivePermissionError):
            service.inventory_approved_folder("1Wbo-Vwj01NiWlYS_F1XWoN7umP9DZ6Jm")


class SyncTenantRagCommandTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Granimarmores Pitondo", slug="granimarmores-pitondo")
        self.other_tenant = Tenant.objects.create(name="Outro", slug="outro-tenant")
        self.config = TenantRagConfiguration.objects.create(
            tenant=self.tenant,
            approved_folder_id="1Wbo-Vwj01NiWlYS_F1XWoN7umP9DZ6Jm",
            sync_enabled=True,
        )
        TenantRagConfiguration.objects.create(
            tenant=self.other_tenant,
            approved_folder_id="another-folder-id",
            sync_enabled=True,
        )

    def test_sync_requires_explicit_single_mode(self):
        with self.assertRaises(CommandError):
            call_command("sync_tenant_rag", "--tenant", self.tenant.slug)
        with self.assertRaises(CommandError):
            call_command("sync_tenant_rag", "--tenant", self.tenant.slug, "--inventory-only", "--export-text")
        with self.assertRaises(CommandError):
            call_command("sync_tenant_rag", "--tenant", self.tenant.slug, "--inventory-only", "--build-chunks")

    def test_sync_rejects_unknown_tenant(self):
        with self.assertRaises(CommandError):
            call_command("sync_tenant_rag", "--tenant", "nao-existe", "--inventory-only")

    def test_sync_rejects_when_configuration_is_missing(self):
        TenantRagConfiguration.objects.filter(tenant=self.tenant).delete()
        with self.assertRaises(CommandError):
            call_command("sync_tenant_rag", "--tenant", self.tenant.slug, "--inventory-only")

    def test_sync_rejects_when_sync_is_disabled(self):
        self.config.sync_enabled = False
        self.config.save(update_fields=["sync_enabled", "updated_at"])
        with self.assertRaises(CommandError):
            call_command("sync_tenant_rag", "--tenant", self.tenant.slug, "--inventory-only")

    def test_sync_handles_missing_credentials(self):
        with patch(
            "knowledge_base.management.commands.sync_tenant_rag.build_google_drive_readonly_service",
            side_effect=GoogleDriveConfigurationError("LIVIA_GOOGLE_SERVICE_ACCOUNT_FILE is required."),
        ):
            with self.assertRaises(CommandError):
                call_command("sync_tenant_rag", "--tenant", self.tenant.slug, "--inventory-only")

    def test_sync_handles_missing_credential_file(self):
        with patch(
            "knowledge_base.management.commands.sync_tenant_rag.build_google_drive_readonly_service",
            side_effect=GoogleDriveConfigurationError("Google service account file was not found."),
        ):
            with self.assertRaises(CommandError):
                call_command("sync_tenant_rag", "--tenant", self.tenant.slug, "--inventory-only")

    def test_sync_handles_invalid_credential_json(self):
        with patch(
            "knowledge_base.management.commands.sync_tenant_rag.build_google_drive_readonly_service",
            side_effect=GoogleDriveConfigurationError("Google service account JSON is invalid."),
        ):
            with self.assertRaises(CommandError):
                call_command("sync_tenant_rag", "--tenant", self.tenant.slug, "--inventory-only")

    def test_sync_handles_authentication_denied(self):
        with patch(
            "knowledge_base.management.commands.sync_tenant_rag.build_google_drive_readonly_service",
            side_effect=GoogleDriveAuthenticationError("Google Drive authentication failed."),
        ):
            with self.assertRaises(CommandError):
                call_command("sync_tenant_rag", "--tenant", self.tenant.slug, "--inventory-only")

    def test_sync_handles_folder_without_permission(self):
        with patch(
            "knowledge_base.management.commands.sync_tenant_rag.build_google_drive_readonly_service",
            side_effect=GoogleDrivePermissionError("Approved folder was not found or is not accessible."),
        ):
            with self.assertRaises(CommandError):
                call_command("sync_tenant_rag", "--tenant", self.tenant.slug, "--inventory-only")

    def test_sync_inventory_updates_state_and_never_calls_openai(self):
        output = StringIO()
        summary = InventorySummary(
            files=[
                type("File", (), {"file_id": "f-1", "name": "A.pdf", "mime_type": "application/pdf", "size_bytes": 120, "modified_time": "2026-07-01T12:00:00Z", "parent_folder_id": "root", "relative_path": "A.pdf"})(),
                type("File", (), {"file_id": "f-2", "name": "B.docx", "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "size_bytes": None, "modified_time": "2026-07-02T12:00:00Z", "parent_folder_id": "root", "relative_path": "B.docx"})(),
            ],
            traversed_folders=3,
            blocked_shortcuts=1,
            scanned_items=7,
        )
        with patch("knowledge_base.management.commands.sync_tenant_rag.build_google_drive_readonly_service", return_value=object()):
            with patch(
                "knowledge_base.management.commands.sync_tenant_rag.GoogleDriveInventoryService.inventory_approved_folder",
                return_value=summary,
            ) as inventory_mock:
                with patch("integrations.openai.client.requests.post") as openai_post:
                    call_command("sync_tenant_rag", "--tenant", self.tenant.slug, "--inventory-only", stdout=output)

        self.config.refresh_from_db()
        other_config = TenantRagConfiguration.objects.get(tenant=self.other_tenant)
        self.assertEqual(self.config.last_inventory_status, TenantRagConfiguration.InventoryStatus.SUCCESS)
        self.assertEqual(self.config.last_inventory_file_count, 2)
        self.assertEqual(other_config.last_inventory_status, TenantRagConfiguration.InventoryStatus.IDLE)
        inventory_mock.assert_called_once_with(self.config.approved_folder_id)
        openai_post.assert_not_called()
        rendered = output.getvalue()
        self.assertIn("Tenant RAG sync completed.", rendered)
        self.assertIn("files=2", rendered)
        self.assertIn("mode=inventory_only", rendered)
        self.assertEqual(TenantRagDriveTextStaging.objects.filter(tenant=self.tenant).count(), 0)

    @override_settings(LIVIA_RAG_EXPORT_MAX_BYTES=1000)
    def test_export_text_creates_manifest_and_staging_and_is_incremental(self):
        summary = InventorySummary(
            files=[
                type(
                    "File",
                    (),
                    {
                        "file_id": "doc-1",
                        "name": "Documento A",
                        "mime_type": "application/vnd.google-apps.document",
                        "size_bytes": None,
                        "modified_time": "2026-07-02T12:00:00Z",
                        "parent_folder_id": "root",
                        "relative_path": "Documento A",
                    },
                )()
            ],
            traversed_folders=2,
            blocked_shortcuts=0,
            scanned_items=1,
        )
        files_resource = _FakeFilesResource(
            folder_payload={"id": self.config.approved_folder_id, "name": "Root", "mimeType": GOOGLE_DRIVE_FOLDER_MIME, "trashed": False},
            children_pages={},
            export_payloads={"doc-1": "Olá   mundo\r\n\r\n\n".encode("utf-8")},
        )
        with patch("knowledge_base.management.commands.sync_tenant_rag.build_google_drive_readonly_service", return_value=_FakeDriveService(files_resource)):
            with patch(
                "knowledge_base.management.commands.sync_tenant_rag.GoogleDriveInventoryService.inventory_approved_folder",
                return_value=summary,
            ):
                call_command("sync_tenant_rag", "--tenant", self.tenant.slug, "--export-text")
                call_command("sync_tenant_rag", "--tenant", self.tenant.slug, "--export-text")

        manifest = TenantRagDriveFileManifest.objects.get(tenant=self.tenant, drive_file_id="doc-1")
        staging = TenantRagDriveTextStaging.objects.get(manifest=manifest)
        self.assertEqual(staging.normalized_text, "Olá mundo")
        self.assertEqual(staging.normalized_text_sha256, compute_text_sha256("Olá mundo"))
        self.assertEqual(files_resource.export_calls.count(("doc-1", "text/plain")), 1)
        self.assertEqual(manifest.status, TenantRagDriveFileManifest.Status.UNCHANGED)

    @override_settings(LIVIA_RAG_EXPORT_MAX_BYTES=4)
    def test_export_text_enforces_max_bytes_limit(self):
        summary = InventorySummary(
            files=[
                type(
                    "File",
                    (),
                    {
                        "file_id": "doc-big",
                        "name": "Grande",
                        "mime_type": "application/vnd.google-apps.document",
                        "size_bytes": None,
                        "modified_time": "2026-07-02T12:00:00Z",
                        "parent_folder_id": "root",
                        "relative_path": "Grande",
                    },
                )()
            ],
            traversed_folders=2,
            blocked_shortcuts=0,
            scanned_items=1,
        )
        files_resource = _FakeFilesResource(
            folder_payload={"id": self.config.approved_folder_id, "name": "Root", "mimeType": GOOGLE_DRIVE_FOLDER_MIME, "trashed": False},
            children_pages={},
            export_payloads={"doc-big": "conteudo grande".encode("utf-8")},
        )
        with patch("knowledge_base.management.commands.sync_tenant_rag.build_google_drive_readonly_service", return_value=_FakeDriveService(files_resource)):
            with patch(
                "knowledge_base.management.commands.sync_tenant_rag.GoogleDriveInventoryService.inventory_approved_folder",
                return_value=summary,
            ):
                with self.assertRaises(CommandError):
                    call_command("sync_tenant_rag", "--tenant", self.tenant.slug, "--export-text")
        manifest = TenantRagDriveFileManifest.objects.get(tenant=self.tenant, drive_file_id="doc-big")
        self.assertEqual(manifest.status, TenantRagDriveFileManifest.Status.FAILED)
        self.assertEqual(TenantRagDriveTextStaging.objects.filter(manifest=manifest).count(), 0)

    @override_settings(LIVIA_RAG_EXPORT_MAX_BYTES=1000)
    def test_export_text_invalid_utf8_preserves_previous_version(self):
        summary_v1 = InventorySummary(
            files=[
                type(
                    "File",
                    (),
                    {
                        "file_id": "doc-utf8",
                        "name": "Utf8",
                        "mime_type": "application/vnd.google-apps.document",
                        "size_bytes": None,
                        "modified_time": "2026-07-02T12:00:00Z",
                        "parent_folder_id": "root",
                        "relative_path": "Utf8",
                    },
                )()
            ],
            traversed_folders=2,
            blocked_shortcuts=0,
            scanned_items=1,
        )
        files_resource = _FakeFilesResource(
            folder_payload={"id": self.config.approved_folder_id, "name": "Root", "mimeType": GOOGLE_DRIVE_FOLDER_MIME, "trashed": False},
            children_pages={},
            export_payloads={"doc-utf8": "conteúdo válido".encode("utf-8")},
        )
        with patch("knowledge_base.management.commands.sync_tenant_rag.build_google_drive_readonly_service", return_value=_FakeDriveService(files_resource)):
            with patch("knowledge_base.management.commands.sync_tenant_rag.GoogleDriveInventoryService.inventory_approved_folder", return_value=summary_v1):
                call_command("sync_tenant_rag", "--tenant", self.tenant.slug, "--export-text")

        files_resource.export_payloads["doc-utf8"] = b"\xff\xfe\xfd"
        summary_v2 = InventorySummary(
            files=[
                type(
                    "File",
                    (),
                    {
                        "file_id": "doc-utf8",
                        "name": "Utf8",
                        "mime_type": "application/vnd.google-apps.document",
                        "size_bytes": None,
                        "modified_time": "2026-07-02T13:00:00Z",
                        "parent_folder_id": "root",
                        "relative_path": "Utf8",
                    },
                )()
            ],
            traversed_folders=2,
            blocked_shortcuts=0,
            scanned_items=1,
        )
        with patch("knowledge_base.management.commands.sync_tenant_rag.build_google_drive_readonly_service", return_value=_FakeDriveService(files_resource)):
            with patch("knowledge_base.management.commands.sync_tenant_rag.GoogleDriveInventoryService.inventory_approved_folder", return_value=summary_v2):
                with self.assertRaises(CommandError):
                    call_command("sync_tenant_rag", "--tenant", self.tenant.slug, "--export-text")

        staging = TenantRagDriveTextStaging.objects.get(tenant=self.tenant, manifest__drive_file_id="doc-utf8")
        self.assertEqual(staging.normalized_text, "conteúdo válido")

    def test_partial_run_does_not_mark_removed(self):
        existing = TenantRagDriveFileManifest.objects.create(
            tenant=self.tenant,
            configuration=self.config,
            drive_file_id="doc-old",
            name="Antigo",
            mime_type="application/vnd.google-apps.document",
            relative_path="Antigo",
            status=TenantRagDriveFileManifest.Status.EXPORTED,
            is_active=True,
        )
        summary = InventorySummary(
            files=[
                type(
                    "File",
                    (),
                    {
                        "file_id": "doc-fail",
                        "name": "Falha",
                        "mime_type": "application/vnd.google-apps.document",
                        "size_bytes": None,
                        "modified_time": "2026-07-02T12:00:00Z",
                        "parent_folder_id": "root",
                        "relative_path": "Falha",
                    },
                )()
            ],
            traversed_folders=2,
            blocked_shortcuts=0,
            scanned_items=1,
        )
        files_resource = _FakeFilesResource(
            folder_payload={"id": self.config.approved_folder_id, "name": "Root", "mimeType": GOOGLE_DRIVE_FOLDER_MIME, "trashed": False},
            children_pages={},
            export_errors={"doc-fail": GoogleDriveApiError("boom")},
        )
        with patch("knowledge_base.management.commands.sync_tenant_rag.build_google_drive_readonly_service", return_value=_FakeDriveService(files_resource)):
            with patch("knowledge_base.management.commands.sync_tenant_rag.GoogleDriveInventoryService.inventory_approved_folder", return_value=summary):
                with self.assertRaises(CommandError):
                    call_command("sync_tenant_rag", "--tenant", self.tenant.slug, "--export-text")
        existing.refresh_from_db()
        self.assertTrue(existing.is_active)

    def test_successful_run_marks_missing_as_removed_and_restores_when_seen_again(self):
        removed_candidate = TenantRagDriveFileManifest.objects.create(
            tenant=self.tenant,
            configuration=self.config,
            drive_file_id="doc-removed",
            name="Removido",
            mime_type="application/vnd.google-apps.document",
            relative_path="Removido",
            status=TenantRagDriveFileManifest.Status.EXPORTED,
            is_active=True,
        )
        summary_without = InventorySummary(files=[], traversed_folders=1, blocked_shortcuts=0, scanned_items=0)
        with patch("knowledge_base.management.commands.sync_tenant_rag.build_google_drive_readonly_service", return_value=object()):
            with patch("knowledge_base.management.commands.sync_tenant_rag.GoogleDriveInventoryService.inventory_approved_folder", return_value=summary_without):
                call_command("sync_tenant_rag", "--tenant", self.tenant.slug, "--inventory-only")
        removed_candidate.refresh_from_db()
        self.assertFalse(removed_candidate.is_active)
        self.assertEqual(removed_candidate.status, TenantRagDriveFileManifest.Status.REMOVED)

        summary_restore = InventorySummary(
            files=[
                type(
                    "File",
                    (),
                    {
                        "file_id": "doc-removed",
                        "name": "Removido",
                        "mime_type": "application/vnd.google-apps.document",
                        "size_bytes": None,
                        "modified_time": "2026-07-02T15:00:00Z",
                        "parent_folder_id": "root",
                        "relative_path": "Removido",
                    },
                )()
            ],
            traversed_folders=1,
            blocked_shortcuts=0,
            scanned_items=1,
        )
        files_resource = _FakeFilesResource(
            folder_payload={"id": self.config.approved_folder_id, "name": "Root", "mimeType": GOOGLE_DRIVE_FOLDER_MIME, "trashed": False},
            children_pages={},
            export_payloads={"doc-removed": "restaurado".encode("utf-8")},
        )
        with patch("knowledge_base.management.commands.sync_tenant_rag.build_google_drive_readonly_service", return_value=_FakeDriveService(files_resource)):
            with patch("knowledge_base.management.commands.sync_tenant_rag.GoogleDriveInventoryService.inventory_approved_folder", return_value=summary_restore):
                call_command("sync_tenant_rag", "--tenant", self.tenant.slug, "--export-text")
        removed_candidate.refresh_from_db()
        self.assertTrue(removed_candidate.is_active)
        self.assertNotEqual(removed_candidate.status, TenantRagDriveFileManifest.Status.REMOVED)

    def test_unsupported_types_are_skipped_and_no_export_happens(self):
        summary = InventorySummary(
            files=[
                type(
                    "File",
                    (),
                    {
                        "file_id": "sheet-1",
                        "name": "Planilha",
                        "mime_type": "application/vnd.google-apps.spreadsheet",
                        "size_bytes": None,
                        "modified_time": "2026-07-02T12:00:00Z",
                        "parent_folder_id": "root",
                        "relative_path": "Planilha",
                    },
                )()
            ],
            traversed_folders=1,
            blocked_shortcuts=0,
            scanned_items=1,
        )
        files_resource = _FakeFilesResource(
            folder_payload={"id": self.config.approved_folder_id, "name": "Root", "mimeType": GOOGLE_DRIVE_FOLDER_MIME, "trashed": False},
            children_pages={},
            export_payloads={"sheet-1": b"not used"},
        )
        with patch("knowledge_base.management.commands.sync_tenant_rag.build_google_drive_readonly_service", return_value=_FakeDriveService(files_resource)):
            with patch("knowledge_base.management.commands.sync_tenant_rag.GoogleDriveInventoryService.inventory_approved_folder", return_value=summary):
                call_command("sync_tenant_rag", "--tenant", self.tenant.slug, "--export-text")
        self.assertEqual(files_resource.export_calls, [])

    def test_shortcuts_are_not_followed_for_export(self):
        summary = InventorySummary(
            files=[
                type(
                    "File",
                    (),
                    {
                        "file_id": "shortcut-1",
                        "name": "Atalho",
                        "mime_type": GOOGLE_DRIVE_SHORTCUT_MIME,
                        "size_bytes": None,
                        "modified_time": "2026-07-02T12:00:00Z",
                        "parent_folder_id": "root",
                        "relative_path": "Atalho",
                    },
                )()
            ],
            traversed_folders=1,
            blocked_shortcuts=1,
            scanned_items=1,
        )
        files_resource = _FakeFilesResource(
            folder_payload={"id": self.config.approved_folder_id, "name": "Root", "mimeType": GOOGLE_DRIVE_FOLDER_MIME, "trashed": False},
            children_pages={},
            export_payloads={"shortcut-1": b"should not be exported"},
        )
        with patch("knowledge_base.management.commands.sync_tenant_rag.build_google_drive_readonly_service", return_value=_FakeDriveService(files_resource)):
            with patch("knowledge_base.management.commands.sync_tenant_rag.GoogleDriveInventoryService.inventory_approved_folder", return_value=summary):
                call_command("sync_tenant_rag", "--tenant", self.tenant.slug, "--export-text")
        self.assertEqual(files_resource.export_calls, [])

    def test_openai_never_called_and_text_not_logged(self):
        summary = InventorySummary(
            files=[
                type(
                    "File",
                    (),
                    {
                        "file_id": "doc-log",
                        "name": "Segredo",
                        "mime_type": "application/vnd.google-apps.document",
                        "size_bytes": None,
                        "modified_time": "2026-07-02T12:00:00Z",
                        "parent_folder_id": "root",
                        "relative_path": "Segredo",
                    },
                )()
            ],
            traversed_folders=1,
            blocked_shortcuts=0,
            scanned_items=1,
        )
        files_resource = _FakeFilesResource(
            folder_payload={"id": self.config.approved_folder_id, "name": "Root", "mimeType": GOOGLE_DRIVE_FOLDER_MIME, "trashed": False},
            children_pages={},
            export_payloads={"doc-log": "texto super secreto".encode("utf-8")},
        )
        with patch("knowledge_base.management.commands.sync_tenant_rag.build_google_drive_readonly_service", return_value=_FakeDriveService(files_resource)):
            with patch("knowledge_base.management.commands.sync_tenant_rag.GoogleDriveInventoryService.inventory_approved_folder", return_value=summary):
                with patch("integrations.openai.client.requests.post") as openai_post:
                    with self.assertLogs("knowledge_base.rag.sync", level="INFO") as logs:
                        call_command("sync_tenant_rag", "--tenant", self.tenant.slug, "--export-text")
        openai_post.assert_not_called()
        self.assertNotIn("texto super secreto", " ".join(logs.output))

    def test_concurrency_guard_same_tenant(self):
        self.config.last_inventory_status = TenantRagConfiguration.InventoryStatus.RUNNING
        self.config.last_inventory_started_at = timezone.now()
        self.config.save(update_fields=["last_inventory_status", "last_inventory_started_at", "updated_at"])
        with self.assertRaises(CommandError):
            call_command("sync_tenant_rag", "--tenant", self.tenant.slug, "--inventory-only")

    def test_concurrency_does_not_block_other_tenant(self):
        self.config.last_inventory_status = TenantRagConfiguration.InventoryStatus.RUNNING
        self.config.last_inventory_started_at = timezone.now()
        self.config.save(update_fields=["last_inventory_status", "last_inventory_started_at", "updated_at"])
        summary = InventorySummary(files=[], traversed_folders=1, blocked_shortcuts=0, scanned_items=0)
        with patch("knowledge_base.management.commands.sync_tenant_rag.build_google_drive_readonly_service", return_value=object()):
            with patch("knowledge_base.management.commands.sync_tenant_rag.GoogleDriveInventoryService.inventory_approved_folder", return_value=summary):
                call_command("sync_tenant_rag", "--tenant", self.other_tenant.slug, "--inventory-only")


@override_settings(
    LIVIA_RAG_CHUNK_SIZE_CHARS=40,
    LIVIA_RAG_CHUNK_OVERLAP_CHARS=8,
    LIVIA_RAG_MAX_CHUNKS_PER_DOCUMENT=20,
)
class ChunkingAlgorithmTests(SimpleTestCase):
    def test_chunking_is_deterministic_and_preserves_accents(self):
        config = load_chunk_config()
        text = "Primeiro parágrafo com acentuação.\n\nSegundo parágrafo com ação e coração."
        chunks_a = build_deterministic_chunks(text, config)
        chunks_b = build_deterministic_chunks(text, config)
        self.assertEqual(chunks_a, chunks_b)
        self.assertTrue(any("ação" in chunk.text for chunk in chunks_a))

    def test_chunking_handles_small_document(self):
        config = load_chunk_config()
        chunks = build_deterministic_chunks("texto curto", config)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].text, "texto curto")

    def test_chunking_avoids_empty_and_keeps_order(self):
        config = load_chunk_config()
        text = ("A.\n\nB.\n\nC.\n\n" * 10).strip()
        chunks = build_deterministic_chunks(text, config)
        self.assertTrue(all(chunk.text for chunk in chunks))
        starts = [chunk.start_char for chunk in chunks]
        self.assertEqual(starts, sorted(starts))

    @override_settings(LIVIA_RAG_CHUNK_SIZE_CHARS=0)
    def test_chunk_settings_fail_closed_for_invalid_size(self):
        with self.assertRaises(RagChunkingError):
            load_chunk_config()

    @override_settings(LIVIA_RAG_CHUNK_SIZE_CHARS=100, LIVIA_RAG_CHUNK_OVERLAP_CHARS=100)
    def test_chunk_settings_fail_closed_for_invalid_overlap(self):
        with self.assertRaises(RagChunkingError):
            load_chunk_config()

    @override_settings(LIVIA_RAG_MAX_CHUNKS_PER_DOCUMENT=0)
    def test_chunk_settings_fail_closed_for_invalid_max_chunks(self):
        with self.assertRaises(RagChunkingError):
            load_chunk_config()


@override_settings(
    LIVIA_RAG_CHUNK_SIZE_CHARS=50,
    LIVIA_RAG_CHUNK_OVERLAP_CHARS=10,
    LIVIA_RAG_MAX_CHUNKS_PER_DOCUMENT=10,
)
class BuildChunksCommandTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Granimarmores Pitondo", slug="granimarmores-pitondo")
        self.other_tenant = Tenant.objects.create(name="Outro", slug="outro-tenant")
        self.config = TenantRagConfiguration.objects.create(
            tenant=self.tenant,
            approved_folder_id="1Wbo-Vwj01NiWlYS_F1XWoN7umP9DZ6Jm",
            sync_enabled=True,
        )
        self.other_config = TenantRagConfiguration.objects.create(
            tenant=self.other_tenant,
            approved_folder_id="outro-folder",
            sync_enabled=True,
        )

    def _create_staging(self, *, tenant, configuration, file_id="doc-1", text="Conteúdo de teste com acentuação."):
        manifest = TenantRagDriveFileManifest.objects.create(
            tenant=tenant,
            configuration=configuration,
            drive_file_id=file_id,
            name=file_id,
            mime_type="application/vnd.google-apps.document",
            relative_path=file_id,
            normalized_text_sha256=compute_text_sha256(normalize_text_for_rag(text)),
            status=TenantRagDriveFileManifest.Status.EXPORTED,
            is_active=True,
        )
        staging = TenantRagDriveTextStaging.objects.create(
            tenant=tenant,
            manifest=manifest,
            normalized_text=normalize_text_for_rag(text),
            normalized_text_sha256=manifest.normalized_text_sha256,
            normalized_text_char_count=len(normalize_text_for_rag(text)),
            normalized_text_byte_count=len(normalize_text_for_rag(text).encode("utf-8")),
            exported_at=timezone.now(),
        )
        return manifest, staging

    def test_build_chunks_mode_does_not_access_google_drive(self):
        self._create_staging(tenant=self.tenant, configuration=self.config)
        with patch("knowledge_base.management.commands.sync_tenant_rag.build_google_drive_readonly_service") as drive_builder:
            call_command("sync_tenant_rag", "--tenant", self.tenant.slug, "--build-chunks")
        drive_builder.assert_not_called()
        self.assertGreater(TenantRagDocumentChunk.objects.filter(tenant=self.tenant, is_active=True).count(), 0)

    def test_build_chunks_is_idempotent_when_staging_unchanged(self):
        self._create_staging(tenant=self.tenant, configuration=self.config)
        call_command("sync_tenant_rag", "--tenant", self.tenant.slug, "--build-chunks")
        first_count = TenantRagDocumentChunk.objects.filter(tenant=self.tenant, is_active=True).count()
        call_command("sync_tenant_rag", "--tenant", self.tenant.slug, "--build-chunks")
        second_count = TenantRagDocumentChunk.objects.filter(tenant=self.tenant, is_active=True).count()
        self.assertEqual(first_count, second_count)

    def test_build_chunks_rebuilds_when_staging_changes(self):
        manifest, staging = self._create_staging(tenant=self.tenant, configuration=self.config, text="Primeiro conteúdo.")
        call_command("sync_tenant_rag", "--tenant", self.tenant.slug, "--build-chunks")
        old_chunks = list(TenantRagDocumentChunk.objects.filter(tenant=self.tenant, manifest=manifest, is_active=True))
        staging.normalized_text = normalize_text_for_rag("Conteúdo alterado com mais linhas.\n\nNovo bloco.")
        staging.normalized_text_sha256 = compute_text_sha256(staging.normalized_text)
        staging.normalized_text_char_count = len(staging.normalized_text)
        staging.normalized_text_byte_count = len(staging.normalized_text.encode("utf-8"))
        staging.save(update_fields=["normalized_text", "normalized_text_sha256", "normalized_text_char_count", "normalized_text_byte_count", "updated_at"])
        manifest.normalized_text_sha256 = staging.normalized_text_sha256
        manifest.save(update_fields=["normalized_text_sha256", "updated_at"])
        call_command("sync_tenant_rag", "--tenant", self.tenant.slug, "--build-chunks")
        self.assertTrue(TenantRagDocumentChunk.objects.filter(pk__in=[c.pk for c in old_chunks], is_active=False).exists())

    def test_build_chunks_rebuilds_when_config_changes(self):
        self._create_staging(tenant=self.tenant, configuration=self.config)
        call_command("sync_tenant_rag", "--tenant", self.tenant.slug, "--build-chunks")
        with override_settings(LIVIA_RAG_CHUNK_SIZE_CHARS=30, LIVIA_RAG_CHUNK_OVERLAP_CHARS=5, LIVIA_RAG_MAX_CHUNKS_PER_DOCUMENT=20):
            call_command("sync_tenant_rag", "--tenant", self.tenant.slug, "--build-chunks")
        signatures = set(
            TenantRagDocumentChunk.objects.filter(tenant=self.tenant, is_active=True).values_list("chunk_config_signature", flat=True)
        )
        self.assertEqual(len(signatures), 1)

    def test_build_chunks_deactivates_when_manifest_inactive(self):
        manifest, _staging = self._create_staging(tenant=self.tenant, configuration=self.config)
        call_command("sync_tenant_rag", "--tenant", self.tenant.slug, "--build-chunks")
        manifest.is_active = False
        manifest.status = TenantRagDriveFileManifest.Status.REMOVED
        manifest.save(update_fields=["is_active", "status", "updated_at"])
        call_command("sync_tenant_rag", "--tenant", self.tenant.slug, "--build-chunks")
        self.assertFalse(TenantRagDocumentChunk.objects.filter(tenant=self.tenant, manifest=manifest, is_active=True).exists())

    def test_build_chunks_restores_after_reactivation(self):
        manifest, _staging = self._create_staging(tenant=self.tenant, configuration=self.config)
        call_command("sync_tenant_rag", "--tenant", self.tenant.slug, "--build-chunks")
        manifest.is_active = False
        manifest.status = TenantRagDriveFileManifest.Status.REMOVED
        manifest.save(update_fields=["is_active", "status", "updated_at"])
        call_command("sync_tenant_rag", "--tenant", self.tenant.slug, "--build-chunks")
        manifest.is_active = True
        manifest.status = TenantRagDriveFileManifest.Status.EXPORTED
        manifest.save(update_fields=["is_active", "status", "updated_at"])
        call_command("sync_tenant_rag", "--tenant", self.tenant.slug, "--build-chunks")
        self.assertTrue(TenantRagDocumentChunk.objects.filter(tenant=self.tenant, manifest=manifest, is_active=True).exists())

    @override_settings(LIVIA_RAG_MAX_CHUNKS_PER_DOCUMENT=1)
    def test_build_chunks_exceeds_limit_preserves_previous(self):
        manifest, staging = self._create_staging(
            tenant=self.tenant,
            configuration=self.config,
            text="Bloco muito grande para quebrar em vários pedaços.\n\nOutro bloco para ampliar o texto.",
        )
        with self.assertRaises(CommandError):
            call_command("sync_tenant_rag", "--tenant", self.tenant.slug, "--build-chunks")
        self.assertFalse(TenantRagDocumentChunk.objects.filter(tenant=self.tenant, manifest=manifest).exists())
        # create a valid version and then fail to ensure preservation
        with override_settings(LIVIA_RAG_MAX_CHUNKS_PER_DOCUMENT=20):
            call_command("sync_tenant_rag", "--tenant", self.tenant.slug, "--build-chunks")
        previous_active = list(TenantRagDocumentChunk.objects.filter(tenant=self.tenant, manifest=manifest, is_active=True).values_list("id", flat=True))
        staging.normalized_text = normalize_text_for_rag("Texto bem maior. " * 50)
        staging.normalized_text_sha256 = compute_text_sha256(staging.normalized_text)
        staging.save(update_fields=["normalized_text", "normalized_text_sha256", "updated_at"])
        manifest.normalized_text_sha256 = staging.normalized_text_sha256
        manifest.save(update_fields=["normalized_text_sha256", "updated_at"])
        with self.assertRaises(CommandError):
            call_command("sync_tenant_rag", "--tenant", self.tenant.slug, "--build-chunks")
        self.assertTrue(TenantRagDocumentChunk.objects.filter(id__in=previous_active, is_active=True).exists())

    def test_empty_staging_does_not_delete_existing_chunks(self):
        manifest, staging = self._create_staging(tenant=self.tenant, configuration=self.config, text="Texto base")
        call_command("sync_tenant_rag", "--tenant", self.tenant.slug, "--build-chunks")
        previous_active = list(TenantRagDocumentChunk.objects.filter(tenant=self.tenant, manifest=manifest, is_active=True).values_list("id", flat=True))
        staging.normalized_text = ""
        staging.normalized_text_sha256 = compute_text_sha256("")
        staging.save(update_fields=["normalized_text", "normalized_text_sha256", "updated_at"])
        manifest.normalized_text_sha256 = staging.normalized_text_sha256
        manifest.save(update_fields=["normalized_text_sha256", "updated_at"])
        call_command("sync_tenant_rag", "--tenant", self.tenant.slug, "--build-chunks")
        self.assertTrue(TenantRagDocumentChunk.objects.filter(id__in=previous_active, is_active=True).exists())

    def test_multi_tenant_isolation_for_chunks(self):
        self._create_staging(tenant=self.tenant, configuration=self.config, file_id="doc-a")
        self._create_staging(tenant=self.other_tenant, configuration=self.other_config, file_id="doc-b")
        call_command("sync_tenant_rag", "--tenant", self.tenant.slug, "--build-chunks")
        self.assertGreater(TenantRagDocumentChunk.objects.filter(tenant=self.tenant).count(), 0)
        self.assertEqual(TenantRagDocumentChunk.objects.filter(tenant=self.other_tenant).count(), 0)

    def test_chunk_model_rejects_cross_tenant_association(self):
        manifest, staging = self._create_staging(tenant=self.tenant, configuration=self.config)
        other_manifest, _ = self._create_staging(tenant=self.other_tenant, configuration=self.other_config, file_id="other-doc")
        chunk = TenantRagDocumentChunk(
            tenant=self.tenant,
            manifest=other_manifest,
            staging=staging,
            ordinal=0,
            chunk_text="abc",
            chunk_sha256=compute_text_sha256("abc"),
            source_text_sha256=staging.normalized_text_sha256,
            chunk_config_signature="x" * 64,
            char_count=3,
            byte_count=3,
            start_char=0,
            end_char=3,
        )
        with self.assertRaises(Exception):
            chunk.full_clean()
