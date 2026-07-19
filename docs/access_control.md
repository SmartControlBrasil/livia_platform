# Controle de acesso por tenant

A Lívia Platform usa `TenantMembership` para ligar usuário, tenant, papel e permissões do portal operacional.

## Modelo

`tenants.TenantMembership` contém:

- `tenant`;
- `user`, usando `settings.AUTH_USER_MODEL`;
- `role`;
- `is_active`;
- `created_by`;
- datas de criação e atualização.

Há unicidade por `tenant + user` e índices para consultas por tenant, usuário, ativo e papel.

## Papéis

- `tenant_admin`: administração operacional completa do próprio tenant.
- `manager`: operação comercial com reprocessamento de CRM e mudança de handoff.
- `operator`: operação de conversas, leads e handoffs, sem reprocessar CRM.
- `viewer`: visualização operacional.

Superuser continua sendo o administrador global da plataforma via `is_superuser`; não existe papel `platform_admin`.

## Capabilities

| Capability | tenant_admin | manager | operator | viewer |
| --- | --- | --- | --- | --- |
| `portal.view_dashboard` | sim | sim | sim | sim |
| `conversations.view` | sim | sim | sim | sim |
| `leads.view` | sim | sim | sim | sim |
| `leads.retry_crm` | sim | sim | não | não |
| `handoffs.view` | sim | sim | sim | sim |
| `handoffs.change_status` | sim | sim | sim | não |
| `assistant_profile.view` | sim | sim | não | não |
| `assistant_profile.change` | sim | não | não | não |
| `memberships.view` | sim | não | não | não |
| `memberships.manage` | sim | não | não | não |

A matriz vive em `tenants/access.py`. Views e serviços devem chamar funções como `user_has_tenant_capability` e `require_tenant_capability`, sem comparar strings de papéis diretamente.

## Tenant ativo

O portal resolve o tenant ativo em `operations_portal/access.py`.

- Superuser pode usar visão global quando nenhuma seleção é enviada.
- Superuser pode selecionar qualquer tenant ativo.
- Usuário com um membership ativo usa esse tenant automaticamente.
- Usuário com múltiplos memberships pode selecionar apenas tenants autorizados.
- Valores vindos de GET, POST ou sessão são revalidados em toda requisição.
- Usuário sem membership ativo recebe 403.

Trocar apenas o tenant ativo na interface não gera `AuditEvent`.

## Isolamento

Selectors do portal recebem `tenant` explicitamente. Quando há tenant ativo, listas, detalhes, analytics e contadores filtram por esse tenant antes de aplicar filtros do usuário.

Detalhes usam `pk + tenant` no mesmo queryset. Assim, um ID válido de outro tenant retorna 404 para usuário sem acesso.

Ocultar menu ou botão no template melhora a experiência, mas não é autorização. Toda ação POST valida capability no backend antes de alterar banco.

## Administração

`TenantMembership` é administrado pelo Django Admin somente por superuser nesta fase. Na criação, `created_by` é preenchido com `request.user`.

Para conceder acesso:

1. Abrir Django Admin.
2. Criar um `TenantMembership`.
3. Escolher tenant, usuário, papel e manter `is_active` marcado.

Para alterar acesso, edite o papel do membership. Para revogar, desmarque `is_active`.

## Auditoria

Criação, alteração e desativação de membership geram:

- `tenant_membership.created`;
- `tenant_membership.updated`;
- `tenant_membership.deactivated`.

Os eventos registram ator, tenant, usuário afetado por identificador seguro, papel anterior/novo e estado ativo anterior/novo. Senhas, hashes, sessões e dados sensíveis não são registrados.

## Limitações

- Não há convites por e-mail nesta fase.
- Não há papéis customizados por tenant.
- Não há interface própria do portal para gerenciar memberships; a administração fica no Django Admin.
- Actions em massa baseadas em `queryset.update()` permanecem fora da auditoria individual.

## Futuro

Possibilidades futuras incluem papéis customizados, grupos por tenant, convites com expiração, auditoria de tentativas negadas e uma tela operacional própria para gestão de membros.
