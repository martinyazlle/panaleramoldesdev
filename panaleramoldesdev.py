import streamlit as st
from supabase import create_client # Importamos el cliente de Supabase
import pandas as pd
from datetime import datetime, timedelta
import re
import requests
import math
import json
import pydeck as pdk
import uuid

# --- CONFIGURACIÓN DE CONEXIÓN ---
# Cargamos los datos de forma segura desde secrets.toml
@st.cache_resource
def init_connection():
    # Lee específicamente la sección [produccion]
    url = st.secrets["produccion"]["SUPABASE_URL"]
    key = st.secrets["produccion"]["SUPABASE_KEY"]
    return create_client(url, key)

# Inicializamos la conexión globalmente
db = init_connection()

# 2. LÓGICA DE LOGIN
if 'logeado' not in st.session_state:
    st.session_state.logeado = False

if not st.session_state.logeado:
    st.title("🔐 Acceso al Sistema")
    usuario_input = st.text_input("Nombre de Usuario")
    password_input = st.text_input("Contraseña", type="password")
    
    if st.button("Iniciar Sesión"):
        # Consulta para verificar usuario y contraseña
        res = db.table("USUARIOS").select("*").eq("Nombre", usuario_input).eq("Contraseña", password_input).maybe_single().execute()
        
        if res.data:
            st.session_state.logeado = True
            st.session_state.usuario_actual = res.data['Nombre']
            st.session_state.rol = res.data['Rol'] # GUARDAMOS EL ROL AQUÍ
            st.rerun()
        else:
            st.error("Usuario o contraseña incorrectos")
            st.stop()

