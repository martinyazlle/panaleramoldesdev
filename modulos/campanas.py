import streamlit as st
import pandas as pd
import urllib.parse


def render_modulo_campanas(db):
    st.header("🎯 Segmentación y Campañas de Difusión")
    st.caption("Filtrá clientes según sus compras reales y enviá ofertas o mensajes de reactivación por WhatsApp.")

    try:
        # 1. Carga de datos desde Supabase
        res_prefs = db.table("preferencias_clientes").select("*").execute()
        res_cli = db.table("CLIENTES").select("*").execute()

        df_prefs = pd.DataFrame(res_prefs.data) if (res_prefs and res_prefs.data) else pd.DataFrame()
        df_cli = pd.DataFrame(res_cli.data) if (res_cli and res_cli.data) else pd.DataFrame()

        if df_prefs.empty or df_cli.empty:
            st.info("📭 Todavía no hay preferencias registradas. A medida que registres ventas a clientes identificados, aparecerán aquí automáticamente.")
            return

        # Cruce de tablas (excluye ID 0 y 1)
        df_prefs["id_cliente"] = df_prefs["id_cliente"].astype(str)
        df_cli["ID_Cliente"] = df_cli["ID_Cliente"].astype(str)
        df_cli = df_cli[~df_cli["ID_Cliente"].isin(["0", "1"])]

        df_merged = pd.merge(df_prefs, df_cli, left_on="id_cliente", right_on="ID_Cliente", how="inner")

        if df_merged.empty:
            st.warning("No se encontraron coincidencias entre las preferencias y la tabla de clientes.")
            return

        df_merged["ultima_compra_dt"] = pd.to_datetime(df_merged["ultima_compra"], errors="coerce", utc=True)
        ahora_utc = pd.Timestamp.now(tz="UTC")

        # --- SELECTOR DE MODO DE CAMPAÑA ---
        modo_campana = st.radio(
            "Seleccioná el objetivo de la campaña:",
            options=[
                "📢 Oferta por Producto (Clientes Activos)",
                "💤 Reactivación (Clientes sin compras recientes)"
            ],
            horizontal=True,
            key="camp_modo_selector"
        )

        es_reactivacion = "Reactivación" in modo_campana
        prefijo_key = "reac" if es_reactivacion else "act"

        st.divider()

        # ==============================================================================
        # PANEL UNIFICADO DE FILTROS DE PRODUCTO + TIEMPO/FRECUENCIA
        # ==============================================================================
        with st.container(border=True):
            if es_reactivacion:
                st.markdown("#### 🔍 1. Filtrar clientes inactivos por producto que consumían")
            else:
                st.markdown("#### 🔍 1. Definí el público objetivo")

            c1, c2, c3, c4 = st.columns(4)

            # Rubro
            rubros_disp = ["TODOS"] + sorted([r for r in df_merged["rubro"].dropna().unique() if str(r).strip()])
            rubro_sel = c1.selectbox("Rubro", rubros_disp, key=f"{prefijo_key}_rubro")

            df_filtrado = df_merged.copy()
            if rubro_sel != "TODOS":
                df_filtrado = df_filtrado[df_filtrado["rubro"] == rubro_sel]

            # Marca
            marcas_disp = ["TODAS"] + sorted([m for m in df_filtrado["marca"].dropna().unique() if str(m).strip()])
            marca_sel = c2.selectbox("Marca", marcas_disp, key=f"{prefijo_key}_marca")
            if marca_sel != "TODAS":
                df_filtrado = df_filtrado[df_filtrado["marca"] == marca_sel]

            # Filtros dinámicos según Rubro (Leche vs Pañales/Todos)
            if "LECHE" in rubro_sel:
                orden_etapas = ["ETAPA 1", "ETAPA 2", "ETAPA 3", "ETAPA 4", "ESCOLAR"]
                raw_etapas = [e for e in df_filtrado["etapa"].dropna().unique() if str(e).strip()]
                etapas_ord = sorted(
                    raw_etapas,
                    key=lambda x: orden_etapas.index(x.upper()) if x.upper() in orden_etapas else 99
                )

                etapa_sel = c3.selectbox("Etapa", ["TODAS"] + etapas_ord, key=f"{prefijo_key}_etapa")
                if etapa_sel != "TODAS":
                    df_filtrado = df_filtrado[df_filtrado["etapa"] == etapa_sel]

                formatos_disp = ["TODOS"] + sorted([f for f in df_filtrado["formato_leche"].dropna().unique() if str(f).strip()])
                formato_sel = c4.selectbox("Formato Leche", formatos_disp, key=f"{prefijo_key}_formato")
                if formato_sel != "TODOS":
                    df_filtrado = df_filtrado[df_filtrado["formato_leche"] == formato_sel]
            else:
                lineas_disp = ["TODAS"] + sorted([l for l in df_filtrado["linea"].dropna().unique() if str(l).strip()])
                linea_sel = c3.selectbox("Línea / Modelo", lineas_disp, key=f"{prefijo_key}_linea")
                if linea_sel != "TODAS":
                    df_filtrado = df_filtrado[df_filtrado["linea"] == linea_sel]

                orden_talles = ["PR", "RN", "RN+", "P", "M", "G", "XG", "XXG", "XXXG", "JUNIOR", "CH", "EG", "EEG"]
                raw_talles = [t for t in df_filtrado["talle"].dropna().unique() if str(t).strip()]
                talles_ord = sorted(
                    raw_talles,
                    key=lambda x: orden_talles.index(x.upper()) if x.upper() in orden_talles else 99
                )

                talle_sel = c4.selectbox("Talle", ["TODOS"] + talles_ord, key=f"{prefijo_key}_talle")
                if talle_sel != "TODOS":
                    df_filtrado = df_filtrado[df_filtrado["talle"] == talle_sel]

            st.divider()

            # Controles de tiempo y frecuencia según el modo seleccionado
            if not es_reactivacion:
                c5, c6, c7 = st.columns(3)
                dias_recencia = c5.number_input(
                    "Compraron en los últimos (días)",
                    min_value=1,
                    max_value=365,
                    value=60,
                    key="act_dias"
                )
                min_compras = c6.number_input(
                    "Mínimo de compras realizadas",
                    min_value=1,
                    max_value=50,
                    value=1,
                    key="act_min_compras"
                )
                zonas_disp = ["TODAS"] + sorted([z for z in df_filtrado["Zona"].dropna().unique() if str(z).strip()])
                zona_sel = c7.selectbox("Zona de reparto (Opcional)", zonas_disp, key="act_zona")
                if zona_sel != "TODAS":
                    df_filtrado = df_filtrado[df_filtrado["Zona"] == zona_sel]
            else:
                r1, r2, r3, r4 = st.columns(4)
                dias_min_inactivo = r1.number_input(
                    "Última compra hace MÁS de (días)",
                    min_value=7,
                    max_value=365,
                    value=35,
                    help="Ej: 35 días muestra clientes que hace más de un mes no vuelven.",
                    key="reac_dias_min"
                )
                dias_max_inactivo = r2.number_input(
                    "Pero hace MENOS de (días)",
                    min_value=15,
                    max_value=730,
                    value=120,
                    help="Evita contactar clientes muy antiguos cuyo bebé ya dejó los pañales.",
                    key="reac_dias_max"
                )
                min_compras = r3.number_input(
                    "Mínimo de compras realizadas",
                    min_value=1,
                    max_value=50,
                    value=1,
                    help="Usá 2+ si querés apuntar solo a clientes habituales que dejaron de venir.",
                    key="reac_min_compras"
                )
                zonas_disp = ["TODAS"] + sorted([z for z in df_filtrado["Zona"].dropna().unique() if str(z).strip()])
                zona_sel = r4.selectbox("Zona de reparto (Opcional)", zonas_disp, key="reac_zona")
                if zona_sel != "TODAS":
                    df_filtrado = df_filtrado[df_filtrado["Zona"] == zona_sel]

        # ==============================================================================
        # PROCESAMIENTO Y CONSOLIDACIÓN DE CLIENTES
        # ==============================================================================
        def armar_etiqueta_prod(row):
            partes = [str(row.get("rubro", "")).strip(), str(row.get("marca", "")).strip()]
            if row.get("linea"):
                partes.append(str(row["linea"]).strip())
            if row.get("talle"):
                partes.append(f"Talle {row['talle']}".strip())
            if row.get("etapa"):
                partes.append(str(row["etapa"]).strip())
            if row.get("formato_leche"):
                partes.append(str(row["formato_leche"]).strip())
            return " - ".join([p for p in partes if p])

        if not es_reactivacion:
            fecha_limite = ahora_utc - pd.Timedelta(days=int(dias_recencia))
            df_filtrado = df_filtrado[
                (df_filtrado["ultima_compra_dt"] >= fecha_limite) &
                (df_filtrado["cantidad_compras"] >= int(min_compras))
            ]

            if df_filtrado.empty:
                st.warning("⚠️ Ningún cliente cumple con esta combinación de filtros.")
                return

            df_sorted = df_filtrado.sort_values(by="ultima_compra_dt", ascending=False).copy()
            df_sorted["etiqueta_prod"] = df_sorted.apply(armar_etiqueta_prod, axis=1)

            df_clientes = df_sorted.groupby(
                ["ID_Cliente", "Nombre", "Apellido", "Razón Social", "Telefono", "Zona"],
                as_index=False,
                dropna=False
            ).agg({
                "cantidad_compras": "sum",
                "unidades_totales": "sum",
                "ultima_compra_dt": "max",
                "etiqueta_prod": "first"
            }).rename(columns={"etiqueta_prod": "detalle_ultimo_prod"}).sort_values(by="ultima_compra_dt", ascending=False)

            df_clientes["dias_inactivo"] = (ahora_utc - df_clientes["ultima_compra_dt"]).dt.days
            plantilla_default = "Hola {nombre}! 👋 Te escribimos de Pañalera Moldes porque tenemos una promo especial en productos que llevás habitualmente. Si querés que te pasemos los precios o te separemos, avisame! 🙏"
            nombre_archivo_csv = f"campana_oferta_{rubro_sel.lower()}.csv"

        else:
            # 1. Calcular la última compra REAL GLOBAL de cada cliente
            ultima_global = df_merged.groupby("ID_Cliente")["ultima_compra_dt"].max().reset_index()
            ultima_global.rename(columns={"ultima_compra_dt": "ultima_visita_global"}, inplace=True)

            df_filtrado = pd.merge(df_filtrado, ultima_global, on="ID_Cliente", how="inner")
            df_sorted = df_filtrado.sort_values(by="ultima_compra_dt", ascending=False).copy()
            df_sorted["etiqueta_prod"] = df_sorted.apply(armar_etiqueta_prod, axis=1)

            df_clientes = df_sorted.groupby(
                ["ID_Cliente", "Nombre", "Apellido", "Razón Social", "Telefono", "Zona"],
                as_index=False,
                dropna=False
            ).agg({
                "cantidad_compras": "sum",
                "unidades_totales": "sum",
                "ultima_visita_global": "max",
                "etiqueta_prod": "first"
            }).rename(columns={
                "ultima_visita_global": "ultima_compra_dt",
                "etiqueta_prod": "detalle_ultimo_prod"
            })

            df_clientes["dias_inactivo"] = (ahora_utc - df_clientes["ultima_compra_dt"]).dt.days

            df_clientes = df_clientes[
                (df_clientes["dias_inactivo"] >= int(dias_min_inactivo)) &
                (df_clientes["dias_inactivo"] <= int(dias_max_inactivo)) &
                (df_clientes["cantidad_compras"] >= int(min_compras))
            ].sort_values(by="dias_inactivo", ascending=True)

            if df_clientes.empty:
                st.warning("⚠️ No se encontraron clientes inactivos con esa combinación de filtros y días.")
                return

            plantilla_default = "Hola {nombre}! 👋 ¿Cómo estás? Hace un tiempito que no te vemos por Pañalera Moldes y queríamos saludarte 😊 Tenemos promos nuevas en pañales y leches (y envío a domicilio sin cargo según zona). ¡Si necesitás reponer algo, escribinos! 🙏"
            nombre_archivo_csv = f"campana_reactivacion_{rubro_sel.lower()}_{dias_min_inactivo}a{dias_max_inactivo}d.csv"

        # ==============================================================================
        # SECCIÓN COMÚN: PLANTILLA Y LISTADO DE CLIENTES
        # ==============================================================================
        st.divider()
        col_m1, col_m2 = st.columns([3, 1], vertical_alignment="bottom")
        col_m1.markdown(f"#### 💬 2. Mensaje de Difusión ({len(df_clientes)} clientes encontrados)")

        csv_export = df_clientes[
            ["ID_Cliente", "Nombre", "Apellido", "Telefono", "cantidad_compras", "unidades_totales", "dias_inactivo", "detalle_ultimo_prod"]
        ].to_csv(index=False).encode("utf-8")

        col_m2.download_button(
            label="📥 Exportar Lista (CSV)",
            data=csv_export,
            file_name=nombre_archivo_csv,
            mime="text/csv",
            use_container_width=True
        )

        col_txt, col_btn = st.columns([4, 1.3], vertical_alignment="center")
        with col_txt:
            plantilla_msg = st.text_area(
                "Editá el mensaje (usá {nombre} para insertar el primer nombre automáticamente):",
                value=plantilla_default,
                height=100,
                key=f"camp_plantilla_{prefijo_key}"
            )
        with col_btn:
            st.write("")
            if st.button("✨ Aplicar a todos", key=f"btn_aplicar_{prefijo_key}", type="primary", use_container_width=True):
                for _, r_ap in df_clientes.iterrows():
                    n_raw = str(r_ap["Nombre"] or "").strip()
                    rz_raw = str(r_ap["Razón Social"] or "").strip()
                    if n_raw:
                        p_nom = n_raw.split()[0].upper()
                    elif rz_raw:
                        p_nom = rz_raw.split()[0].upper()
                    else:
                        p_nom = "Cliente"

                    card_key = f"txt_camp_{prefijo_key}_{r_ap['ID_Cliente']}"
                    st.session_state[card_key] = plantilla_msg.replace("{nombre}", p_nom)
                st.toast("✅ Mensaje actualizado en todas las tarjetas.")

        st.markdown("#### 📋 3. Listado de Clientes Segmentados")
        for _, row in df_clientes.iterrows():
            nombre_raw = str(row["Nombre"] or "").strip()
            apellido_raw = str(row["Apellido"] or "").strip()
            razon_raw = str(row["Razón Social"] or "").strip()

            if nombre_raw:
                display_nombre = f"{nombre_raw} {apellido_raw}".strip().upper()
                primer_nombre = nombre_raw.split()[0].upper()
            elif razon_raw:
                display_nombre = razon_raw.upper()
                primer_nombre = razon_raw.split()[0].upper()
            else:
                display_nombre = "CLIENTE SIN NOMBRE"
                primer_nombre = "Cliente"

            tel_raw = str(row["Telefono"] or "").strip().replace(".0", "")
            tel_limpio = "".join(filter(str.isdigit, tel_raw))
            fecha_str = row["ultima_compra_dt"].strftime("%d/%m/%Y") if pd.notnull(row["ultima_compra_dt"]) else "-"
            dias_inact = int(row["dias_inactivo"]) if pd.notnull(row["dias_inactivo"]) else 0
            zona_str = str(row["Zona"]).strip() if pd.notnull(row["Zona"]) and str(row["Zona"]).strip() else "Sin zona"

            card_key = f"txt_camp_{prefijo_key}_{row['ID_Cliente']}"
            if card_key not in st.session_state:
                st.session_state[card_key] = plantilla_msg.replace("{nombre}", primer_nombre)

            with st.container(border=True):
                c_datos, c_stats, c_msg, c_wsp = st.columns([2.2, 1.8, 3, 1.2], vertical_alignment="center")

                with c_datos:
                    st.markdown(f"👤 **{display_nombre}**")
                    st.caption(f"📞 `{tel_raw if tel_raw else 'Sin teléfono'}` | 📍 {zona_str}")
                    if row["detalle_ultimo_prod"]:
                        st.caption(f"🛍️ Último: **{row['detalle_ultimo_prod']}**")

                with c_stats:
                    st.caption(f"🛒 Compras: **{int(row['cantidad_compras'])}** ({int(row['unidades_totales'])} un.)")
                    st.caption(f"📅 Última compra: **{fecha_str}**")
                    st.caption(f"⏳ Hace **{dias_inact} días**")

                with c_msg:
                    mensaje_actual = st.text_area(
                        "Mensaje:",
                        height=95,
                        key=card_key,
                        label_visibility="collapsed"
                    )

                with c_wsp:
                    if len(tel_limpio) >= 10:
                        num_wsp = tel_limpio if tel_limpio.startswith("54") else f"549{tel_limpio}"
                        texto_url = urllib.parse.quote(mensaje_actual)
                        url_wa = f"https://wa.me/{num_wsp}?text={texto_url}"
                        st.link_button("📲 Enviar WA", url=url_wa, use_container_width=True, type="primary")
                    else:
                        st.button("Sin Teléfono", disabled=True, key=f"no_tel_{prefijo_key}_{row['ID_Cliente']}", use_container_width=True)

    except Exception as e:
        st.error(f"Error al cargar el módulo de Campañas: {e}")
