import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
from fpdf import FPDF


def _txt_pdf(texto):
    """Sanitiza caracteres fuera de Latin-1 para las fuentes estándar de FPDF."""
    if texto is None:
        return ""
    s = str(texto)
    reemplazos = {
        "’": "'", "‘": "'", "“": '"', "”": '"',
        "–": "-", "—": "-", "•": "-", "°": "°"
    }
    for k, v in reemplazos.items():
        s = s.replace(k, v)
    return s.encode("latin-1", "ignore").decode("latin-1")


def _fmt_moneda(valor):
    """Formatea números al estilo argentino ($1.250,00)."""
    val = float(valor or 0)
    txt = f"{val:,.2f}"
    txt = txt.replace(",", "X").replace(".", ",").replace("X", ".")
    return f"${txt}"


def _limpiar_val(val):
    if pd.isna(val) or val is None or str(val).strip().upper() in ["NAN", "NONE", "NULL"]:
        return ""
    return str(val).strip()


def _obtener_display_cliente(row):
    razon = _limpiar_val(row.get("Razón Social"))
    nombre = _limpiar_val(row.get("Nombre"))
    apellido = _limpiar_val(row.get("Apellido"))
    tel = _limpiar_val(row.get("Telefono")).replace(".0", "")
    id_c = row.get("ID_Cliente")
    txt_tel = f" ({tel})" if tel else ""

    if razon:
        nombre_completo = f"{nombre} {apellido}".strip()
        if nombre_completo:
            return f"{razon.upper()} | {nombre_completo.upper()}{txt_tel} - ID: {id_c}"
        return f"{razon.upper()}{txt_tel} - ID: {id_c}"
    else:
        nombre_completo = f"{nombre} {apellido}".strip()
        if not nombre_completo:
            nombre_completo = "SIN NOMBRE"
        return f"{nombre_completo.upper()}{txt_tel} - ID: {id_c}"


