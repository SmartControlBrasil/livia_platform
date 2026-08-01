from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from urllib.parse import urljoin

import requests

PILOT_TENANT_SLUG = "granimarmores-pitondo"
DEFAULT_ORIGIN = "https://www.granimarmorespitondo.com.br"
INVALID_ORIGIN = "https://evil-example.invalid"


@dataclass
class HttpCheckResult:
    code: str
    status: str
    detail: str


@dataclass
class PostdeployReport:
    checks: list[HttpCheckResult] = field(default_factory=list)

    def add(self, code: str, status: str, detail: str) -> None:
        self.checks.append(HttpCheckResult(code=code, status=status, detail=detail))

    @property
    def summary(self) -> str:
        if any(item.status == "FAIL" for item in self.checks):
            return "FAIL"
        if any(item.status == "WARN" for item in self.checks):
            return "WARN"
        return "PASS"


def _request(session: requests.Session, method: str, url: str, **kwargs) -> requests.Response:
    return session.request(method, url, timeout=kwargs.pop("timeout", 20), **kwargs)


def run_postdeploy_checks(
    *,
    base_url: str,
    tenant: str = PILOT_TENANT_SLUG,
    origin: str = DEFAULT_ORIGIN,
    verify_tls: bool = True,
    chat_smoke: bool = False,
) -> PostdeployReport:
    report = PostdeployReport()
    base = base_url.rstrip("/") + "/"
    session = requests.Session()

    for path, code in (("health/", "health"), ("health/?readiness=1", "health_readiness")):
        url = urljoin(base, path)
        try:
            resp = _request(session, "GET", url, verify=verify_tls)
        except requests.RequestException as exc:
            report.add(code, "FAIL", exc.__class__.__name__)
            continue
        if resp.status_code != 200:
            report.add(code, "FAIL", f"http={resp.status_code}")
            continue
        if code == "health_readiness":
            try:
                payload = resp.json()
            except ValueError:
                report.add(code, "WARN", "non-json readiness response")
            else:
                if "readiness" not in payload:
                    report.add(code, "WARN", "legacy readiness payload (missing readiness field)")
                else:
                    report.add(code, "PASS", f"readiness={payload.get('readiness')}")
        else:
            report.add(code, "PASS", "ok")

    widget_url = urljoin(base, "widget.js")
    try:
        widget_resp = _request(session, "GET", widget_url, verify=verify_tls)
        content_type = widget_resp.headers.get("Content-Type", "").lower()
        if widget_resp.status_code == 200 and (widget_resp.text.strip() or "javascript" in content_type):
            report.add("widget_js", "PASS", f"http={widget_resp.status_code}")
        else:
            report.add("widget_js", "FAIL", f"http={widget_resp.status_code}")
    except requests.RequestException as exc:
        report.add("widget_js", "FAIL", exc.__class__.__name__)

    config_url = urljoin(base, f"api/widget/config/?tenant={tenant}")
    try:
        cfg_resp = _request(
            session,
            "GET",
            config_url,
            headers={"Origin": origin, "Referer": origin + "/"},
            verify=verify_tls,
        )
        if cfg_resp.status_code == 200:
            report.add("widget_config", "PASS", "ok")
        else:
            report.add("widget_config", "FAIL", f"http={cfg_resp.status_code}")
    except requests.RequestException as exc:
        report.add("widget_config", "FAIL", exc.__class__.__name__)

    chat_url = urljoin(base, "api/chat/")
    try:
        options_ok = _request(
            session,
            "OPTIONS",
            chat_url,
            headers={"Origin": origin, "Access-Control-Request-Method": "POST"},
            verify=verify_tls,
        )
        allow_origin = options_ok.headers.get("Access-Control-Allow-Origin", "")
        if options_ok.status_code in {200, 204} and allow_origin == origin:
            report.add("cors_valid_origin", "PASS", allow_origin)
        else:
            report.add("cors_valid_origin", "FAIL", f"http={options_ok.status_code} allow={allow_origin or '(none)'}")
    except requests.RequestException as exc:
        report.add("cors_valid_origin", "FAIL", exc.__class__.__name__)

    try:
        options_bad = _request(
            session,
            "OPTIONS",
            chat_url,
            headers={"Origin": INVALID_ORIGIN, "Access-Control-Request-Method": "POST"},
            verify=verify_tls,
        )
        bad_allow = options_bad.headers.get("Access-Control-Allow-Origin", "")
        if bad_allow == "*" or (bad_allow and bad_allow != INVALID_ORIGIN):
            report.add("cors_invalid_origin", "FAIL", f"permissive allow={bad_allow}")
        else:
            report.add("cors_invalid_origin", "PASS", f"allow={bad_allow or '(none)'}")
    except requests.RequestException as exc:
        report.add("cors_invalid_origin", "WARN", exc.__class__.__name__)

    try:
        bad_tenant_resp = _request(
            session,
            "POST",
            chat_url,
            headers={"Origin": origin, "Content-Type": "application/json"},
            json={
                "tenant": "tenant-inexistente-staging-test",
                "session_id": f"postdeploy-{uuid.uuid4().hex[:8]}",
                "request_id": str(uuid.uuid4()),
                "message": "staging postdeploy invalid tenant probe",
            },
            verify=verify_tls,
        )
        if bad_tenant_resp.status_code in {403, 404}:
            report.add("invalid_tenant", "PASS", f"http={bad_tenant_resp.status_code}")
        else:
            report.add("invalid_tenant", "WARN", f"http={bad_tenant_resp.status_code}")
    except requests.RequestException as exc:
        report.add("invalid_tenant", "FAIL", exc.__class__.__name__)

    try:
        missing = _request(session, "GET", urljoin(base, "rota-inexistente-staging-probe/"), verify=verify_tls)
        if missing.status_code in {404, 403}:
            report.add("missing_route", "PASS", f"http={missing.status_code}")
        else:
            report.add("missing_route", "WARN", f"http={missing.status_code}")
    except requests.RequestException as exc:
        report.add("missing_route", "WARN", exc.__class__.__name__)

    try:
        health_resp = _request(session, "GET", urljoin(base, "health/"), verify=verify_tls)
        headers = {k.lower(): v for k, v in health_resp.headers.items()}
        if base_url.lower().startswith("https://"):
            if "strict-transport-security" in headers:
                report.add("security_hsts", "PASS", headers["strict-transport-security"])
            else:
                report.add("security_hsts", "WARN", "missing HSTS")
        if "x-content-type-options" in headers:
            report.add("security_headers", "PASS", "present")
        else:
            report.add("security_headers", "WARN", "x-content-type-options missing")
    except requests.RequestException as exc:
        report.add("security_headers", "WARN", exc.__class__.__name__)

    if chat_smoke:
        smoke_message = "[STAGING POSTDEPLOY SMOKE] ping sem lead/handoff/CRM"
        try:
            chat_resp = _request(
                session,
                "POST",
                chat_url,
                headers={
                    "Origin": origin,
                    "Referer": origin + "/",
                    "Content-Type": "application/json",
                    "X-Livia-Tenant": tenant,
                },
                json={
                    "tenant": tenant,
                    "session_id": f"postdeploy-smoke-{uuid.uuid4().hex[:8]}",
                    "request_id": str(uuid.uuid4()),
                    "message": smoke_message,
                },
                verify=verify_tls,
                timeout=120,
            )
            if chat_resp.status_code == 200:
                body = chat_resp.json()
                if body.get("human_handoff", {}).get("active"):
                    report.add("chat_smoke", "FAIL", "handoff unexpectedly active")
                else:
                    report.add("chat_smoke", "PASS", f"reply_len={len(str(body.get('reply', '')))}")
            else:
                report.add("chat_smoke", "FAIL", f"http={chat_resp.status_code}")
        except requests.RequestException as exc:
            report.add("chat_smoke", "FAIL", exc.__class__.__name__)

    return report
