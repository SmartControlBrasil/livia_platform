from django.http import HttpResponse


def widget_js(request):
    content = """
(function () {
  const currentScript = document.currentScript;
  const tenant = currentScript ? currentScript.getAttribute("data-tenant") : "default";

  const button = document.createElement("button");
  button.innerText = "Lívia";
  button.style.position = "fixed";
  button.style.right = "20px";
  button.style.bottom = "20px";
  button.style.zIndex = "99999";
  button.style.border = "0";
  button.style.borderRadius = "999px";
  button.style.padding = "12px 18px";
  button.style.cursor = "pointer";
  button.style.boxShadow = "0 8px 24px rgba(0,0,0,.2)";

  button.addEventListener("click", function () {
    alert("Lívia carregada para o tenant: " + tenant);
  });

  document.body.appendChild(button);
})();
"""
    return HttpResponse(
        content,
        content_type="application/javascript; charset=utf-8",
    )