else:

    if 'lista_global_vta' not in st.session_state:
        st.session_state.lista_global_vta = "Automática (P1/P2)"

    # --- FUNCIONES DE UTILIDAD ---    
    def normalizar_numero(valor):
        """Convierte cualquier valor a float de forma segura."""
        try:
            if pd.isna(valor) or valor == "":
                return 0.0
            valor_str = str(valor).replace('.', '').replace(',', '.')
            return float(valor_str)
        except:
            return 0.0

    def asegurar_float(val):
        try:
            s = str(val).replace(',', '.').strip()
            return float(s) if s and s != '' else 0.0
        except:
            return 0.0

    def formato_moneda(valor):
        return f"{valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

    def obtener_turno_activo():
        # Consulta a Supabase: busca el primer turno que esté "Abierto"
        respuesta = db.table("CONTROL_TURNOS").select("*").eq("Estado", "Abierto").execute()
        turnos = respuesta.data
        
        if len(turnos) > 0:
            return turnos[0] # Retorna el primer turno abierto encontrado
        return None # Si no hay ninguno, retorna None

    def iniciar_turno(monto_inicial, usuario):
        # Generamos un ID único simple para el turno (ej: fecha y hora)
        id_turno = datetime.now().strftime("%Y%m%d%H%M%S")
        
        db.table("CONTROL_TURNOS").insert({
            "ID_Turno": id_turno,
            "Usuario": usuario,
            "Fecha_Hora_Apertura": datetime.now().isoformat(),
            "Monto_Apertura": float(monto_inicial),
            "Estado": "Abierto"
        }).execute()
        
        # Registramos también el movimiento inicial en la tabla CAJA
        db.table("CAJA").insert({
            "ID_Turno": id_turno,
            "Fecha": datetime.now().isoformat(),
            "Tipo": "Ingreso",
            "Concepto": "APERTURA DE CAJA",
            "Monto": float(monto_inicial),
            "Forma_Pago": "Efectivo"
        }).execute()

    def modulo_ventas():
        st.header("📋 Historial de Ventas")
        try:
            # Usamos .limit(100000) para traer todo de una vez
            # Además, eliminamos el '*' y listamos columnas si es necesario para evitar errores
            respuesta = db.table("VENTAS_CABECERA").select("ID_Venta, Fecha, ID_Cliente, ID_Vendedor, Total, Forma_Pago, Estado, VENTAS_DETALLE(*)").limit(100000).execute()
            
            df_ventas = pd.DataFrame(respuesta.data)

            # 1. FORZAR LA LIMPIEZA DE TOTALES SIN DEPENDER DE FUNCIONES EXTERNAS
            # Convertimos todo a string, quitamos posibles espacios, reemplazamos comas por puntos
            # y forzamos la conversión a numérico (float)
            df_ventas['Total'] = (df_ventas['Total']
                                .replace({',': ''}, regex=True) # Si hay comas de miles
                                .astype(str)
                                .str.replace(',', '.')         # Asegurar punto decimal
                                .apply(pd.to_numeric, errors='coerce'))
            
            # Llenar posibles nulos con 0 para que la suma no falle
            df_ventas['Total'] = df_ventas['Total'].fillna(0)
            
            st.write(f"Filas totales cargadas: {len(df_ventas)}")
            
            # Necesitamos clientes y productos para mostrar nombres en lugar de IDs
            df_clientes = pd.DataFrame(db.table("CLIENTES").select("ID_Cliente, Nombre, Apellido").execute().data)
            df_prod = pd.DataFrame(db.table("PRODUCTOS").select("ID_Producto, Nombre").execute().data)
            df_vend = pd.DataFrame(db.table("VENDEDORES").select("ID_Vendedor, Nombre, Apellido").execute().data)
            
            # --- 1. APLICAR LIMPIEZA ANTES DEL MERGE ---
            # Convertimos todas las llaves foráneas a string para asegurar el match
            df_ventas['ID_Cliente'] = df_ventas['ID_Cliente'].astype(str)
            df_ventas['ID_Vendedor'] = df_ventas['ID_Vendedor'].astype(str)

            df_clientes['ID_Cliente'] = df_clientes['ID_Cliente'].astype(str)
            df_vend['ID_Vendedor'] = df_vend['ID_Vendedor'].astype(str)

            # --- 2. AHORA REALIZAMOS LOS MERGES ---
            df_ventas = df_ventas.merge(df_clientes, on="ID_Cliente", how="left")
            df_ventas['Cliente_Full'] = df_ventas['Nombre'].fillna("Sin Nombre") + " " + df_ventas['Apellido'].fillna("")

            df_ventas = df_ventas.merge(df_vend, on="ID_Vendedor", how="left", suffixes=('_vta', '_vend'))
            df_ventas['Vendedor_Full'] = df_ventas['Nombre_vend'].fillna("Sin Vendedor") + " " + df_ventas['Apellido_vend'].fillna("")

            # --- 3. LIMPIEZA DE FECHAS ---
            # Aseguramos que la columna Fecha sea tipo fecha real
            df_ventas['Fecha'] = pd.to_datetime(df_ventas['Fecha']).dt.date
        except Exception as e:
            st.error(f"Error al cargar datos: {e}")
            return

        # 2. Filtros
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            rango_fechas = st.date_input("Rango de fechas", value=(pd.to_datetime(df_ventas['Fecha']).min(), pd.to_datetime(df_ventas['Fecha']).max()))
        with c2:
            cliente_filtro = st.selectbox("Cliente", ["Todos"] + df_ventas['Cliente_Full'].unique().tolist())
        with c3:
            vendedor_filtro = st.selectbox("Vendedor", ["Todos"] + df_ventas['Vendedor_Full'].unique().tolist())
        with c4:
            pago_filtro = st.selectbox("Pago", ["Todos"] + df_ventas['Forma_Pago'].unique().tolist())

        # 3. Aplicar Filtros
        df_f = df_ventas.copy()
        if len(rango_fechas) == 2:
            df_f = df_f[(pd.to_datetime(df_f['Fecha']).dt.date >= rango_fechas[0]) & (pd.to_datetime(df_f['Fecha']).dt.date <= rango_fechas[1])]
        
        if cliente_filtro != "Todos": df_f = df_f[df_f['Cliente_Full'] == cliente_filtro]
        if vendedor_filtro != "Todos": df_f = df_f[df_f['Vendedor_Full'] == vendedor_filtro]
        if pago_filtro != "Todos": df_f = df_f[df_f['Forma_Pago'] == pago_filtro]

        # --- AUDITORÍA DE DATOS (Pon esto antes de la sección 4) ---
        st.divider()
        st.subheader("🔍 Auditoría de Diferencias")
        col_a, col_b = st.columns(2)
        with col_a:
            st.metric("Total en DF_Ventas (Original)", f"${df_ventas['Total'].sum():,.2f}")
        with col_b:
            st.metric("Total en DF_F (Filtrado)", f"${df_f['Total'].sum():,.2f}")
        
        # Esto te dirá exactamente cuántas filas se pierden y por qué
        st.write(f"Filas totales: {len(df_ventas)} | Filas tras filtros: {len(df_f)}")
        
        # Si detectas una diferencia, mira los clientes o vendedores nulos
        if df_f['Cliente_Full'].str.contains("Sin Nombre").sum() > 0:
            st.warning(f"¡Atención! Hay {df_f['Cliente_Full'].str.contains('Sin Nombre').sum()} ventas con 'Sin Nombre'. Esto puede estar afectando tus filtros.")

        # 4. Mostrar Tabla Principal y Sumatoria
        st.dataframe(df_f[['ID_Venta', 'Fecha', 'Cliente_Full', 'Vendedor_Full', 'Total', 'Forma_Pago']], use_container_width=True)
        st.metric("Total Acumulado Filtrado", f"${df_f['Total'].sum():,.2f}")

        # 5. Detalle de Venta
        st.subheader("Detalle de Venta Seleccionada")
        id_sel = st.text_input("Ingrese ID de Venta para ver detalle:")
        
        if id_sel:
            # Buscamos en el DF original (o el filtrado, según prefieras)
            # Usamos df_ventas (el completo) para asegurar que encuentre el ID si el usuario lo escribe
            venta_sel = df_ventas[df_ventas['ID_Venta'].astype(str) == id_sel]
            
            if not venta_sel.empty:
                detalles = venta_sel.iloc[0]['VENTAS_DETALLE']
                df_det = pd.DataFrame(detalles)
                
                # Unir con productos para obtener nombre
                df_det = df_det.merge(df_prod, on="ID_Producto", how="left")
                
                # Ordenar columnas como pediste
                columnas_ordenadas = ['ID_Venta', 'Nombre', 'Precio_Unitario', 'Cantidad', 'Subtotal']
                
                # Mostrar tabla
                st.table(df_det[columnas_ordenadas])
                
                # --- AGREGAMOS EL TOTALIZADOR ---
                total_detalle = df_det['Subtotal'].sum()
                st.markdown(f"### **Total de la Venta {id_sel}: ${total_detalle:,.2f}**")
                
                # --- BOTÓN DE ANULACIÓN (CORREGIDO) ---
                estado_actual = venta_sel.iloc[0].get('Estado', 'ACTIVA')
                
                if estado_actual != "ANULADA":
                    # Aquí llamamos a la función completa que ya definimos
                    if st.button("🚫 ANULAR ESTA VENTA", type="primary"):
                        try:
                            # Llamamos a la función que ya gestiona:
                            # 1. Reversa de Pagos/Caja
                            # 2. Devolución de Stock
                            # 3. Marcado como Anulada
                            anular_venta(id_sel)
                            
                            st.success("✅ Venta anulada, stock devuelto y caja ajustada correctamente.")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Error al anular: {e}")
                else:
                    st.warning("⚠️ Esta venta ya se encuentra ANULADA.")
            else:
                st.error("Venta no encontrada.")

    def anular_venta(id_vta_a_anular):
        # 1. Buscamos el turno abierto
        turno_res = db.table("CONTROL_TURNOS").select("ID_Turno").eq("Estado", "Abierto").maybe_single().execute()
        id_turno_actual = turno_res.data['ID_Turno'] if (turno_res and turno_res.data) else "SIN_TURNO"
        
        # 2. REVERSA DE PAGOS Y CAJA
        pagos_de_la_venta = db.table("VENTAS_PAGOS").select("*").eq("ID_Venta", id_vta_a_anular).execute().data
        
        for p in pagos_de_la_venta:
            metodo = p["Metodo_Pago"]
            monto = float(p["Monto"])
            
            # Egreso de caja por la anulación
            db.table("CAJA").insert({
                "ID_Turno": id_turno_actual,
                "Fecha": datetime.now().isoformat(),
                "Tipo": "Egreso",
                "Concepto": f"ANULACIÓN Venta {id_vta_a_anular} ({metodo})",
                "Monto": monto,
                "Forma_Pago": metodo
            }).execute()
            
            # Reversa de compensación (si no fue efectivo)
            if metodo != "Efectivo":
                db.table("CAJA").insert({
                    "ID_Turno": id_turno_actual,
                    "Fecha": datetime.now().isoformat(),
                    "Tipo": "Ingreso",
                    "Concepto": f"REVERSA RETIRO {metodo.upper()}",
                    "Monto": monto,
                    "Forma_Pago": metodo
                }).execute()

        # 3. DEVOLUCIÓN DE STOCK
        detalle_venta = db.table("VENTAS_DETALLE").select("*").eq("ID_Venta", id_vta_a_anular).execute().data
        
        for item in detalle_venta:
            id_prod = item['ID_Producto']
            cant_vendida = item['Cantidad']
            
            # Obtener stock actual
            prod_res = db.table("PRODUCTOS").select("Stock_Actual").eq("ID_Producto", id_prod).single().execute()
            if prod_res.data:
                stock_actual = int(prod_res.data.get('Stock_Actual', 0))
                # Sumamos la cantidad vendida al stock actual
                db.table("PRODUCTOS").update({"Stock_Actual": stock_actual + cant_vendida}) \
                    .eq("ID_Producto", id_prod).execute()

        # 4. MARCAR COMO ANULADA
        db.table("VENTAS_CABECERA").update({"Estado": "Anulada"}).eq("ID_Venta", id_vta_a_anular).execute()        

    def procesar_seleccion_manual():
        seleccion = st.session_state.prod_manual_key
        if seleccion:
            # Extraemos el ID del texto "Nombre - ID"
            id_seleccionado = seleccion.split(" - ")[-1]
            
            # Buscamos el producto en tu dataframe usando el ID extraído
            producto = df_prod[df_prod['ID_Producto'].astype(str) == id_seleccionado].iloc[0]
            
            # CORRECCIÓN DE LA INDENTACIÓN Y DE LA VARIABLE 'producto'
            st.session_state.carrito_vta.append({
                "id": str(producto['ID_Producto']), 
                "nombre": producto['Nombre'], 
                "cantidad": 1,
                "precio": float(producto['Precio_1'] or 0), 
                "subtotal": float(producto['Precio_1'] or 0)
            })
            
            # IMPORTANTE: Reseteamos el selector para que no se repita
            st.session_state.prod_manual_key = None

    def procesar_escaneo():
        barcode = st.session_state.barcode_input
        if barcode:
            # Buscar producto
            res = df_prod[df_prod['ID_Producto'].astype(str) == str(barcode)]
            if not res.empty:
                p = res.iloc[0]
                st.session_state.carrito_vta.append({
                    "id": str(p['ID_Producto']), 
                    "nombre": p['Nombre'], 
                    "cantidad": 1,
                    "precio": float(p['Precio_1']), 
                    "subtotal": float(p['Precio_1'])
                })
            st.session_state.barcode_input = ""

    def modulo_config_pagos():
        st.subheader("⚙️ Configuración de Formas de Pago")
        
        # Formulario para agregar nuevo
        with st.form("nuevo_pago"):
            nuevo_pago = st.text_input("Nombre del nuevo medio de pago")
            if st.form_submit_button("Agregar"):
                db.table("FORMAS_PAGO").insert({"Nombre_Pago": nuevo_pago, "Activo": True}).execute()
                st.rerun()

        # Mostrar existentes para desactivar
        pagos = db.table("FORMAS_PAGO").select("*").execute().data
        for p in pagos:
            col1, col2 = st.columns([3, 1])
            col1.write(p['Nombre_Pago'])
            if col2.button("Desactivar", key=f"del_{p['ID_Pago']}"):
                db.table("FORMAS_PAGO").update({"Activo": False}).eq("ID_Pago", p['ID_Pago']).execute()
                st.rerun()

    def calcular_y_actualizar_stock_automatico():
        # 1. Definir rango de fechas
        hace_60_dias = (datetime.now() - timedelta(days=60)).strftime('%Y-%m-%d')

        # 2. Traer ventas de los últimos 60 días (Join básico)
        # Obtenemos detalle y traemos la fecha desde la cabecera
        ventas = db.table("VENTAS_DETALLE").select("ID_Producto, Cantidad, VENTAS_CABECERA(Fecha)").gte("VENTAS_CABECERA.Fecha", hace_60_dias).execute().data
        
        # 3. Procesar con Pandas
        df_ventas = pd.DataFrame(ventas)
        # Aplanamos la estructura de la relación
        df_ventas['Fecha'] = df_ventas['VENTAS_CABECERA'].apply(lambda x: x[0]['Fecha'] if isinstance(x, list) else None)
        
        # Sumar cantidades por producto
        rotacion = df_ventas.groupby('ID_Producto')['Cantidad'].sum().reset_index()
        
        # 4. Calcular y Actualizar
        for _, fila in rotacion.iterrows():
            id_prod = fila['ID_Producto']
            total_vendido = fila['Cantidad']
            
            # Fórmulas
            promedio_diario = total_vendido / 60
            stock_min = max(1, int(promedio_diario * 7))  # Mínimo 1 para evitar errores
            stock_max = int(promedio_diario * 30)
            
            # Actualizar en Supabase
            db.table("PRODUCTOS").update({
                "Stock_Min": stock_min,
                "Stock_Max": stock_max
            }).eq("ID_Producto", id_prod).execute()
            
        return True

    def resetear_punto_venta():
        # Lista de claves que queremos limpiar
        keys_a_limpiar = [
            'carrito_vta', 'pagos_split', 'id_cliente_recuperado', 
            'tipo_entrega', 'direccion_entrega', 'link_maps_entrega', 
            'fecha_reparto', 'id_pendiente_cargado', 'prod_manual_key'
        ]
        for key in keys_a_limpiar:
            if key in st.session_state:
                del st.session_state[key]
        
        # Opcional: recargar estados por defecto necesarios
        st.session_state.carrito_vta = []
        st.session_state.pagos_split = [{"metodo": "Efectivo", "monto": 0.0}]
        st.rerun()

    def mostrar_reporte_utilidad():
        st.subheader("📈 Reporte de Rentabilidad Detallado")
        
        # 1. Traer datos
        # Necesitamos la fecha de la venta, así que traemos VENTAS_CABECERA también
        ventas_det = db.table("VENTAS_DETALLE").select("ID_Venta, ID_Producto, Cantidad, Precio_Unitario, Precio_Costo_Unitario").execute().data
        ventas_cab = db.table("VENTAS_CABECERA").select("ID_Venta, Fecha").execute().data
        prods = db.table("PRODUCTOS").select("ID_Producto, Nombre, Rubro, Marca").execute().data
        
        df_vd = pd.DataFrame(ventas_det)
        df_vc = pd.DataFrame(ventas_cab)
        df_p = pd.DataFrame(prods)
        
        # Unir datos
        df = df_vd.merge(df_vc, on="ID_Venta").merge(df_p, on="ID_Producto")
        df['Fecha'] = pd.to_datetime(df['Fecha'])
        df['Utilidad_Bruta'] = df['Cantidad'] * (df['Precio_Unitario'] - df['Precio_Costo_Unitario'])
        
        # 2. FILTROS EN LA BARRA LATERAL O SUPERIOR
        st.write("---")
        c1, c2 = st.columns(2)
        
        # Filtro Fecha
        fecha_inicio = c1.date_input("Desde", df['Fecha'].min())
        fecha_fin = c2.date_input("Hasta", df['Fecha'].max())
        
        # Filtros multiselección
        rubros = st.multiselect("Filtrar por Rubro", df['Rubro'].unique())
        marcas = st.multiselect("Filtrar por Marca", df['Marca'].unique())
        nombres = st.multiselect("Filtrar por Producto", df['Nombre'].unique())
        
        # Aplicar filtros
        mask = (df['Fecha'].dt.date >= fecha_inicio) & (df['Fecha'].dt.date <= fecha_fin)
        if rubros: mask &= df['Rubro'].isin(rubros)
        if marcas: mask &= df['Marca'].isin(marcas)
        if nombres: mask &= df['Nombre'].isin(nombres)
        
        df_filtrado = df[mask]
        
        # 3. Visualización
        st.metric("💰 Utilidad Total Filtrada", f"${df_filtrado['Utilidad_Bruta'].sum():,.2f}")
        
        st.dataframe(df_filtrado[['Fecha', 'Nombre', 'Rubro', 'Marca', 'Cantidad', 'Utilidad_Bruta']])

    def obtener_coordenadas(link_maps):
        """
        Intenta extraer coordenadas de un link de Google Maps acortado.
        Como los links de google (goo.gl o maps.app.goo.gl) son redirecciones,
        primero resolvemos la URL final y luego buscamos los números en el texto.
        """
        try:
            # Resolvemos el link corto a la URL real
            response = requests.head(link_maps, allow_redirects=True)
            url_final = response.url
            
            # Buscamos patrones de coordenadas en la URL (ej: /@lat,lng)
            # Esto busca números decimales separados por coma después de un @
            coordenadas = re.findall(r'@(-?\d+\.\d+),(-?\d+\.\d+)', url_final)
            
            if coordenadas:
                return float(coordenadas[0][0]), float(coordenadas[0][1])
        except:
            return None, None
        return None, None

    def calcular_distancia(coord1, coord2):
        # Fórmula de Haversine para calcular distancia en línea recta entre dos puntos
        lat1, lon1 = coord1
        lat2, lon2 = coord2
        R = 6371  # Radio de la tierra en km
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
        return R * c
    
    def optimizar_ruta(origen, destinos):
        """
        Ordena los destinos usando el algoritmo del 'vecino más cercano'
        origen: (lat, lng)
        destinos: lista de diccionarios con {'Cliente': '...', 'Latitud': x, 'Longitud': y}
        """
        ruta_ordenada = []
        pendientes = destinos.copy()
        actual = origen
        
        while pendientes:
            # Busca el destino más cercano al punto actual
            mas_cercano = min(pendientes, key=lambda p: calcular_distancia(actual, (p['Latitud'], p['Longitud'])))
            ruta_ordenada.append(mas_cercano)
            actual = (mas_cercano['Latitud'], mas_cercano['Longitud'])
            pendientes.remove(mas_cercano)
            
        return ruta_ordenada

    def generar_diagrama_optimizada(grupo_repartos, punto_origen, fecha):
        repartos_validos = grupo_repartos.dropna(subset=['Latitud', 'Longitud'])
        ruta_optima = optimizar_ruta(punto_origen, repartos_validos.to_dict('records'))
        
        # 2. Inicializamos el estado del orden
        if f"orden_{fecha}" not in st.session_state:
            st.session_state[f"orden_{fecha}"] = {v['Cliente']: i+1 for i, v in enumerate(ruta_optima)}
    
        st.write("### 🗺️ Previsualización de Ruta")
    
        # 1. Blindaje: Verificamos si hay datos antes de intentar crear el mapa
        if not ruta_optima:
            st.warning("No hay suficientes datos con coordenadas para mostrar el mapa.")
        else:
            # Creamos el DataFrame y forzamos a que las coordenadas sean números
            df_mapa = pd.DataFrame(ruta_optima)
            df_mapa['Latitud'] = pd.to_numeric(df_mapa['Latitud'], errors='coerce')
            df_mapa['Longitud'] = pd.to_numeric(df_mapa['Longitud'], errors='coerce')
            
            # Limpiamos filas con coordenadas nulas después de la conversión
            df_mapa = df_mapa.dropna(subset=['Latitud', 'Longitud'])
            
            if df_mapa.empty:
                st.warning("No se pudieron procesar las coordenadas para el mapa.")
            else:
                # Renombramos para PyDeck
                df_mapa = df_mapa.rename(columns={'Latitud': 'lat', 'Longitud': 'lon'})
                
                # Creamos el mapa solo si hay datos válidos
                st.pydeck_chart(pdk.Deck(
                    map_style=None,
                    initial_view_state=pdk.ViewState(
                        latitude=df_mapa['lat'].mean(),
                        longitude=df_mapa['lon'].mean(),
                        zoom=12,
                        pitch=0,
                    ),
                    layers=[
                        pdk.Layer(
                            'ScatterplotLayer',
                            df_mapa,
                            get_position='[lon, lat]',
                            get_color='[200, 30, 0, 160]',
                            get_radius=100,
                        ),
                        pdk.Layer(
                            'TextLayer',
                            df_mapa,
                            get_position='[lon, lat]',
                            get_text='Cliente',
                            get_color='[0, 0, 0, 200]',
                            get_size=16,
                            get_alignment_baseline='"bottom"',
                            get_pixel_offset='[0, -15]',
                        ),
                    ],
                ))
        
        # Formulario de orden
        with st.form(key=f"form_orden_{fecha}"):
            orden_manual = {}
            for idx, v in enumerate(ruta_optima):
                orden_manual[v['Cliente']] = st.number_input(
                    f"Orden para {v['Cliente']}", min_value=1, max_value=len(ruta_optima),
                    value=st.session_state.get(f"pos_{v['Cliente']}_{fecha}", idx + 1)
                )
            submit = st.form_submit_button("Aplicar nuevo orden")
            
            if submit:
                # Guardamos los nuevos valores en session_state
                for cliente, valor in orden_manual.items():
                    st.session_state[f"pos_{cliente}_{fecha}"] = valor
                st.rerun() # Fuerza la recarga para que se ordene la lista
    
        # Generación de lista final
        # Usamos el orden guardado en session_state o el original
        ruta_reordenada = sorted(ruta_optima, key=lambda x: st.session_state.get(f"pos_{x['Cliente']}_{fecha}", 0))
        
        # 3. Mostrar resultados finales
        st.write("### 🚚 Ruta Optimizada Final")
        texto_whatsapp = f"*DIAGRAMA DE REPARTOS {fecha}*\n\n"
        
        for i, v in enumerate(ruta_reordenada, 1):
            monto = "0"
            try:
                if v.get('Pagos_JSON'):
                    pagos = json.loads(v['Pagos_JSON'])
                    if isinstance(pagos, list) and len(pagos) > 0:
                        monto = pagos[0].get('monto', '0')
            except:
                monto = "0"
            
            st.write(f"{i}. **{v['Cliente']}** - ${monto} - {v.get('Metodo_Pago', 'N/A')}")
            texto_whatsapp += f"{i}. {v['Cliente']} ${monto} {v.get('Metodo_Pago', 'N/A')}\n"
        
        st.divider()
        st.text_area("Selecciona y copia:", value=texto_whatsapp, height=200)

    def extraer_coords_desde_link(link):
        # Busca el patrón @-XX.XXXX,-YY.YYYY en el link
        match = re.search(r'@(-?\d+\.\d+),(-?\d+\.\d+)', link)
        if match:
            return float(match.group(1)), float(match.group(2))
        return None # Si no encuentra nada

    # --- DIÁLOGO DE ALTA RÁPIDA ---
    # --- DIÁLOGO DE ALTA RÁPIDA ---
    @st.dialog("➕ Nuevo Cliente Rápido")
    def abrir_alta_cliente_rapida():
        with st.form("form_nuevo_cliente_rapido"):
            nombre = st.text_input("Nombre*")
            apellido = st.text_input("Apellido*")
            telefono = st.text_input("Teléfono* (10 dígitos)", max_chars=10)
            dir1 = st.text_input("Dirección 1*")
            link1 = st.text_input("Link Dirección 1 (Google Maps)") # <--- NUEVO CAMPO
            zona = st.selectbox("Zona*", ["NORTE", "SUR", "CENTRO", "ESTE", "OESTE", "SANLO CHICO"])
            
            submitted = st.form_submit_button("Guardar Cliente")
            if submitted:
                if not all([nombre, apellido, telefono, dir1]):
                    st.error("Faltan completar campos obligatorios")
                else:
                    nuevo_cliente = {
                        "Nombre": nombre.upper(), 
                        "Apellido": apellido.upper(),
                        "Telefono": telefono, 
                        "Direccion_1": dir1.upper(),
                        "Link_Direccion_1": link1, # <--- SE AGREGA AL GUARDADO
                        "Zona": zona, 
                        "Tipo_Cliente": "CONSUMIDOR FINAL"
                    }
                    db.table("CLIENTES").insert(nuevo_cliente).execute()
                    st.success("✅ Cliente guardado!")
                    st.rerun()

    @st.dialog("➕ Asignar Nueva Gift Card")
    def abrir_asignacion_gift_card(id_cliente, nombre_cliente):
        st.write(f"Asignando Gift Card a: **{nombre_cliente}**")
        
        with st.form("form_asignar_gift"):
            monto = st.number_input("Monto inicial de la Gift Card", min_value=0.0, step=100.0)
            
            # Obtenemos las formas de pago disponibles
            metodos_db = db.table("FORMAS_PAGO").select("Nombre_Pago").eq("Activo", True).execute()
            opciones = [item['Nombre_Pago'] for item in metodos_db.data] if metodos_db.data else ["Efectivo"]
            forma_pago = st.selectbox("Forma de pago de la Gift Card", opciones)
            
            if st.form_submit_button("Confirmar Emisión"):
                nueva_gc = {
                    "ID_GiftCard": str(uuid.uuid4()), 
                    "ID_Cliente": int(id_cliente), # Ya aseguramos que es int8/bigint
                    "Saldo_Actual": float(monto),
                    "Saldo_Inicial": float(monto), # <--- NUEVO
                    "Forma_Pago_Adquisicion": forma_pago, # <--- NUEVO
                    "Estado": True,
                    "Fecha_Creacion": datetime.now().isoformat()
                }

                try:
                    db.table("GIFT_CARDS").insert(nueva_gc).execute()
                    st.success(f"✅ Gift Card de ${monto:,.2f} asignada!")
                    st.rerun()
                except Exception as e:
                    st.error(f"Error al guardar en la base de datos: {e}")
    
    # --- CONFIGURACIÓN ESTÉTICA ---
    st.set_page_config(page_title="Pañalera Moldes - ERP", layout="wide")

    LISTA_RUBROS = [
        "ACEITE", "ACONDICIONADOR", "ALGODON", 
        "APOSITOS", "BAÑO LIQUIDO", "CAMBIADOR", "CHUPETE", 
        "COLONIA", "CREMA", "CUCHARAS", "DESCONGESTIONADORES NASALES", 
        "ESPONJA", "HIGIENE BUCAL", "HISOPOS", "JABON", 
        "LECHE", "LIMPIEZA ROPA", "MAMADERA", "MOCHILA MATERNAL", "MORDILLOS", 
        "OLEO CALCAREO", "PAÑALES", "PLATOS", "PROTECTOR MAMARIO", 
        "SACALECHES", "SEGURIDAD", "SHAMPOO", "TALCO", 
        "TETINAS", "TIJERAS", "TOALLITAS FEMENINAS", 
        "TOALLITAS HUMEDAS", "VASOS"
    ]

    # --- SIDEBAR CON PERMISOS ---
    with st.sidebar:
        st.title("🛡️ Pañalera Moldes")
        st.write(f"👤 Usuario: {st.session_state.usuario_actual}")
        st.write(f"💼 Rol: {st.session_state.rol}")
        
        # Lógica de permisos para el menú
        opciones_disponibles = ["💰 Caja"]
        
        if st.session_state.rol == "Administrador":
            # Agregamos "📈 Reporte de Utilidades" a la lista
            opciones_disponibles.extend([
                "🛒 Punto de Venta", "👥 Clientes", "📋 Historial de Ventas", 
                "⚙️ Configuración Pagos", "🚚 Gestión de Repartos", "📦 Productos",
                "📦 Stock", "🚚 Proveedores", "📦 Compras", "👥 Vendedores", 
                "📈 Estadísticas", "📈 Reporte de Utilidades" # <--- AQUÍ LO AGREGAMOS
            ])
        elif st.session_state.rol == "Vendedor":
            opciones_disponibles.extend(["🛒 Punto de Venta", "📦 Productos", "👥 Clientes"])
        
        menu = st.selectbox("Menú Principal", opciones_disponibles)
        
        st.divider()
        if st.button("🚪 Cerrar Sesión"):
            st.session_state.logeado = False
            st.rerun()

    # --- LÓGICA DE MÓDULOS ---

    # =====================================================================
    # MODULO: 👥 CLIENTES
    # =====================================================================
    if menu == "👥 Clientes":
        st.header("👥 Gestión de Clientes")
        
        # 1. Lectura de datos
        try:
            response = db.table("CLIENTES").select("*").execute()
            df_clientes = pd.DataFrame(response.data)
        except Exception as e:
            st.error(f"Error al conectar con Supabase: {e}")
            st.stop()

        # 2. DEFINIR PESTAÑAS DINÁMICAS SEGÚN ROL
        if st.session_state.rol == "Administrador":
            nombres_tabs = ["🔍 Explorador", "➕ Nuevo Cliente", "✏️ Modificar"]
        else:
            # El Vendedor solo ve estas dos
            nombres_tabs = ["➕ Nuevo Cliente", "✏️ Modificar"]

        # Creamos las pestañas en una sola llamada
        tabs = st.tabs(nombres_tabs)

        # 3. ASIGNAR PESTAÑAS SEGÚN ROL
        if st.session_state.rol == "Administrador":
            tab_explorador, tab_nuevo, tab_modificar = tabs
        else:
            tab_explorador = None
            tab_nuevo, tab_modificar = tabs

        # 4. CONTENIDO (Solo se ejecuta si la pestaña existe)
        
        if tab_explorador:
            with tab_explorador:
                st.subheader("Buscador de Clientes")
                query = st.text_input("Buscar por nombre, apellido, DNI, CUIT, teléfono o dirección...")
                if query:
                    mask = (df_clientes.apply(lambda row: row.astype(str).str.contains(query, case=False).any(), axis=1))
                    st.dataframe(df_clientes[mask], use_container_width=True)
                else:
                    st.dataframe(df_clientes, use_container_width=True)
            
        with tab_nuevo:
            with st.form("form_nuevo_cliente"):
                c1, c2 = st.columns(2)
                with c1:
                    nombre = st.text_input("Nombre*")
                    apellido = st.text_input("Apellido*")
                    dni = st.text_input("DNI", max_chars=8)
                    cuit = st.text_input("CUIT", max_chars=13)
                    telefono = st.text_input("Teléfono* (10 dígitos)", max_chars=10)
                with c2:
                    dir1 = st.text_input("Dirección 1*")
                    link1 = st.text_input("Link Dirección 1")
                    dir2 = st.text_input("Dirección 2")
                    link2 = st.text_input("Link Dirección 2")
                    dir3 = st.text_input("Dirección 3")
                    link3 = st.text_input("Link Dirección 3")
                    zona = st.selectbox("Zona*", ["NORTE", "SUR", "CENTRO", "ESTE", "OESTE", "SANLO CHICO"])
                    tipo = st.selectbox("Tipo Cliente", ["CONSUMIDOR FINAL", "EMPRESA/ORGANISMO"])
                
                submitted = st.form_submit_button("Guardar Cliente")
                
                if submitted:
                    if not all([nombre, apellido, telefono, dir1]):
                        st.error("Faltan completar campos obligatorios")
                    elif telefono in df_clientes['Telefono'].astype(str).values:
                        st.error("⚠️ Ya existe un cliente con este teléfono!")
                    else:
                        nuevo_cliente = {
                            "Nombre": nombre.upper(), "Apellido": apellido.upper(), "DNI": dni,
                            "CUIT": cuit, "Telefono": telefono, "Direccion_1": dir1.upper(),
                            "Direccion_2": dir2.upper(), "Direccion_3": dir3.upper(),
                            "Link_Direccion_1": link1, "Link_Direccion_2": link2,
                            "Link_Direccion_3": link3, "Zona": zona, "Tipo_Cliente": tipo
                        }
                        db.table("CLIENTES").insert(nuevo_cliente).execute()
                        st.success("✅ Cliente cargado!")
                        st.rerun()

        if tab_modificar is not None:
            with tab_modificar:
                st.subheader("Modificar Cliente Existente")

                # 1. Selector (Mover esto fuera del 'if seleccion')
                lista_clientes = df_clientes['Nombre'].astype(str) + " " + df_clientes['Apellido'].astype(str) + " (ID: " + df_clientes['ID_Cliente'].astype(str) + ")"
                seleccion = st.selectbox("Seleccione el cliente", [""] + lista_clientes.tolist(), key="sel_modificar")
                
                # Ahora evaluamos seleccion aquí:
                if seleccion:
                    id_modificar = seleccion.split("(ID: ")[1].replace(")", "")
                    fila = df_clientes[df_clientes['ID_Cliente'].astype(str) == id_modificar].iloc[0]

                    # --- AQUÍ ES DONDE DEBE IR ---
                    # Primero buscamos si tiene gift card activa
                    gc_data = db.table("GIFT_CARDS").select("*").eq("ID_Cliente", int(id_modificar)).eq("Estado", True).execute().data
                    
                    if gc_data:
                        gc = gc_data[0]
                        st.info(f"""
                        **Detalles de Gift Card Activa:**
                        - Saldo Inicial: ${gc['Saldo_Inicial']:,.2f}
                        - Saldo Actual: ${gc['Saldo_Actual']:,.2f}
                        - Pagada con: {gc['Forma_Pago_Adquisicion']}
                        """)
                    # -----------------------------
                    
                    # --- NUEVO: BOTÓN ASIGNAR GIFT CARD (ESTO SOLO LO VE EL ADMINISTRADOR) ---
                    if st.session_state.get('rol') == "Administrador":
                        if st.button("🎁 Gestionar Gift Card"):
                            abrir_asignacion_gift_card(id_modificar, f"{fila['Nombre']} {fila['Apellido']}")
                    # --------------------------------------------------------------------------

                    # 2. Formulario
                    with st.form("form_datos"):
                        c1, c2 = st.columns(2)
                        with c1:
                            nuevo_nombre = st.text_input("Nombre", value=fila.get('Nombre', ''))
                            nuevo_apellido = st.text_input("Apellido", value=fila.get('Apellido', ''))
                            nuevo_dni = st.text_input("DNI", value=fila.get('DNI', ''))
                            nueva_razon = st.text_input("Razón Social", value=fila.get('Razón Social', ''))
                            nuevo_cuit = st.text_input("CUIT", value=fila.get('CUIT', ''))
                            nuevo_telefono = st.text_input("Teléfono", value=fila.get('Telefono', ''), max_chars=10)
                        
                        with c2:
                            nuevo_dir1 = st.text_input("Dirección 1", value=fila.get('Direccion_1', ''))
                            nuevo_link1 = st.text_input("Link Dirección 1", value=fila.get('Link_Direccion_1', ''))
                            nuevo_dir2 = st.text_input("Dirección 2", value=fila.get('Direccion_2', ''))
                            nuevo_link2 = st.text_input("Link Dirección 2", value=fila.get('Link_Direccion_2', ''))
                            nuevo_dir3 = st.text_input("Dirección 3", value=fila.get('Direccion_3', ''))
                            nuevo_link3 = st.text_input("Link Dirección 3", value=fila.get('Link_Direccion_3', ''))
                        
                        nueva_obs = st.text_area("Observaciones", value=fila.get('Observaciones', ''))
                        
                        zonas_lista = ["NORTE", "SUR", "CENTRO", "ESTE", "OESTE", "SANLO CHICO"]
                        idx_zona = zonas_lista.index(fila.get('Zona')) if fila.get('Zona') in zonas_lista else 0
                        input_zona = st.selectbox("Zona", zonas_lista, index=idx_zona)
                        
                        tipos_lista = ["CONSUMIDOR FINAL", "MAYORISTA"]
                        idx_tipo = 0 if fila.get('Tipo_Cliente') == "CONSUMIDOR FINAL" else 1
                        input_tipo = st.selectbox("Tipo Cliente", tipos_lista, index=idx_tipo)
                        
                        guardar_btn = st.form_submit_button("Guardar Cambios")
            
                    # ACCIÓN DE GUARDAR (FUERA DEL FORM)
                    if guardar_btn:
                        db.table("CLIENTES").update({
                            "Nombre": str(nuevo_nombre or "").upper(),
                            "Apellido": (nuevo_apellido or "").upper(),
                            "DNI": nuevo_dni,
                            "Razón Social": (nueva_razon or "").upper(),
                            "CUIT": nuevo_cuit,
                            "Telefono": nuevo_telefono,
                            "Direccion_1": (nuevo_dir1 or "").upper(),
                            "Link_Direccion_1": nuevo_link1,
                            "Direccion_2": (nuevo_dir2 or "").upper(),
                            "Link_Direccion_2": nuevo_link2,
                            "Direccion_3": (nuevo_dir3 or "").upper(),
                            "Link_Direccion_3": nuevo_link3,
                            "Observaciones": nueva_obs,
                            "Zona": input_zona,
                            "Tipo_Cliente": input_tipo
                        }).eq("ID_Cliente", int(id_modificar)).execute()
                        st.success("Guardado")
                        st.rerun()

                    # ACCIÓN DE ELIMINAR (FUERA DEL FORM Y CON KEY ÚNICA)
                    st.divider()
                    if st.session_state.get('rol') == "Administrador":
                        confirmar_del = st.checkbox("Confirmar eliminación", key="check_del_final")
                        if st.button("🗑️ Eliminar Cliente", key="btn_del_final"):
                            if confirmar_del:
                                db.table("CLIENTES").delete().eq("ID_Cliente", int(id_modificar)).execute()
                                st.success("🗑️ Cliente eliminado.")
                                st.rerun()
                            else:
                                st.warning("⚠️ Debes marcar la casilla de confirmación.")

    # =====================================================================
    # MODULO: 🛒 PUNTO DE VENTA
    # =====================================================================
    if menu == "🛒 Punto de Venta":
        col_t1, col_t2 = st.columns([4, 1])
        col_t1.header("🚀 Venta Rápida - Pañalera Moldes")
        if col_t2.button("🧹 Limpiar Todo", type="secondary", use_container_width=True):
            resetear_punto_venta()
        # 1.5. BOTÓN PARA VER PENDIENTES (Adaptado a Supabase)
        @st.dialog("Ventas Pendientes")
        def abrir_pendientes():
            import json
            import re
            
            def limpiar_monto(texto):
                """Extrae números de un string como 'Transferencia: $59,600'"""
                # Elimina símbolos de moneda, comas y espacios, dejando solo números y puntos
                solo_numeros = re.sub(r'[^\d.]', '', texto.replace(',', ''))
                try:
                    return float(solo_numeros)
                except:
                    return 0.0
        
            try:
                pendientes = db.table("VENTAS_PENDIENTES").select("*").execute().data
                
                if not pendientes:
                    st.info("📭 No hay ventas pendientes registradas.")
                else:
                    for v in pendientes:
                        with st.container(border=True):
                            st.markdown(f"**ID:** {v['ID_Pendiente']} | 📅 {v['Fecha']}")
                            
                            # Extraemos el monto desde la columna Metodo_Pago
                            monto_numerico = limpiar_monto(v.get('Metodo_Pago', '0'))
                            st.markdown(f"💰 **Total Venta: ${monto_numerico:,.2f}**")
                            
                            st.caption(f"👤 {v['Cliente']} | 👔 {v['Vendedor']}")
                            
                            if st.button("📥 Cargar", key=f"recup_{v['ID_Pendiente']}"):
                                # ... (tu lógica de carga se mantiene igual)
                                st.session_state.id_pendiente_cargado = v['ID_Pendiente']
                                st.session_state.carrito_vta = json.loads(v['Detalle_JSON'])
                                st.session_state.pagos_split = json.loads(v.get('Pagos_JSON', '[{"metodo": "Efectivo", "monto": 0.0}]'))
                                st.session_state.cliente_recuperado = v['Cliente']
                                st.session_state.id_cliente_recuperado = v.get('ID_Cliente_Pendiente', "0")
                                st.session_state.tipo_entrega = v.get('Forma_Entrega', 'Mostrador')
                                st.session_state.direccion_entrega = v.get('Direccion_Entrega', 'N/A')
                                st.session_state.link_maps_entrega = v.get('Link_Maps_Entrega', 'N/A')
                                st.session_state.fecha_reparto = v.get('Fecha_Entrega', str(datetime.today().date()))
                                st.rerun() 
                            
                            if st.button("🗑️ Eliminar", key=f"del_{v['ID_Pendiente']}"):
                                db.table("VENTAS_PENDIENTES").delete().eq("ID_Pendiente", v['ID_Pendiente']).execute()
                                st.rerun()
            except Exception as e:
                st.error(f"Error al leer pendientes: {e}")

        # Ubicación del botón
        if st.button("📂 VER PENDIENTES", use_container_width=True):
            abrir_pendientes()

        # 1. CARGA DE DATOS DESDE SUPABASE
        # Cargamos las tablas necesarias
        try:
            df_clie = pd.DataFrame(db.table("CLIENTES").select("*").execute().data)
            df_prod = pd.DataFrame(db.table("PRODUCTOS").select("*").execute().data)
            df_vend = pd.DataFrame(db.table("VENDEDORES").select("*").execute().data)
        except Exception as e:
            st.error(f"Error al conectar con Supabase: {e}")
            st.stop()

        if 'carrito_vta' not in st.session_state:
            st.session_state.carrito_vta = []

        # 2. INTERFAZ: SELECTORES
        with st.container(border=True):
            c1, c2, c3, c4 = st.columns([2.5, 0.5, 1, 1]) 
            
            # --- 1. CREAR LA COLUMNA DISPLAY ---
            # Incluimos el ID de forma oculta pero estructurada para poder extraerlo fácil
            df_clie['Display'] = (
                df_clie['Nombre'].astype(str) + " " + 
                df_clie['Apellido'].astype(str) + " (" + 
                df_clie['Telefono'].astype(str) + ") - ID: " + 
                df_clie['ID_Cliente'].astype(str)
            )
            
            # --- 2. AHORA SÍ: LÓGICA DE PERSISTENCIA ---
            valor_inicial = None
            if 'id_cliente_recuperado' in st.session_state:
                candidatos = df_clie[df_clie['ID_Cliente'].astype(str) == str(st.session_state.id_cliente_recuperado)]
                if not candidatos.empty:
                    valor_inicial = candidatos.iloc[0]['Display']

            # --- 3. SELECTOR DE CLIENTE ---
            cliente_display = c1.selectbox(
                "👤 Buscar Cliente", 
                options=df_clie['Display'].tolist(),
                index=df_clie['Display'].tolist().index(valor_inicial) if valor_inicial and valor_inicial in df_clie['Display'].tolist() else None, 
                placeholder="Seleccione o busque un cliente..."
            )

            # --- EXTRACCIÓN SEGURA Y ÚNICA ---
            if cliente_display and " - ID: " in cliente_display:
                try:
                    # Extraemos el ID después de " - ID: "
                    id_str = cliente_display.split(" - ID: ")[1]
                    st.session_state.cliente_actual_id = int(id_str)
                except Exception as e:
                    st.error(f"Error al procesar ID: {e}")
                    st.session_state.cliente_actual_id = None
            else:
                st.session_state.cliente_actual_id = None
            
            # --- BOTÓN DE ACCESO DIRECTO ---
            if c2.button("➕", help="Agregar nuevo cliente"):
                abrir_alta_cliente_rapida()
            
            # --- LÓGICA DE ASIGNACIÓN (Indentación corregida) ---
            cliente_sel_row = None
            if cliente_display:
                cliente_sel_row = df_clie[df_clie['Display'] == cliente_display].iloc[0]
                cliente_nombre_final = cliente_sel_row['Nombre'] + " " + cliente_sel_row['Apellido']
                id_cliente_final = str(cliente_sel_row['ID_Cliente'])
                st.session_state.id_cliente_recuperado = id_cliente_final 
            else:
                id_cliente_final = "0"
                cliente_nombre_final = "Consumidor Final"
                if 'id_cliente_recuperado' in st.session_state:
                    del st.session_state.id_cliente_recuperado

            # Vendedor ahora en c4 (columna nueva)
            vendedor_sel = c4.selectbox("👔 Vendedor", df_vend['Nombre'].tolist())
            
            # Lista ahora en c3
            def cambiar_lista_global():
                st.session_state.lista_global_vta = st.session_state.selector_global

            lista_opciones = ["Lista 1", "Lista 2", "Lista 3", "Lista 4", "Lista 5"]
            lista_global = c3.selectbox(
                "🏷️ Lista", 
                options=lista_opciones,
                index=0, 
                key="selector_global",
                on_change=cambiar_lista_global
            )
            
        # 3. BUSCADOR DE PRODUCTOS
        st.divider()
        st.subheader("🔍 Añadir Productos")

        # --- FILTRO DE DISPONIBILIDAD ---
        # Filtramos: (Tiene stock positivo) O (Es un concepto financiero / No stockeable)
        # Nota: Asegúrate que el nombre de columna sea el correcto (Stock_Actual)
        df_disponible = df_prod[
            (df_prod['Stock_Actual'] > 0) | (df_prod['Es_Stockeable'] == False)
        ].copy()
        
        # Creamos la lista formateada con los resultados del filtro
        opciones_productos = (df_disponible['Nombre'] + " - " + df_disponible['ID_Producto'].astype(str)).tolist()

        col_bus1, col_bus2 = st.columns([2, 1])
        
        col_bus1.selectbox(
            "Buscar por nombre o código", 
            options=opciones_productos, 
            index=None, 
            placeholder="Escriba para buscar producto...",
            key="prod_manual_key",
            on_change=procesar_seleccion_manual 
        )
        
        # Aviso: Ahora solo avisaremos si un producto FÍSICO no tiene stock. 
        # Los productos con Es_Stockeable = False no dispararán esta advertencia.
        if 'prod_manual_key' in st.session_state and st.session_state.prod_manual_key:
            busqueda = st.session_state.prod_manual_key
            id_buscado = busqueda.split(" - ")[-1]
            prod_buscado = df_prod[df_prod['ID_Producto'].astype(str) == id_buscado].iloc[0]
            
            # Solo advertimos si es un producto real sin stock
            if prod_buscado['Stock_Actual'] <= 0 and prod_buscado['Es_Stockeable'] == True:
                st.warning(f"⚠️ El producto '{prod_buscado['Nombre']}' no se puede agregar porque no cuenta con stock.")

        # 4. CARRITO (Versión Final con Títulos ajustados)
        if st.session_state.carrito_vta:
            st.write("### 🛒 Detalle de la Venta")
            
            global_val = st.session_state.lista_global_vta
            
            for i, item in enumerate(st.session_state.carrito_vta):
                res_p = df_prod[df_prod['ID_Producto'].astype(str) == str(item['id'])]
                if res_p.empty: continue
                p_data = res_p.iloc[0]
                
                c1, c2, c3, c4, c5, c6 = st.columns([2, 1.2, 0.8, 1.2, 1, 0.5])
                
                # AJUSTE: Nombre en h4 (más grande) y Código en negrita (sin #)
                with c1:
                    st.markdown(f"#### {p_data['Nombre']}")
                    st.markdown(f"<p style='font-size:14px;'><strong>{p_data['ID_Producto']}</strong></p>", unsafe_allow_html=True)
                
                # 1. Selector de Lista
                lista_actual_producto = item.get('lista_local', global_val)
                lista_item = c2.selectbox(
                    "Lista", 
                    ["Automática (P1/P2)", "Lista 1", "Lista 2", "Lista 3", "Lista 4", "Lista 5"],
                    index=["Automática (P1/P2)", "Lista 1", "Lista 2", "Lista 3", "Lista 4", "Lista 5"].index(lista_actual_producto),
                    key=f"L_{i}_{global_val}" 
                )
                
                if lista_item != lista_actual_producto:
                    item['lista_local'] = lista_item
                    st.rerun()
                
                # 2. Cantidad
                n_cant = c3.number_input("Cant.", min_value=1, value=int(item['cantidad']), key=f"Q_{i}")
                
                # 3. Calcular el precio SUGERIDO
                if lista_item == "Automática (P1/P2)":
                    if n_cant == 1: col_p = 'Precio_1'
                    elif n_cant == 2: col_p = 'Precio_2'
                    else: col_p = 'Precio_3'
                else:
                    col_p = lista_item.replace("Lista ", "Precio_")
                
                precio_sugerido = float(p_data[col_p])
                
                # 4. Input Precio
                n_prec = c4.number_input(
                    "Precio", 
                    value=precio_sugerido, 
                    key=f"P_{i}_{lista_item}_{n_cant}_{precio_sugerido}", 
                    format="%.2f"
                )
                
                # 5. Actualización
                sub = n_cant * n_prec
                st.session_state.carrito_vta[i].update({
                    'cantidad': n_cant,
                    'precio': n_prec,
                    'subtotal': sub
                })
                
                c5.write(f"Sub: **${sub:,.2f}**")
                if c6.button("🗑️", key=f"del_{i}"):
                    st.session_state.carrito_vta.pop(i)
                    st.rerun()

        # TOTAL (Al final del carrito)
            total_final_vta = sum(art['subtotal'] for art in st.session_state.carrito_vta)
            st.divider()
            st.markdown(f"### 💰 **Total a Cobrar: ${total_final_vta:,.2f}**")

            # --- SECCIÓN DE PAGOS ---
            st.subheader("💳 Formas de Pago")
            
            # 1. CARGAR MÉTODOS DESDE SUPABASE (Dinámico)
            try:
                # Traemos solo los métodos activos
                metodos_db = db.table("FORMAS_PAGO").select("Nombre_Pago").eq("Activo", True).execute().data
                lista_pagos = [m["Nombre_Pago"] for m in metodos_db]
            except Exception as e:
                # Fallback por si hay error en la tabla, para no romper la app
                lista_pagos = ["Efectivo", "Transferencia", "Débito", "Crédito"]
            
            # 2. AGREGAR GIFT CARD SI APLICA
            if 'cliente_actual_id' in st.session_state and st.session_state.cliente_actual_id is not None:
                id_busqueda = int(st.session_state.cliente_actual_id)
                
                gc_data = db.table("GIFT_CARDS") \
                            .select("Saldo_Actual, ID_GiftCard") \
                            .eq("ID_Cliente", id_busqueda) \
                            .eq("Estado", True) \
                            .execute().data
                
                if gc_data and gc_data[0]['Saldo_Actual'] > 0:
                    saldo_disponible = gc_data[0]['Saldo_Actual']
                    nombre_opcion = f"Gift Card (${saldo_disponible:,.0f})"
                    lista_pagos.append(nombre_opcion)
                    st.session_state['gc_activa_id'] = gc_data[0]['ID_GiftCard']
                    st.session_state['gc_saldo_disponible'] = saldo_disponible
            # ----------------------------------------
            
            # Aquí sigue tu código original que genera los selectores (el for loop)
            if 'pagos_split' not in st.session_state:
                st.session_state.pagos_split = [{"metodo": "Efectivo", "monto": 0.0}]

            # Esta función se ejecuta apenas el usuario cambia el número y presiona TAB
            def actualizar_valor_pago(indice):
                # El valor nuevo ya está en st.session_state porque la key del input 
                # coincide con el nombre de la variable
                valor_nuevo = st.session_state[f"temp_mon_{indice}"]
                st.session_state.pagos_split[indice]["monto"] = float(valor_nuevo)

            # --- CALCULADOR DE SALDO (Se recalcula al inicio de cada rerun) ---
            suma_pagos_actual = sum(float(p["monto"]) for p in st.session_state.pagos_split)
            saldo_pendiente = total_final_vta - suma_pagos_actual

            if saldo_pendiente > 0.01: # 0.01 por tolerancia de flotantes
                st.warning(f"⚠️ Faltan completar: **${saldo_pendiente:,.2f}**")
            elif saldo_pendiente < -0.01:
                st.error(f"❌ Exceso de: **${abs(saldo_pendiente):,.2f}**")
            else:
                st.success("✅ Pago completo.")

            # Iteración para mostrar los inputs
            for i, p in enumerate(st.session_state.pagos_split):
                col_p1, col_p2, col_p3 = st.columns([2, 1, 0.5])
                
                # Selector de método
                st.session_state.pagos_split[i]["metodo"] = col_p1.selectbox(
                    f"Método {i+1}", lista_pagos, key=f"p_met_{i}"
                )
                
                # Monto con callback inmediato
                col_p2.number_input(
                    f"Monto {i+1}", 
                    min_value=0.0, 
                    value=float(p["monto"]), 
                    key=f"temp_mon_{i}", 
                    on_change=actualizar_valor_pago, 
                    args=(i,) # Le pasamos el índice a la función
                )
                
                if col_p3.button("🗑️", key=f"del_p_{i}"):
                    st.session_state.pagos_split.pop(i)
                    st.rerun()

            if st.button("➕ Añadir otro método de pago"):
                st.session_state.pagos_split.append({"metodo": "Efectivo", "monto": 0.0})
                st.rerun()

        # --- 5. FORMA DE ENTREGA ---
            st.divider()
            st.subheader("🚚 Forma de Entrega")

            # Ajustamos las columnas para incluir la tercera (col_e3)
            col_e1, col_e2, col_e3 = st.columns([1, 2, 1]) 

            # 1. Determinamos el índice inicial del radio
            estado_tipo = st.session_state.get("tipo_entrega", "Mostrador")
            idx_radio = 0 if estado_tipo == "Mostrador" else 1
                
            tipo_entrega = col_e1.radio("¿Cómo se entrega?", ["Mostrador", "Reparto"], index=idx_radio)

            from datetime import datetime

            # Convertir el string guardado a objeto date para el input
            fecha_val = datetime.strptime(st.session_state.get("fecha_reparto", str(datetime.today().date())), "%Y-%m-%d")

            fecha_reparto = col_e3.date_input("Fecha de entrega", value=fecha_val)

            # Inicializamos valores con lo que traemos de la venta recuperada
            direccion_elegida = st.session_state.get("direccion_entrega", "N/A")
            link_elegido = st.session_state.get("link_maps_entrega", "N/A")

            if tipo_entrega == "Reparto":
                opciones_map = {}
                
                # --- CORRECCIÓN: Verificar si tenemos el cliente seleccionado ---
                if cliente_sel_row is not None:
                    for i in [1, 2, 3]:
                        dir_val = cliente_sel_row.get(f'Direccion_{i}')
                        link_val = cliente_sel_row.get(f'Link_Direccion_{i}')
                        if dir_val and str(dir_val).strip() != "":
                            opciones_map[dir_val] = link_val if link_val else "N/A"
                
                if opciones_map:
                    # Determinamos el índice si la dirección recuperada está en las opciones
                    lista_dirs = list(opciones_map.keys())
                    idx_sel = lista_dirs.index(direccion_elegida) if direccion_elegida in lista_dirs else 0
                    
                    seleccion = col_e2.selectbox("Seleccionar dirección", lista_dirs, index=idx_sel)
                    direccion_elegida = seleccion
                    link_elegido = opciones_map[seleccion]
                else:
                    direccion_elegida = col_e2.text_input("Dirección de entrega", value=direccion_elegida)
            
            # Actualizamos el estado
            st.session_state.tipo_entrega = tipo_entrega
            st.session_state.fecha_reparto = str(fecha_reparto)
            st.session_state.direccion_entrega = direccion_elegida
            st.session_state.link_maps_entrega = link_elegido

        # --- 6. BOTONES DE CIERRE (CORREGIDO) ---
            st.divider()
            col_f1, col_f2 = st.columns(2)

            with col_f1:
                if st.button("🏁 FINALIZAR Y REGISTRAR VENTA", use_container_width=True, type="primary"):
                    # 0. Verificación de sumas
                    suma_pagos = sum(float(p["monto"]) for p in st.session_state.pagos_split)
                    if abs(suma_pagos - total_final_vta) > 0.01:
                        st.error(f"¡Error! La suma de los pagos (${suma_pagos:.2f}) no coincide con el total (${total_final_vta:.2f})")
                        st.stop()
                    
                    # --- NUEVO: BLINDAJE DE SEGURIDAD PARA GIFT CARDS ---
                    for pago in st.session_state.pagos_split:
                        if "Gift Card" in pago["metodo"]:
                            # Re-consultamos el saldo real en la base de datos en este instante
                            gc_check = db.table("GIFT_CARDS") \
                                         .select("Saldo_Actual") \
                                         .eq("ID_GiftCard", st.session_state.get('gc_activa_id')) \
                                         .single().execute()
                            
                            saldo_real = gc_check.data['Saldo_Actual'] if gc_check.data else 0
                            
                            if pago["monto"] > saldo_real:
                                st.error(f"❌ ¡Saldo insuficiente en Gift Card! Disponible: ${saldo_real:,.2f}")
                                st.stop() # Detiene todo el proceso antes de que se grabe nada
                    # ----------------------------------------------------
                    
                    try:
                        # 1. DEFINIR DATOS BÁSICOS PRIMERO
                        id_v = datetime.now().strftime("%Y%m%d%H%M%S")
                        f = datetime.now().strftime("%Y-%m-%d")
                        
                        # --- OBTENER TURNO ANTES DE NADA ---
                        turno_res = db.table("CONTROL_TURNOS").select("ID_Turno").eq("Estado", "Abierto").maybe_single().execute()
                        id_turno_val = turno_res.data['ID_Turno'] if (turno_res and turno_res.data) else "SIN_TURNO"

                        # 2. Registrar Cabecera (AÑADIDO ID_Vendedor)
                        desglose_pagos = " | ".join([f"{p['metodo']}: ${p['monto']:,.0f}" for p in st.session_state.pagos_split])
                        db.table("VENTAS_CABECERA").insert({
                            "ID_Venta": id_v,
                            "Fecha": f,
                            "ID_Cliente": id_cliente_final,
                            "ID_Vendedor": st.session_state.get("id_vendedor", "1"), # <--- CORRECCIÓN AQUÍ
                            "Forma_Pago": desglose_pagos,
                            "Total": total_final_vta,
                            "Forma_Entrega": st.session_state.tipo_entrega,
                            "Direccion_Entrega": st.session_state.direccion_entrega if st.session_state.tipo_entrega == "Reparto" else "N/A"
                        }).execute()
                        
                        for art in st.session_state.carrito_vta:
                            # 1. CONSULTA EL COSTO ACTUAL DEL PRODUCTO
                            prod_data = db.table("PRODUCTOS").select("Precio_Costo").eq("ID_Producto", str(art['id'])).single().execute()
                            
                            # 2. DEFINIMOS EL COSTO (si no encuentra el producto, usamos 0 por seguridad)
                            costo_historico = prod_data.data.get('Precio_Costo', 0) if prod_data.data else 0

                            # 3. INSERTAMOS EN VENTAS_DETALLE INCLUYENDO EL COSTO CAPTURADO
                            db.table("VENTAS_DETALLE").insert({
                                "ID_Venta": id_v,
                                "ID_Producto": str(art['id']),
                                "Cantidad": int(art['cantidad']),
                                "Precio_Unitario": float(art['precio']),
                                "Precio_Costo_Unitario": float(costo_historico), # <--- AQUÍ ESTÁ LA MAGIA
                                "Subtotal": float(art['subtotal'])
                            }).execute()
                            
                            prod_res = db.table("PRODUCTOS").select("Stock_Actual").eq("ID_Producto", art['id']).single().execute()
                            if prod_res.data:
                                stock_actual = int(prod_res.data.get('Stock_Actual', 0))
                                db.table("PRODUCTOS").update({"Stock_Actual": stock_actual - art['cantidad']}) \
                                    .eq("ID_Producto", art['id']).execute()

                        # 4. Registrar Pagos en la nueva tabla VENTAS_PAGOS
                        for pago in st.session_state.pagos_split:
                            db.table("VENTAS_PAGOS").insert({
                                "ID_Venta": id_v,
                                "Metodo_Pago": pago["metodo"],
                                "Monto": float(pago["monto"])
                            }).execute()

                        # 5. Registrar en Caja
                        for pago in st.session_state.pagos_split:
                            metodo = pago["metodo"]
                            monto = float(pago["monto"])
                            
                            # --- A. REGISTRO DE INGRESO (Siempre ocurre) ---
                            db.table("CAJA").insert({
                                "ID_Turno": id_turno_val,
                                "Fecha": datetime.now().isoformat(),
                                "Tipo": "Ingreso",
                                "Concepto": f"Venta {id_v} ({metodo})",
                                "Monto": monto,
                                "Forma_Pago": metodo
                            }).execute()

                            # --- B. LÓGICA DE EGRESO AUTOMÁTICO ---
                            # Si es efectivo y reparto, O si es otro método (incluyendo Gift Card)
                            es_efectivo_reparto = (metodo == "Efectivo" and st.session_state.tipo_entrega == "Reparto")
                            es_otro_metodo = (metodo != "Efectivo")
                            
                            if es_efectivo_reparto or es_otro_metodo:
                                # Aquí incluimos la lógica de descuento de saldo si es Gift Card
                                if "Gift Card" in metodo:
                                    gc_id = st.session_state.get('gc_activa_id')
                                    nuevo_saldo = st.session_state.get('gc_saldo_disponible', 0) - monto
                                    db.table("GIFT_CARDS").update({"Saldo_Actual": float(nuevo_saldo)}).eq("ID_GiftCard", gc_id).execute()
                                    if nuevo_saldo <= 0:
                                        db.table("GIFT_CARDS").update({"Estado": False}).eq("ID_GiftCard", gc_id).execute()

                                # Registro del egreso en caja
                                db.table("CAJA").insert({
                                    "ID_Turno": id_turno_val,
                                    "Fecha": datetime.now().isoformat(),
                                    "Tipo": "Egreso",
                                    "Concepto": f"RETIRO PAGO {metodo.upper()} (Venta {id_v})",
                                    "Monto": monto,
                                    "Forma_Pago": metodo
                                }).execute()
                        if 'id_pendiente_cargado' in st.session_state:
                            db.table("VENTAS_PENDIENTES").delete().eq("ID_Pendiente", st.session_state.id_pendiente_cargado).execute()
                            del st.session_state.id_pendiente_cargado

                        st.success("✅ Venta registrada correctamente!")
                        st.session_state.carrito_vta = []
                        st.session_state.pagos_split = [{"metodo": "Efectivo", "monto": 0.0}]
                        st.rerun()

                    except Exception as e:
                        st.error(f"Error al registrar: {e}")

            with col_f2:
                if st.button("⏳ GUARDAR COMO PENDIENTE", use_container_width=True):
                    import json
                    import re # Asegúrate de importar re al principio de tu archivo
                    
                    try:
                        # --- NUEVO: Lógica de extracción de coordenadas ---
                        lat, lng = None, None
                        link = st.session_state.link_maps_entrega
                        if link:
                            # Busca números decimales después de un '@' o en parámetros 'll='
                            coords = re.findall(r'@(-?\d+\.\d+),(-?\d+\.\d+)', link)
                            if coords:
                                lat, lng = float(coords[0][0]), float(coords[0][1])
                        
                        desglose_pagos = " | ".join([f"{p['metodo']}: ${p['monto']:,.0f}" for p in st.session_state.pagos_split])
                        
                        data_to_save = {
                            "Fecha": datetime.now().strftime('%Y-%m-%d'),
                            "Hora": datetime.now().strftime('%H:%M:%S'),
                            "Cliente": cliente_nombre_final,
                            "ID_Cliente_Pendiente": id_cliente_final,
                            "Vendedor": vendedor_sel,
                            "Metodo_Pago": desglose_pagos,
                            "Pagos_JSON": json.dumps(st.session_state.pagos_split),
                            "Detalle_JSON": json.dumps(st.session_state.carrito_vta),
                            "Forma_Entrega": st.session_state.tipo_entrega,
                            "Direccion_Entrega": st.session_state.direccion_entrega,
                            "Link_Maps_Entrega": link,
                            "Fecha_Entrega": st.session_state.fecha_reparto,
                            # --- AGREGAMOS LAS NUEVAS COLUMNAS ---
                            "Latitud": lat,
                            "Longitud": lng
                        }

                        # --- LA LOGICA DE DETECCIÓN ---
                        if 'id_pendiente_cargado' in st.session_state and st.session_state.id_pendiente_cargado:
                            # Si existe el ID, actualizamos el registro existente
                            db.table("VENTAS_PENDIENTES") \
                            .update(data_to_save) \
                            .eq("ID_Pendiente", st.session_state.id_pendiente_cargado) \
                            .execute()
                            st.toast("Venta pendiente actualizada", icon="🔄")
                        else:
                            # Si no existe, es una venta nueva: insertamos
                            data_to_save["ID_Pendiente"] = f"PEND-{datetime.now().strftime('%Y%m%d%H%M%S')}"
                            db.table("VENTAS_PENDIENTES").insert(data_to_save).execute()
                            st.toast("Venta guardada como nuevo pendiente", icon="⏳")

                        # Limpieza post-guardado
                        st.session_state.carrito_vta = []
                        st.session_state.pagos_split = [{"metodo": "Efectivo", "monto": 0.0}]
                        if 'id_pendiente_cargado' in st.session_state:
                            del st.session_state.id_pendiente_cargado
                        if 'id_cliente_recuperado' in st.session_state:
                            del st.session_state.id_cliente_recuperado
                            
                        st.rerun()
                    except Exception as e:
                        st.error(f"Error al guardar pendiente: {e}")
        else:
            st.info("El carrito está vacío.")

    # =====================================================================
    # MODULO: 📦 STOCK
    # =====================================================================
    elif menu == "📦 Stock":
        st.header("📊 Gestión y Análisis de Stock")
        
        # 1. Carga de datos
        df_prod = pd.DataFrame(db.table("PRODUCTOS").select("*").execute().data)
        df_prov = pd.DataFrame(db.table("PROVEEDORES").select("*").execute().data)
        
        # --- BUSCADOR FLEXIBLE ---
        st.subheader("🔍 Buscar Artículos")
        
        # Usamos text_input para escritura libre
        busqueda_texto = st.text_input(
            "Escriba para filtrar por nombre o código:", 
            placeholder="Ej: babydry, pampers, 779..."
        )
        
        # --- FILTROS ---
        c1, c2, c3 = st.columns(3)
        rubros = ["Todos"] + df_prod['Rubro'].unique().tolist()
        marcas = ["Todos"] + df_prod['Marca'].unique().tolist()
        provs = ["Todos"] + df_prov['Razon_Social'].tolist()
        
        filtro_rubro = c1.selectbox("Filtrar por Rubro", rubros)
        filtro_marca = c2.selectbox("Filtrar por Marca", marcas)
        filtro_prov = c3.selectbox("Filtrar por Proveedor", provs)
        
        # Aplicar filtros
        df_f = df_prod.copy()
        
        # 1. Filtro de texto flexible (busca en Nombre y en ID)
        if busqueda_texto:
            busqueda_texto = busqueda_texto.lower()
            mask = df_f['Nombre'].str.lower().str.contains(busqueda_texto, na=False) | \
                   df_f['ID_Producto'].astype(str).str.lower().str.contains(busqueda_texto, na=False)
            df_f = df_f[mask]
        else:
            # Si no hay buscador, aplicamos los filtros normales
            if filtro_rubro != "Todos": df_f = df_f[df_f['Rubro'] == filtro_rubro]
            if filtro_marca != "Todos": df_f = df_f[df_f['Marca'] == filtro_marca]
        
        # 2. Cálculos en tiempo real
        df_f['Faltante_Min'] = (df_f['Stock_Min'] - df_f['Stock_Actual']).clip(lower=0)
        df_f['Faltante_Max'] = (df_f['Stock_Max'] - df_f['Stock_Actual']).clip(lower=0)
        
        # Mostrar tabla
        st.dataframe(df_f[['Nombre', 'Stock_Actual', 'Stock_Min', 'Stock_Max', 'Faltante_Min', 'Faltante_Max']], use_container_width=True)
        
        # 3. Acciones (Exportación)
        col_exp1, col_exp2 = st.columns(2)
        
        # Exportar a Excel
        import io
        buffer = io.BytesIO()
        # Usamos un escritor para manejar el archivo en memoria
        with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
            df_f.to_excel(writer, index=False)
        
        col_exp1.download_button(
            label="📥 Exportar a Excel", 
            data=buffer.getvalue(), 
            file_name="reporte_stock.xlsx", 
            mime="application/vnd.ms-excel"
        )
        
        # Preparar mensaje WhatsApp
        if col_exp2.button("💬 Generar Resumen para WhatsApp"):
            mensaje = "🛒 *Pedido Sugerido (Faltantes a Mínimo):*\n"
            faltantes = df_f[df_f['Faltante_Min'] > 0]
            for _, item in faltantes.iterrows():
                mensaje += f"- {item['Nombre']}: Faltan {item['Faltante_Min']}\n"
            
            st.text_area("Copia este mensaje para WhatsApp:", value=mensaje)

        # 4. Botón de Mantenimiento (El motor que hicimos antes)
        if st.button("🔄 RECALCULAR STOCK MÍNIMO/MÁXIMO"):
            with st.spinner("Calculando rotación de 60 días..."):
                if calcular_y_actualizar_stock_automatico():
                    st.success("¡Stock mínimo y máximo actualizado!")
                    st.rerun()

    # =====================================================================
    # MODULO: 📋 HISTORIAL DE VENTAS
    # =====================================================================
    elif menu == "📋 Historial de Ventas":
        modulo_ventas()

    # =====================================================================
    # MODULO: ⚙️ CONFIGURACION PAGOS
    # =====================================================================
    elif menu == "⚙️ Configuración Pagos":
        modulo_config_pagos()

    # =====================================================================
    # MODULO: 🚚 GESTION DE REPARTOS
    # =====================================================================
    if menu == "🚚 Gestión de Repartos":
        
        # Obtenemos ventas pendientes de reparto
        # NOTA: Asegúrate de que las columnas existan en VENTAS_PENDIENTES
        ventas_reparto = db.table("VENTAS_PENDIENTES") \
                        .select("*") \
                        .eq("Forma_Entrega", "Reparto") \
                        .execute().data
        
        if not ventas_reparto:
            st.info("No hay repartos pendientes.")
        else:
            df = pd.DataFrame(ventas_reparto)
            df['Fecha_Entrega'] = pd.to_datetime(df['Fecha_Entrega']).dt.date
            df = df.sort_values(by='Fecha_Entrega')
            
            # 1. Calculamos el total general
            total_general = len(df)
            
            # 2. Título con el número integrado entre paréntesis
            st.markdown(f"## 🗺️ Planificación de Repartos ({total_general})")
            st.divider()
            
            # 3. Agrupamos por fecha
            for fecha, grupo in df.groupby('Fecha_Entrega'):
                # Título del día con su propio contador entre paréntesis
                st.subheader(f"📅 {fecha} ({len(grupo)})")
                
                # --- AQUÍ EMPIEZA LA MODIFICACIÓN ---
                # Usamos una clave única basada en la fecha para que no haya conflictos
                with st.expander(f"⚙️ Configurar Origen para {fecha}"):
                    opciones = {"Pañalera (Local)": (-24.793734909695726, -65.42769672376464), "Otro (Link de Maps)": "link"}
                    sel_origen = st.selectbox("¿Desde dónde sale el reparto?", list(opciones.keys()), key=f"sel_{fecha}")
                    
                    if sel_origen == "Otro (Link de Maps)":
                        link_maps = st.text_input("Pega el link de Google Maps aquí:")
                        if link_maps:
                            coords = extraer_coords_desde_link(link_maps)
                            if coords:
                                st.success(f"Coordenadas detectadas: {coords}")
                                punto_partida = coords
                            else:
                                st.error("No pude leer el link. Asegúrate de copiarlo desde el botón 'Compartir' de Google Maps.")
                                punto_partida = (-24.7825, -65.4111) # Default
                    else:
                        punto_partida = opciones[sel_origen]

                # Botón de optimización (Ahora usa 'punto_partida' definido arriba)
                if st.button(f"🚀 Generar Diagrama Optimizado para {fecha}", key=f"btn_{fecha}"):
                    st.session_state[f"mostrar_diagrama_{fecha}"] = True
                
                # Si la bandera es True, mostramos la función SIEMPRE (para que el formulario sobreviva)
                if st.session_state.get(f"mostrar_diagrama_{fecha}", False):
                    generar_diagrama_optimizada(grupo, punto_partida, fecha)
                # --- AQUÍ TERMINA LA MODIFICACIÓN ---
                
                # 3. Iteramos sobre los repartos de ESE día
                for _, v in grupo.iterrows():
                    with st.container(border=True):
                        c1, c2, c3 = st.columns([2, 2, 1])
                        c1.write(f"👤 **Cliente:** {v['Cliente']}")
                        c2.write(f"📍 **Dir:** {v['Direccion_Entrega']}")
                        
                        if v.get('Link_Maps_Entrega'):
                            c3.link_button("📍 Maps", v['Link_Maps_Entrega'])
                        
                        st.caption(f"💰 {v['Metodo_Pago']}")

    # =====================================================================
    # MODULO: 📦 PRODUCTOS
    # =====================================================================
    elif menu == "📦 Productos":
        st.title("📦 Gestión de Productos")

        # 1. CARGA INICIAL DE DATOS NECESARIOS (PARA TODOS)
        try:
            data = db.table("PRODUCTOS").select("*").execute().data
            df_prod = pd.DataFrame(data)
            
            # Carga de proveedores (ahora es global para el módulo)
            df_prov = pd.DataFrame(db.table("PROVEEDORES").select("Razon_Social").execute().data)
            lista_proveedores = df_prov['Razon_Social'].tolist() if not df_prov.empty else ["Sin proveedores"]
        except Exception as e:
            st.error(f"Error al conectar con Supabase: {e}")
            st.stop()

        # Inicialización de DF si está vacío
        columnas_requeridas = ['ID_Producto', 'Nombre', 'Rubro', 'ID_Proveedor', 'Marca', 
                               'Stock_Actual', 'Stock_Min', 'Stock_Max', 'Precio_Costo', 
                               'Precio_1', 'Precio_2', 'Precio_3', 'Precio_4', 'Precio_5', 'Imagen']
        if df_prod.empty:
            df_prod = pd.DataFrame(columns=columnas_requeridas)
        
        st.session_state.df_prod = df_prod.copy()

        # 2. DEFINICIÓN DINÁMICA DE PESTAÑAS SEGÚN ROL
        if st.session_state.rol == "Administrador":
            tabs = st.tabs(["🔍 Buscar", "➕ Alta", "✏️ Modificar", "🔄 Cambios", "📥 Importar", "✂️ Divisor"])
            tab_buscar, tab_alta, tab_modificar, tab_cambios, tab_importar, tab_divisor = tabs
        else:
            # Definimos solo las 3 pestañas y ponemos las otras como None
            tabs = st.tabs(["🔍 Buscar", "🔄 Cambios", "✂️ Divisor"])
            tab_buscar, tab_cambios, tab_divisor = tabs
            tab_alta, tab_modificar, tab_importar = None, None, None
            
        # --- PESTAÑA BUSCAR (VISIBLE PARA TODOS) ---
        with tab_buscar:
            st.subheader("🔍 Buscador de Productos")
            
            # 1. Crear las opciones igual que en el carrito
            opciones_productos = (st.session_state.df_prod['Nombre'] + " (ID: " + 
                                 st.session_state.df_prod['ID_Producto'].astype(str) + ")").tolist()
            
            # 2. Usar el selectbox flexible
            seleccion = st.selectbox(
                "Buscar producto por nombre o código:",
                options=opciones_productos,
                index=None,
                placeholder="Escriba para buscar...",
                key="buscador_productos_tab"
            )
            
            # 3. Lógica para filtrar el dataframe
            if seleccion:
                # Extraer el ID de la selección (el texto entre paréntesis)
                id_seleccionado = seleccion.split("(ID: ")[1].replace(")", "")
                df_filtrado = st.session_state.df_prod[st.session_state.df_prod['ID_Producto'].astype(str) == id_seleccionado]
            else:
                # Si no hay selección, mostrar todo (o podrías mostrar vacío si prefieres)
                df_filtrado = st.session_state.df_prod

            # 4. Mostrar según el rol
            if st.session_state.rol != "Administrador":
                cols_vendedor = ['Nombre', 'Precio_1', 'Precio_2', 'Precio_3']
                df_filtrado = df_filtrado[cols_vendedor]

            st.dataframe(df_filtrado, use_container_width=True, hide_index=True)

        # --- PESTAÑA CAMBIOS (Mejorada) ---
        with tab_cambios:
            st.subheader("🔄 Gestión de Cambios y Devoluciones")

            # 1. PANEL ADMINISTRADOR (SIEMPRE VISIBLE PARA ADMIN)
            if st.session_state.get('rol') == "Administrador":
                st.divider()
                st.subheader("🛡️ Panel de Supervisión (Admin)")
                pendientes = db.table("PRE_CAMBIOS").select("*").eq("Estado", "PENDIENTE").execute().data
                
                if pendientes:
                    for p in pendientes:
                        # Estilo más prolijo con una caja (container)
                        with st.container(border=True):
                            c1, c2 = st.columns([3, 1])
                            with c1:
                                st.markdown(f"**Producto:** {p['Nombre']} | **Usuario:** {p['Usuario']}")
                                st.caption(f"Motivo original: {p['Descripción']}")
                            
                            with st.form(f"form_admin_{p['id']}"):
                                col_a, col_b, col_c = st.columns(3)
                                new_cant = col_a.number_input("Cantidad:", value=max(p['Entra'], p['Sale']), key=f"cant_{p['id']}")
                                new_tipo = col_b.selectbox("Tipo:", ["ENTRA", "SALE"], index=0 if p['Entra'] > 0 else 1, key=f"tipo_{p['id']}")
                                new_desc = col_c.text_input("Motivo editado:", value=p['Descripción'], key=f"desc_{p['id']}")
                                
                                # Botones alineados en el mismo formulario
                                btn_col1, btn_col2 = st.columns(2)
                                if btn_col1.form_submit_button("💾 Aprobar y Procesar", use_container_width=True):
                                    # 1. Obtener datos actuales
                                    prod_data = db.table("PRODUCTOS").select("Stock_Actual").eq("ID_Producto", p['Código']).execute().data
                                    
                                    if prod_data:
                                        stock_viejo = int(prod_data[0]['Stock_Actual'])
                                        stock_nuevo = (stock_viejo + new_cant) if new_tipo == 'ENTRA' else (stock_viejo - new_cant)
                                            
                                        # 2. Actualizar stock
                                        db.table("PRODUCTOS").update({"Stock_Actual": stock_nuevo}).eq("ID_Producto", p['Código']).execute()
                                        
                                        # 3. Insertar en CAMBIOS (con nombres de columnas sin espacios)
                                        try:
                                            db.table("CAMBIOS").insert({
                                                "Fecha": datetime.now().isoformat(),
                                                "Usuario": st.session_state.get('usuario_actual', 'Administrador'), # <--- AGREGA ESTO
                                                "Código": p['Código'],
                                                "Nombre": p['Nombre'],
                                                "Descripción": new_desc,
                                                "Entra": int(new_cant) if new_tipo == 'ENTRA' else 0,
                                                "Sale": int(new_cant) if new_tipo == 'SALE' else 0,
                                                "existencia_ant": stock_viejo,
                                                "existencia_actual": stock_nuevo
                                            }).execute()
                                            
                                            # 4. Marcar como procesado
                                            db.table("PRE_CAMBIOS").update({"Estado": "PROCESADO"}).eq("id", p['id']).execute()
                                            
                                            st.success("✅ Stock actualizado correctamente.")
                                            st.rerun()

                                        except Exception as e:
                                            st.error(f"Error al insertar en CAMBIOS: {e}")
                                
                                if btn_col2.form_submit_button("❌ Rechazar", use_container_width=True):
                                    db.table("PRE_CAMBIOS").update({"Estado": "RECHAZADO"}).eq("id", p['id']).execute()
                                    st.rerun()
                else:
                    st.info("No hay cambios pendientes.")
                st.divider()
            
            # 1. Inicializar lista de items
            if 'lista_cambios' not in st.session_state:
                st.session_state.lista_cambios = []
            
            # 2. Buscador (Solo se muestra para cargar items)
            opciones_productos = (st.session_state.df_prod['Nombre'] + " (ID: " + 
                                 st.session_state.df_prod['ID_Producto'].astype(str) + ")").tolist()
            
            prod_seleccionado = st.selectbox("Buscar producto", options=opciones_productos, index=None, placeholder="Escriba para buscar...", key="buscador_cambios")
            
            if prod_seleccionado:
                nombre_real = prod_seleccionado.split(" (ID: ")[0]
                id_real = prod_seleccionado.split("(ID: ")[1].replace(")", "")
                
                c1, c2 = st.columns(2)
                cant_sel = c1.number_input("Cantidad:", min_value=1, value=1, key="cant_input")
                tipo_sel = c2.radio("Tipo:", ["ENTRA", "SALE"], horizontal=True, key="tipo_input")
                
                if st.button("➕ Añadir a la lista"):
                    st.session_state.lista_cambios.append({
                        "ID": id_real,
                        "Producto": nombre_real,
                        "Cantidad": cant_sel,
                        "Tipo": tipo_sel
                    })
                    st.rerun()
            
            # 3. Mostrar resumen y botones de acción
            if st.session_state.lista_cambios:
                st.write("Resumen del movimiento:")
                st.table(pd.DataFrame(st.session_state.lista_cambios))
                
                if st.button("❌ Limpiar lista"):
                    st.session_state.lista_cambios = []
                    st.rerun()
            
                motivo = st.text_input("Motivo del cambio:")
                   
                # PANEL VENDEDOR
                if st.button("📤 Enviar Pre-cambio a Revisión"):
                    try:
                        for item in st.session_state.lista_cambios:
                            db.table("PRE_CAMBIOS").insert({
                                "Fecha": datetime.now().isoformat(),
                                "Código": item['ID'],
                                "Nombre": item['Producto'],
                                "Descripción": motivo,
                                "Entra": int(item['Cantidad']) if item['Tipo'] == 'ENTRA' else 0,
                                "Sale": int(item['Cantidad']) if item['Tipo'] == 'SALE' else 0,
                                "Estado": "PENDIENTE",
                                "Usuario": st.session_state.get('usuario_actual', 'Desconocido') # <--- CAMBIA 'usuario' por 'usuario_actual'
                            }).execute()
                        st.success("✅ Enviado a revisión.")
                        st.session_state.lista_cambios = []
                        st.rerun()
                    except Exception as e:
                        st.error(f"Error: {e}")

        # --- PESTAÑA DIVISOR ---
        with tab_divisor:
            st.subheader("✂️ Divisor de Fardos")
            
            # 1. Definimos los patrones de fardo
            patrones_fardo = [r'\bx12\b', r'\bx24\b', r'\bx30\b', 
                              r'\bX12\b', r'\bX24\b', r'\bX30\b']
            
            regex_patron = '|'.join(patrones_fardo)
            
            # 2. Filtramos: Rubro LECHE + Stock > 0 + Que contenga el patrón exacto como palabra
            df_filtrado_div = st.session_state.df_prod[
                (st.session_state.df_prod['Rubro'] == 'LECHE') & 
                (st.session_state.df_prod['Stock_Actual'] > 0) &
                (st.session_state.df_prod['Nombre'].str.contains(regex_patron, regex=True, na=False))
            ].copy()
            
            if df_filtrado_div.empty:
                st.warning("No hay productos de 'LECHE' identificados como fardos (x12, x24, x30) con stock disponible.")
            else:
                opciones_prod = (df_filtrado_div['ID_Producto'].astype(str) + " - " + df_filtrado_div['Nombre']).tolist()
                id_fardo_sel = st.selectbox("Seleccionar Fardo a dividir:", [""] + opciones_prod, key="div_fardo")
                
                if id_fardo_sel:
                    # Guardamos el ID en sesión
                    id_fardo = id_fardo_sel.split(" - ")[0]
                    st.session_state.id_fardo_temp = id_fardo 
                    
                    # Guardamos la fila completa en sesión
                    st.session_state.fila_fardo_temp = df_filtrado_div[df_filtrado_div['ID_Producto'].astype(str) == id_fardo].iloc[0]
                    
                    st.info(f"Fardo: {st.session_state.fila_fardo_temp['Nombre']} | Stock: {st.session_state.fila_fardo_temp['Stock_Actual']}")
                    
                    with st.form("form_divisor"):
                        c1, c2 = st.columns(2)
                        unidades = c1.number_input("¿Cuántas unidades trae el fardo?", min_value=1, value=24)
                        id_cajita = c2.text_input("Código de la Cajita Individual:")
                        
                        # Usamos los datos de la sesión
                        fila_fardo = st.session_state.fila_fardo_temp
                        id_fardo = st.session_state.id_fardo_temp # <--- RECUPERAMOS EL ID AQUÍ
                        
                        costo_fardo = float(fila_fardo['Precio_Costo'])
                        costo_unitario = costo_fardo / unidades
                        precio_sugerido = ( (int((costo_unitario * 1.40) // 100) + 1) * 100 )
                        
                        st.write(f"Costo unitario: ${costo_unitario:,.2f} | Precio Sugerido: ${precio_sugerido:,.0f}")
                        
                        if st.form_submit_button("🚀 Confirmar División"):
                            # --- VALIDACIÓN DE STOCK ---
                            # Todo esto DEBE estar indentado bajo el if de arriba
                            if int(fila_fardo['Stock_Actual']) <= 0:
                                st.error(f"⚠️ ¡Error! El fardo '{fila_fardo['Nombre']}' no cuenta con existencias para dividir (Stock actual: 0).")
                                st.stop() 
                            
                            # --- LÓGICA DE ACTUALIZACIÓN ---
                            # Todo esto TAMBIÉN debe estar indentado bajo el if
                            try:
                                # Descontar 1 al fardo
                                nuevo_stock_fardo = int(fila_fardo['Stock_Actual']) - 1
                                db.table("PRODUCTOS").update({"Stock_Actual": nuevo_stock_fardo}).eq("ID_Producto", id_fardo).execute()
                                
                                # Sumar unidades a la cajita
                                prod_cajita = db.table("PRODUCTOS").select("Stock_Actual").eq("ID_Producto", id_cajita).execute().data
                                if not prod_cajita:
                                    st.error("¡Error! El código de la cajita no existe en la base de datos.")
                                    st.stop()
                                    
                                stock_cajita_old = int(prod_cajita[0]['Stock_Actual'])
                                db.table("PRODUCTOS").update({"Stock_Actual": stock_cajita_old + unidades}).eq("ID_Producto", id_cajita).execute()
                                
                                # --- REGISTRO EN CAMBIOS ---
                                # Usamos st.session_state.usuario_actual
                                usuario_logueado = st.session_state.get('usuario_actual', 'Desconocido')
                                
                                db.table("CAMBIOS").insert({
                                    "Fecha": datetime.now().isoformat(),
                                    "Usuario": usuario_logueado, # <--- Aquí queda registrado tu nombre
                                    "Código": id_fardo,
                                    "Nombre": fila_fardo['Nombre'],
                                    "Descripción": f"División de fardo: Se transformó en {unidades} unidades de {id_cajita}",
                                    "Entra": 0, "Sale": 1,
                                    "existencia_ant": int(fila_fardo['Stock_Actual']),
                                    "existencia_actual": nuevo_stock_fardo
                                }).execute()
                                
                                db.table("CAMBIOS").insert({
                                    "Fecha": datetime.now().isoformat(),
                                    "Usuario": usuario_logueado, # <--- Aquí también
                                    "Código": id_cajita,
                                    "Nombre": "Cajitas (División)",
                                    "Descripción": f"Ingreso por división de fardo {id_fardo}",
                                    "Entra": int(unidades), "Sale": 0,
                                    "existencia_ant": stock_cajita_old,
                                    "existencia_actual": stock_cajita_old + unidades
                                }).execute()
                                
                                st.success(f"✅ ¡División realizada por {usuario_logueado}!")
                                st.rerun()
                                
                            except Exception as e:
                                st.error(f"Error al procesar la división: {e}")
        
        # --- PESTAÑAS DE ADMINISTRADOR ---
        if st.session_state.rol == "Administrador":
            
            # --- PESTAÑA ALTA ---
            with tab_alta:
                st.subheader("➕ Registrar Nuevo Artículo")
                
                with st.form("form_alta_producto_unico", clear_on_submit=True):
                    c_alta1, c_alta2 = st.columns(2)
                    
                    with c_alta1:
                        id_nuevo = st.text_input("Código / ID Producto*", key="alta_id").strip()
                        nombre_nuevo = st.text_input("Descripción / Nombre*", key="alta_nom").strip()
                        marca_nueva = st.text_input("Marca", key="alta_marca").strip()
                        rubro_nuevo = st.selectbox("Rubro", options=LISTA_RUBROS)
                        prov_seleccionado = st.selectbox("Proveedor", options=lista_proveedores)
                        
                    with c_alta2:
                        stock_ini = st.number_input("Stock Inicial", min_value=0, value=0, step=1)
                        costo_ini = st.number_input("Precio Costo ($)", min_value=0.0, value=0.0, step=10.0)
                        p1 = st.number_input("Precio Lista 1 ($)*", min_value=0.0, value=0.0, step=10.0)
                        p2 = st.number_input("Precio Lista 2 ($)", min_value=0.0, value=0.0, step=10.0)
                        p3 = st.number_input("Precio Lista 3 ($)", min_value=0.0, value=0.0, step=10.0)
                        p4 = st.number_input("Precio Lista 4 ($)", min_value=0.0, value=0.0, step=10.0)
                        p5 = st.number_input("Precio Lista 5 ($)", min_value=0.0, value=0.0, step=10.0)

                    st.caption("* Campos obligatorios")
                    btn_guardar = st.form_submit_button("💾 Guardar Producto en Base de Datos")

                if btn_guardar:
                    if not id_nuevo or not nombre_nuevo or p1 <= 0:
                        st.error("Por favor, completa los campos obligatorios (ID, Nombre y Precio 1 > 0).")
                    else:
                        # Nos aseguramos de que no envíe strings vacíos a columnas numéricas
                        nuevo_prod = {
                            "ID_Producto": id_nuevo,
                            "Nombre": nombre_nuevo,
                            "Rubro": rubro_nuevo if rubro_nuevo != "" else None,
                            "Marca": marca_nueva if marca_nueva != "" else None,
                            "Stock_Actual": int(stock_ini),
                            "Precio_Costo": float(costo_ini),
                            "Precio_1": float(p1),
                            "Precio_2": float(p2),
                            "Precio_3": float(p3),
                            "Precio_4": float(p4),
                            "Precio_5": float(p5),
                            "ID_Proveedor": None, # Cambiado a None (null)
                            "Stock_Min": 0,       # Aseguramos entero 0
                            "Stock_Max": 0,       # Aseguramos entero 0
                            "Imagen": None        # Cambiado a None (null)
                        }
                        
                        try:
                            db.table("PRODUCTOS").insert(nuevo_prod).execute()
                            st.success(f"🎉 ¡Producto '{nombre_nuevo}' guardado!")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Error técnico: {e}")

            # --- PESTAÑA MODIFICAR ---
            with tab_modificar:
                st.subheader("✏️ Modificar Producto Completo")
                
                if not st.session_state.df_prod.empty:
                    opciones = (st.session_state.df_prod['ID_Producto'].astype(str) + " - " + st.session_state.df_prod['Nombre']).tolist()
                    prod_sel = st.selectbox("Seleccionar producto:", [""] + opciones)
                    
                    def asegurar_float(valor):
                        try:
                            return float(valor) if valor not in [None, ''] else 0.0
                        except:
                            return 0.0

                    if prod_sel:
                        id_sel = prod_sel.split(" - ")[0]
                        fila = st.session_state.df_prod[st.session_state.df_prod['ID_Producto'].astype(str) == id_sel].iloc[0]
                        
                        with st.form("form_mod_completo"):
                            c1, c2, c3 = st.columns(3)
                            with c1:
                                n_nom = st.text_input("Nombre", value=str(fila.get('Nombre', '')))
                                idx_rubro = LISTA_RUBROS.index(fila.get('Rubro')) if fila.get('Rubro') in LISTA_RUBROS else 0
                                n_rub = st.selectbox("Rubro", options=LISTA_RUBROS, index=idx_rubro)
                                n_mar = st.text_input("Marca", value=str(fila.get('Marca', '')))
                                prov_actual = fila.get('ID_Proveedor', "")
                                idx_prov = lista_proveedores.index(prov_actual) if prov_actual in lista_proveedores else 0
                                n_prov = st.selectbox("Proveedor", options=lista_proveedores, index=idx_prov)
                            with c2:
                                n_stk = st.number_input("Stock Actual", value=int(asegurar_float(fila.get('Stock_Actual', 0))))
                                n_min = st.number_input("Stock Min", value=int(asegurar_float(fila.get('Stock_Min', 0))))
                                n_max = st.number_input("Stock Max", value=int(asegurar_float(fila.get('Stock_Max', 0))))
                                n_img = st.text_input("URL Imagen", value=str(fila.get('Imagen', '')))
                            with c3:
                                n_cos = st.number_input("Costo", value=asegurar_float(fila.get('Precio_Costo', 0)), format="%.2f")
                                n_p1 = st.number_input("Precio 1", value=asegurar_float(fila.get('Precio_1', 0)), format="%.2f")
                                n_p2 = st.number_input("Precio 2", value=asegurar_float(fila.get('Precio_2', 0)), format="%.2f")
                                n_p3 = st.number_input("Precio 3", value=asegurar_float(fila.get('Precio_3', 0)), format="%.2f")
                                n_p4 = st.number_input("Precio 4", value=asegurar_float(fila.get('Precio_4', 0)), format="%.2f")
                                n_p5 = st.number_input("Precio 5", value=asegurar_float(fila.get('Precio_5', 0)), format="%.2f")
                            
                            if st.form_submit_button("✅ Guardar Todos los Cambios"):
                                # Función para limpiar campos de texto: si es "None" o vacío, devuelve None (nulo)
                                def clean_text(val):
                                    if val is None or val == "" or str(val).lower() == "none":
                                        return None
                                    return str(val)

                                # Función para asegurar número
                                def clean_num(val, is_float=False):
                                    try:
                                        if val in [None, '', 'None']: return 0.0 if is_float else 0
                                        return float(val) if is_float else int(val)
                                    except:
                                        return 0.0 if is_float else 0

                                # Diccionario corregido
                                datos_update = {
                                    "Nombre": str(n_nom) if n_nom else "Sin nombre",
                                    "Rubro": clean_text(n_rub),
                                    "Marca": clean_text(n_mar),
                                    "ID_Proveedor": clean_num(n_prov), # Lo pasamos por clean_num porque es bigint
                                    "Stock_Actual": clean_num(n_stk),
                                    "Stock_Min": clean_num(n_min),
                                    "Stock_Max": clean_num(n_max),
                                    "Imagen": clean_text(n_img),
                                    "Precio_Costo": clean_num(n_cos, True),
                                    "Precio_1": clean_num(n_p1, True),
                                    "Precio_2": clean_num(n_p2, True),
                                    "Precio_3": clean_num(n_p3, True),
                                    "Precio_4": clean_num(n_p4, True),
                                    "Precio_5": clean_num(n_p5, True)
                                }
                                
                                try:
                                    # Ejecutamos el update
                                    db.table("PRODUCTOS").update(datos_update).eq("ID_Producto", id_sel).execute()
                                    st.success("¡Producto actualizado exitosamente!")
                                    if 'df_prod' in st.session_state: del st.session_state['df_prod']
                                    st.rerun()
                                except Exception as e:
                                    st.error(f"Error al actualizar en Supabase: {e}")
                                    st.write("Datos enviados:", datos_update) # Esto te ayudará a ver qué campo falla exactamente
                else:
                    st.info("No hay productos para modificar.")

            # --- PESTAÑA IMPORTAR (Versión Optimizada) ---
            with tab_importar:
                st.subheader("📥 Importación Masiva de Productos")
                st.markdown("Subí un archivo CSV (UTF-8 o Latin-1) o Excel.")
                
                archivo = st.file_uploader("Seleccioná el archivo", type=['csv', 'xlsx'])

                if archivo and st.button("🚀 Procesar e Importar"):
                    try:
                        # 1. Lectura robusta
                        if archivo.name.endswith('.csv'):
                            try:
                                df_i = pd.read_csv(archivo, encoding='utf-8')
                            except UnicodeDecodeError:
                                archivo.seek(0)
                                df_i = pd.read_csv(archivo, encoding='latin-1')
                        else:
                            df_i = pd.read_excel(archivo)

                        # --- LIMPIEZA INTELIGENTE ---
                        
                        # A. Eliminamos columnas que estén COMPLETAMENTE vacías
                        df_i = df_i.dropna(axis=1, how='all')
                        
                        # B. Aseguramos ID como string
                        df_i['ID_Producto'] = df_i['ID_Producto'].astype(str)
                        
                        # C. Limpieza selectiva: 
                        # Solo limpiamos los valores NaN en las columnas que SÍ existen en el archivo
                        for col in df_i.columns:
                            if col in ['Stock_Actual', 'Stock_Min', 'Stock_Max']:
                                df_i[col] = pd.to_numeric(df_i[col], errors='coerce').fillna(0).astype(int)
                            elif 'Precio' in col:
                                df_i[col] = pd.to_numeric(df_i[col], errors='coerce').fillna(0.0)
                            else:
                                # Para Nombre, Rubro, etc., rellenamos con texto vacío
                                df_i[col] = df_i[col].fillna('')

                        # D. Convertir a lista de diccionarios
                        data_to_upsert = df_i.to_dict(orient='records')

                        # E. Importación
                        db.table("PRODUCTOS").upsert(data_to_upsert).execute()

                        st.success(f"✅ Importación exitosa: {len(df_i)} productos procesados.")
                        st.balloons()
                        st.rerun()

                    except Exception as e:
                        st.error(f"Error al procesar el archivo: {e}")
                        st.write("Asegurate de que las columnas coincidan exactamente con: ID_Producto, Nombre, etc.")

    # =====================================================================
    # MODULO: 🚚 PROVEEDORES
    # =====================================================================
    if menu == "🚚 Proveedores":
        st.title("🚚 Gestión de Proveedores")
        
        # 1. Carga de datos desde Supabase
        response = db.table("PROVEEDORES").select("*").execute()
        df_prov = pd.DataFrame(response.data)
        
        tab1, tab2, tab3 = st.tabs(["🔍 Explorador", "➕ Nuevo Proveedor", "✏️ Modificar"])
        
        with tab1:
            st.subheader("Lista de Proveedores")
            busqueda_prov = st.text_input("🔍 Filtrar por Nombre, CUIT o Rubro:")
            
            df_filtrado = df_prov
            if busqueda_prov and not df_prov.empty:
                df_filtrado = df_prov[
                    df_prov.apply(lambda row: busqueda_prov.lower() in str(row['Razon_Social']).lower() or 
                                            busqueda_prov.lower() in str(row['CUIT']).lower() or 
                                            busqueda_prov.lower() in str(row['Rubros_Asociados']).lower(), axis=1)
                ]
            st.dataframe(df_filtrado, use_container_width=True)
            
        with tab2:
            with st.form("nuevo_prov", clear_on_submit=True):
                # ID automático simple
                nuevo_id = str(len(df_prov) + 1).zfill(4) 
                st.info(f"ID Sugerido: {nuevo_id}")
                
                col1, col2 = st.columns(2)
                with col1:
                    razon_social = st.text_input("Razón Social")
                    cuit = st.text_input("CUIT (Formato: XX-XXXXXXXX-X)")
                    direccion = st.text_input("Dirección")
                with col2:
                    telefono = st.text_input("Teléfono")
                    condicion = st.selectbox("Condición Fiscal", ["Responsable Inscripto", "Monotributo", "Exento"])
                
                rubros_seleccionados = st.multiselect("Asociar Rubros", LISTA_RUBROS)
                
                btn_guardar = st.form_submit_button("Guardar Proveedor")
                
                if btn_guardar:
                    # Validaciones (Tu lógica original)
                    if not re.match(r'^\d{2}-\d{8}-\d{1}$', cuit):
                        st.error("Error: El CUIT debe tener formato XX-XXXXXXXX-X")
                    elif not df_prov.empty and cuit in df_prov['CUIT'].astype(str).values:
                        st.error("Error: Ya existe un proveedor con ese CUIT.")
                    else:
                        try:
                            db.table("PROVEEDORES").insert({
                                "ID_Proveedor": nuevo_id,
                                "Razon_Social": razon_social,
                                "Rubros_Asociados": ", ".join(rubros_seleccionados),
                                "CUIT": cuit,
                                "Condicion_Fiscal": condicion,
                                "Direccion": direccion,
                                "Telefono": telefono
                            }).execute()
                            st.success("¡Proveedor cargado exitosamente!")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Error al guardar: {e}")

        with tab3:
            if not df_prov.empty:
                prov_seleccionado = st.selectbox("Seleccionar proveedor a editar", df_prov['Razon_Social'].tolist())
                datos = df_prov[df_prov['Razon_Social'] == prov_seleccionado].iloc[0]
                
                with st.form("modificar_prov"):
                    col1, col2 = st.columns(2)
                    with col1:
                        razon_social = st.text_input("Razón Social", value=datos['Razon_Social'])
                        cuit = st.text_input("CUIT", value=datos['CUIT'])
                        direccion = st.text_input("Dirección", value=datos['Direccion'])
                    with col2:
                        telefono = st.text_input("Teléfono", value=datos['Telefono'])
                        condicion = st.selectbox("Condición Fiscal", ["Responsable Inscripto", "Monotributo", "Exento"], 
                                            index=["Responsable Inscripto", "Monotributo", "Exento"].index(datos['Condicion_Fiscal']) if datos['Condicion_Fiscal'] in ["Responsable Inscripto", "Monotributo", "Exento"] else 0)
                        
                        # Recuperar rubros guardados
                        raw_rubros = str(datos['Rubros_Asociados']) if pd.notna(datos['Rubros_Asociados']) else ""
                        rubros_defecto = [r.strip() for r in raw_rubros.split(",") if r.strip() in LISTA_RUBROS]
                        rubros = st.multiselect("Rubros", LISTA_RUBROS, default=rubros_defecto)
                    
                    btn_mod = st.form_submit_button("Actualizar Proveedor")
                    
                    if btn_mod:
                        try:
                            db.table("PROVEEDORES").update({
                                "Razon_Social": razon_social,
                                "Rubros_Asociados": ", ".join(rubros),
                                "CUIT": cuit,
                                "Condicion_Fiscal": condicion,
                                "Direccion": direccion,
                                "Telefono": telefono
                            }).eq("ID_Proveedor", datos['ID_Proveedor']).execute()
                            st.success("Datos actualizados correctamente.")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Error al actualizar: {e}")
            else:
                st.info("No hay proveedores para modificar.")

    # =====================================================================
    # MODULO: 📦 COMPRAS
    # =====================================================================
    elif menu == "📦 Compras":
        st.header("📦 Registro de Compras (Entrada de Mercadería)")

        # Diccionario de Márgenes
        MARGENES_RUBROS = {
            "ACEITE": [0.35, 0.35, 0.25, 0.15, 0.0], "ACONDICIONADOR": [0.35, 0.35, 0.25, 0.15, 0.0],
            "ALGODON": [0.35, 0.35, 0.25, 0.15, 0.0], "APOSITOS": [0.35, 0.35, 0.25, 0.15, 0.0],
            "BAÑO LIQUIDO": [0.35, 0.35, 0.25, 0.15, 0.0], "CAMBIADOR": [1.0, 0.5, 0.4, 0.3, 0.0],
            "CHUPETE": [0.35, 0.35, 0.25, 0.15, 0.0], "COLONIA": [0.35, 0.35, 0.25, 0.15, 0.0],
            "CREMA": [0.35, 0.35, 0.25, 0.15, 0.0], "CUCHARAS": [0.35, 0.35, 0.25, 0.15, 0.0],
            "DESCONGESTIONADORES NASALES": [0.35, 0.35, 0.25, 0.15, 0.0], "ESPONJA": [0.35, 0.35, 0.25, 0.15, 0.0],
            "HIGIENE BUCAL": [0.35, 0.35, 0.25, 0.15, 0.0], "HISOPOS": [0.35, 0.35, 0.25, 0.15, 0.0],
            "JABON": [0.35, 0.35, 0.25, 0.15, 0.0], "LECHE": [0.40, 0.15, 0.10, 0.08, 0.0],
            "LIMPIEZA ROPA": [0.35, 0.35, 0.25, 0.15, 0.0], "MAMADERA": [0.35, 0.35, 0.25, 0.15, 0.0],
            "MOCHILA MATERNAL": [0.35, 0.35, 0.25, 0.15, 0.0], "MORDILLOS": [0.35, 0.35, 0.25, 0.15, 0.0],
            "OLEO CALCAREO": [0.35, 0.35, 0.25, 0.15, 0.0], "PAÑALES": [0.20, 0.15, 0.10, 0.08, 0.0],
            "PLATOS": [0.35, 0.35, 0.25, 0.15, 0.0], "PROTECTOR MAMARIO": [0.35, 0.35, 0.25, 0.15, 0.0],
            "SACALECHES": [0.35, 0.35, 0.25, 0.15, 0.0], "SEGURIDAD": [0.35, 0.35, 0.25, 0.15, 0.0],
            "SHAMPOO": [0.35, 0.35, 0.25, 0.15, 0.0], "TALCO": [0.35, 0.35, 0.25, 0.15, 0.0],
            "TETINAS": [0.35, 0.35, 0.25, 0.15, 0.0], "TIJERAS": [0.35, 0.35, 0.25, 0.15, 0.0],
            "TOALLITAS FEMENINAS": [0.35, 0.35, 0.25, 0.15, 0.0], "TOALLITAS HUMEDAS": [0.35, 0.35, 0.25, 0.15, 0.0],
            "VASOS": [0.35, 0.35, 0.25, 0.15, 0.0]
        }

        def calcular_sugerido(costo, rubro, tipo_precio):
            margen = MARGENES_RUBROS.get(rubro, {"P1": 0.30, "P2": 0.25, "P3": 0.20, "P4": 0.15, "P5": 0.10}) # Default
            return costo * (1 + margen.get(tipo_precio, 0))
        
        # 1. CARGA DE DATOS Y ESTADO
        df_prod = pd.DataFrame(db.table("PRODUCTOS").select("*").execute().data)
        df_prov = pd.DataFrame(db.table("PROVEEDORES").select("*").execute().data)
        lista_proveedores = df_prov['Razon_Social'].tolist() if not df_prov.empty else ["No hay proveedores"]
        
        if 'carrito_compra' not in st.session_state: st.session_state.carrito_compra = []
        if "ver_historial" not in st.session_state: st.session_state.ver_historial = False
        if "reset_manual" not in st.session_state: st.session_state.reset_manual = 0
        if "txt_barcode" not in st.session_state: st.session_state.txt_barcode = ""

        # --- BOTÓN PARA ACTIVAR/DESACTIVAR HISTORIAL ---
        if st.button("📂 Ver/Ocultar Historial"):
            st.session_state.ver_historial = not st.session_state.ver_historial
            st.rerun()

        # --- 2. EL GABINETE (HISTORIAL) ---
        if st.session_state.ver_historial:
            st.subheader("🗄️ Gabinete de Gestión de Compras")
            tab_facturas, tab_ordenes = st.tabs(["📄 Facturas", "📝 Órdenes de Compra"])

            with tab_facturas:
                df_hist = pd.DataFrame(db.table("COMPRAS_CABECERA").select("*").execute().data)
                if not df_hist.empty: st.dataframe(df_hist, use_container_width=True)
                else: st.info("No hay facturas.")

            with tab_ordenes:
                df_oc = pd.DataFrame(db.table("ORDENES_COMPRA").select("*").execute().data)
                if not df_oc.empty:
                    opciones_oc = df_oc['ID_Compra'].astype(str) + " - " + df_oc['Proveedor']
                    oc_sel = st.selectbox("¿Qué orden procesar?", ["-- Seleccionar --"] + opciones_oc.tolist())
                    if oc_sel != "-- Seleccionar --":
                        id_oc = oc_sel.split(" - ")[0]
                        det_oc = pd.DataFrame(db.table("DETALLE_ORDENES").select("*").eq("ID_Compra", id_oc).execute().data)
                        st.dataframe(det_oc, use_container_width=True)
                        if st.button("✏️ PROCESAR / EDITAR ORDEN"):
                            st.session_state.oc_en_edicion = id_oc
                            
                            # Recuperamos cabecera y detalle
                            cabecera_oc = db.table("ORDENES_COMPRA").select("*").eq("ID_Compra", id_oc).execute().data[0]
                            det_oc = db.table("DETALLE_ORDENES").select("*").eq("ID_Compra", id_oc).execute().data
                            
                            # 1. Precargar datos de cabecera en session_state
                            # (Usaremos variables temporales en session_state para alimentar los inputs)
                            st.session_state.temp_prov = cabecera_oc['Proveedor']
                            st.session_state.temp_pago = cabecera_oc['Metodo_Pago']
                            # Separamos el Nro_Factura (ej: "00001-00000123")
                            nro_parts = cabecera_oc['Nro_Factura'].split("-")
                            st.session_state.temp_punto = nro_parts[0]
                            st.session_state.temp_nro = nro_parts[1]
                            
                            # 2. Cargar carrito
                            df_det_oc = pd.DataFrame(det_oc) 
                            
                            if df_det_oc.empty:
                                st.warning("La tabla de detalles está vacía para este ID.")
                            else:
                                carrito_cargado = []
                                for _, fila in df_det_oc.iterrows(): 
                            
                                    prod_info = df_prod[df_prod['ID_Producto'].astype(str) == str(fila['ID_Producto'])]
                                    nombre_prod = prod_info.iloc[0]['Nombre'] if not prod_info.empty else "Producto no encontrado"
                                    carrito_cargado.append({
                                        "id": str(fila['ID_Producto']), 
                                        "nombre": nombre_prod,
                                        "cantidad": int(fila['Cantidad']), 
                                        "costo": float(fila['Precio_Costo_Unitario']),
                                        "subtotal": float(fila['Subtotal'])
                                    })
                            
                            st.session_state.carrito_compra = carrito_cargado
                            st.session_state.ver_historial = False
                            st.rerun()
                        
                        if st.button("🗑️ ELIMINAR ORDEN"):
                            db.table("DETALLE_ORDENES").delete().eq("ID_Compra", id_oc).execute()
                            db.table("ORDENES_COMPRA").delete().eq("ID_Compra", id_oc).execute()
                            st.success("Eliminada.")
                            st.rerun()

                else: st.info("No hay órdenes.")
            
            if st.button("⬅️ Volver al Registro"):
                st.session_state.ver_historial = False
                st.rerun()
            st.stop() # Detiene el renderizado del registro mientras se ve el gabinete

        # --- FUNCIÓN DE ESCANEO ---
        def procesar_escaneo():
            barcode = st.session_state.txt_barcode
            if barcode != "":
                res = df_prod[df_prod['ID_Producto'].astype(str) == barcode]
                if not res.empty:
                    p = res.iloc[0]
                    st.session_state.carrito_compra.append({
                        "id": str(p['ID_Producto']), "nombre": p['Nombre'], 
                        "cantidad": 1, "costo": float(p['Precio_Costo'] or 0), 
                        "subtotal": float(p['Precio_Costo'] or 0)
                    })
            st.session_state.txt_barcode = ""

        # --- 3. SECCIÓN: DATOS DE FACTURA (MODIFICADA PARA VALIDACIÓN) ---
        with st.expander("📄 Datos de la Factura Actual", expanded=True):
            # Verificación de duplicados
            df_hist_check = pd.DataFrame(db.table("COMPRAS_CABECERA").select("Nro_Factura").execute().data)
            facturas_existentes = df_hist_check['Nro_Factura'].tolist() if not df_hist_check.empty else []

            c1, c2, c3 = st.columns([1, 1.5, 1])
            with c1:
                prov_sel = st.selectbox("Proveedor", lista_proveedores, 
                                        index=lista_proveedores.index(st.session_state.get("temp_prov", lista_proveedores[0])),
                                        key="prov_main")
                fecha_factura = st.date_input("Fecha de Factura")
            
            with c2:
                f1, _, f2 = st.columns([1, 0.2, 2])
                f_punto = f1.text_input("00000", value=st.session_state.get("temp_punto", ""), max_chars=5)
                f_nro = f2.text_input("00000000", value=st.session_state.get("temp_nro", ""), max_chars=8)
                
                # LÓGICA FLEXIBLE: 
                # Si está vacío, asignamos el código de pre-carga
                if not f_punto and not f_nro:
                    nro_fact_completo = "00000-00000000"
                else:
                    nro_fact_completo = f"{f_punto.zfill(5)}-{f_nro.zfill(8)}"
                    
                    # Solo validamos duplicados si NO es el código de pre-carga
                    if nro_fact_completo != "00000-00000000" and nro_fact_completo in facturas_existentes:
                        st.error(f"⚠️ La factura {nro_fact_completo} ya existe.")
                        nro_fact_completo = "DUPLICADA"
            with c3:
                pago_compra = st.selectbox("Método de Pago", ["Contado", "Transferencia", "Cuenta Corriente"], key="pago_main")

        # --- SECCIÓN: BUSCADOR UNIFICADO (Estilo Punto de Venta) ---
        st.subheader("🔍 Añadir Productos a la Compra")

        # 1. Definición de la función de selección unificada
        def procesar_seleccion_compra():
            # Buscamos en el session_state el valor que eligió el usuario
            seleccion = st.session_state.prod_compra_key
            if seleccion:
                # Extraemos el ID del texto "Nombre - ID"
                id_seleccionado = seleccion.split(" - ")[-1]
                
                # Buscamos el producto en el dataframe
                pm = df_prod[df_prod['ID_Producto'].astype(str) == id_seleccionado].iloc[0]
                
                # Agregamos al carrito
                st.session_state.carrito_compra.append({
                    "id": str(pm['ID_Producto']), 
                    "nombre": pm['Nombre'], 
                    "cantidad": 1, 
                    "costo": float(pm['Precio_Costo'] or 0), 
                    "subtotal": float(pm['Precio_Costo'] or 0),
                    "Precio_1": float(pm['Precio_1'] or 0),
                    "Precio_2": float(pm['Precio_2'] or 0),
                    "Precio_3": float(pm['Precio_3'] or 0),
                    "Precio_4": float(pm['Precio_4'] or 0),
                    "Precio_5": float(pm['Precio_5'] or 0)
                })
                
                # IMPORTANTE: Al poner None, el selectbox se limpia automáticamente
                st.session_state.prod_compra_key = None 

        # 2. Preparamos las opciones de búsqueda (Nombre + ID)
        # Esto permite que el usuario escriba tanto el nombre como el código en el mismo lugar
        opciones_busqueda = (df_prod['Nombre'] + " - " + df_prod['ID_Producto'].astype(str)).tolist()
        
        # 3. Interfaz única (Sin columnas redundantes)
        st.selectbox(
            "Buscar por nombre o código", 
            options=opciones_busqueda, 
            index=None, # Esto hace que no aparezca el "-- Seleccionar --" y se vea el placeholder
            placeholder="Escriba para buscar producto o escanee...",
            key="prod_compra_key",
            on_change=procesar_seleccion_compra
        )

        # --- MOSTRAR CARRITO Y EDICIÓN DE PRECIOS ---
        if st.session_state.carrito_compra:
            st.subheader("🛒 Detalle de Items")
            for i, item in enumerate(st.session_state.carrito_compra):
                # Obtener rubro
                p_info = df_prod[df_prod['ID_Producto'].astype(str) == str(item['id'])]
                rubro = p_info.iloc[0]['Rubro'] if not p_info.empty else "OTROS"
                margenes = MARGENES_RUBROS.get(rubro, [0.3, 0.2, 0.1, 0.05, 0.0])

                with st.container(border=True):
                    c_head, c_btn = st.columns([6, 1])
                    c_head.write(f"**{item['nombre']}** | Rubro: {rubro}")
                    if c_btn.button("🗑️ Eliminar", key=f"del_final_{i}"):
                        st.session_state.carrito_compra.pop(i)
                        st.rerun()

                    # --- LÓGICA DE ACTUALIZACIÓN EN VIVO ---
                    cols = st.columns([1, 1, 5])
                    
                    # Usamos key para que el valor esté siempre sincronizado en session_state
                    n_cant = cols[0].number_input("Cant", min_value=1, value=int(item['cantidad']), key=f"q_{i}")
                    n_costo = cols[1].number_input("Costo $", value=float(item['costo']), key=f"p_{i}")
                    
                    # Fila 3: Inputs de 5 precios
                    cols_p = st.columns(5)
                    nuevos_precios = {}
                    
                    for j in range(5):
                        # 1. Calculamos el sugerido solo para mostrarlo en el label
                        sugerido = n_costo * (1 + margenes[j])
                        
                        # 2. LÓGICA DE PERSISTENCIA:
                        # Si el ítem YA tiene un precio guardado, usamos ese.
                        # Si es nuevo (no tiene precio guardado), usamos el valor que tenga en la BD (p[f'Precio_{j+1}'])
                        # Si tampoco hay valor en BD, ahí recién usamos el 'sugerido'.
                        precio_inicial = item.get(f'Precio_{j+1}')
                        if precio_inicial is None:
                            # Buscamos el precio actual en la base de datos (p_info es la serie del producto)
                            precio_inicial = p_info.iloc[0][f'Precio_{j+1}'] if not p_info.empty else sugerido
                        
                        nuevos_precios[f'Precio_{j+1}'] = cols_p[j].number_input(
                            f"P{j+1} (S:${sugerido:.0f})", 
                            value=float(precio_inicial or 0),
                            key=f"p{j+1}_{i}"
                        )
                    
                    # Actualizar el carrito con los nuevos valores del widget
                    st.session_state.carrito_compra[i].update({
                        'cantidad': n_cant, 
                        'costo': n_costo, 
                        'subtotal': n_cant * n_costo, 
                        **nuevos_precios
                    })

        # --- 4. BOTONES DE REGISTRO FINAL (CON VALIDACIÓN) ---
        if st.session_state.carrito_compra:
            total_final = sum(item['subtotal'] for item in st.session_state.carrito_compra)
            st.markdown(f"### Total Factura: ${total_final:,.2f}")
            
            col_reg1, col_reg2 = st.columns(2)
            
            # Función interna modificada
            def validar_y_grabar(es_obligatorio=True):
                # Si es_obligatorio es False, permitimos campos vacíos
                if not es_obligatorio and (not f_punto and not f_nro):
                    return True, "00000-00000000"
                
                # Si es obligatorio, validamos que no estén vacíos
                if not f_punto or not f_nro:
                    st.error("⚠️ Para registrar el stock se debe completar un número de factura.")
                    return False, None
                
                nro_final = f"{f_punto.zfill(5)}-{f_nro.zfill(8)}"
                
                # Chequeo de duplicados
                df_hist_check = pd.DataFrame(db.table("COMPRAS_CABECERA").select("Nro_Factura").execute().data)
                if nro_final in df_hist_check['Nro_Factura'].tolist():
                    st.error("⚠️ El número de factura ingresado ya existe.")
                    return False, None
                
                return True, nro_final

            # --- BOTÓN GUARDAR ORDEN ---
            if col_reg1.button("📝 GUARDAR ORDEN"):
                _, nro_oc = validar_y_grabar(es_obligatorio=False) 
                id_oc = f"OC-{datetime.now().strftime('%Y%m%d%H%M%S')}"
                
                # 1. Insertar Cabecera
                db.table("ORDENES_COMPRA").insert({
                    "ID_Compra": id_oc, "Fecha": str(fecha_factura), "Proveedor": prov_sel, 
                    "Nro_Factura": nro_oc, "Metodo_Pago": pago_compra, "Total_Compra": float(total_final)
                }).execute()
                    
                # 2. Guardar Detalle con validación de tipo de dato
                for item in st.session_state.carrito_compra:
                    # Nos aseguramos de que el ID_Producto sea texto limpio
                    db.table("DETALLE_ORDENES").insert({
                        "ID_Compra": id_oc,
                        "ID_Producto": str(item['id']).strip(), 
                        "Cantidad": int(item['cantidad']),
                        "Precio_Costo_Unitario": float(item['costo']),
                        "Subtotal": float(item['subtotal'])
                    }).execute()
                    
                st.session_state.carrito_compra = []
                st.success("Orden guardada correctamente.")
                st.rerun()

            # --- BOTÓN REGISTRAR STOCK (dentro de tu loop de registro) ---
            if col_reg2.button("💾 REGISTRAR Y CARGAR STOCK", type="primary"):
                es_valido, nro_fact = validar_y_grabar(es_obligatorio=True)
                
                if es_valido:
                    id_c = f"COM-{datetime.now().strftime('%Y%m%d%H%M%S')}"
                    
                    # 1. Guardar Cabecera de Compra
                    db.table("COMPRAS_CABECERA").insert({
                        "ID_Compra": id_c, "Fecha": str(fecha_factura), "Proveedor": prov_sel, 
                        "Nro_Factura": nro_fact, "Metodo_Pago": pago_compra, "Total_Compra": float(total_final)
                    }).execute()
                    
                    # 2. Guardar Detalle y Actualizar Stock Y PRECIOS
                    for item in st.session_state.carrito_compra:
                        # A. Actualizar Stock, Costo y Precios (1 al 5) en la tabla PRODUCTOS
                        # Solo si el producto es stockeable, o siempre si quieres que los precios siempre se actualicen:
                        
                        data_update = {
                            "Precio_Costo": float(item['costo']),
                            "Precio_1": float(item.get('Precio_1', 0)),
                            "Precio_2": float(item.get('Precio_2', 0)),
                            "Precio_3": float(item.get('Precio_3', 0)),
                            "Precio_4": float(item.get('Precio_4', 0)),
                            "Precio_5": float(item.get('Precio_5', 0))
                        }
                        
                        # Si además quieres actualizar el stock:
                        prod_info = df_prod[df_prod['ID_Producto'].astype(str) == str(item['id'])]
                        if not prod_info.empty and prod_info.iloc[0].get('Es_Stockeable') == True:
                            data_update["Stock_Actual"] = int(prod_info.iloc[0]['Stock_Actual']) + int(item['cantidad'])
                        
                        # Ejecutamos el update en la tabla PRODUCTOS
                        db.table("PRODUCTOS").update(data_update).eq("ID_Producto", str(item['id'])).execute()

                        # B. Guardar Detalle (en tu nueva tabla DETALLE_COMPRAS)
                        db.table("DETALLE_COMPRAS").insert({
                            "ID_Compra": id_c,
                            "ID_Producto": str(item['id']),
                            "Cantidad": int(item['cantidad']),
                            "Precio_Costo_Unitario": float(item['costo']),
                            "Subtotal": float(item['subtotal'])
                        }).execute()
                    
                    # --- AQUÍ ESTÁ EL CAMBIO CLAVE ---
                    # Si la orden estaba en edición, la eliminamos de las tablas de Órdenes
                    if 'oc_en_edicion' in st.session_state:
                        id_a_borrar = st.session_state.oc_en_edicion
                        db.table("DETALLE_ORDENES").delete().eq("ID_Compra", id_a_borrar).execute()
                        db.table("ORDENES_COMPRA").delete().eq("ID_Compra", id_a_borrar).execute()
                        
                        # Limpiamos el estado
                        del st.session_state.oc_en_edicion
                    # ----------------------------------
                    
                    st.success("¡Compra registrada y orden procesada correctamente!")
                    st.session_state.carrito_compra = []
                    st.rerun()

    # =====================================================================
    # MODULO: 👤 VENDEDORES
    # =====================================================================
    elif menu == "👥 Vendedores":
        st.title("👥 Gestión de Vendedores")
        
        # Carga de datos
        response = db.table("VENDEDORES").select("*").execute()
        df_vend = pd.DataFrame(response.data)

        # --- SOLUCIÓN: Asegurar que las columnas existan ---
        columnas_necesarias = ['ID_Vendedor', 'Nombre', 'Apellido', 'Estado']
        for col in columnas_necesarias:
            if col not in df_vend.columns:
                df_vend[col] = None # Crea la columna si no existe

        # Aseguramos que 'Estado' tenga un valor por defecto para evitar errores
        df_vend['Estado'] = df_vend['Estado'].fillna("Activo")
        
        tab1, tab2, tab3 = st.tabs(["🔍 Listado", "➕ Nuevo Vendedor", "✏️ Modificar"])
        
        with tab1:
            st.subheader("Personal de Ventas Activo")
            # Filtro directo sobre el DataFrame de Supabase
            df_activos = df_vend[df_vend['Estado'] == "Activo"]
            st.dataframe(df_activos, use_container_width=True, hide_index=True)
            
        # MÓDULO VENDEDORES: Pestaña Nuevo Vendedor
        with tab2:
            with st.form("nuevo_vendedor", clear_on_submit=True):
                # Calculamos el ID (mantenemos tu lógica)
                nuevo_id = int(pd.to_numeric(df_vend['ID_Vendedor'], errors='coerce').max() + 1) if not df_vend.empty else 1
                st.info(f"ID Automático: {nuevo_id}")
                
                # --- DISEÑO MEJORADO EN COLUMNAS ---
                col_a, col_b = st.columns(2)
                
                with col_a:
                    nombre = st.text_input("Nombre")
                    apellido = st.text_input("Apellido")
                    
                with col_b:
                    mail = st.text_input("Correo Electrónico")
                    url_foto = st.text_input("URL o Nombre de archivo de foto")
                
                # Fecha de nacimiento fuera de las columnas para mayor espacio
                fecha_nac = st.date_input("Fecha de Nacimiento", 
                                        value=datetime(1990, 1, 1), 
                                        min_value=datetime(1900, 1, 1))
                
                btn_guardar = st.form_submit_button("Registrar Vendedor")
                
                if btn_guardar:
                    if nombre and apellido and mail:
                        # Aquí tu inserción a Supabase
                        try:
                            db.table("VENDEDORES").insert({
                                "ID_Vendedor": nuevo_id,
                                "Mail": mail,
                                "Nombre": nombre,
                                "Apellido": apellido,
                                "Fecha de Nacimiento": str(fecha_nac), # Cambiado para coincidir
                                "Imagen": url_foto,                    # Cambiado de "Foto" a "Imagen"
                                "Estado": "Activo"
                            }).execute()
                            st.success(f"¡Vendedor {nombre} registrado exitosamente!")
                            st.balloons()
                            st.rerun()
                        except Exception as e:
                            st.error(f"Error al guardar: {e}")
                    else:
                        st.error("Por favor, completá los campos obligatorios.")

        with tab3:
            if not df_vend.empty:
                # Lista de vendedores
                nombres_v = df_vend['Nombre'].astype(str) + " " + df_vend['Apellido'].astype(str)
                vendedor_sel = st.selectbox("Seleccionar vendedor a editar", nombres_v)
                
                # Filtramos la fila seleccionada
                datos_v = df_vend[df_vend['Nombre'].astype(str) + " " + df_vend['Apellido'].astype(str) == vendedor_sel].iloc[0]
                
                with st.form("modificar_vendedor"):
                    st.write(f"**Editando Vendedor ID:** {datos_v['ID_Vendedor']}")
                    
                    col1, col2 = st.columns(2)
                    with col1:
                        n_nombre = st.text_input("Nombre", value=str(datos_v.get('Nombre', '')))
                        n_apellido = st.text_input("Apellido", value=str(datos_v.get('Apellido', '')))
                        n_mail = st.text_input("Mail", value=str(datos_v.get('Mail', '')))
                    with col2:
                        # Manejo de fecha: si el dato es nulo, usamos una fecha por defecto
                        fecha_raw = datos_v.get('Fecha de Nacimiento')
                        if fecha_raw and pd.notna(fecha_raw):
                            fecha_guardada = pd.to_datetime(fecha_raw).date()
                        else:
                            fecha_guardada = datetime(1990, 1, 1).date()
                            
                        n_fecha_nac = st.date_input("Fecha de Nacimiento", value=fecha_guardada, min_value=datetime(1900, 1, 1))
                        
                        estado_actual = str(datos_v.get('Estado', 'Activo'))
                        n_estado = st.selectbox("Estado", ["Activo", "Inactivo"], 
                                            index=0 if estado_actual == "Activo" else 1)
                        
                        n_foto = st.text_input("URL/Archivo de Foto", value=str(datos_v.get('Imagen', '')))
                    
                    btn_modificar = st.form_submit_button("Guardar Cambios")
                    
                    if btn_modificar:
                        try:
                            # En Supabase, los nombres de columnas con espacios deben ser exactos
                            db.table("VENDEDORES").update({
                                "Mail": n_mail,
                                "Nombre": n_nombre,
                                "Apellido": n_apellido,
                                "Fecha de Nacimiento": str(n_fecha_nac),
                                "Imagen": n_foto,
                                "Estado": n_estado
                            }).eq("ID_Vendedor", datos_v['ID_Vendedor']).execute()
                            
                            st.success(f"Datos de {n_nombre} actualizados correctamente.")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Error al actualizar: {e}")
            else:
                st.info("No hay vendedores registrados.")

    # =====================================================================
    # MODULO: 💰 CAJA
    # =====================================================================
    elif menu == "💰 Caja":
        st.title("💰 Gestión de Caja")
        
        # 1. Obtenemos estado
        turno_actual = obtener_turno_activo() 
        
        tab_turno, tab_explorar, tab_config = st.tabs(["🕒 Turno Actual", "🔍 Explorador", "⚙️ Configuración"])
        
        with tab_turno:
            if turno_actual is None:
                st.warning("⚠️ No hay ningún turno abierto.")
                monto_inicial = st.number_input("Ingrese monto de apertura (efectivo inicial)", min_value=0.0)
                if st.button("🚀 Abrir Turno"):
                    iniciar_turno(monto_inicial, "Martin")
                    st.rerun()
            else:
                st.success(f"✅ Turno Activo: {turno_actual['ID_Turno']}")
                
                with st.expander("🔒 Finalizar Turno"):
                    with st.form("form_cierre"):
                        monto_cierre = st.number_input("Monto final en caja", min_value=0.0)
                        if st.form_submit_button("Confirmar Cierre"):
                            # A. Cerrar turno en CONTROL_TURNOS
                            db.table("CONTROL_TURNOS").update({
                                "Fecha_Hora_Cierre": datetime.now().isoformat(),
                                "Monto_Cierre_Declarado": float(monto_cierre),
                                "Estado": "Cerrado"
                            }).eq("ID_Turno", turno_actual['ID_Turno']).execute()
                            
                            # B. Registrar egreso en CAJA
                            db.table("CAJA").insert({
                                "Fecha": datetime.now().isoformat(),
                                "Tipo": "Egreso",
                                "Concepto": "CIERRE CAJA DIARIO",
                                "Monto": float(monto_cierre),
                                "Forma_Pago": "Efectivo",
                                "ID_Turno": turno_actual['ID_Turno']
                            }).execute()
                            st.success("Turno cerrado correctamente.")
                            st.rerun()

        with tab_explorar:
            # Carga datos
            try:
                res_caja = db.table("CAJA").select("*").execute()
                df_caja = pd.DataFrame(res_caja.data)
            except Exception:
                df_caja = pd.DataFrame()

            fecha_sel = st.date_input("Consultar fecha", datetime.now())
            
            if not df_caja.empty:
                df_caja['Fecha'] = pd.to_datetime(df_caja['Fecha'])
                df_filtrado = df_caja[df_caja['Fecha'].dt.date == fecha_sel]
            else:
                df_filtrado = pd.DataFrame() # Tabla vacía si no hay datos

            # --- MOSTRAR MÉTRICAS (SOLO SALDO) ---
            # Ajustamos a una sola columna para que el saldo quede centrado o destacado
            col_saldo = st.columns(1)
            
            if not df_filtrado.empty:
                ingresos = df_filtrado[df_filtrado['Tipo'] == 'Ingreso']['Monto'].sum()
                egresos = df_filtrado[df_filtrado['Tipo'] == 'Egreso']['Monto'].sum()
                saldo_final = ingresos - egresos
            else:
                saldo_final = 0.0
            
            # Mostramos únicamente la métrica del saldo
            st.metric("Saldo", f"${saldo_final:,.2f}")
            
            st.divider() # Un separador visual para que quede más prolijo

            # --- MOSTRAR TABLA O AVISO ---
            if not df_filtrado.empty:
                # Definimos las columnas que SÍ queremos mostrar
                columnas_a_mostrar = ['Fecha', 'Tipo', 'Concepto', 'Monto', 'Forma_Pago']
                
                # Renderizamos solo esas columnas y ocultamos el índice
                st.dataframe(
                    df_filtrado[columnas_a_mostrar], 
                    use_container_width=True, 
                    hide_index=True  # Esto oculta el número de fila a la izquierda
                )
            else:
                st.info("No hay movimientos registrados para la fecha seleccionada.")

            # --- REGISTRO MANUAL (Fuera del if para que siempre se vea) ---
            with st.expander("➕ Registrar Movimiento Manual"):
                # ... (el código del formulario que ya tienes)
                conceptos_data = db.table("LISTA_CONCEPTOS").select("CONCEPTO").execute().data
                lista_c = [c['CONCEPTO'] for c in conceptos_data]
                
                with st.form("nuevo_movimiento", clear_on_submit=True):
                    concepto = st.selectbox("Concepto", lista_c)
                    tipo = st.radio("Tipo", ["Ingreso", "Egreso"])
                    importe = st.number_input("Importe", min_value=0.0)
                    forma_pago = st.selectbox("Forma de Pago", ["Efectivo", "Crédito", "Débito", "Transferencia"])
                    
                    if st.form_submit_button("Guardar"):
                        # Tu lógica de inserción actual...
                        db.table("CAJA").insert({
                            "ID_Turno": turno_actual['ID_Turno'] if turno_actual else "SIN_TURNO",
                            "Fecha": datetime.now().isoformat(),
                            "Tipo": tipo,
                            "Concepto": concepto,
                            "Monto": float(importe),
                            "Forma_Pago": forma_pago
                        }).execute()
                        
                        if tipo == "Ingreso" and forma_pago != "Efectivo":
                            db.table("CAJA").insert({
                                "ID_Turno": turno_actual['ID_Turno'] if turno_actual else "SIN_TURNO",
                                "Fecha": datetime.now().isoformat(),
                                "Tipo": "Egreso",
                                "Concepto": f"RETIRO PAGO {forma_pago.upper()}",
                                "Monto": float(importe),
                                "Forma_Pago": forma_pago
                            }).execute()
                        st.success("✅ Registro realizado.")
                        st.rerun()

    # =====================================================================
    # MODULO: 📈 REPORTE DE UTILIDADES
    # =====================================================================
    elif menu == "📈 Reporte de Utilidades":
        mostrar_reporte_utilidad()
