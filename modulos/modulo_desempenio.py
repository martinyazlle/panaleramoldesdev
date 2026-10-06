import streamlit as st
import pandas as pd
from datetime import datetime, time

def render_modulo_desempenio(db):
    st.title("🛡️ Gestión de Desempeño y Tareas Diarias")
    
    es_admin = st.session_state.get('rol') == "Administrador"

    # Definimos las pestañas dinámicamente según el rol
    if es_admin:
        tab_checklist, tab_bono, tab_historial = st.tabs([
            "📋 Checklist Diario", 
            "🏆 Bono de Excelencia", 
            "📊 Historial & Auditoría"
        ])
    else:
        tab_checklist, tab_bono = st.tabs([
            "📋 Checklist Diario", 
            "🏆 Bono de Excelencia"
        ])

    # -----------------------------------------------------------------
    # PESTAÑA 1: CHECKLIST DIARIO DE TAREAS (REGISTRO INDIVIDUAL E INMUTABLE)
    # -----------------------------------------------------------------
    with tab_checklist:
        st.subheader("📋 Registro Operativo Diario por Tarea")
        st.caption("Completa y registra cada tarea a medida que la ejecutes durante tu turno. Una vez enviada, no podrá modificarse.")

        col_f1, col_f2 = st.columns([1, 2])
        fecha_sel = col_f1.date_input("Fecha de Trabajo", value=datetime.now().date(), key="chk_fecha_sel")
        usuario_actual = st.session_state.get("usuario_actual", st.session_state.get("usuario", "Vendedora"))

        # 1. Cargar tareas maestras (Plantilla)
        res_plantilla = db.table("TAREAS_PLANTILLA").select("*").eq("activo", True).order("orden").execute().data or []

        # 2. Cargar registros existentes para la fecha y usuario seleccionados
        res_existente = db.table("REGISTRO_TAREAS_DIARIAS")\
            .select("*")\
            .eq("fecha", str(fecha_sel))\
            .eq("usuario", usuario_actual)\
            .execute().data or []

        # Mapa de tareas ya registradas para rápido acceso
        mapa_registrados = {r["nombre_tarea"]: r for r in res_existente}

        st.divider()

        # Renderizado de tareas individuales
        for idx, t in enumerate(res_plantilla):
            nombre_t = t["nombre_tarea"]
            horario_sug = t.get("horario_sugerido", "")
            
            # Verificamos si la tarea ya fue enviada/registrada
            ya_registrada = nombre_t in mapa_registrados
            reg_datos = mapa_registrados.get(nombre_t, {})

            # Encabezado de la tarea con indicador de estado
            if ya_registrada:
                st.markdown(f"##### 📌 {nombre_t} &nbsp;&nbsp; `🔒 REGISTRADO`", unsafe_allow_html=True)
            else:
                st.markdown(f"##### 📌 {nombre_t}")

            if horario_sug:
                st.caption(f"Horario Sugerido: {horario_sug}")

            # Valores por defecto o precargados si ya existe
            if ya_registrada:
                h_ini_val = datetime.strptime(reg_datos["hora_inicio"], "%H:%M:%S").time() if reg_datos.get("hora_inicio") else time(14, 0)
                h_fin_val = datetime.strptime(reg_datos["hora_fin"], "%H:%M:%S").time() if reg_datos.get("hora_fin") else time(14, 15)
                est_val = reg_datos.get("estado", "✅ CUMPLIDA")
                obs_val = reg_datos.get("observaciones", "") or ""
            else:
                h_ini_val = time(14, 0)
                h_fin_val = time(14, 15)
                est_val = "PENDIENTE"
                obs_val = ""

            c1, c2, c3, c4 = st.columns([1, 1, 1.2, 2.5])

            # Controles deshabilitados si ya se envió
            h_inicio = c1.time_input("Inicio Real", value=h_ini_val, disabled=ya_registrada, key=f"h_ini_{idx}")
            h_fin = c2.time_input("Fin Real", value=h_fin_val, disabled=ya_registrada, key=f"h_fin_{idx}")

            opts_estado = ["PENDIENTE", "✅ CUMPLIDA", "⚠️ PARCIAL", "❌ NO REALIZADA"]
            idx_est = opts_estado.index(est_val) if est_val in opts_estado else 0
            est_sel = c3.selectbox("Estado", opts_estado, index=idx_est, disabled=ya_registrada, key=f"est_{idx}")

            obs_txt = c4.text_input("Observaciones / Novedad", value=obs_val, placeholder="Ej: Demora por atencion en caja", disabled=ya_registrada, key=f"obs_{idx}")

            # Botón individual de envío por tarea
            c_btn1, _ = st.columns([1.5, 3])
            
            if ya_registrada:
                c_btn1.button("✅ Enviado", key=f"btn_single_{idx}", disabled=True)
            else:
                if c_btn1.button("📩 Registrar Tarea", key=f"btn_single_{idx}", type="primary"):
                    if est_sel == "PENDIENTE":
                        st.warning("⚠️ Selecciona un estado distinto a 'PENDIENTE' antes de registrar.")
                    else:
                        payload_single = {
                            "fecha": str(fecha_sel),
                            "usuario": usuario_actual,
                            "nombre_tarea": nombre_t,
                            "horario_sugerido": horario_sug,
                            "hora_inicio": str(h_inicio),
                            "hora_fin": str(h_fin),
                            "estado": est_sel,
                            "observaciones": obs_txt
                        }
                        try:
                            db.table("REGISTRO_TAREAS_DIARIAS").insert(payload_single).execute()
                            st.toast(f"✅ ¡'{nombre_t}' registrada correctamente!", icon="🎉")
                            st.rerun()
                        except Exception as e:
                            st.error(f"❌ Error al registrar tarea: {e}")

            st.markdown("---")

    # -----------------------------------------------------------------
    # PESTAÑA 2: BONO DE EXCELENCIA & PUNTAJE
    # -----------------------------------------------------------------
    with tab_bono:
        st.subheader("🏆 Control y Seguimiento del Bono de Excelencia")
        
        # ---------------------------------------------------------
        # 🖼️ GUÍA VISUAL DEL BONO DE EXCELENCIA (Expander)
        # ---------------------------------------------------------
        with st.expander("📖 Ver Guía Rápida del Bono de Excelencia (Premios y Faltas)", expanded=False):
            try:
                # Usamos el mismo diseño de columnas que en el inventario
                col_img, col_vacia = st.columns([0.8, 0.2])
                with col_img:
                    st.image("guia_bono_excelencia.png", use_container_width=True)
            except Exception:
                st.warning("⚠️ No se encontró la imagen 'guia_bono_excelencia.png' en el directorio del proyecto.")

        st.divider()

        mes_activo = datetime.now().strftime("%Y-%m")
        st.info(f"📅 **Periodo Activo:** {datetime.now().strftime('%B %Y').title()} (`{mes_activo}`)")

        # Cargar puntos acumulados del mes
        res_puntos = db.table("PUNTOS_BONO_EXCELENCIA").select("*").eq("mes_anio", mes_activo).execute().data or []
        
        puntos_base = 100
        ajuste_puntos = sum([p["puntos"] for p in res_puntos])
        total_puntos = puntos_base + ajuste_puntos

        # KPIs Visuales
        k1, k2, k3 = st.columns(3)
        k1.metric("💯 Puntos Base", f"{puntos_base} pts")
        k2.metric("📊 Ajuste acumulado", f"{ajuste_puntos:+d} pts")
        k3.metric("🎯 PUNTAJE ACTUAL", f"{total_puntos} pts")

        # Escala de Recompensas
        if total_puntos >= 90:
            st.success("🏆 **Escalón Alcanzado:** $100.000 (Bono Sobresaliente 100%)")
        elif total_puntos >= 80:
            st.info("🥇 **Escalón Alcanzado:** $75.000 (Bono Alto)")
        elif total_puntos >= 60:
            st.warning("🥈 **Escalón Alcanzado:** $50.000 (Mínimo Aceptable)")
        else:
            st.error("🚨 **Escalón Actual:** $0 (Sin Bono + Requiere Capacitación)")

        st.divider()

        # Registro de Novedades del Bono (Solo Admin o Encargada)
        if st.session_state.get('rol') == "Administrador":
            st.markdown("##### ➕ Registrar Ajuste de Puntos")
            
            with st.form("form_bono_puntos"):
                f_col1, f_col2 = st.columns([1, 2])
                
                tipo_evt = f_col1.selectbox("Tipo de Evento", [
                    "⭐ +10 PTS: Reseña Positiva (5 estrellas en Google/Redes)",
                    "📦 +5 PTS: Inventario Perfecto Semanal (100% coincidencia)",
                    "🎁 +10 PTS: Propuesta de Contenido (Scouting)",
                    "🎁 +5 PTS: Semana Perfecta (100% Checklists)",
                    "⚠️ -5 PTS: Orden e Imagen (Basura, Microzona, etc.)",
                    "⚠️ -10 PTS: Fallas Operativas (Quiebre stock, etc.)",
                    "❌ -25 PTS: Falta Crítica (Circuito de oro, Error envío, etc.)",
                    "⚙️ Ajuste Manual Personalizado"
                ])
                
                motivo_evt = f_col2.text_input("Motivo / Detalle de la novedad", placeholder="Ej: Error en pedido Tomas Garavaglia")
                pts_custom = f_col1.number_input("Puntos a aplicar (si es manual)", value=0, step=1)
                
                btn_reg_pts = st.form_submit_button("📌 Registrar Evento en el Bono", type="primary")

                if btn_reg_pts:
                    # Determinar puntos según selección exacta
                    if "Reseña Positiva" in tipo_evt:
                        pts = 10
                    elif "Inventario Perfecto Semanal" in tipo_evt:
                        pts = 5
                    elif "Propuesta de Contenido" in tipo_evt:
                        pts = 10
                    elif "Semana Perfecta" in tipo_evt:
                        pts = 5
                    elif "Orden e Imagen" in tipo_evt:
                        pts = -5
                    elif "Fallas Operativas" in tipo_evt:
                        pts = -10
                    elif "Falta Crítica" in tipo_evt:
                        pts = -25
                    else:
                        pts = int(pts_custom)

                    if not motivo_evt:
                        st.warning("⚠️ Debes ingresar un motivo.")
                    else:
                        payload_bono = {
                            "fecha": str(datetime.now().date()),
                            "mes_anio": mes_activo,
                            "usuario": st.session_state.get("usuario", "Vendedora"),
                            "tipo_evento": tipo_evt.split(":")[0],
                            "puntos": pts,
                            "motivo": motivo_evt,
                            "registrado_por": st.session_state.get("usuario", "Admin")
                        }
                        db.table("PUNTOS_BONO_EXCELENCIA").insert(payload_bono).execute()
                        st.success("✅ Novedad registrada en el historial del Bono.")
                        st.rerun()

        # Tabla del Historial del Mes
        st.markdown("##### 📜 Novedades Aplicadas en el Mes")
        if res_puntos:
            df_pts = pd.DataFrame(res_puntos)[["fecha", "tipo_evento", "puntos", "motivo", "registrado_por"]]
            st.dataframe(df_pts, use_container_width=True, hide_index=True)
        else:
            st.info("Sin faltas ni premios registrados en lo que va del mes. ¡Puntaje intacto! 👏")

    # -----------------------------------------------------------------
    # PESTAÑA 3: HISTORIAL & AUDITORÍA (SOLO ADMINISTRADOR)
    # -----------------------------------------------------------------
    if es_admin:
        with tab_historial:
            st.subheader("📊 Historial General y Auditoría")
            
            # 1. Obtener de forma dinámica la última fecha registrada en Supabase
            res_ultima_fecha = db.table("REGISTRO_TAREAS_DIARIAS")\
                .select("fecha")\
                .order("fecha", desc=True)\
                .limit(1)\
                .execute().data or []

            if res_ultima_fecha and res_ultima_fecha[0].get("fecha"):
                # Parseamos la última fecha enviada
                ultima_fecha_envio = datetime.strptime(res_ultima_fecha[0]["fecha"], "%Y-%m-%d").date()
            else:
                # Fallback a hoy si la tabla está vacía
                ultima_fecha_envio = datetime.now().date()

            # 2. Renderizado de selectores de fecha con el valor por defecto ajustado
            c_h1, c_h2 = st.columns(2)
            f_desde = c_h1.date_input("Desde", value=ultima_fecha_envio, key="audit_f_desde")
            f_hasta = c_h2.date_input("Hasta", value=ultima_fecha_envio, key="audit_f_hasta")

            # 3. Consulta de registros filtrados por el rango seleccionado
            res_audit = db.table("REGISTRO_TAREAS_DIARIAS")\
                .select("*")\
                .gte("fecha", str(f_desde))\
                .lte("fecha", str(f_hasta))\
                .order("fecha", desc=True)\
                .execute().data or []

            if res_audit:
                df_audit = pd.DataFrame(res_audit)
                cols_mostrar = ['fecha', 'usuario', 'nombre_tarea', 'hora_inicio', 'hora_fin', 'estado', 'observaciones']
                cols_presentes = [c for c in cols_mostrar if c in df_audit.columns]
                
                st.dataframe(
                    df_audit[cols_presentes], 
                    use_container_width=True, 
                    hide_index=True
                )
            else:
                st.info("No se encontraron registros de tareas en el rango de fechas seleccionado.")
