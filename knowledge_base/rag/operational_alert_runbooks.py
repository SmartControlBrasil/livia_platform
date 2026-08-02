from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OperationalAlertRunbook:
    rule_id: str
    category: str
    default_severity: str
    title: str
    recommended_action: str
    documentation_reference: str = ""


RUNBOOKS: dict[str, OperationalAlertRunbook] = {
    "environment_not_ready": OperationalAlertRunbook(
        rule_id="environment_not_ready",
        category="environment",
        default_severity="critical",
        title="Ambiente não pronto",
        recommended_action=(
            "1. Revise LIVIA_ENVIRONMENT e flags de dry-run.\n"
            "2. Execute environment_readiness.\n"
            "3. Corrija checks críticos antes de side effects reais."
        ),
        documentation_reference="docs/rag_staging_runbook.md",
    ),
    "database_not_ready": OperationalAlertRunbook(
        rule_id="database_not_ready",
        category="database",
        default_severity="critical",
        title="Database readiness NOT_READY",
        recommended_action=(
            "1. Execute database_readiness.\n"
            "2. Aplique migrations pendentes.\n"
            "3. Revalide readiness antes de operar."
        ),
        documentation_reference="docs/deploy/sqlite_to_postgresql.md",
    ),
    "vector_incompatible": OperationalAlertRunbook(
        rule_id="vector_incompatible",
        category="vector_health",
        default_severity="warning",
        title="Embeddings incompatíveis",
        recommended_action=(
            "1. Valide provider/model/dimension.\n"
            "2. Execute rag_vector_health.\n"
            "3. Faça dry-run de indexação.\n"
            "4. Reindexe somente após confirmação."
        ),
        documentation_reference="docs/rag_embedding_evolution.md",
    ),
    "provider_forbidden": OperationalAlertRunbook(
        rule_id="provider_forbidden",
        category="openai_provider",
        default_severity="critical",
        title="Provider de embedding proibido",
        recommended_action=(
            "1. Remova LIVIA_RAG_EMBEDDING_PROVIDER=fake em staging/produção.\n"
            "2. Valide environment_readiness.\n"
            "3. Não exponha API keys no painel."
        ),
        documentation_reference="docs/rag_embedding_evolution.md",
    ),
    "integration_safety": OperationalAlertRunbook(
        rule_id="integration_safety",
        category="integration_safety",
        default_severity="critical",
        title="Side effect inseguro em staging",
        recommended_action=(
            "1. Reative dry-run de CRM, webhooks e notificações.\n"
            "2. Confirme SMART360_LEAD_DISPATCH_DRY_RUN=True.\n"
            "3. Reexecute sync de alertas."
        ),
        documentation_reference="docs/rag_staging_runbook.md",
    ),
    "rag_operation_failed": OperationalAlertRunbook(
        rule_id="rag_operation_failed",
        category="rag_operations",
        default_severity="warning",
        title="Operação RAG falhou",
        recommended_action=(
            "1. Abra o detalhe da operação.\n"
            "2. Revise error_code sanitizado.\n"
            "3. Corrija causa raiz antes de re-solicitar."
        ),
        documentation_reference="docs/knowledge_base.md",
    ),
    "rag_operation_stale": OperationalAlertRunbook(
        rule_id="rag_operation_stale",
        category="rag_operations",
        default_severity="critical",
        title="Operação RAG stale",
        recommended_action=(
            "1. Verifique worker process_tenant_rag_operations.\n"
            "2. Revise lease/heartbeat.\n"
            "3. Execute tenant_rag_operations_readiness.\n"
            "4. Use recuperação transacional existente."
        ),
        documentation_reference="docs/rag_staging_runbook.md",
    ),
    "openai_failures": OperationalAlertRunbook(
        rule_id="openai_failures",
        category="openai_provider",
        default_severity="warning",
        title="Falhas OpenAI recorrentes",
        recommended_action=(
            "1. Verifique configuração e gates de IA.\n"
            "2. Revise categorias de erro sanitizadas.\n"
            "3. Confirme fallback determinístico.\n"
            "4. Não exponha API key."
        ),
        documentation_reference="docs/phase10_rag_ai_observability.md",
    ),
    "retrieval_empty_elevated": OperationalAlertRunbook(
        rule_id="retrieval_empty_elevated",
        category="retrieval",
        default_severity="warning",
        title="Retrieval empty elevado",
        recommended_action=(
            "1. Revise corpus e cobertura de embeddings.\n"
            "2. Valide threshold efetivo.\n"
            "3. Execute busca diagnóstica controlada."
        ),
        documentation_reference="docs/knowledge_base.md",
    ),
    "token_usage_elevated": OperationalAlertRunbook(
        rule_id="token_usage_elevated",
        category="token_usage",
        default_severity="warning",
        title="Uso elevado de tokens",
        recommended_action=(
            "1. Revise volume de requests no período.\n"
            "2. Confirme grounded synthesis e retrieval.\n"
            "3. Ajuste limites operacionais se necessário."
        ),
        documentation_reference="docs/phase10_rag_ai_observability.md",
    ),
}


def get_runbook(rule_id: str) -> OperationalAlertRunbook | None:
    base = str(rule_id or "").split(":", 1)[0]
    return RUNBOOKS.get(base)
