from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from django.core.exceptions import ValidationError
from django.utils.text import slugify

from knowledge_base.models import KnowledgeDocument

SUPPORTED_EXTENSIONS = {".txt", ".md", ".markdown"}


class TenantKnowledgeImportError(Exception):
    pass


@dataclass(frozen=True)
class TenantKnowledgeImportItem:
    title: str
    slug: str
    content: str
    source_type: str
    source_url: str
    tags: list[str]
    status: str


@dataclass(frozen=True)
class TenantKnowledgeImportResult:
    tenant: object
    planned: list[TenantKnowledgeImportItem] = field(default_factory=list)
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    skipped: int = 0
    dry_run: bool = False


def collect_supported_files(source: Path) -> list[Path]:
    source = Path(source).expanduser()
    if not source.exists():
        raise TenantKnowledgeImportError("Source path does not exist.")
    if source.is_file():
        return [source] if source.suffix.lower() in SUPPORTED_EXTENSIONS else []
    return sorted(
        path
        for path in source.rglob("*")
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
    )


def normalize_tags(raw_tags) -> list[str]:
    if isinstance(raw_tags, str):
        candidates = raw_tags.replace("\r", "\n").replace(",", "\n").splitlines()
    else:
        candidates = raw_tags or []
    tags = []
    for candidate in candidates:
        value = str(candidate or "").strip()
        if value and value not in tags:
            tags.append(value)
    return tags


def build_document_item(
    *,
    filename: str,
    content: str,
    source_type: str = "import",
    tags=None,
    status: str = KnowledgeDocument.Status.ACTIVE,
    source_url: str = "",
) -> TenantKnowledgeImportItem:
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise TenantKnowledgeImportError("Unsupported file format. Use .txt, .md or .markdown.")
    cleaned_content = str(content or "").strip()
    if not cleaned_content:
        raise TenantKnowledgeImportError(f"File is empty: {filename}")
    stem = Path(filename).stem
    title = stem.replace("_", " ").replace("-", " ").strip().title()
    slug = slugify(stem)[:110].strip("-") or "knowledge-document"
    item = TenantKnowledgeImportItem(
        title=title,
        slug=slug,
        content=cleaned_content,
        source_type=str(source_type or "import").strip() or "import",
        source_url=str(source_url or "").strip(),
        tags=normalize_tags(tags),
        status=status or KnowledgeDocument.Status.ACTIVE,
    )
    document = KnowledgeDocument(**item.__dict__)
    try:
        document.full_clean(exclude=["tenant"])
    except ValidationError as exc:
        raise TenantKnowledgeImportError("Invalid document payload.") from exc
    return item


def build_document_item_from_path(path: Path, *, source_type="import", tags=None, status=KnowledgeDocument.Status.ACTIVE) -> TenantKnowledgeImportItem:
    try:
        content = Path(path).read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise TenantKnowledgeImportError(f"Could not read {path} as UTF-8.") from exc
    return build_document_item(
        filename=Path(path).name,
        content=content,
        source_type=source_type,
        tags=tags,
        status=status,
    )


def build_document_item_from_upload(uploaded_file, *, source_type="import", tags=None, status=KnowledgeDocument.Status.ACTIVE) -> TenantKnowledgeImportItem:
    name = getattr(uploaded_file, "name", "") or "upload.txt"
    if Path(name).suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise TenantKnowledgeImportError("Unsupported file format. Use .txt, .md or .markdown.")
    try:
        content = uploaded_file.read().decode("utf-8")
    except UnicodeDecodeError as exc:
        raise TenantKnowledgeImportError("Could not read uploaded file as UTF-8.") from exc
    return build_document_item(filename=name, content=content, source_type=source_type, tags=tags, status=status)


def import_tenant_knowledge_items(*, tenant, items: list[TenantKnowledgeImportItem], replace: bool = False, dry_run: bool = False) -> TenantKnowledgeImportResult:
    if not items:
        raise TenantKnowledgeImportError("No supported .txt/.md/.markdown files found.")
    if dry_run:
        return TenantKnowledgeImportResult(tenant=tenant, planned=items, dry_run=True)

    from knowledge_base.services.lifecycle import IMPORT_CREATED, IMPORT_UNCHANGED, IMPORT_UPDATED, KnowledgeLifecycleService

    created = updated = unchanged = skipped = 0
    service = KnowledgeLifecycleService()
    for item in items:
        existing = KnowledgeDocument.objects.filter(tenant=tenant, slug=item.slug).first()
        if existing is not None and not replace:
            result = service.upsert_document(
                tenant=tenant,
                title=item.title,
                slug=item.slug,
                content=item.content,
                source_type=item.source_type,
                source_url=item.source_url,
                tags=item.tags,
                status=item.status,
                replace=False,
                source="knowledge_import",
            )
            if result.status == IMPORT_UNCHANGED:
                unchanged += 1
            else:
                skipped += 1
            continue
        result = service.upsert_document(
            tenant=tenant,
            title=item.title,
            slug=item.slug,
            content=item.content,
            source_type=item.source_type,
            source_url=item.source_url,
            tags=item.tags,
            status=item.status,
            replace=True,
            source="knowledge_import",
        )
        if result.status == IMPORT_CREATED:
            created += 1
        elif result.status == IMPORT_UPDATED:
            updated += 1
        else:
            unchanged += 1
    return TenantKnowledgeImportResult(
        tenant=tenant, planned=items, created=created, updated=updated, unchanged=unchanged, skipped=skipped, dry_run=False
    )


def import_tenant_knowledge_path(*, tenant, source: Path, source_type="import", tags=None, status=KnowledgeDocument.Status.ACTIVE, replace: bool = False, dry_run: bool = False) -> TenantKnowledgeImportResult:
    files = collect_supported_files(Path(source))
    items = [build_document_item_from_path(path, source_type=source_type, tags=tags, status=status) for path in files]
    return import_tenant_knowledge_items(tenant=tenant, items=items, replace=replace, dry_run=dry_run)
