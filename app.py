# ── Bot de atención BHD ───────────────────────────────────────────────────────
# Responde mensajes de clientes (WhatsApp/IG/FB vía Kommo) con Claude.
# Kommo Salesbot llama a POST /api/responder y recibe {respuesta, derivar}:
# manda `respuesta` al cliente y, si `derivar` es true, etiqueta la
# conversación para que la tome un humano.

from flask import Flask, render_template, request, jsonify, redirect
from sqlalchemy import create_engine, text
from datetime import datetime
import os, re, json
import requests as req_lib
import anthropic

app = Flask(__name__)

BOT_KEY       = os.environ.get('BOT_KEY', 'bot123')
CLAUDE_MODEL  = os.environ.get('CLAUDE_MODEL', 'claude-opus-4-8')
DEPOSITO_URL  = os.environ.get('DEPOSITO_URL', 'https://deposito-app-production.up.railway.app').rstrip('/')
TIENDA_URL    = os.environ.get('TIENDA_URL', 'https://www.brothershomedeco.com.ar').rstrip('/')
TN_CLIENT_ID     = os.environ.get('TN_CLIENT_ID', '')
TN_CLIENT_SECRET = os.environ.get('TN_CLIENT_SECRET', '')
# Ojo: paréntesis/espacios en el User-Agent disparan el WAF de Cloudflare de TN
TN_UA         = 'BotBHD/1.0'
HISTORIAL_MAX = 12  # últimos mensajes por conversación que ve el bot

# La clave sale de la variable de entorno ANTHROPIC_API_KEY (Railway) o,
# para pruebas locales, del archivo apikey.txt junto a este script (gitignoreado)
_api_key = os.environ.get('ANTHROPIC_API_KEY', '')
if not _api_key:
    _ruta_clave = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'apikey.txt')
    if os.path.exists(_ruta_clave):
        _api_key = open(_ruta_clave).read().strip()
claude = anthropic.Anthropic(api_key=_api_key) if _api_key else anthropic.Anthropic()

DATABASE_URL = os.environ.get('DATABASE_URL', '')
if DATABASE_URL.startswith('postgres://'):
    DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)
IS_PG = DATABASE_URL.startswith('postgresql')
engine = create_engine(DATABASE_URL if IS_PG else 'sqlite:///bot.db', pool_pre_ping=True)

AUTOINC = 'SERIAL PRIMARY KEY' if IS_PG else 'INTEGER PRIMARY KEY AUTOINCREMENT'
with engine.begin() as conn:
    conn.execute(text(f'''
        CREATE TABLE IF NOT EXISTS mensajes (
            id      {AUTOINC},
            lead_id TEXT NOT NULL,
            rol     TEXT NOT NULL,
            texto   TEXT NOT NULL,
            fecha   TEXT NOT NULL
        )'''))
    conn.execute(text(
        'CREATE INDEX IF NOT EXISTS idx_mensajes_lead ON mensajes (lead_id, id)'))
    conn.execute(text('''
        CREATE TABLE IF NOT EXISTS config (
            key   TEXT PRIMARY KEY,
            value TEXT
        )'''))


def _now():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


# ── Conocimiento del negocio (parte ESTÁTICA — se cachea en la API) ───────────

