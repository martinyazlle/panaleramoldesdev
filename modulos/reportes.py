import streamlit as st
import pandas as pd
import plotly.express as px
from datetime import datetime

# ==========================================
# 1. FUNCIONES DE CARGA DE DATOS DESDE BD
# ==========================================

def cargar_datos_reportes_ventas(db, mes_num, anio_num):
    """Carga y procesa los datos de VENTAS desde Supabase para el mes/año seleccionado."""
    try:
        fecha_inicio = f"{anio_num}-{mes_num:02d}-01"
        fecha_fin = f"{anio_num + 1}-01-01" if mes_num == 12 else f"{anio_num}-{mes_num + 1:02d}-01"

        # 1. VENTAS_CABECERA
        res_cab = db.table("VENTAS_CABECERA").select("*") \
            .gte("Fecha", fecha_inicio) \
            .lt("Fecha", fecha_fin) \
            .neq("Estado", "Anulada") \
            .execute()
        df_cabecera = pd.DataFrame(res_cab.data)

        if df_cabecera.empty:
            return None

        ids_ventas = df_cabecera["ID_Venta"].tolist()

        # 2. VENTAS_DETALLE
        res_det = db.table("VENTAS_DETALLE").select("*").in_("ID_Venta", ids_ventas).execute()
        df_detalle = pd.DataFrame(res_det.data)

        # 3. VENTAS_PAGOS
        res_pagos = db.table("VENTAS_PAGOS").select("*").in_("ID_Venta", ids_ventas).execute()
        df_pagos = pd.DataFrame(res_pagos.data)

        # 4. PRODUCTOS
        res_prod = db.table("PRODUCTOS").select("ID_Producto, Nombre, Marca, Rubro").execute()
        df_productos = pd.DataFrame(res_prod.data)

        # 5. CLIENTES
        res_cli = db.table("CLIENTES").select("*").execute()
        df_clientes = pd.DataFrame(res_cli.data)

        # 6. VENDEDORES
        res_vend = db.table("VENDEDORES").select("ID_Vendedor, Nombre, Apellido").execute()
        df_vendedores = pd.DataFrame(res_vend.data)

        # --- ENSAMBLADO Y CRUCES DE DATOS ---
        if not df_detalle.empty and not df_productos.empty:
            df_detalle = df_detalle.merge(df_productos, on="ID_Producto", how="left")
            df_detalle["Nombre"] = df_detalle["Nombre"].fillna(df_detalle["ID_Producto"])
            df_detalle["Precio_Costo_Unitario"] = df_detalle["Precio_Costo_Unitario"].fillna(0)
            df_detalle["Costo_Total"] = df_detalle["Cantidad"] * df_detalle["Precio_Costo_Unitario"]
            df_detalle["Ganancia_Bruta"] = df_detalle["Subtotal"] - df_detalle["Costo_Total"]

        if not df_clientes.empty and "ID_Cliente" in df_cabecera.columns:
            col_razon = "Razón Social" if "Razón Social" in df_clientes.columns else "RazónSocial"
            df_clientes["Cliente_Nombre"] = df_clientes.apply(
                lambda r: r[col_razon] if col_razon in r and pd.notnull(r[col_razon]) and str(r[col_razon]).strip() != ""
                else f"{r.get('Nombre', '') or ''} {r.get('Apellido', '') or ''}".strip(), axis=1
            )
            df_cabecera = df_cabecera.merge(df_clientes[["ID_Cliente", "Cliente_Nombre"]], on="ID_Cliente", how="left")
            df_cabecera["Cliente_Nombre"] = df_cabecera["Cliente_Nombre"].fillna("Cliente General")

        if not df_vendedores.empty and "ID_Vendedor" in df_cabecera.columns:
            df_vendedores["Vendedor_Nombre"] = (df_vendedores["Nombre"].fillna('') + " " + df_vendedores["Apellido"].fillna('')).str.strip()
            df_cabecera = df_cabecera.merge(df_vendedores[["ID_Vendedor", "Vendedor_Nombre"]], on="ID_Vendedor", how="left")
            df_cabecera["Vendedor_Nombre"] = df_cabecera["Vendedor_Nombre"].fillna(df_cabecera["ID_Vendedor"])

        return {
            "cabecera": df_cabecera,
            "detalle": df_detalle,
            "pagos": df_pagos
        }

    except Exception as e:
        st.error(f"Error cargando datos de Ventas: {e}")
        return None