def generar_pdf_presupuesto(datos):
    """Genera el PDF replicando el diseño corporativo y devuelve bytes."""
    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=False)
    pdf.add_page()

    # Paleta de colores del diseño
    c_oscuro = (58, 58, 58)        # #3A3A3A Gris carbón
    c_dorado = (196, 160, 39)      # #C4A027 Dorado / Mostaza
    c_gris_texto = (110, 110, 110) # #6E6E6E Gris medio
    c_negro = (35, 35, 35)         # #232323 Texto principal
    c_fila_alt = (242, 242, 242)   # #F2F2F2 Fila alternada

    margen_izq = 18
    ancho_util = 174

    # 1. ENCABEZADO SUPERIOR
    pdf.set_fill_color(*c_oscuro)
    pdf.rect(margen_izq, 0, 74, 38, style="F")

    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 14)
    pdf.set_xy(margen_izq + 5, 12)
    pdf.cell(64, 7, _txt_pdf("PAÑALERA MOLDES"), align="C")

    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(215, 215, 215)
    pdf.set_xy(margen_izq + 5, 20)
    pdf.cell(64, 5, _txt_pdf("ARTÍCULOS PARA BEBÉS Y NIÑOS"), align="C")

    pdf.set_text_color(*c_negro)
    pdf.set_font("Helvetica", "B", 21)
    pdf.set_xy(105, 13)
    pdf.cell(87, 9, _txt_pdf("PRESUPUESTO"), align="R")

    pdf.set_font("Helvetica", "", 12)
    pdf.set_text_color(*c_gris_texto)
    pdf.set_xy(105, 23)
    pdf.cell(87, 6, _txt_pdf(f"N° {datos['numero']}"), align="R")

    # 2. BLOQUE CLIENTE Y FECHAS
    y_cli = 48
    pdf.set_font("Helvetica", "B", 8.5)
    pdf.set_text_color(*c_dorado)
    pdf.set_xy(margen_izq, y_cli)
    pdf.cell(95, 5, _txt_pdf("CLIENTE"))

    pdf.set_font("Helvetica", "B", 15)
    pdf.set_text_color(*c_negro)
    pdf.set_xy(margen_izq, y_cli + 5)
    pdf.cell(105, 7, _txt_pdf(datos["cliente_nombre"]))

    if datos.get("cliente_detalle"):
        pdf.set_font("Helvetica", "", 9.5)
        pdf.set_text_color(*c_gris_texto)
        pdf.set_xy(margen_izq, y_cli + 12.5)
        pdf.cell(105, 5, _txt_pdf(datos["cliente_detalle"]))

    pdf.set_font("Helvetica", "B", 9.5)
    pdf.set_text_color(*c_negro)
    pdf.set_xy(125, y_cli + 5)
    pdf.cell(67, 5.5, _txt_pdf(f"Fecha: {datos['fecha_str']}"), align="R")

    pdf.set_xy(125, y_cli + 11)
    pdf.cell(67, 5.5, _txt_pdf(f"Vencimiento: {datos['vencimiento_str']}"), align="R")

    # 3. PERSONA DE CONTACTO Y DATOS DE PAGO
    y_cont = 72
    pdf.set_font("Helvetica", "B", 8.5)
    pdf.set_text_color(*c_dorado)
    pdf.set_xy(margen_izq, y_cont)
    pdf.cell(90, 5, _txt_pdf("PERSONA DE CONTACTO"))

    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(*c_negro)
    tel_cli = datos.get("cliente_telefono") or "-"
    pdf.set_xy(margen_izq, y_cont + 5.5)
    pdf.cell(90, 5, _txt_pdf(f"Teléfono: {tel_cli}"))

    if datos.get("cliente_email"):
        pdf.set_xy(margen_izq, y_cont + 10.5)
        pdf.cell(90, 5, _txt_pdf(f"Email: {datos['cliente_email']}"))

    pdf.set_font("Helvetica", "B", 8.5)
    pdf.set_text_color(*c_dorado)
    pdf.set_xy(115, y_cont)
    pdf.cell(77, 5, _txt_pdf("DATOS DE PAGO"), align="R")

    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(*c_negro)
    lineas_pago = [l.strip() for l in str(datos.get("datos_pago", "")).split("\n") if l.strip()]
    for idx_p, l_pago in enumerate(lineas_pago[:2]):
        pdf.set_xy(105, y_cont + 5.5 + (idx_p * 5))
        pdf.cell(87, 5, _txt_pdf(l_pago), align="R")

    # 4. TABLA DE ARTÍCULOS
    y_tabla = 95
    w_cols = [14, 84, 26, 22, 28]
    headers = ["#", "DESCRIPCIÓN", "PRECIO", "CANTIDAD", "TOTAL"]

    pdf.set_fill_color(*c_oscuro)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 8.5)
    pdf.set_xy(margen_izq, y_tabla)
    for w, h in zip(w_cols, headers):
        pdf.cell(w, 9, _txt_pdf(h), border=0, align="C", fill=True)

    y_actual = y_tabla + 9
    alto_fila = 8.5
    items = datos.get("items", [])

    for idx, item in enumerate(items, start=1):
        if y_actual > 215:
            pdf.add_page()
            y_actual = 20
            pdf.set_fill_color(*c_oscuro)
            pdf.set_text_color(255, 255, 255)
            pdf.set_font("Helvetica", "B", 8.5)
            pdf.set_xy(margen_izq, y_actual)
            for w, h in zip(w_cols, headers):
                pdf.cell(w, 9, _txt_pdf(h), border=0, align="C", fill=True)
            y_actual += 9

        if idx % 2 == 0:
            pdf.set_fill_color(*c_fila_alt)
        else:
            pdf.set_fill_color(255, 255, 255)

        pdf.set_text_color(*c_negro)
        pdf.set_font("Helvetica", "", 9)
        pdf.set_xy(margen_izq, y_actual)

        desc_corta = _txt_pdf(item.get("nombre", ""))[:46]
        precio_txt = _fmt_moneda(item.get("precio", 0))
        cant_txt = str(int(item.get("cantidad", 1)))
        sub_txt = _fmt_moneda(item.get("subtotal", 0))

        pdf.cell(w_cols[0], alto_fila, str(idx), align="C", fill=True)
        pdf.cell(w_cols[1], alto_fila, desc_corta, align="C", fill=True)
        pdf.cell(w_cols[2], alto_fila, precio_txt, align="C", fill=True)
        pdf.cell(w_cols[3], alto_fila, cant_txt, align="C", fill=True)
        pdf.cell(w_cols[4], alto_fila, sub_txt, align="C", fill=True)

        y_actual += alto_fila

    pdf.set_draw_color(180, 180, 180)
    pdf.line(margen_izq, y_actual, margen_izq + ancho_util, y_actual)
    y_post_tabla = y_actual + 4

    # 5. BLOQUE TOTALES + CONDICIONES / DETALLES
    x_tot = margen_izq + w_cols[0] + w_cols[1]
    w_tot_lbl = w_cols[2] + w_cols[3]
    w_tot_val = w_cols[4]

    y_tot = y_post_tabla
    pdf.set_font("Helvetica", "", 9.5)
    pdf.set_text_color(*c_negro)
    pdf.set_xy(x_tot, y_tot)
    pdf.cell(w_tot_lbl, 7, _txt_pdf("Subtotal"), align="C")
    pdf.cell(w_tot_val, 7, _fmt_moneda(datos["subtotal"]), align="C")
    y_tot += 7.5

    ajuste = float(datos.get("descuento_recargo", 0))
    if abs(ajuste) > 0.001:
        pdf.set_draw_color(160, 160, 160)
        pdf.line(x_tot, y_tot, x_tot + w_tot_lbl + w_tot_val, y_tot)
        lbl_ajuste = "Descuento" if ajuste < 0 else "Recargo / Envío"
        pdf.set_xy(x_tot, y_tot + 1)
        pdf.cell(w_tot_lbl, 7, _txt_pdf(lbl_ajuste), align="C")
        pdf.cell(w_tot_val, 7, _fmt_moneda(ajuste), align="C")
        y_tot += 8.5

    pdf.set_fill_color(*c_oscuro)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 9.5)
    pdf.set_xy(x_tot, y_tot + 1)
    pdf.cell(w_tot_lbl, 8.5, _txt_pdf("TOTAL"), align="C", fill=True)
    pdf.cell(w_tot_val, 8.5, _fmt_moneda(datos["total"]), align="C", fill=True)

    y_notas = max(y_post_tabla + 2, 185) if y_post_tabla < 185 else y_post_tabla + 2
    if y_notas > 225:
        y_notas = y_post_tabla + 2

    pdf.set_font("Helvetica", "B", 8.5)
    pdf.set_text_color(*c_dorado)
    pdf.set_xy(margen_izq, y_notas)
    pdf.cell(85, 5, _txt_pdf("CONDICIONES DE PAGO"))

    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(*c_negro)
    pdf.set_xy(margen_izq, y_notas + 5)
    pdf.multi_cell(82, 4, _txt_pdf(datos.get("condiciones_pago", "")))

    y_det = pdf.get_y() + 4
    pdf.set_font("Helvetica", "B", 8.5)
    pdf.set_text_color(*c_dorado)
    pdf.set_xy(margen_izq, y_det)
    pdf.cell(85, 5, _txt_pdf("OTROS DETALLES"))

    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(*c_negro)
    pdf.set_xy(margen_izq, y_det + 5)
    pdf.multi_cell(82, 4, _txt_pdf(datos.get("otros_detalles", "")))

    # 6. FIRMA Y PIE DE CONTACTO LOCAL
    y_pie = 252
    pdf.set_font("Helvetica", "B", 8.5)
    pdf.set_text_color(*c_dorado)
    pdf.set_xy(margen_izq, y_pie)
    pdf.cell(65, 5, _txt_pdf("FIRMA"))

    pdf.set_draw_color(150, 150, 150)
    pdf.line(margen_izq, y_pie + 16, margen_izq + 62, y_pie + 16)

    pdf.set_font("Helvetica", "", 8.5)
    pdf.set_text_color(*c_negro)
    pdf.set_xy(margen_izq, y_pie + 17.5)
    pdf.cell(62, 5, _txt_pdf("Pañalera Moldes"))

    pdf.set_font("Helvetica", "B", 9.5)
    pdf.set_text_color(*c_negro)
    pdf.set_xy(105, y_pie + 2)
    pdf.cell(87, 5, _txt_pdf("Mendoza 1797 - Salta Capital"), align="R")

    pdf.set_xy(105, y_pie + 7.5)
    pdf.cell(87, 5, _txt_pdf("WhatsApp: 3876300527"), align="R")

    pdf.set_fill_color(*c_dorado)
    pdf.rect(105, y_pie + 15, 87, 7, style="F")
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_xy(105, y_pie + 15.8)
    pdf.cell(87, 5.5, _txt_pdf("PAÑALERA MOLDES - SALTA"), align="C")

    return bytes(pdf.output())