CONOCIMIENTO = """Sos el asistente virtual de Brothers Home & Deco (BHD), tienda argentina de muebles y decoración.

# Tu forma de responder
- Tono cálido y cercano, español argentino (vos/podés). Emojis con moderación (1-2 por mensaje).
- Respuestas CORTAS, aptas para WhatsApp: 2 a 5 líneas. Sin títulos ni listas largas.
- Respondé SOLO con la información de este documento, el catálogo web y la lista de stock provistos. NUNCA inventes precios, medidas, stock ni promociones.
- Precios: usá el catálogo web provisto. Son precios de lista; podés calcular y ofrecer el precio con descuento (30% efectivo, 20% transferencia). Escribí los precios con formato argentino ($39.999).
- Cuando un producto le interese al cliente, compartile el link directo del catálogo.
- Si un producto no está en el catálogo web ni en la lista de stock, decí que no lo encontrás y ofrecé derivar con un asesor.
- Si el cliente ya te saludó antes en la conversación, no vuelvas a saludar.

# Ubicación y horarios
Showroom: José Hernández 5070, Munro, Buenos Aires.
Horarios: lunes a viernes de 9 a 12 hs y de 13 a 17 hs; sábados de 10 a 17 hs.
También se puede retirar por el depósito en Munro, Vicente López.

# Productos
Catálogo completo en www.brothershomedeco.com.ar (muebles y deco).

# Formas de pago
- Tarjeta: precio de lista en 3 y 6 cuotas sin interés.
- EFECTIVO: 30% de descuento sobre el precio de lista, abonando en el local — o, si es CABA/Zona Norte y alrededores con nuestro flete de confianza, a contra entrega. (En Cyber Brothers el descuento aplica únicamente abonando el total en el local.)
- TRANSFERENCIA BANCARIA: 20% de descuento sobre el precio de lista.
- Motomensajería: únicamente por transferencia o tarjeta (no se toman pagos al momento de la entrega).

# Datos para transferencia
Titular: Tamara Ayelen Martin — Mercado Pago
CVU: 0000003100030370818262
Alias: BROTHERSHOMEDECO.
CUIT/CUIL: 27404592063
(Compartir estos datos solo cuando el cliente confirma que quiere pagar por transferencia.)

# Envíos
Productos pequeños:
- CABA, Zona Norte, Zona Sur, Zona Oeste y alrededores: motomensajería, costo según zona.
- Interior del país: por correo; el costo se calcula con el código postal.
Productos grandes (muebles):
- CABA, Zona Norte/Sur/Oeste y alrededores: flete de confianza; se abona en efectivo al recibir; entrega hasta puerta o hall.
- Interior: transporte a cargo del comprador — el cliente elige y contrata el transporte de su preferencia y nos avisa cuál para coordinar el despacho. El pedido se entrega preparado y listo para enviar; el traslado queda a cargo del transporte (suelen tener seguro por el valor de la mercadería).
Más info: www.brothershomedeco.com.ar/entregas
También pueden retirar por el depósito en Munro.

# Contacto humano
WhatsApp: https://wa.me/5491122548842

# Cuándo derivar a un humano (derivar=true)
- Reclamos, productos dañados, devoluciones o cambios.
- Cliente enojado o frustrado.
- Consultas sobre un pedido YA REALIZADO (estado, demora, factura).
- Negociación de precios o descuentos especiales.
- Cualquier cosa que no puedas responder con seguridad con la información disponible.
Al derivar: avisale amablemente que lo va a atender una persona del equipo a la brevedad."""


# ── Tienda Nube (catálogo web con precios y links) ────────────────────────────

def cfg_get(key):
    with engine.connect() as conn:
        row = conn.execute(text('SELECT value FROM config WHERE key=:k'), {'k': key}).fetchone()
    return row[0] if row else ''

def cfg_set(key, value):
    with engine.begin() as conn:
        if IS_PG:
            conn.execute(text('INSERT INTO config (key, value) VALUES (:k, :v) '
                              'ON CONFLICT (key) DO UPDATE SET value=:v'), {'k': key, 'v': value})
        else:
            conn.execute(text('INSERT OR REPLACE INTO config (key, value) VALUES (:k, :v)'),
                         {'k': key, 'v': value})

def tn_headers():
    return {'Authentication': f"bearer {cfg_get('tn_token')}",
            'User-Agent': TN_UA, 'Content-Type': 'application/json'}

def tn_base():
    return f"https://api.tiendanube.com/v1/{cfg_get('tn_store')}"


@app.route('/tn/conectar')
def tn_conectar():
    if request.args.get('clave') != BOT_KEY:
        return 'No autorizado', 401
    if not TN_CLIENT_ID:
        return 'Falta configurar TN_CLIENT_ID.', 500
    return redirect(f'https://www.tiendanube.com/apps/{TN_CLIENT_ID}/authorize')


@app.route('/tn/callback')
def tn_callback():
    code = request.args.get('code')
    if not code:
        return 'Error: Tienda Nube no devolvió el código.', 400
    r = req_lib.post('https://www.tiendanube.com/apps/authorize/token', json={
        'client_id': TN_CLIENT_ID, 'client_secret': TN_CLIENT_SECRET,
        'grant_type': 'authorization_code', 'code': code,
    }, headers={'User-Agent': TN_UA})
    data = r.json()
    if 'access_token' not in data:
        return f'Error al obtener token: {data}', 500
    cfg_set('tn_token', data['access_token'])
    cfg_set('tn_store', str(data.get('user_id', '')))
    _productos_cache['ts'] = 0  # forzar recarga del catálogo
    return ('✅ Tienda Nube conectada. El bot ya conoce el catálogo con precios. '
            'Podés cerrar esta pestaña.')


