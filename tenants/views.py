from django.http import JsonResponse
from django.shortcuts import render

from tenants.models import Tenant
from tenants.services.install_package import TenantInstallPackageService


class FriendlyTenantNotFound(Exception):
    pass


def _get_install_package_or_404(tenant_slug):
    try:
        return TenantInstallPackageService().build_for_slug(tenant_slug)
    except Tenant.DoesNotExist as exc:
        raise FriendlyTenantNotFound from exc


def tenant_install_page(request, tenant_slug):
    try:
        package = _get_install_package_or_404(tenant_slug)
    except FriendlyTenantNotFound:
        return render(
            request,
            "tenants/install_not_found.html",
            {"tenant_slug": tenant_slug},
            status=404,
        )

    return render(request, "tenants/install.html", {"package": package})


def tenant_install_json(request, tenant_slug):
    try:
        package = _get_install_package_or_404(tenant_slug)
    except FriendlyTenantNotFound:
        return JsonResponse({"error": "Tenant not found."}, status=404)

    return JsonResponse(package.to_dict())
