# Bot de atención BHD (bot-bhd)

Bot que responde consultas de clientes de **Brothers Home & Deco (BHD)** — tienda de muebles y deco — por WhatsApp / Instagram / Facebook, usando la **API de Claude**. Está pensado para engancharse al **Salesbot de Kommo**.

> Este archivo es la guía del proyecto para quien lo continúe con Claude Code. Es el traspaso del bot: cómo correrlo, cómo "entrenarlo", cómo se despliega y qué NO tocar.

---

## Qué hace

- Kommo (u otro sistema) le manda un mensaje del cliente a `POST /api/responder` y el bot devuelve `{respuesta, derivar, motivo}`:
  - `respuesta`: el texto para enviarle al cliente (corto, estilo WhatsApp).
  - `derivar`: `true` si la conversación debería pasar a una persona del equipo.
  - `motivo`: si deriva, una nota breve para el equipo.
- Mantiene el **historial de cada conversación** por `lead_id` (últimos 12 mensajes) para no perder contexto.

## Arquitectura rápida

- **1 solo archivo principal:** [`app.py`](app.py) (Flask). No hay módulos aparte.
- **Base de datos:** SQLite local (`bot.db`) o PostgreSQL en Railway (variable `DATABASE_URL`). Tablas: `mensajes` (historial) y `config` (token de Tienda Nube).
- **Frontend:** solo `templates/probar.html`, el chat de prueba.
- **Modelo:** Claude, id en la variable `CLAUDE_MODEL` (por defecto `claude-opus-4-8`). Usa **salida estructurada** (JSON schema `ESQUEMA_RESPUESTA`) y **prompt caching** en dos bloques.

## Cómo correrlo localmente

1. Tener Python instalado y las deps: `pip install -r requirements.txt`
2. Necesita una **API key de Anthropic**. Dos formas (cualquiera sirve):
   - Variable de entorno `ANTHROPIC_API_KEY`, o
   - Un archivo `apikey.txt` al lado de `app.py` con la key (está **gitignoreado**, no se sube).
3. Correr: `python app.py` (o doble clic en `iniciar.bat` en Windows). Levanta en el puerto **5004**.
4. Abrir el **chat de prueba**: `http://localhost:5004/probar?clave=bot123`
   - `bot123` es la `BOT_KEY` por defecto en local. En producción es otra (variable `BOT_KEY` en Railway).

## Cómo se "entrena" el bot  ⭐ (lo más importante)

El bot **no aprende solo**. Se ajusta editando su instructivo y probándolo:

1. **El conocimiento y las reglas** viven en la constante **`CONOCIMIENTO`** dentro de [`app.py`](app.py) (bloque de texto grande, arranca en la sección "Conocimiento del negocio"). Ahí está: tono de respuesta, ubicación y horarios, formas de pago, datos de transferencia, envíos, y **cuándo derivar a un humano**. Para enseñarle algo nuevo o corregir una respuesta, se edita ese texto.
2. **Probar el cambio:** reiniciar `app.py` y chatear en `/probar?clave=...` como si fueras un cliente. Ver la respuesta y si `derivar` se activa cuando corresponde.
3. Iterar: ajustar el texto de `CONOCIMIENTO`, volver a probar.

### Lo que el bot toma solo (NO se carga a mano)

- **Catálogo con precios, variantes y links:** se lee de la **Tienda Nube** de BHD automáticamente (`contexto_productos()`, refresco cada 10 min).
- **Disponibilidad de stock:** se lee de **deposito-app** (`contexto_stock()`, refresco cada 10 min).
- **Regla de oro:** los **precios/links** salen de la web (Tienda Nube), pero **si hay o no hay stock lo dice EXCLUSIVAMENTE el depósito**. El stock de la web se ignora a propósito porque contradecía al del depósito y confundía al bot. No cambiar esta regla sin entender por qué está.

## Despliegue (Railway)

- Al hacer **`git push` a `main`**, Railway **despliega solo** (auto-deploy conectado al repo).
- Corre con gunicorn (ver `Procfile`).
- **Variables de entorno en Railway** (no van en el repo):
  - `ANTHROPIC_API_KEY` — la key de Claude.
  - `BOT_KEY` — clave para autorizar `/api/responder` y `/probar` (en prod NO es `bot123`).
  - `DATABASE_URL` — la da Railway (PostgreSQL).
  - `CLAUDE_MODEL` (opcional) — id del modelo.
  - `DEPOSITO_URL`, `TIENDA_URL` — URLs de origen de datos.
  - `TN_CLIENT_ID`, `TN_CLIENT_SECRET` — app de Tienda Nube (para leer el catálogo).
- URL de producción: `bot-bhd-production.up.railway.app` · chat de prueba en `/probar?clave=<BOT_KEY>`.

## Conexión con Kommo (lo que queda pendiente)

En el Salesbot de Kommo hay que agregar un paso **HTTP** que llame a `POST /api/responder` con `{lead_id, mensaje}` (y la `BOT_KEY` en el header `X-Bot-Key` o como `?clave=`). Después: enviar `{{respuesta}}` al cliente y, si `derivar=true`, etiquetar la conversación y frenar el bot para que la tome una persona.

## Endpoints

- `POST /api/responder` — el principal. Body `{lead_id, mensaje}`, auth por `BOT_KEY`. Devuelve `{respuesta, derivar, motivo}`.
- `GET /probar?clave=<BOT_KEY>` — chat de prueba.
- `GET /tn/conectar?clave=<BOT_KEY>` y `GET /tn/callback` — OAuth de Tienda Nube (para que el bot lea el catálogo). Autorizar **logueado en la cuenta de BHD**.
- `POST /webhooks/tn` — webhooks de privacidad de TN (solo acusa recibo).

## Gotchas / cosas a no romper

- **User-Agent de Tienda Nube:** debe ser simple (`BotBHD/1.0`). Paréntesis o espacios disparan el WAF de Cloudflare de TN y fallan las llamadas.
- **Stock solo del depósito** (ver "Regla de oro" arriba).
- **Nunca commitear secretos:** `apikey.txt`, `bot.db` y `.env` están gitignoreados; mantenerlos así.
- **Prompt caching:** el bloque `CONOCIMIENTO` y el catálogo se cachean (ahorran ~90% de costo en mensajes repetidos). Si se reordenan los bloques `system`, se rompe el caché.
- Ante cualquier error de Claude, el bot **deriva a humano** en vez de dejar al cliente sin respuesta (comportamiento intencional).