@app.route('/webhooks/tn', methods=['POST'])
def webhook_tn():
    # Webhooks de privacidad de TN (store/redact etc.): acusar recibo alcanza,
    # el bot no guarda datos personales de clientes de la tienda
    return jsonify({'ok': True})


_productos_cache = {'texto': '', 'ts': 0}

def _sin_html(s):
    return re.sub(r'<[^>]+>', ' ', s or '').replace('&nbsp;', ' ').strip()

def contexto_productos():
    """Catálogo web: nombre, precio de lista, variantes con stock y link."""
    ahora = datetime.now().timestamp()
    if _productos_cache['texto'] and ahora - _productos_cache['ts'] < 600:
        return _productos_cache['texto']
    if not cfg_get('tn_token'):
        return ('# Catálogo web\nNo disponible. Ante consultas de precios, '
                'indicá que pueden verlos en ' + TIENDA_URL)
    try:
        lineas, page = [], 1
        while page <= 10:
            r = req_lib.get(f'{tn_base()}/products', headers=tn_headers(),
                            params={'per_page': 100, 'page': page,
                                    'published': 'true'}, timeout=30)
            if r.status_code != 200:
                break
            prods = r.json()
            if not prods:
                break
            for p in prods:
                nombre = (p.get('name') or {}).get('es', '').strip()
                if not nombre:
                    continue
                handle = (p.get('handle') or {}).get('es', '')
                link = f'{TIENDA_URL}/productos/{handle}/' if handle else TIENDA_URL
                variantes = []
                precios = []
                for v in (p.get('variants') or []):
                    try:
                        precio = float(v.get('promotional_price') or v.get('price') or 0)
                    except (TypeError, ValueError):
                        precio = 0
                    if precio > 0:
                        precios.append(precio)
                    color = ' '.join((val.get('es') or '') for val in (v.get('values') or [])).strip()
                    stock = v.get('stock')  # None = sin control de stock (hay)
                    con_stock = (stock is None) or (stock or 0) > 0
                    if color:
                        variantes.append(f"{color}{'' if con_stock else ' (sin stock web)'}")
                precio_txt = f"${min(precios):,.0f}".replace(',', '.') if precios else 's/precio'
                var_txt = f" | variantes: {', '.join(variantes)}" if variantes else ''
                lineas.append(f'- {nombre}: {precio_txt}{var_txt} | {link}')
            page += 1
        if lineas:
            _productos_cache['texto'] = (
                '# Catálogo web con precios de lista (a estos precios aplican los '
                'descuentos por forma de pago). Compartí el link del producto '
                'cuando ayude al cliente.\n' + '\n'.join(lineas))
            _productos_cache['ts'] = ahora
    except Exception as ex:
        print(f'  Aviso: no se pudo leer catálogo TN ({ex})')
    return _productos_cache['texto'] or ('# Catálogo web\nNo disponible ahora. '
                                         'Ante consultas de precios, indicá ' + TIENDA_URL)


# ── Stock real desde deposito-app (parte DINÁMICA — se refresca cada 10 min) ──

_stock_cache = {'texto': '', 'ts': 0}

def contexto_stock():
    ahora = datetime.now().timestamp()
    if _stock_cache['texto'] and ahora - _stock_cache['ts'] < 600:
        return _stock_cache['texto']
    try:
        prods = req_lib.get(f'{DEPOSITO_URL}/api/proyecciones', timeout=15).json()
        lineas = []
        for p in prods:
            total = (p.get('stock_deposito') or 0) + (p.get('stock_separado') or 0) + (p.get('stock_full') or 0)
            nombre = f"{p.get('nombre', '')} {p.get('color') or ''}".strip()
            lineas.append(f"- {nombre}: {'HAY STOCK' if total > 0 else 'SIN STOCK (consultar reposición)'}")
        _stock_cache['texto'] = (
            '# Disponibilidad actual de stock (solo productos de esta lista)\n'
            + '\n'.join(lineas)
        )
        _stock_cache['ts'] = ahora
    except Exception as ex:
        print(f'  Aviso: no se pudo leer stock ({ex})')
        if not _stock_cache['texto']:
            _stock_cache['texto'] = ('# Disponibilidad de stock\n'
                                     'No disponible en este momento: ante consultas de stock, derivá a un humano.')
    return _stock_cache['texto']