def cargar_datos_reportes_compras(db, mes_num, anio_num):
    """Carga y procesa los datos de COMPRAS desde Supabase para el mes/año seleccionado."""
    try:
        fecha_inicio = f"{anio_num}-{mes_num:02d}-01"
        fecha_fin = f"{anio_num + 1}-01-01" if mes_num == 12 else f"{anio_num}-{mes_num + 1:02d}-01"

        # 1. COMPRAS_CABECERA
        res_cab = db.table("COMPRAS_CABECERA").select("*") \
            .gte("Fecha", fecha_inicio) \
            .lt("Fecha", fecha_fin) \
            .execute()
        df_cabecera = pd.DataFrame(res_cab.data)

        if df_cabecera.empty:
            return None

        ids_compras = df_cabecera["ID_Compra"].tolist()

        # 2. DETALLE_COMPRAS
        res_det = db.table("DETALLE_COMPRAS").select("*").in_("ID_Compra", ids_compras).execute()
        df_detalle = pd.DataFrame(res_det.data)

        # 3. PROVEEDORES
        res_prov = db.table("PROVEEDORES").select("*").execute()
        df_proveedores = pd.DataFrame(res_prov.data)

        # 4. PRODUCTOS
        res_prod = db.table("PRODUCTOS").select("ID_Producto, Nombre, Marca, Rubro").execute()
        df_productos = pd.DataFrame(res_prod.data)

        # --- ENSAMBLADO Y CRUCES DE DATOS ---
        if not df_detalle.empty and not df_productos.empty:
            df_detalle = df_detalle.merge(df_productos, on="ID_Producto", how="left")
            df_detalle["Nombre"] = df_detalle["Nombre"].fillna(df_detalle["ID_Producto"])

        if not df_proveedores.empty and "Proveedor" in df_cabecera.columns:
            # Cruzamos por Proveedor (o ID_Proveedor / Razón Social según corresponda)
            if "ID_Proveedor" in df_proveedores.columns and "Razon_Social" in df_proveedores.columns:
                df_cabecera = df_cabecera.merge(
                    df_proveedores[["ID_Proveedor", "Razon_Social"]], 
                    left_on="Proveedor", right_on="ID_Proveedor", how="left"
                )
                df_cabecera["Proveedor_Nombre"] = df_cabecera["Razon_Social"].fillna(df_cabecera["Proveedor"])
            else:
                df_cabecera["Proveedor_Nombre"] = df_cabecera["Proveedor"]
        else:
            df_cabecera["Proveedor_Nombre"] = df_cabecera["Proveedor"] if "Proveedor" in df_cabecera.columns else "Desconocido"

        return {
            "cabecera": df_cabecera,
            "detalle": df_detalle
        }

    except Exception as e:
        st.error(f"Error cargando datos de Compras: {e}")
        return None


# ==========================================
# 2. RENDERIZADO DE PESTAÑAS
# ==========================================

