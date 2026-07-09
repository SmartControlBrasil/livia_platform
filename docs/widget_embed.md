# Embed do widget da Lívia

## Snippet padrão

```html
<script
  src="https://livia.smartcontrolbrasil.com.br/widget.js"
  data-tenant="smart-control-brasil"
  data-api-url="https://livia.smartcontrolbrasil.com.br/api/chat/">
</script>
```

## Smart Control Brasil

```html
<script
  src="https://livia.smartcontrolbrasil.com.br/widget.js"
  data-tenant="smart-control-brasil"
  data-api-url="https://livia.smartcontrolbrasil.com.br/api/chat/">
</script>
```

## Granimármores Pitondo

```html
<script
  src="https://livia.smartcontrolbrasil.com.br/widget.js"
  data-tenant="granimarmores-pitondo"
  data-api-url="https://livia.smartcontrolbrasil.com.br/api/chat/">
</script>
```

## Atributos

`data-tenant` identifica o tenant que receberá a conversa. O valor deve ser o `slug` ativo cadastrado na Lívia Platform.

`data-api-url` define a URL absoluta do endpoint de chat. Em sites externos, prefira informar este atributo para evitar que o navegador chame o domínio do site hospedeiro. Se ele não for informado, o widget usa a origem do próprio `script src` e completa com `/api/chat/`.

Em produção, inclua os domínios autorizados para embed em `LIVIA_ALLOWED_WIDGET_ORIGINS`, separados por vírgula.