# ── Claude ────────────────────────────────────────────────────────────────────

ESQUEMA_RESPUESTA = {
    "type": "object",
    "properties": {
        "respuesta": {
            "type": "string",
            "description": "El mensaje para enviar al cliente, corto y apto para WhatsApp."
        },
        "derivar": {
            "type": "boolean",
            "description": "true si la conversación debe pasar a un humano."
        },
        "motivo": {
            "type": "string",
            "description": "Si derivar=true, motivo breve para el equipo (ej: 'reclamo por producto dañado'). Si no, string vacío."
        }
    },
    "required": ["respuesta", "derivar", "motivo"],
    "additionalProperties": False
}


def responder_con_claude(lead_id, mensaje):
    # Historial de la conversación
    with engine.connect() as conn:
        rows = conn.execute(text('''
            SELECT rol, texto FROM (
                SELECT id, rol, texto FROM mensajes
                WHERE lead_id=:l ORDER BY id DESC LIMIT :n
            ) sub ORDER BY id
        '''), {'l': str(lead_id), 'n': HISTORIAL_MAX}).fetchall()

    historial = [{'role': r[0], 'content': r[1]} for r in rows]
    messages = historial + [{'role': 'user', 'content': mensaje}]

    response = claude.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=1000,
        system=[
            # Bloque estático: se cachea (~90% menos costo en llamadas repetidas)
            {"type": "text", "text": CONOCIMIENTO,
             "cache_control": {"type": "ephemeral"}},
            # Catálogo web (precios/links): cambia cada 10 min, con su propio
            # punto de caché para no pagarlo entero en cada mensaje
            {"type": "text", "text": contexto_productos(),
             "cache_control": {"type": "ephemeral"}},
            # Stock físico del depósito, cambia cada 10 min
            {"type": "text", "text": contexto_stock()},
        ],
        messages=messages,
        output_config={"format": {"type": "json_schema", "schema": ESQUEMA_RESPUESTA}},
    )

    if response.stop_reason == "refusal":
        return {"respuesta": "Enseguida te va a atender una persona del equipo 😊",
                "derivar": True, "motivo": "el asistente no pudo responder"}

    texto = next(b.text for b in response.content if b.type == "text")
    return json.loads(texto)


# ── API ───────────────────────────────────────────────────────────────────────

@app.route('/api/responder', methods=['POST'])
def api_responder():
    d = request.get_json(silent=True) or {}
    clave = request.headers.get('X-Bot-Key') or request.args.get('clave') or d.get('clave') or ''
    if clave != BOT_KEY:
        return jsonify({'error': 'No autorizado'}), 401

    lead_id = str(d.get('lead_id') or 'sin-lead')
    mensaje = (d.get('mensaje') or '').strip()
    if not mensaje:
        return jsonify({'error': 'Falta el mensaje'}), 400

    try:
        r = responder_con_claude(lead_id, mensaje)
    except Exception as ex:
        print(f'ERROR Claude: {ex}')
        # Ante cualquier falla: derivar a humano, nunca dejar al cliente colgado
        r = {'respuesta': 'Dame un minuto que te contacto con una persona del equipo 😊',
             'derivar': True, 'motivo': f'error del bot: {str(ex)[:120]}'}

    with engine.begin() as conn:
        conn.execute(text(
            'INSERT INTO mensajes (lead_id, rol, texto, fecha) VALUES (:l, :r, :t, :f)'),
            {'l': lead_id, 'r': 'user', 't': mensaje, 'f': _now()})
        conn.execute(text(
            'INSERT INTO mensajes (lead_id, rol, texto, fecha) VALUES (:l, :r, :t, :f)'),
            {'l': lead_id, 'r': 'assistant', 't': r['respuesta'], 'f': _now()})

    return jsonify({
        'respuesta': r.get('respuesta', ''),
        'derivar': bool(r.get('derivar')),
        'motivo': r.get('motivo', ''),
    })


@app.route('/probar')
def probar():
    if request.args.get('clave') != BOT_KEY:
        return 'No autorizado. Agregá ?clave=... a la URL.', 401
    return render_template('probar.html', clave=BOT_KEY, modelo=CLAUDE_MODEL)


@app.route('/')
def home():
    return 'Bot de atención BHD — OK'


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5004))
    print(f'\n  Bot BHD corriendo en: http://localhost:{port}/probar?clave={BOT_KEY}\n')
    app.run(debug=False, host='0.0.0.0', port=port)