def render_tab_ventas(datos, mes_sel, anio_sel):
    """Renderiza la pestaña de Inteligencia de Ventas."""
    if datos is None or datos["cabecera"].empty:
        st.warning(f"No hay registros de ventas para el período seleccionado ({mes_sel} {anio_sel}).")
        return

    df_cab = datos["cabecera"]
    df_det = datos["detalle"]
    df_pag = datos["pagos"]

    # --- METRICAS PRINCIPALES (KPIs) ---
    ventas_totales = float(df_cab["Total"].sum()) if "Total" in df_cab.columns else 0.0
    total_operaciones = len(df_cab)
    ticket_promedio = ventas_totales / total_operaciones if total_operaciones > 0 else 0.0
    utilidad_total = float(df_det["Ganancia_Bruta"].sum()) if not df_det.empty and "Ganancia_Bruta" in df_det.columns else 0.0

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("💰 Ventas Totales", f"${ventas_totales:,.2f}")
    col2.metric("📈 Utilidad Bruta Est.", f"${utilidad_total:,.2f}")
    col3.metric("🧾 Total Operaciones", f"{total_operaciones}")
    col4.metric("🎯 Ticket Promedio", f"${ticket_promedio:,.2f}")

    st.divider()

    # --- SECCIÓN 1: TOP PRODUCTOS Y FORMAS DE PAGO ---
    c1, c2 = st.columns(2)

    with c1:
        st.subheader("🔥 Top 10 Productos Más Vendidos")
        if not df_det.empty and "Nombre" in df_det.columns and "Cantidad" in df_det.columns:
            df_det["Cantidad"] = pd.to_numeric(df_det["Cantidad"], errors="coerce").fillna(0)
            df_det["Nombre"] = df_det["Nombre"].astype(str)
            
            top_prod = df_det.groupby("Nombre")["Cantidad"].sum().reset_index()
            top_prod = top_prod.sort_values(by="Cantidad", ascending=True).tail(10)
            
            fig_prod = px.bar(
                top_prod, x="Cantidad", y="Nombre", orientation="h",
                text="Cantidad", color_discrete_sequence=["#2E86C1"]
            )
            fig_prod.update_layout(xaxis_title="Unidades Vendidas", yaxis_title="")
            st.plotly_chart(fig_prod, use_container_width=True)
        else:
            st.info("Sin detalle de productos disponible.")

    with c2:
        st.subheader("💳 Distribución por Formas de Pago")
        if not df_pag.empty and "Metodo_Pago" in df_pag.columns and "Monto" in df_pag.columns:
            df_pag["Monto"] = pd.to_numeric(df_pag["Monto"], errors="coerce").fillna(0)
            pagos_sum = df_pag.groupby("Metodo_Pago")["Monto"].sum().reset_index()
            
            fig_pago = px.pie(
                pagos_sum, names="Metodo_Pago", values="Monto",
                hole=0.4, color_discrete_sequence=px.colors.qualitative.Pastel
            )
            st.plotly_chart(fig_pago, use_container_width=True)
        else:
            st.info("Sin datos de formas de pago.")

    st.divider()

    # --- SECCIÓN 2: MARCAS Y REPARTOS ---
    c3, c4 = st.columns(2)

    with c3:
        st.subheader("🏷️ Top Marcas por Facturación")
        if not df_det.empty and "Marca" in df_det.columns and "Subtotal" in df_det.columns:
            df_det["Subtotal"] = pd.to_numeric(df_det["Subtotal"], errors="coerce").fillna(0)
            df_det_marca = df_det.dropna(subset=["Marca"])
            top_marcas = df_det_marca.groupby("Marca")["Subtotal"].sum().reset_index()
            top_marcas = top_marcas.sort_values(by="Subtotal", ascending=False).head(5)
            
            fig_marcas = px.bar(
                top_marcas, x="Marca", y="Subtotal", text_auto="$.2s",
                color_discrete_sequence=["#28B463"]
            )
            fig_marcas.update_layout(yaxis_title="Total Facturado ($)", xaxis_title="")
            st.plotly_chart(fig_marcas, use_container_width=True)
        else:
            st.info("Sin información de marcas.")

    with c4:
        st.subheader("🚚 Ventas: Mostrador vs Reparto")
        if "Forma_Entrega" in df_cab.columns:
            entrega_df = df_cab["Forma_Entrega"].value_counts().reset_index()
            entrega_df.columns = ["Forma_Entrega", "Cantidad"]
            fig_entrega = px.pie(
                entrega_df, names="Forma_Entrega", values="Cantidad",
                color_discrete_sequence=["#F39C12", "#8E44AD"]
            )
            st.plotly_chart(fig_entrega, use_container_width=True)
        else:
            st.info("Sin datos sobre forma de entrega.")

    st.divider()

    # --- SECCIÓN 3: VENDEDORES Y CLIENTES ---
    c5, c6 = st.columns(2)

    with c5:
        st.subheader("👥 Ranking de Vendedores")
        if "Vendedor_Nombre" in df_cab.columns and "Total" in df_cab.columns:
            df_cab["Total"] = pd.to_numeric(df_cab["Total"], errors="coerce").fillna(0)
            vend_df = df_cab.groupby("Vendedor_Nombre")["Total"].sum().reset_index()
            vend_df = vend_df.sort_values(by="Total", ascending=False)
            
            fig_vend = px.bar(
                vend_df, x="Vendedor_Nombre", y="Total", text_auto="$.2s",
                color_discrete_sequence=["#16A085"]
            )
            fig_vend.update_layout(xaxis_title="", yaxis_title="Total Vendido ($)")
            st.plotly_chart(fig_vend, use_container_width=True)
        else:
            st.info("Sin información de vendedores.")

    with c6:
        st.subheader("⭐ Top 10 Clientes del Mes")
        if "Cliente_Nombre" in df_cab.columns:
            cli_df = df_cab[df_cab["Cliente_Nombre"] != "Cliente General"]
            if not cli_df.empty:
                top_cli = cli_df.groupby("Cliente_Nombre")["Total"].sum().reset_index()
                top_cli = top_cli.sort_values(by="Total", ascending=False).head(10)
                st.dataframe(
                    top_cli.rename(columns={"Cliente_Nombre": "Cliente", "Total": "Total Comprado ($)"}),
                    use_container_width=True, hide_index=True
                )
            else:
                st.info("Todas las ventas del mes fueron registradas a Cliente General / Mostrador.")

    st.divider()

    # --- SECCIÓN 4: MATRIZ DE RENTABILIDAD ---
    st.subheader("📊 Matriz de Rentabilidad por Producto")
    if not df_det.empty and "Ganancia_Bruta" in df_det.columns:
        rent_df = df_det.groupby("Nombre").agg(
            Unidades_Vendidas=("Cantidad", "sum"),
            Facturacion_Total=("Subtotal", "sum"),
            Ganancia_Total=("Ganancia_Bruta", "sum")
        ).reset_index()

        rent_df["Margen_%"] = (rent_df["Ganancia_Total"] / rent_df["Facturacion_Total"] * 100).round(2)
        rent_df = rent_df.sort_values(by="Ganancia_Total", ascending=False)

        st.dataframe(
            rent_df.style.format({
                "Facturacion_Total": "${:,.2f}",
                "Ganancia_Total": "${:,.2f}",
                "Margen_%": "{:.2f}%"
            }),
            use_container_width=True, hide_index=True
        )