def render_modulo_presupuestos(db, nombre_usuario_actual="Administrador"):
    # Inicializar estados propios del módulo (independientes de carrito_vta)
    if "carrito_pres" not in st.session_state:
        st.session_state.carrito_pres = []
    if "ultimo_pdf_presupuesto" not in st.session_state:
        st.session_state.ultimo_pdf_presupuesto = None
    if "pres_form_id" not in st.session_state:
        st.session_state.pres_form_id = 0

    def limpiar_todo_presupuesto():
        st.session_state.carrito_pres = []
        st.session_state.ultimo_pdf_presupuesto = None
        st.session_state.pres_form_id += 1

    fid = st.session_state.pres_form_id

    # ==========================================================================
    # BOTONERA SUPERIOR ALINEADA (IDÉNTICA AL PUNTO DE VENTA)
    # ==========================================================================
    col_t1, col_t2 = st.columns([4.5, 1.1], vertical_alignment="bottom")
    with col_t1:
        st.header("📄 Presupuestos - Pañalera Moldes")
    with col_t2:
        st.button(
            "🧹 Limpiar Todo",
            type="secondary",
            use_container_width=True,
            key=f"pres_limpiar_todo_{fid}",
            on_click=limpiar_todo_presupuesto
        )

    tab_nuevo, tab_hist = st.tabs(["📝 Armar Presupuesto", "📜 Historial Emitidos"])

    # ==========================================================================
    # PESTAÑA 1: ARMAR PRESUPUESTO (MISMO FLUJO QUE VENTA RÁPIDA)
    # ==========================================================================
    with tab_nuevo:
        try:
            df_clie = pd.DataFrame(db.table("CLIENTES").select("*").execute().data)
            df_prod = pd.DataFrame(db.table("PRODUCTOS").select("*").execute().data)
            df_vend = pd.DataFrame(db.table("VENDEDORES").select("*").execute().data)
        except Exception as e:
            st.error(f"Error al conectar con Supabase: {e}")
            return

        if not df_prod.empty and "Estado" in df_prod.columns:
            df_prod = df_prod[df_prod["Estado"].fillna("ACTIVO").str.upper() != "INACTIVO"].copy()

        # ----------------------------------------------------------------------
        # 1. SELECTOR DE CLIENTE Y VENDEDOR (MISMA ESTRUCTURA DEL POS)
        # ----------------------------------------------------------------------
        with st.container(border=True):
            c1, c2, c3 = st.columns([3, 1.2, 1.5])

            modo_cli = c2.radio(
                "Tipo Cliente",
                ["Registrado", "Eventual"],
                horizontal=True,
                key=f"pres_tipo_cli_radio_{fid}"
            )

            cliente_sel_row = None
            id_cliente_final = None
            cliente_nombre_final = "Consumidor Final"
            cliente_detalle_final = ""
            cliente_tel_final = ""
            cliente_email_final = ""

            if modo_cli == "Registrado" and not df_clie.empty:
                df_clie["Display"] = df_clie.apply(_obtener_display_cliente, axis=1)
                cliente_display = c1.selectbox(
                    "👤 Buscar Cliente (Nombre, Apellido, Teléfono o Razón Social)",
                    options=df_clie["Display"].tolist(),
                    index=None,
                    placeholder="Seleccione o busque un cliente...",
                    key=f"pres_cli_sel_{fid}"
                )

                if cliente_display:
                    cliente_sel_row = df_clie[df_clie["Display"] == cliente_display].iloc[0]
                    razon_sel = _limpiar_val(cliente_sel_row.get("Razón Social"))
                    nombre_sel = _limpiar_val(cliente_sel_row.get("Nombre"))
                    apellido_sel = _limpiar_val(cliente_sel_row.get("Apellido"))
                    tel_sel = _limpiar_val(cliente_sel_row.get("Telefono")).replace(".0", "")
                    dir_sel = _limpiar_val(cliente_sel_row.get("Direccion_1"))
                    zona_sel = _limpiar_val(cliente_sel_row.get("Zona"))

                    if razon_sel:
                        cliente_nombre_final = razon_sel.upper()
                        nom_sub = f"{nombre_sel} {apellido_sel}".strip().upper()
                        cliente_detalle_final = nom_sub if nom_sub else dir_sel
                    else:
                        cliente_nombre_final = f"{nombre_sel} {apellido_sel}".strip().upper() or "CONSUMIDOR FINAL"
                        cliente_detalle_final = f"{dir_sel} ({zona_sel})" if (dir_sel and zona_sel) else dir_sel

                    cliente_tel_final = tel_sel
                    try:
                        id_val = int(cliente_sel_row["ID_Cliente"])
                        id_cliente_final = id_val if id_val not in (0, 1) else None
                    except Exception:
                        id_cliente_final = None
            else:
                ev_nom = c1.text_input(
                    "👤 Nombre / Razón Social del Cliente Eventual",
                    placeholder="Ej: María Gómez / Jardín Maternal...",
                    key=f"pres_ev_nombre_{fid}"
                )
                cliente_nombre_final = ev_nom.strip().upper() if ev_nom.strip() else "CONSUMIDOR FINAL"

            # Selector de Vendedor igual al POS
            vendedor_id_final = "1"
            vendedor_nombre_final = str(nombre_usuario_actual)
            if not df_vend.empty:
                dict_vendedores = {
                    str(row["ID_Vendedor"]): f"{row['Nombre']} {row['Apellido']}"
                    for _, row in df_vend.iterrows()
                }
                lista_opciones_v = list(dict_vendedores.keys())
                vendedor_id_sel = c3.selectbox(
                    "👔 Vendedor",
                    options=lista_opciones_v,
                    format_func=lambda x: dict_vendedores[x],
                    index=0,
                    key=f"pres_vendedor_selector_{fid}"
                )
                if vendedor_id_sel:
                    vendedor_id_final = str(vendedor_id_sel)
                    vendedor_nombre_final = dict_vendedores[vendedor_id_final]
            else:
                c3.selectbox("👔 Vendedor", options=["1"], format_func=lambda x: "Vendedor Genérico", key=f"pres_vend_fb_{fid}")

            # Campos extra opcionales del cliente
            with st.expander("📋 Completar o editar teléfono, dirección o email del cliente (Opcional)", expanded=(modo_cli == "Eventual")):
                ce1, ce2, ce3 = st.columns(3)
                cliente_tel_final = ce1.text_input("Teléfono / WhatsApp", value=cliente_tel_final, key=f"pres_tel_ed_{fid}_{cliente_nombre_final}")
                cliente_detalle_final = ce2.text_input("Dirección / Empresa", value=cliente_detalle_final, key=f"pres_det_ed_{fid}_{cliente_nombre_final}")
                cliente_email_final = ce3.text_input("Email (Opcional)", value="", key=f"pres_email_ed_{fid}")

        # ----------------------------------------------------------------------
        # 2. BUSCADOR INSTANTÁNEO DE PRODUCTOS (MISMA MECÁNICA ON_CHANGE DEL POS)
        # ----------------------------------------------------------------------
        st.divider()
        st.subheader("🔍 Añadir Productos")

        key_buscador = f"pres_prod_manual_key_{fid}"

        def procesar_seleccion_presupuesto():
            seleccion = st.session_state.get(key_buscador)
            if not seleccion:
                return
            st.session_state.ultimo_pdf_presupuesto = None
            id_buscado = seleccion.split(" - ")[-1]
            res = df_prod[df_prod["ID_Producto"].astype(str) == str(id_buscado)]
            if not res.empty:
                p_row = res.iloc[0]
                id_p = str(p_row["ID_Producto"])
                nom_p = str(p_row["Nombre"])
                p1_val = float(p_row.get("Precio_1") or 0.0)

                existente = next(
                    (x for x in st.session_state.carrito_pres if str(x["id"]) == id_p and x.get("lista_local") == "Automática (P1/P2)"),
                    None
                )
                if existente:
                    existente["cantidad"] = int(existente["cantidad"]) + 1
                else:
                    st.session_state.carrito_pres.append({
                        "id": id_p,
                        "nombre": nom_p,
                        "cantidad": 1,
                        "lista_local": "Automática (P1/P2)",
                        "precio": p1_val,
                        "subtotal": p1_val
                    })
            st.session_state[key_buscador] = None

        opciones_productos = (
            df_prod["Nombre"] + " - " + df_prod["ID_Producto"].astype(str)
        ).tolist() if not df_prod.empty else []

        col_bus1, col_bus2 = st.columns([2, 1], vertical_alignment="bottom")
        col_bus1.selectbox(
            "Buscar por nombre o código",
            options=opciones_productos,
            index=None,
            placeholder="Escriba para buscar producto y añadirlo al instante...",
            key=key_buscador,
            on_change=procesar_seleccion_presupuesto
        )

        with col_bus2.popover("✏️ Ítem manual (fuera de catálogo)", use_container_width=True):
            man_desc = st.text_input("Descripción", key=f"pres_man_desc_{fid}")
            man_cant = st.number_input("Cantidad", min_value=1, value=1, key=f"pres_man_cant_{fid}")
            man_prec = st.number_input("Precio Unitario ($)", min_value=0.0, step=100.0, key=f"pres_man_prec_{fid}")
            if st.button("➕ Sumar al presupuesto", type="primary", use_container_width=True, key=f"pres_man_btn_{fid}"):
                if man_desc.strip():
                    st.session_state.ultimo_pdf_presupuesto = None
                    st.session_state.carrito_pres.append({
                        "id": "MANUAL",
                        "nombre": man_desc.strip().upper(),
                        "cantidad": int(man_cant),
                        "lista_local": "Manual",
                        "precio": float(man_prec),
                        "subtotal": round(int(man_cant) * float(man_prec), 2)
                    })
                    st.rerun()

        # ----------------------------------------------------------------------
        # 3. CARRITO DEL PRESUPUESTO (MISMA ESTRUCTURA Y REGLAS P1/P2/P3 DEL POS)
        # ----------------------------------------------------------------------
        if st.session_state.carrito_pres:
            st.write("### 📋 Detalle del Presupuesto")

            for i, item in enumerate(st.session_state.carrito_pres):
                is_manual = (str(item["id"]) == "MANUAL")
                p_data = None
                if not is_manual:
                    res_p = df_prod[df_prod["ID_Producto"].astype(str) == str(item["id"])]
                    if not res_p.empty:
                        p_data = res_p.iloc[0]

                c1, c2, c3, c4, c5, c6 = st.columns([2, 1.2, 0.8, 1.2, 1, 0.5])

                with c1:
                    nombre_mostrar = p_data["Nombre"] if p_data is not None else item["nombre"]
                    st.markdown(f"#### {nombre_mostrar}")
                    if p_data is not None:
                        st.markdown(
                            f"<p style='font-size:14px;'><strong>{p_data['ID_Producto']}</strong> (Stock ref: {p_data.get('Stock_Actual', 0)})</p>",
                            unsafe_allow_html=True
                        )
                    else:
                        st.caption("Ítem manual")

                if p_data is not None:
                    lista_actual_producto = item.get("lista_local", "Automática (P1/P2)")
                    opciones_lista = [
                        "Automática (P1/P2)",
                        "Lista 1",
                        "Lista 2",
                        "Lista 3",
                        "Lista 4",
                        "Lista 5",
                    ]
                    idx_l = opciones_lista.index(lista_actual_producto) if lista_actual_producto in opciones_lista else 0
                    lista_item = c2.selectbox(
                        "Lista",
                        opciones_lista,
                        index=idx_l,
                        key=f"pres_L_{fid}_{i}"
                    )
                    if lista_item != lista_actual_producto:
                        item["lista_local"] = lista_item
                        st.rerun()
                else:
                    lista_item = "Manual"
                    c2.text_input("Lista", value="Manual", disabled=True, key=f"pres_L_man_{fid}_{i}")

                n_cant = c3.number_input(
                    "Cant.", min_value=1, value=int(item["cantidad"]), key=f"pres_Q_{fid}_{i}"
                )

                if p_data is not None:
                    if lista_item == "Automática (P1/P2)":
                        if n_cant == 1:
                            col_p = "Precio_1"
                        elif n_cant == 2:
                            col_p = "Precio_2"
                        else:
                            col_p = "Precio_3"
                    else:
                        col_p = lista_item.replace("Lista ", "Precio_")
                    precio_sugerido = float(p_data.get(col_p) or 0.0)
                    key_precio = f"pres_P_{fid}_{i}_{lista_item}_{n_cant}_{precio_sugerido}"
                else:
                    precio_sugerido = float(item["precio"])
                    key_precio = f"pres_P_man_{fid}_{i}"

                n_prec = c4.number_input(
                    "Precio",
                    value=precio_sugerido,
                    key=key_precio,
                    format="%.2f"
                )

                sub = round(n_cant * n_prec, 2)
                st.session_state.carrito_pres[i].update({
                    "nombre": p_data["Nombre"] if p_data is not None else item["nombre"],
                    "cantidad": int(n_cant),
                    "precio": float(n_prec),
                    "subtotal": sub
                })

                c5.write(f"Sub: **${sub:,.2f}**")
                if c6.button("🗑️", key=f"pres_del_{fid}_{i}"):
                    st.session_state.carrito_pres.pop(i)
                    st.rerun()

            # ------------------------------------------------------------------
            # 4. TOTALES, ENTREGA Y CONDICIONES DEL PRESUPUESTO
            # ------------------------------------------------------------------
            subtotal_pres = round(sum(art["subtotal"] for art in st.session_state.carrito_pres), 2)
            st.divider()

            col_tot1, col_tot2, col_tot3 = st.columns([1.2, 1.2, 2], vertical_alignment="center")
            dias_validez = col_tot1.number_input(
                "⏳ Validez del presupuesto (días)",
                min_value=1, max_value=60, value=7,
                key=f"pres_dias_val_{fid}"
            )
            ajuste_monto = col_tot2.number_input(
                "Descuento (-) / Envío (+) ($)",
                value=0.0,
                step=100.0,
                help="Ingresá un valor negativo (ej. -1000) para aplicar un descuento global.",
                key=f"pres_ajuste_monto_{fid}"
            )
            total_final_pres = round(subtotal_pres + float(ajuste_monto), 2)
            col_tot3.markdown(f"### 💰 **Total Presupuestado: ${total_final_pres:,.2f}**")

            st.divider()
            st.subheader("🚚 Forma de Entrega y Condiciones del PDF")

            col_e1, col_e2 = st.columns([1, 2.5])
            tipo_entrega_pres = col_e1.radio(
                "Modalidad cotizada",
                ["Mostrador", "Reparto"],
                index=0,
                key=f"pres_tipo_entrega_{fid}"
            )

            direccion_pres = "Retira en local (Mendoza 1797)"
            if tipo_entrega_pres == "Reparto":
                opciones_dir = []
                if cliente_sel_row is not None:
                    for idx_d in [1, 2, 3]:
                        d_val = _limpiar_val(cliente_sel_row.get(f"Direccion_{idx_d}"))
                        if d_val:
                            opciones_dir.append(d_val)
                if opciones_dir:
                    direccion_pres = col_e2.selectbox("Dirección de entrega", opciones_dir, key=f"pres_dir_sel_{fid}")
                else:
                    direccion_pres = col_e2.text_input("Dirección / Zona de entrega", value=cliente_detalle_final, key=f"pres_dir_txt_{fid}")
            else:
                col_e2.text_input("Lugar de retiro", value="Mendoza 1797 - Salta Capital", disabled=True, key=f"pres_retiro_txt_{fid}")

            with st.expander("⚙️ Personalizar textos legales / medios de pago del PDF", expanded=False):
                datos_pago_txt = st.text_area(
                    "Datos de Pago (bloque superior derecho del PDF):",
                    value="Efectivo / Transferencia / Tarjetas\nPañalera Moldes",
                    height=68,
                    key=f"pres_datos_pago_{fid}"
                )
                cond_pago_txt = st.text_area(
                    "Condiciones de Pago:",
                    value="Los precios expresados son finales e incluyen IVA. Presupuesto válido hasta la fecha de vencimiento indicada, sujeto a disponibilidad de stock.",
                    height=72,
                    key=f"pres_cond_pago_{fid}"
                )
                default_det = f"Modalidad: {tipo_entrega_pres} ({direccion_pres}). Consultá por envíos a domicilio en Salta Capital."
                otros_det_txt = st.text_area(
                    "Otros Detalles:",
                    value=default_det,
                    height=68,
                    key=f"pres_otros_det_{fid}"
                )

            # ------------------------------------------------------------------
            # 5. BOTÓN DE EMISIÓN
            # ------------------------------------------------------------------
            st.divider()
            if st.button("📄 EMITIR PRESUPUESTO", use_container_width=True, type="primary", key=f"btn_emitir_pres_{fid}"):
                try:
                    ahora = datetime.now()
                    venc = ahora + timedelta(days=int(dias_validez))

                    payload_db = {
                        "Fecha": ahora.isoformat(),
                        "Fecha_Vencimiento": venc.strftime("%Y-%m-%d"),
                        "ID_Cliente": id_cliente_final,
                        "Cliente_Nombre": cliente_nombre_final,
                        "Cliente_Detalle": cliente_detalle_final,
                        "Cliente_Telefono": cliente_tel_final,
                        "Cliente_Email": cliente_email_final,
                        "Lista_Precio": "Mixta / POS",
                        "Items_JSON": st.session_state.carrito_pres,
                        "Subtotal": subtotal_pres,
                        "Descuento_Recargo": float(ajuste_monto),
                        "Total": total_final_pres,
                        "Datos_Pago": datos_pago_txt.strip(),
                        "Condiciones_Pago": cond_pago_txt.strip(),
                        "Otros_Detalles": otros_det_txt.strip(),
                        "Usuario_Emisor": vendedor_nombre_final
                    }

                    res_ins = db.table("PRESUPUESTOS").insert(payload_db).execute()
                    nuevo_id = res_ins.data[0]["ID_Presupuesto"]
                    num_formateado = f"001-{int(nuevo_id):06d}"

                    db.table("PRESUPUESTOS").update(
                        {"Numero_Formateado": num_formateado}
                    ).eq("ID_Presupuesto", nuevo_id).execute()

                    datos_pdf = {
                        "numero": num_formateado,
                        "fecha_str": ahora.strftime("%d/%m/%Y"),
                        "vencimiento_str": venc.strftime("%d/%m/%Y"),
                        "cliente_nombre": cliente_nombre_final,
                        "cliente_detalle": cliente_detalle_final,
                        "cliente_telefono": cliente_tel_final,
                        "cliente_email": cliente_email_final,
                        "datos_pago": datos_pago_txt.strip(),
                        "condiciones_pago": cond_pago_txt.strip(),
                        "otros_detalles": otros_det_txt.strip(),
                        "items": st.session_state.carrito_pres,
                        "subtotal": subtotal_pres,
                        "descuento_recargo": float(ajuste_monto),
                        "total": total_final_pres
                    }

                    pdf_bytes = generar_pdf_presupuesto(datos_pdf)
                    st.session_state.ultimo_pdf_presupuesto = {
                        "bytes": pdf_bytes,
                        "filename": f"Presupuesto_{num_formateado}_{cliente_nombre_final.replace(' ', '_')}.pdf",
                        "numero": num_formateado,
                        "cliente": cliente_nombre_final
                    }
                    st.rerun()

                except Exception as e_em:
                    st.error(f"Error al emitir presupuesto: {e_em}")
        else:
            st.info("El presupuesto está vacío. Buscá un producto arriba para comenzar.")

        # ----------------------------------------------------------------------
        # 6. BANNER DE DESCARGA CON LIMPIEZA AUTOMÁTICA AL HACER CLIC
        # ----------------------------------------------------------------------
        if st.session_state.ultimo_pdf_presupuesto:
            ult = st.session_state.ultimo_pdf_presupuesto
            with st.container(border=True):
                c_ok, c_dl = st.columns([2.3, 1.5], vertical_alignment="center")
                c_ok.success(f"✅ Presupuesto **N° {ult['numero']}** ({ult.get('cliente', '')}) generado. Al descargar el PDF se limpiará la pantalla.")
                c_dl.download_button(
                    label=f"📥 DESCARGAR PDF Y LIMPIAR (N° {ult['numero']})",
                    data=ult["bytes"],
                    file_name=ult["filename"],
                    mime="application/pdf",
                    type="primary",
                    use_container_width=True,
                    key=f"btn_descargar_pdf_pres_{ult['numero']}",
                    on_click=limpiar_todo_presupuesto
                )

    # ==========================================================================
    # PESTAÑA 2: HISTORIAL DE PRESUPUESTOS EMITIDOS
    # ==========================================================================
    with tab_hist:
        try:
            res_h = db.table("PRESUPUESTOS").select("*").order("ID_Presupuesto", desc=True).limit(50).execute()
            df_h = pd.DataFrame(res_h.data) if (res_h and res_h.data) else pd.DataFrame()

            if df_h.empty:
                st.info("Todavía no hay presupuestos emitidos.")
            else:
                busq_h = st.text_input("Buscar por cliente o número:", key="pres_busq_hist")
                if busq_h.strip():
                    mask = (
                        df_h["Cliente_Nombre"].fillna("").str.contains(busq_h, case=False) |
                        df_h["Numero_Formateado"].fillna("").str.contains(busq_h, case=False)
                    )
                    df_h = df_h[mask]

                for _, r_h in df_h.iterrows():
                    num_h = r_h.get("Numero_Formateado") or f"001-{int(r_h['ID_Presupuesto']):06d}"
                    fecha_h = pd.to_datetime(r_h["Fecha"]).strftime("%d/%m/%Y") if pd.notnull(r_h.get("Fecha")) else "-"
                    venc_h = pd.to_datetime(r_h["Fecha_Vencimiento"]).strftime("%d/%m/%Y") if pd.notnull(r_h.get("Fecha_Vencimiento")) else "-"

                    with st.container(border=True):
                        ch1, ch2, ch3 = st.columns([2.5, 1.5, 1.2], vertical_alignment="center")
                        ch1.markdown(f"**N° {num_h}** — {r_h['Cliente_Nombre']}")
                        ch1.caption(f"Emitido: {fecha_h} | Vence: {venc_h} | Emisor: {r_h.get('Usuario_Emisor', '-')}")
                        ch2.markdown(f"**{_fmt_moneda(r_h.get('Total', 0))}**")
                        ch2.caption(f"{len(r_h.get('Items_JSON') or [])} ítems")

                        datos_reimp = {
                            "numero": num_h,
                            "fecha_str": fecha_h,
                            "vencimiento_str": venc_h,
                            "cliente_nombre": r_h.get("Cliente_Nombre", ""),
                            "cliente_detalle": r_h.get("Cliente_Detalle", ""),
                            "cliente_telefono": r_h.get("Cliente_Telefono", ""),
                            "cliente_email": r_h.get("Cliente_Email", ""),
                            "datos_pago": r_h.get("Datos_Pago", ""),
                            "condiciones_pago": r_h.get("Condiciones_Pago", ""),
                            "otros_detalles": r_h.get("Otros_Detalles", ""),
                            "items": r_h.get("Items_JSON") or [],
                            "subtotal": float(r_h.get("Subtotal") or 0),
                            "descuento_recargo": float(r_h.get("Descuento_Recargo") or 0),
                            "total": float(r_h.get("Total") or 0)
                        }
                        pdf_reimp = generar_pdf_presupuesto(datos_reimp)
                        ch3.download_button(
                            label="📥 PDF",
                            data=pdf_reimp,
                            file_name=f"Presupuesto_{num_h}.pdf",
                            mime="application/pdf",
                            key=f"reimp_{r_h['ID_Presupuesto']}",
                            use_container_width=True
                        )
        except Exception as e_h:
            st.error(f"Error al cargar historial: {e_h}")
