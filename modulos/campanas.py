import streamlit as st
import pandas as pd
import urllib.parse

def render_modulo_campanas(db):
    st.header("🎯 Segmentación y Campañas de Difusión")
    st.caption("Filtrá clientes según sus compras reales y enviá ofertas personalizadas por WhatsApp.")

    try:
        # 1. Carga de datos desde Supabase
        res_prefs = db.table("preferencias_clientes").select("*").execute()
        res_cli = db.table("CLIENTES").select("*").execute()

        df_prefs = pd.DataFrame(res_prefs.data) if (res_prefs and res_prefs.data) else pd.DataFrame()
        df_cli = pd.DataFrame(res_cli.data) if (res_cli and res_cli.data) else pd.DataFrame()

        if df_prefs.empty or df_cli.empty:
            st.info("📭 Todavía no hay preferencias registradas. A medida que registres ventas a clientes identificados, aparecerán aquí automáticamente.")
            return

        # Cruce de tablas (Inner Join: solo clientes existentes)
        df_prefs["id_cliente"] = df_prefs["id_cliente"].astype(str)
        df_cli["ID_Cliente"] = df_cli["ID_Cliente"].astype(str)
        df_merged = pd.merge(df_prefs, df_cli, left_on="id_cliente", right_on="ID_Cliente", how="inner")

        if df_merged.empty:
            st.warning("No se encontraron coincidencias entre las preferencias y la tabla de clientes.")
            return

        df_merged["ultima_compra_dt"] = pd.to_datetime(df_merged["ultima_compra"], errors="coerce", utc=True)

        # --- 2. PANEL DE FILTROS DE SEGMENTACIÓN ---
        with st.container(border=True):
            st.markdown("#### 🔍 1. Definí el público objetivo")
            c1, c2, c3, c4 = st.columns(4)

            # Rubro
            rubros_disp = ["TODOS"] + sorted([r for r in df_merged["rubro"].dropna().unique() if str(r).strip()])
            rubro_sel = c1.selectbox("Rubro", rubros_disp, key="camp_rubro")

            df_filtrado = df_merged.copy()
            if rubro_sel != "TODOS":
                df_filtrado = df_filtrado[df_filtrado["rubro"] == rubro_sel]

            # Marca
            marcas_disp = ["TODAS"] + sorted([m for m in df_filtrado["marca"].dropna().unique() if str(m).strip()])
            marca_sel = c2.selectbox("Marca", marcas_disp, key="camp_marca")
            if marca_sel != "TODAS":
                df_filtrado = df_filtrado[df_filtrado["marca"] == marca_sel]

            # Filtros dinámicos según Rubro
            if "LECHE" in rubro_sel:
                orden_etapas = ["ETAPA 1", "ETAPA 2", "ETAPA 3", "ETAPA 4", "ESCOLAR"]
                raw_etapas = [e for e in df_filtrado["etapa"].dropna().unique() if str(e).strip()]
                etapas_ord = sorted(raw_etapas, key=lambda x: orden_etapas.index(x.upper()) if x.upper() in orden_etapas else 99)
                
                etapa_sel = c3.selectbox("Etapa", ["TODAS"] + etapas_ord, key="camp_etapa")
                if etapa_sel != "TODAS":
                    df_filtrado = df_filtrado[df_filtrado["etapa"] == etapa_sel]

                formatos_disp = ["TODOS"] + sorted([f for f in df_filtrado["formato_leche"].dropna().unique() if str(f).strip()])
                formato_sel = c4.selectbox("Formato Leche", formatos_disp, key="camp_formato")
                if formato_sel != "TODOS":
                    df_filtrado = df_filtrado[df_filtrado["formato_leche"] == formato_sel]
            else:
                lineas_disp = ["TODAS"] + sorted([l for l in df_filtrado["linea"].dropna().unique() if str(l).strip()])
                linea_sel = c3.selectbox("Línea / Modelo", lineas_disp, key="camp_linea")
                if linea_sel != "TODAS":
                    df_filtrado = df_filtrado[df_filtrado["linea"] == linea_sel]

                orden_talles = ["PR", "RN", "RN+", "P", "M", "G", "XG", "XXG", "XXXG", "JUNIOR", "CH", "EG", "EEG"]
                raw_talles = [t for t in df_filtrado["talle"].dropna().unique() if str(t).strip()]
                talles_ord = sorted(raw_talles, key=lambda x: orden_talles.index(x.upper()) if x.upper() in orden_talles else 99)

                talle_sel = c4.selectbox("Talle", ["TODOS"] + talles_ord, key="camp_talle")
                if talle_sel != "TODOS":
                    df_filtrado = df_filtrado[df_filtrado["talle"] == talle_sel]

            st.divider()
            c5, c6, c7 = st.columns(3)
            dias_recencia = c5.number_input(
                "Compraron en los últimos (días)", 
                min_value=1, max_value=365, value=60, 
                help="Evita ofrecer talles o etapas que el bebé ya superó."
            )
            min_compras = c6.number_input(
                "Mínimo de compras realizadas", 
                min_value=1, max_value=50, value=1,
                help="Usá 2 o más para filtrar compradores habituales y descartar regalos únicos."
            )
            zonas_disp = ["TODAS"] + sorted([z for z in df_filtrado["Zona"].dropna().unique() if str(z).strip()])
            zona_sel = c7.selectbox("Zona de reparto (Opcional)", zonas_disp, key="camp_zona")
            if zona_sel != "TODAS":
                df_filtrado = df_filtrado[df_filtrado["Zona"] == zona_sel]

        # Aplicar filtros de recencia y frecuencia
        fecha_limite = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=int(dias_recencia))
        df_filtrado = df_filtrado[
            (df_filtrado["ultima_compra_dt"] >= fecha_limite) &
            (df_filtrado["cantidad_compras"] >= int(min_compras))
        ]

        if df_filtrado.empty:
            st.warning("⚠️ Ningún cliente cumple con esta combinación de filtros. Probá ampliar los días de recencia o quitar algún filtro.")
            return

        # Consolidar por cliente único (sumando métricas si matcheó múltiples variantes dentro de un filtro amplio)
        df_clientes = df_filtrado.groupby(
            ["ID_Cliente", "Nombre", "Apellido", "Razón Social", "Telefono", "Zona"], 
            as_index=False, 
            dropna=False
        ).agg({
            "cantidad_compras": "sum",
            "unidades_totales": "sum",
            "ultima_compra_dt": "max"
        }).sort_values(by="ultima_compra_dt", ascending=False)

        # --- 3. ARMADO DE PLANTILLA DE MENSAJE ---
        st.divider()
        col_m1, col_m2 = st.columns([3, 1], vertical_alignment="bottom")
        col_m1.markdown(f"#### 💬 2. Mensaje de Difusión ({len(df_clientes)} clientes encontrados)")
        
        # Botón de descarga CSV para envíos masivos externos
        csv_export = df_clientes[["ID_Cliente", "Nombre", "Apellido", "Telefono", "cantidad_compras", "unidades_totales"]].to_csv(index=False).encode("utf-8")
        col_m2.download_button(
            label="📥 Exportar Lista (CSV)",
            data=csv_export,
            file_name=f"campana_{rubro_sel.lower()}.csv",
            mime="text/csv",
            use_container_width=True
        )

        plantilla_msg = st.text_area(
            "Editá el mensaje (usá {nombre} para insertar el primer nombre automáticamente):",
            value="Hola {nombre}! 👋 Te escribimos de Pañalera Moldes porque tenemos una promo especial en productos que llevás habitualmente. Si querés que te pasemos los precios o te separemos, avisame! 🙏",
            height=100,
            key="camp_plantilla_txt"
        )

        # --- 4. LISTADO DE CLIENTES Y ACCIONES RÁPIDAS ---
        st.markdown("#### 📋 3. Listado de Clientes Segmentados")
        for _, row in df_clientes.iterrows():
            nombre_raw = str(row["Nombre"] or "").strip()
            apellido_raw = str(row["Apellido"] or "").strip()
            razon_raw = str(row["Razón Social"] or "").strip()

            # Determinar nombre a mostrar y primer nombre para el saludo
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
            zona_str = str(row["Zona"]).strip() if pd.notnull(row["Zona"]) and str(row["Zona"]).strip() else "Sin zona"

            mensaje_personalizado = plantilla_msg.replace("{nombre}", primer_nombre)

            with st.container(border=True):
                c_datos, c_stats, c_msg, c_wsp = st.columns([2, 1.5, 3, 1.2], vertical_alignment="center")
                
                with c_datos:
                    st.markdown(f"👤 **{display_nombre}**")
                    st.caption(f"📞 `{tel_raw if tel_raw else 'Sin teléfono'}` | 📍 {zona_str}")
                
                with c_stats:
                    st.caption(f"🛒 Compras: **{int(row['cantidad_compras'])}**")
                    st.caption(f"📦 Unidades: **{int(row['unidades_totales'])}**")
                    st.caption(f"📅 Última: **{fecha_str}**")
                
                with c_msg:
                    st.text_area(
                        "Mensaje:",
                        value=mensaje_personalizado,
                        height=95,
                        key=f"txt_camp_{row['ID_Cliente']}",
                        label_visibility="collapsed"
                    )
                
                with c_wsp:
                    if len(tel_limpio) >= 10:
                        # Agregar código de país Argentina (549) si no lo tiene
                        num_wsp = tel_limpio if tel_limpio.startswith("54") else f"549{tel_limpio}"
                        texto_url = urllib.parse.quote(mensaje_personalizado)
                        url_wa = f"https://wa.me/{num_wsp}?text={texto_url}"
                        st.link_button("📲 Enviar WA", url=url_wa, use_container_width=True, type="primary")
                    else:
                        st.button("Sin Teléfono", disabled=True, key=f"no_tel_{row['ID_Cliente']}", use_container_width=True)

    except Exception as e:
        st.error(f"Error al cargar el módulo de Campañas: {e}")
