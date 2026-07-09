from django.http import HttpResponse


def widget_js(request):
    content = """(function () {
  const currentScript = document.currentScript;
  const tenant = currentScript ? (currentScript.getAttribute("data-tenant") || "default") : "default";
  const sessionStorageKey = "livia_session_id_" + tenant;
  const apiUrl = resolveApiUrl(currentScript);

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
      "#livia-launcher { position: fixed; left: 20px; bottom: 20px; z-index: 2147483000; border: 0; border-radius: 999px; padding: 12px 18px; cursor: pointer; background: #0f172a; color: #fff; box-shadow: 0 12px 30px rgba(15, 23, 42, .25); font: 600 14px/1.2 Arial, sans-serif; }",
      "#livia-panel { position: fixed; left: 20px; bottom: 76px; width: min(360px, calc(100vw - 40px)); height: 520px; max-height: calc(100vh - 110px); z-index: 2147483000; display: none; flex-direction: column; background: #fff; border: 1px solid rgba(15, 23, 42, .12); border-radius: 18px; box-shadow: 0 18px 45px rgba(15, 23, 42, .2); overflow: hidden; font: 14px/1.4 Arial, sans-serif; }",
      "@media (max-width: 480px) { #livia-launcher { left: 16px; bottom: 16px; } #livia-panel { left: 16px; bottom: 72px; width: calc(100vw - 32px); max-height: calc(100vh - 96px); } }",
      "#livia-panel.livia-open { display: flex; }",
      "#livia-header { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 14px 16px; background: #0f172a; color: #fff; }",
      "#livia-header strong { font-size: 15px; }",
      "#livia-close { border: 0; background: transparent; color: #fff; font-size: 24px; line-height: 1; cursor: pointer; padding: 0; }",
      "#livia-messages { flex: 1; overflow-y: auto; padding: 14px; background: #f8fafc; display: flex; flex-direction: column; gap: 10px; }",
      ".livia-message { max-width: 85%; padding: 10px 12px; border-radius: 14px; white-space: pre-wrap; word-break: break-word; }",
      ".livia-message.user { align-self: flex-end; background: #2563eb; color: #fff; border-bottom-right-radius: 6px; }",
      ".livia-message.assistant { align-self: flex-start; background: #fff; color: #0f172a; border: 1px solid rgba(15, 23, 42, .08); border-bottom-left-radius: 6px; }",
      ".livia-message.system { align-self: center; background: transparent; color: #64748b; font-size: 12px; padding: 0; }",
      "#livia-footer { display: flex; gap: 8px; padding: 12px; border-top: 1px solid rgba(15, 23, 42, .08); background: #fff; }",
      "#livia-input { flex: 1; border: 1px solid rgba(15, 23, 42, .15); border-radius: 12px; padding: 10px 12px; outline: none; font: inherit; }",
      "#livia-send { border: 0; border-radius: 12px; padding: 10px 14px; background: #0f172a; color: #fff; cursor: pointer; font: 600 14px/1 Arial, sans-serif; }",
      "#livia-send:disabled, #livia-input:disabled { opacity: .65; cursor: not-allowed; }",
      "#livia-typing { align-self: flex-start; color: #64748b; font-size: 12px; padding: 2px 4px; }"
    ].join("\\n");
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

  function buildWidget() {
    injectStyles();

    const launcher = createButton("Lívia", "livia-launcher");
    const panel = document.createElement("div");
    panel.id = "livia-panel";

    const header = document.createElement("div");
    header.id = "livia-header";

    const title = document.createElement("strong");
    title.textContent = "Lívia";

    const closeButton = createButton("×", "livia-close");

    const messages = document.createElement("div");
    messages.id = "livia-messages";

    const footer = document.createElement("form");
    footer.id = "livia-footer";
    footer.autocomplete = "off";

    const input = document.createElement("input");
    input.id = "livia-input";
    input.type = "text";
    input.placeholder = "Digite sua mensagem...";
    input.setAttribute("aria-label", "Mensagem para a Lívia");

    const sendButton = createButton("Enviar", "livia-send");
    sendButton.type = "submit";

    const sessionId = getSessionId();
    let assistantName = "Lívia";
    let typingIndicator = null;
    let isOpen = false;

    function updateAssistantProfile(data) {
      const nextName = String((data && data.assistant_name) || "").trim();
      if (nextName && nextName !== assistantName) {
        assistantName = nextName;
        launcher.textContent = assistantName;
        title.textContent = assistantName;
      }
      const initialMessage = String((data && data.initial_message) || "").trim();
      const firstMessage = messages.querySelector(".livia-message.assistant");
      if (initialMessage && firstMessage && firstMessage.textContent !== initialMessage) {
        firstMessage.textContent = initialMessage;
      }
    }

    function openPanel() {
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

    async function sendMessage(rawMessage) {
      const message = String(rawMessage || "").trim();
      if (!message) {
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
    panel.appendChild(footer);

    document.body.appendChild(launcher);
    document.body.appendChild(panel);

    appendMessage(messages, "assistant", "Olá! Sou a Lívia. Como posso te ajudar?");
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