def render_tab_compras(datos, mes_sel, anio_sel):
    """Renderiza la pestaña de Inteligencia de Compras."""
    if datos is None or datos["cabecera"].empty:
        st.warning(f"No hay registros de compras para el período seleccionado ({mes_sel} {anio_sel}).")
        return

    df_cab = datos["cabecera"]
    df_det = datos["detalle"]

    # --- METRICAS PRINCIPALES (KPIs) ---
    compras_totales = float(df_cab["Total_Compra"].sum()) if "Total_Compra" in df_cab.columns else 0.0
    total_recepciones = len(df_cab)
    compra_promedio = compras_totales / total_recepciones if total_recepciones > 0 else 0.0
    unidades_compradas = int(df_det["Cantidad"].sum()) if not df_det.empty and "Cantidad" in df_det.columns else 0

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("🛍️ Inversión Compras", f"${compras_totales:,.2f}")
    col2.metric("📦 Unidades Ingresadas", f"{unidades_compradas:,}")
    col3.metric("🧾 Facturas / Pedidos", f"{total_recepciones}")
    col4.metric("🎯 Pedido Promedio", f"${compra_promedio:,.2f}")

    st.divider()

    # --- SECCIÓN 1: PROVEEDORES Y METODOS DE PAGO ---
    c1, c2 = st.columns(2)

    with c1:
        st.subheader("🏢 Top Proveedores por Inversión")
        if "Proveedor_Nombre" in df_cab.columns and "Total_Compra" in df_cab.columns:
            df_cab["Total_Compra"] = pd.to_numeric(df_cab["Total_Compra"], errors="coerce").fillna(0)
            top_prov = df_cab.groupby("Proveedor_Nombre")["Total_Compra"].sum().reset_index()
            top_prov = top_prov.sort_values(by="Total_Compra", ascending=True).tail(10)

            fig_prov = px.bar(
                top_prov, x="Total_Compra", y="Proveedor_Nombre", orientation="h",
                text_auto="$.2s", color_discrete_sequence=["#8E44AD"]
            )
            fig_prov.update_layout(xaxis_title="Inversión Total ($)", yaxis_title="")
            st.plotly_chart(fig_prov, use_container_width=True)
        else:
            st.info("Sin datos de proveedores.")

    with c2:
        st.subheader("💳 Formas de Pago a Proveedores")
        if "Metodo_Pago" in df_cab.columns and "Total_Compra" in df_cab.columns:
            pago_prov = df_cab.groupby("Metodo_Pago")["Total_Compra"].sum().reset_index()
            fig_pago_c = px.pie(
                pago_prov, names="Metodo_Pago", values="Total_Compra",
                hole=0.4, color_discrete_sequence=px.colors.qualitative.Bold
            )
            st.plotly_chart(fig_pago_c, use_container_width=True)
        else:
            st.info("Sin datos sobre métodos de pago.")

    st.divider()

    # --- SECCIÓN 2: INVERSIÓN POR MARCA Y ARTÍCULOS MÁS COMPRADOS ---
    c3, c4 = st.columns(2)

    with c3:
        st.subheader("🏷️ Inversión por Marca Repuesta")
        if not df_det.empty and "Marca" in df_det.columns and "Subtotal" in df_det.columns:
            df_det["Subtotal"] = pd.to_numeric(df_det["Subtotal"], errors="coerce").fillna(0)
            df_det_marca = df_det.dropna(subset=["Marca"])
            top_marcas_c = df_det_marca.groupby("Marca")["Subtotal"].sum().reset_index()
            top_marcas_c = top_marcas_c.sort_values(by="Subtotal", ascending=False).head(5)

            fig_marcas_c = px.bar(
                top_marcas_c, x="Marca", y="Subtotal", text_auto="$.2s",
                color_discrete_sequence=["#D35400"]
            )
            fig_marcas_c.update_layout(yaxis_title="Inversión ($)", xaxis_title="")
            st.plotly_chart(fig_marcas_c, use_container_width=True)
        else:
            st.info("Sin información de marcas repuestas.")

    with c4:
        st.subheader("📦 Top 10 Productos Más Comprados")
        if not df_det.empty and "Nombre" in df_det.columns and "Cantidad" in df_det.columns:
            df_det["Cantidad"] = pd.to_numeric(df_det["Cantidad"], errors="coerce").fillna(0)
            top_prod_c = df_det.groupby("Nombre")["Cantidad"].sum().reset_index()
            top_prod_c = top_prod_c.sort_values(by="Cantidad", ascending=True).tail(10)

            fig_prod_c = px.bar(
                top_prod_c, x="Cantidad", y="Nombre", orientation="h",
                text="Cantidad", color_discrete_sequence=["#27AE60"]
            )
            fig_prod_c.update_layout(xaxis_title="Unidades Recibidas", yaxis_title="")
            st.plotly_chart(fig_prod_c, use_container_width=True)
        else:
            st.info("Sin detalle de unidades compradas.")

    st.divider()

    # --- SECCIÓN 3: MATRIZ DE REPOSICIÓN Y COSTOS DE COMPRA ---
    st.subheader("📋 Matriz de Historial de Reposición por Producto")
    if not df_det.empty and "Precio_Costo_Unitario" in df_det.columns:
        matriz_compra = df_det.groupby("Nombre").agg(
            Unidades_Compradas=("Cantidad", "sum"),
            Inversion_Total=("Subtotal", "sum"),
            Costo_Promedio_Unitario=("Precio_Costo_Unitario", "mean"),
            Ultimo_Costo_Registrado=("Precio_Costo_Unitario", "last")
        ).reset_index()

        matriz_compra = matriz_compra.sort_values(by="Inversion_Total", ascending=False)

        st.dataframe(
            matriz_compra.style.format({
                "Inversion_Total": "${:,.2f}",
                "Costo_Promedio_Unitario": "${:,.2f}",
                "Ultimo_Costo_Registrado": "${:,.2f}"
            }),
            use_container_width=True, hide_index=True
        )


