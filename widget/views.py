from django.http import HttpResponse, JsonResponse

from tenants.services.widget_config import build_widget_config_for_tenant_slug


def widget_js(request):
    content = """(function () {
  const currentScript = document.currentScript;
  const tenant = currentScript ? (currentScript.getAttribute("data-tenant") || "default") : "default";
  const sessionStorageKey = "livia_session_id_" + tenant;
  const apiUrl = resolveApiUrl(currentScript);
  const configUrl = resolveConfigUrl(currentScript);
  const defaultConfig = {
    assistant_name: "Lívia",
    widget_title: "Lívia",
    launcher_label: "Fale com a Lívia",
    initial_message: "Olá! Sou a Lívia. Como posso te ajudar?",
    primary_color: "#2563eb",
    position: "bottom_right",
    placeholder_text: "Digite sua mensagem...",
    show_branding: true,
    is_widget_enabled: true
  };

  function resolveApiUrl(scriptEl) {
    if (scriptEl) {
      const configuredUrl = scriptEl.getAttribute("data-api-url");
      if (configuredUrl) {
        return configuredUrl;
      }
      if (scriptEl.src) {
        try {
          return new URL("/api/chat/", scriptEl.src).href;
        } catch (error) {
          return "/api/chat/";
        }
      }
    }
    return "/api/chat/";
  }

  function resolveConfigUrl(scriptEl) {
    if (scriptEl && scriptEl.src) {
      try {
        const url = new URL("/api/widget/config/", scriptEl.src);
        url.searchParams.set("tenant", tenant);
        return url.href;
      } catch (error) {
        return "/api/widget/config/?tenant=" + encodeURIComponent(tenant);
      }
    }
    return "/api/widget/config/?tenant=" + encodeURIComponent(tenant);
  }

  function getSessionId() {
    try {
      const existing = window.localStorage.getItem(sessionStorageKey);
      if (existing) {
        return existing;
      }
      const generated = generateSessionId();
      window.localStorage.setItem(sessionStorageKey, generated);
      return generated;
    } catch (error) {
      return generateSessionId();
    }
  }

  function generateSessionId() {
    if (window.crypto && typeof window.crypto.randomUUID === "function") {
      return window.crypto.randomUUID();
    }

    const timestamp = Date.now().toString(16);
    const randomPart = Math.random().toString(16).slice(2, 14);
    return "livia-" + timestamp + "-" + randomPart;
  }

  function injectStyles() {
    if (document.getElementById("livia-widget-styles")) {
      return;
    }

    const style = document.createElement("style");
    style.id = "livia-widget-styles";
    style.textContent = [
      "#livia-launcher { position: fixed; right: 20px; bottom: 20px; z-index: 2147483000; border: 0; border-radius: 999px; padding: 12px 18px; cursor: pointer; background: var(--livia-primary, #2563eb); color: #fff; box-shadow: 0 12px 30px rgba(15, 23, 42, .25); font: 600 14px/1.2 Arial, sans-serif; }",
      "#livia-launcher.livia-hidden { display: none; }",
      "#livia-panel { position: fixed; right: 20px; bottom: 76px; width: min(360px, calc(100vw - 40px)); height: 520px; max-height: calc(100vh - 110px); z-index: 2147483000; display: none; flex-direction: column; background: #fff; border: 1px solid rgba(15, 23, 42, .12); border-radius: 18px; box-shadow: 0 18px 45px rgba(15, 23, 42, .2); overflow: hidden; font: 14px/1.4 Arial, sans-serif; }",
      "#livia-panel.livia-open { display: flex; }",
      ".livia-bottom-left#livia-launcher { left: 20px; right: auto; }",
      ".livia-bottom-left#livia-panel { left: 20px; right: auto; }",
      ".livia-bottom-right#livia-launcher { right: 20px; left: auto; }",
      ".livia-bottom-right#livia-panel { right: 20px; left: auto; }",
      "@media (max-width: 480px) { #livia-launcher { right: 16px; bottom: 16px; } #livia-panel { right: 16px; bottom: 72px; width: calc(100vw - 32px); max-height: calc(100vh - 96px); } .livia-bottom-left#livia-launcher { left: 16px; right: auto; } .livia-bottom-left#livia-panel { left: 16px; right: auto; } .livia-bottom-right#livia-launcher { right: 16px; left: auto; } .livia-bottom-right#livia-panel { right: 16px; left: auto; } }",
      "#livia-header { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 14px 16px; background: var(--livia-primary, #2563eb); color: #fff; }",
      "#livia-header strong { font-size: 15px; }",
      "#livia-close { border: 0; background: transparent; color: #fff; font-size: 24px; line-height: 1; cursor: pointer; padding: 0; }",
      "#livia-messages { flex: 1; overflow-y: auto; padding: 14px; background: #f8fafc; display: flex; flex-direction: column; gap: 10px; }",
      ".livia-message { max-width: 85%; padding: 10px 12px; border-radius: 14px; white-space: pre-wrap; word-break: break-word; }",
      ".livia-message.user { align-self: flex-end; background: var(--livia-primary, #2563eb); color: #fff; border-bottom-right-radius: 6px; }",
      ".livia-message.assistant { align-self: flex-start; background: #fff; color: #0f172a; border: 1px solid rgba(15, 23, 42, .08); border-bottom-left-radius: 6px; }",
      ".livia-message.system { align-self: center; background: transparent; color: #64748b; font-size: 12px; padding: 0; }",
      "#livia-branding { color: #64748b; font-size: 11px; padding: 0 12px 10px; text-align: center; background: #fff; }",
      "#livia-footer { display: flex; gap: 8px; padding: 12px; border-top: 1px solid rgba(15, 23, 42, .08); background: #fff; }",
      "#livia-input { flex: 1; border: 1px solid rgba(15, 23, 42, .15); border-radius: 12px; padding: 10px 12px; outline: none; font: inherit; min-width: 0; }",
      "#livia-send { border: 0; border-radius: 12px; padding: 10px 14px; background: var(--livia-primary, #2563eb); color: #fff; cursor: pointer; font: 600 14px/1 Arial, sans-serif; }",
      "#livia-send:disabled, #livia-input:disabled { opacity: .65; cursor: not-allowed; }",
      "#livia-typing { align-self: flex-start; color: #64748b; font-size: 12px; padding: 2px 4px; }"
    ].join("\n");
    document.head.appendChild(style);
  }

  function createButton(label, id) {
    const button = document.createElement("button");
    button.id = id;
    button.type = "button";
    button.textContent = label;
    return button;
  }

  function createMessageElement(role, text) {
    const bubble = document.createElement("div");
    bubble.className = "livia-message " + role;
    bubble.textContent = text;
    return bubble;
  }

  function createTypingIndicator() {
    const typing = document.createElement("div");
    typing.id = "livia-typing";
    typing.textContent = "Digitando...";
    return typing;
  }

  function appendMessage(messagesEl, role, text) {
    const message = createMessageElement(role, text);
    messagesEl.appendChild(message);
    messagesEl.scrollTop = messagesEl.scrollHeight;
    return message;
  }

  function setLoading(inputEl, sendButton, loading) {
    inputEl.disabled = loading;
    sendButton.disabled = loading;
    sendButton.textContent = loading ? "..." : "Enviar";
  }

  function isHexColor(value) {
    return /^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/.test(String(value || ""));
  }

  function buildWidget() {
    injectStyles();

    const launcher = createButton(defaultConfig.launcher_label, "livia-launcher");
    const panel = document.createElement("div");
    panel.id = "livia-panel";

    const header = document.createElement("div");
    header.id = "livia-header";

    const title = document.createElement("strong");
    title.textContent = defaultConfig.widget_title;

    const closeButton = createButton("×", "livia-close");

    const messages = document.createElement("div");
    messages.id = "livia-messages";

    const footer = document.createElement("form");
    footer.id = "livia-footer";
    footer.autocomplete = "off";

    const input = document.createElement("input");
    input.id = "livia-input";
    input.type = "text";
    input.placeholder = defaultConfig.placeholder_text;
    input.setAttribute("aria-label", "Mensagem para a Lívia");

    const sendButton = createButton("Enviar", "livia-send");
    sendButton.type = "submit";

    const branding = document.createElement("div");
    branding.id = "livia-branding";
    branding.textContent = "Atendimento por Lívia";

    const sessionId = getSessionId();
    let assistantName = defaultConfig.assistant_name;
    let typingIndicator = null;
    let isOpen = false;
    let widgetEnabled = true;

    function applyConfig(rawConfig) {
      const config = Object.assign({}, defaultConfig, rawConfig || {});
      const color = isHexColor(config.primary_color) ? config.primary_color : defaultConfig.primary_color;
      const position = config.position === "bottom_left" ? "bottom_left" : "bottom_right";
      const positionClass = position === "bottom_left" ? "livia-bottom-left" : "livia-bottom-right";
      const otherPositionClass = position === "bottom_left" ? "livia-bottom-right" : "livia-bottom-left";

      document.documentElement.style.setProperty("--livia-primary", color);
      launcher.classList.remove(otherPositionClass);
      panel.classList.remove(otherPositionClass);
      launcher.classList.add(positionClass);
      panel.classList.add(positionClass);

      assistantName = String(config.assistant_name || defaultConfig.assistant_name).trim() || defaultConfig.assistant_name;
      title.textContent = String(config.widget_title || assistantName).trim() || assistantName;
      launcher.textContent = String(config.launcher_label || defaultConfig.launcher_label).trim() || defaultConfig.launcher_label;
      input.placeholder = String(config.placeholder_text || defaultConfig.placeholder_text).trim() || defaultConfig.placeholder_text;

      const firstMessage = messages.querySelector(".livia-message.assistant");
      const initialMessage = String(config.initial_message || defaultConfig.initial_message).trim() || defaultConfig.initial_message;
      if (firstMessage) {
        firstMessage.textContent = initialMessage;
      }

      branding.style.display = config.show_branding === false ? "none" : "block";
      widgetEnabled = config.is_widget_enabled !== false;
      if (!widgetEnabled) {
        launcher.classList.add("livia-hidden");
        closePanel();
      } else {
        launcher.classList.remove("livia-hidden");
      }
    }

    function updateAssistantProfile(data) {
      const nextName = String((data && data.assistant_name) || "").trim();
      if (nextName) {
        assistantName = nextName;
      }
    }

    function openPanel() {
      if (!widgetEnabled) {
        return;
      }
      panel.classList.add("livia-open");
      isOpen = true;
      input.focus();
    }

    function closePanel() {
      panel.classList.remove("livia-open");
      isOpen = false;
    }

    function togglePanel() {
      if (isOpen) {
        closePanel();
        return;
      }
      openPanel();
    }

    function ensureTyping() {
      if (!typingIndicator) {
        typingIndicator = createTypingIndicator();
        messages.appendChild(typingIndicator);
        messages.scrollTop = messages.scrollHeight;
      }
    }

    function removeTyping() {
      if (typingIndicator && typingIndicator.parentNode) {
        typingIndicator.parentNode.removeChild(typingIndicator);
      }
      typingIndicator = null;
    }

    async function loadConfig() {
      try {
        const response = await fetch(configUrl, { method: "GET" });
        if (!response.ok) {
          return;
        }
        const data = await response.json().catch(function () {
          return {};
        });
        applyConfig(data);
      } catch (error) {
        applyConfig(defaultConfig);
      }
    }

    async function sendMessage(rawMessage) {
      const message = String(rawMessage || "").trim();
      if (!message || !widgetEnabled) {
        return;
      }

      appendMessage(messages, "user", message);
      input.value = "";
      setLoading(input, sendButton, true);
      ensureTyping();

      try {
        const response = await fetch(apiUrl, {
          method: "POST",
          headers: {
            "Content-Type": "application/json"
          },
          body: JSON.stringify({
            tenant: tenant,
            session_key: sessionId,
            session_id: sessionId,
            message: message,
            source_page: window.location.href
          })
        });

        const data = await response.json().catch(function () {
          return {};
        });

        removeTyping();
        updateAssistantProfile(data);

        if (!response.ok) {
          appendMessage(messages, "assistant", data.error || "Não consegui responder agora. Tente novamente em instantes.");
          return;
        }

        appendMessage(messages, "assistant", data.reply || "Recebi sua mensagem.");
      } catch (error) {
        removeTyping();
        appendMessage(messages, "assistant", "Houve um problema ao conectar com a Lívia. Tente novamente.");
      } finally {
        setLoading(input, sendButton, false);
      }
    }

    closeButton.addEventListener("click", closePanel);
    launcher.addEventListener("click", togglePanel);
    footer.addEventListener("submit", function (event) {
      event.preventDefault();
      sendMessage(input.value);
    });
    input.addEventListener("keydown", function (event) {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        sendMessage(input.value);
      }
    });

    header.appendChild(title);
    header.appendChild(closeButton);
    footer.appendChild(input);
    footer.appendChild(sendButton);
    panel.appendChild(header);
    panel.appendChild(messages);
    panel.appendChild(branding);
    panel.appendChild(footer);

    document.body.appendChild(launcher);
    document.body.appendChild(panel);

    appendMessage(messages, "assistant", defaultConfig.initial_message);
    applyConfig(defaultConfig);
    loadConfig();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", buildWidget);
  } else {
    buildWidget();
  }
})();"""
    return HttpResponse(
        content,
        content_type="application/javascript; charset=utf-8",
    )


def widget_config(request):
    tenant_slug = request.GET.get("tenant", "").strip()
    return JsonResponse(build_widget_config_for_tenant_slug(tenant_slug))


def demo_page(request):
    content = """<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Demo da Lívia</title>
</head>
<body style="font-family: Arial, sans-serif; margin: 0; min-height: 100vh; background: #f8fafc;">
  <main style="max-width: 900px; margin: 0 auto; padding: 48px 24px;">
    <h1>Demo da Lívia</h1>
    <p>Use esta página para testar o widget localmente.</p>
  </main>
  <script src="/widget.js" data-tenant="smart-control-brasil"></script>
</body>
</html>
"""
    return HttpResponse(content, content_type="text/html; charset=utf-8")