# ==========================================
# 3. FUNCIÓN PRINCIPAL DEL MÓDULO
# ==========================================

def render_reportes(db):
    """Función principal de renderizado del módulo de Reportes con Pestañas."""
    if db is None:
        st.error("No se pudo establecer la conexión con la base de datos.")
        return

    st.title("📈 Panel de Reportes e Inteligencia de Negocio")

    # --- FILTROS DE FECHA GLOBALES ---
    meses_dict = {
        "Enero": 1, "Febrero": 2, "Marzo": 3, "Abril": 4,
        "Mayo": 5, "Junio": 6, "Julio": 7, "Agosto": 8,
        "Septiembre": 9, "Octubre": 10, "Noviembre": 11, "Diciembre": 12
    }

    col_f1, col_f2 = st.columns(2)
    with col_f1:
        mes_sel = st.selectbox("Mes", list(meses_dict.keys()), index=datetime.now().month - 1)
    with col_f2:
        anio_sel = st.selectbox("Año", [2024, 2025, 2026, 2027], index=2)

    mes_num = meses_dict[mes_sel]

    # --- PESTAÑAS PRINCIPALES ---
    tab_ventas, tab_compras = st.tabs(["📊 Inteligencia de Ventas", "🛒 Inteligencia de Compras"])

    with tab_ventas:
        with st.spinner("Consultando datos de Ventas..."):
            datos_v = cargar_datos_reportes_ventas(db, mes_num, anio_sel)
        render_tab_ventas(datos_v, mes_sel, anio_sel)

    with tab_compras:
        with st.spinner("Consultando datos de Compras..."):
            datos_c = cargar_datos_reportes_compras(db, mes_num, anio_sel)
        render_tab_compras(datos_c, mes_sel, anio_sel)
