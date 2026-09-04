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
import os
import numpy as np
from math import radians, cos, sin, asin, sqrt
from ortools.constraint_solver import routing_enums_pb2
from ortools.constraint_solver import pywrapcp
from modulos import reportes

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
        st.dataframe(df_f[['ID_Venta', 'Fecha', 'Cliente_Full', 'Vendedor_Full', 'Total', 'Forma_Pago']], width='stretch')
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
                    # 1. Usamos st.button directamente
                    if st.button("🚫 ANULAR ESTA VENTA", type="primary"):
                        try:
                            # Llamamos a la función que ahora valida internamente
                            exito, mensaje = anular_venta(id_sel)
                            
                            if exito:
                                st.success(mensaje)
                                st.rerun()
                            else:
                                st.warning(mensaje)
                                
                        except Exception as e:
                            st.error(f"Error al anular: {e}")
                else:
                    st.warning("⚠️ Esta venta ya se encuentra ANULADA.")
            else:
                st.error("Venta no encontrada.")

    def anular_venta(id_vta_a_anular, usuario_actual="Martin"):
        # 0. VALIDACIÓN DE SEGURIDAD CRÍTICA (Evita doble ejecución/clics)
        vta_check = db.table("VENTAS_CABECERA").select("Estado").eq("ID_Venta", id_vta_a_anular).single().execute()
        
        if not vta_check.data:
            return False, "❌ La venta no fue encontrada."
            
        if vta_check.data.get("Estado") == "Anulada":
            return False, "⚠️ Esta venta ya fue anulada previamente. No se realizaron cambios."
    
        # 1. MARCAR COMO ANULADA INMEDIATAMENTE
        # Se actualiza primero para bloquear segundas llamadas
        db.table("VENTAS_CABECERA").update({"Estado": "Anulada"}).eq("ID_Venta", id_vta_a_anular).execute()
    
        # 2. BUSCAR TURNO ABIERTO
        turno_res = db.table("CONTROL_TURNOS").select("ID_Turno").eq("Estado", "Abierto").maybe_single().execute()
        id_turno_actual = turno_res.data['ID_Turno'] if (turno_res and turno_res.data) else "SIN_TURNO"
        
        # 3. REVERSA DE PAGOS Y CAJA
        pagos_de_la_venta = db.table("VENTAS_PAGOS").select("*").eq("ID_Venta", id_vta_a_anular).execute().data or []
        
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
    
        # 4. DEVOLUCIÓN DE STOCK Y REGISTRO EN KARDEX (MOVIMIENTOS_STOCK)
        detalle_venta = db.table("VENTAS_DETALLE").select("*").eq("ID_Venta", id_vta_a_anular).execute().data or []
        
        for item in detalle_venta:
            id_prod = str(item['ID_Producto'])
            cant_vendida = int(item['Cantidad'])
            
            # Obtener stock actual y nombre del producto desde la BD
            prod_res = db.table("PRODUCTOS").select("Nombre", "Stock_Actual").eq("ID_Producto", id_prod).single().execute()
            
            if prod_res.data:
                nombre_prod = prod_res.data.get("Nombre", "Producto")
                stock_actual = int(prod_res.data.get('Stock_Actual', 0))
                stock_nuevo = stock_actual + cant_vendida
                
                # A. Actualizar el stock disponible en la tabla PRODUCTOS
                db.table("PRODUCTOS").update({"Stock_Actual": stock_nuevo}).eq("ID_Producto", id_prod).execute()
    
                # B. Insertar movimiento en MOVIMIENTOS_STOCK (Kardex)
                # La cantidad va positiva (+) porque reingresa mercadería
                db.table("MOVIMIENTOS_STOCK").insert({
                    "id_producto": id_prod,
                    "nombre_producto": nombre_prod,
                    "tipo_movimiento": "ANULACION_VENTA",
                    "cantidad": cant_vendida,
                    "stock_anterior": stock_actual,
                    "stock_nuevo": stock_nuevo,
                    "origen_referencia": f"Anulación Venta ID: {id_vta_a_anular}",
                    "usuario": str(usuario_actual),
                    "fecha": datetime.now().isoformat()
                }).execute()
    
        return True, "✅ Venta anulada, stock devuelto, caja ajustada y Kardex actualizado correctamente."        

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

    def calcular_y_actualizar_stock_automatico(ids_filtrados=None):
        """
        Recalcula Stock_Min y Stock_Max basado en las ventas de los últimos 60 días.
        Si recibe 'ids_filtrados', solo afecta a esa lista de productos.
        """
        try:
            # 1. Definir rango de fechas (60 días atrás)
            hace_60_dias = (datetime.now() - timedelta(days=60)).strftime('%Y-%m-%d')
    
            # 2. Traer ventas de los últimos 60 días
            ventas = db.table("VENTAS_DETALLE").select("ID_Producto, Cantidad, VENTAS_CABECERA(Fecha)").gte("VENTAS_CABECERA.Fecha", hace_60_dias).execute().data
            
            # 3. Si se pasaron IDs filtrados, filtramos la lista de productos a procesar
            if ids_filtrados is not None:
                # Nos aseguramos de tenerlos como texto/string para comparar bien
                lista_ids_target = [str(i) for i in ids_filtrados]
            else:
                # Si no hay filtro, traemos TODOS los productos activos/existentes de la base
                prods_base = db.table("PRODUCTOS").select("ID_Producto").execute().data
                lista_ids_target = [str(p['ID_Producto']) for p in prods_base]
    
            if not lista_ids_target:
                st.warning("No hay productos seleccionados para recalcular.")
                return False
    
            # 4. Mapear ventas por producto
            dict_ventas = {str(id_p): 0.0 for id_p in lista_ids_target}
    
            if ventas:
                df_ventas = pd.DataFrame(ventas)
                df_ventas['ID_Producto'] = df_ventas['ID_Producto'].astype(str)
                
                # Filtramos ventas solo de los productos objetivo
                df_ventas = df_ventas[df_ventas['ID_Producto'].isin(lista_ids_target)]
    
                if not df_ventas.empty:
                    rotacion = df_ventas.groupby('ID_Producto')['Cantidad'].sum().to_dict()
                    for id_p, cant in rotacion.items():
                        dict_ventas[id_p] = float(cant)
    
            # 5. Calcular y Actualizar en Supabase
            for id_prod, total_vendido in dict_ventas.items():
                promedio_diario = total_vendido / 60
                
                if total_vendido > 0:
                    stock_min = max(1, int(promedio_diario * 7))   # Mínimo para 7 días
                    stock_max = max(1, int(promedio_diario * 30))  # Máximo para 30 días
                else:
                    # Si no tuvo ventas en 60 días
                    stock_min = 1
                    stock_max = 0
    
                # Actualizar en Supabase
                db.table("PRODUCTOS").update({
                    "Stock_Min": stock_min,
                    "Stock_Max": stock_max
                }).eq("ID_Producto", id_prod).execute()
    
            return True
    
        except Exception as e:
            st.error(f"Error al recalcular stock automático: {e}")
            return False

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
            coordenadas = re.findall(r'@(-?\d+\.\d+),(-?\d+\.\d+)', url_final)
            
            if coordenadas:
                return float(coordenadas[0][0]), float(coordenadas[0][1])
        except:
            return None, None
        return None, None
    
    def calcular_distancia_haversine(p1, p2):
        """Calcula la distancia en km entre dos puntos (lat, lng)"""
        lat1, lon1 = p1
        lat2, lon2 = p2
        R = 6371.0  # Radio terrestre en km
        
        lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
        dlat = lat2 - lat1
        dlon = lon2 - lon1
        a = sin(dlat / 2)**2 + cos(lat1) * cos(lat2) * sin(dlon / 2)**2
        return R * (2 * asin(sqrt(a)))
    
    def optimizar_ruta(origen, destinos, destino_final=None):
        """
        Optimiza la ruta global usando Google OR-Tools entre un origen y un destino final.
        origen: tuple (lat, lng)
        destinos: lista de dicts [{'Cliente': '...', 'Latitud': x, 'Longitud': y}, ...]
        destino_final: tuple (lat, lng) opcional para el punto donde finaliza el recorrido.
        """
        if not destinos:
            return []
    
        def extraer_coords(p):
            if isinstance(p, (tuple, list)):
                return float(p[0]), float(p[1])
            elif isinstance(p, dict):
                return float(p.get('Latitud', 0)), float(p.get('Longitud', 0))
            return 0.0, 0.0
    
        lat_origen, lng_origen = extraer_coords(origen)
    
        # 1. Construir la lista de puntos
        # Puntos: [0: Origen] + [1..N: Entregas] (+ [N+1: Destino Final] si existe)
        tiene_destino_final = destino_final is not None
        
        puntos = [{'Latitud': lat_origen, 'Longitud': lng_origen}] + list(destinos)
        
        if tiene_destino_final:
            lat_fin, lng_fin = extraer_coords(destino_final)
            puntos.append({'Latitud': lat_fin, 'Longitud': lng_fin})
    
        n_puntos = len(puntos)
        idx_inicio = 0
        idx_fin = n_puntos - 1 if tiene_destino_final else None
    
        # 2. Construir la matriz de distancias
        matriz_distancias = np.zeros((n_puntos, n_puntos), dtype=int)
        for i in range(n_puntos):
            for j in range(n_puntos):
                if i != j:
                    p1 = (puntos[i]['Latitud'], puntos[i]['Longitud'])
                    p2 = (puntos[j]['Latitud'], puntos[j]['Longitud'])
                    dist_km = calcular_distancia_haversine(p1, p2)
                    matriz_distancias[i][j] = int(dist_km * 1000)
    
        # 3. Configurar OR-Tools
        if tiene_destino_final:
            # Asigna inicio en 0 y final en la última posición (destino_final)
            manager = pywrapcp.RoutingIndexManager(n_puntos, 1, [idx_inicio], [idx_fin])
        else:
            # Comienza en 0 y termina en cualquier último cliente
            manager = pywrapcp.RoutingIndexManager(n_puntos, 1, idx_inicio)
    
        routing = pywrapcp.RoutingModel(manager)
    
        def callback_distancia(from_index, to_index):
            from_node = manager.IndexToNode(from_index)
            to_node = manager.IndexToNode(to_index)
            return matriz_distancias[from_node][to_node]
    
        transit_callback_index = routing.RegisterTransitCallback(callback_distancia)
        routing.SetArcCostEvaluatorOfAllVehicles(transit_callback_index)
    
        # 4. Parámetros de búsqueda
        search_parameters = pywrapcp.DefaultRoutingSearchParameters()
        search_parameters.first_solution_strategy = (
            routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
        )
        search_parameters.local_search_metaheuristic = (
            routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
        )
        search_parameters.time_limit.seconds = 2
    
        # 5. Resolver y retornar orden
        solucion = routing.SolveWithParameters(search_parameters)
    
        ruta_ordenada = []
        if solucion:
            index = routing.Start(0)
            while not routing.IsEnd(index):
                node = manager.IndexToNode(index)
                # Filtramos el punto de origen (0) y el de destino final para retornar solo repartos
                if node != 0 and (not tiene_destino_final or node != idx_fin):
                    ruta_ordenada.append(destinos[node - 1])
                index = solucion.Value(routing.NextVar(index))
        else:
            ruta_ordenada = list(destinos)
    
        return ruta_ordenada
    
    def generar_diagrama_optimizada(grupo_repartos, punto_origen, fecha, punto_destino=None):
        repartos_validos = grupo_repartos.dropna(subset=['Latitud', 'Longitud'])
        
        # Pasamos el punto_destino a la optimización de OR-Tools
        ruta_optima = optimizar_ruta(
            origen=punto_origen, 
            destinos=repartos_validos.to_dict('records'), 
            destino_final=punto_destino
        )
        
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
        
        # Se corrige la iteración sobre ruta_reordenada
        for i, row in enumerate(ruta_reordenada, start=1):
            cliente = row['Cliente']
            total = row.get('Total', 0)
            metodo_pago = row.get('Metodo_Pago', '')
            
            # 1. Armamos la base de la línea
            linea = f"{i}. {cliente} ${total} {metodo_pago}"
            
            # 2. Extraemos y validamos si existen observaciones
            obs = row.get('Observaciones', '')
            if pd.notna(obs) and str(obs).strip() and str(obs).strip().lower() not in ["nan", "none"]:
                linea += f" *{str(obs).strip()}*"
                
            texto_whatsapp += linea + "\n"
        
        # Muestra el resultado listo para copiar
        st.text_area("📋 Copiar para WhatsApp:", value=texto_whatsapp, height=200)
    
    def extraer_coords_desde_link(link):
        # Busca el patrón @-XX.XXXX,-YY.YYYY en el link
        match = re.search(r'@(-?\d+\.\d+),(-?\d+\.\d+)', link)
        if match:
            return float(match.group(1)), float(match.group(2))
        return None # Si no encuentra nada

    # --- DIÁLOGO DE ALTA RÁPIDA ---
    @st.dialog("➕ Nuevo Cliente Rápido")
    def abrir_alta_cliente_rapida():
        with st.form("form_nuevo_cliente_rapido"):
            nombre = st.text_input("Nombre")
            apellido = st.text_input("Apellido")
            # 🔥 NUEVO CAMPO: Agregado para empresas en el diálogo rápido
            razon_social = st.text_input("Razón Social")
            telefono = st.text_input("Teléfono* (10 dígitos)", max_chars=10)
            dir1 = st.text_input("Dirección 1*")
            link1 = st.text_input("Link Dirección 1 (Google Maps)")
            zona = st.selectbox("Zona*", ["NORTE", "SUR", "CENTRO", "ESTE", "OESTE", "SANLO CHICO"])
            
            submitted = st.form_submit_button("Guardar Cliente")
            if submitted:
                # 🔥 VALIDACIÓN CONDICIONAL HOMOLOGADA: (Nombre y Apellido) O (Razón Social)
                tiene_datos_persona = bool(nombre and apellido)
                tiene_razon_social = bool(razon_social)
                
                if not (tiene_datos_persona or tiene_razon_social):
                    st.error("⚠️ Debes completar obligatoriamente el 'Nombre y Apellido' o la 'Razón Social'.")
                elif not all([telefono, dir1]):
                    st.error("⚠️ El 'Teléfono' y la 'Dirección 1' son campos obligatorios.")
                else:
                    try:
                        # VALIDACIÓN: Consulta directa a Supabase en tiempo real
                        existe_telefono = db.table("CLIENTES").select("ID_Cliente").eq("Telefono", str(telefono)).execute().data
                        
                        if existe_telefono:
                            st.error("⚠️ Ya existe un cliente con este teléfono!")
                        else:
                            # Asignamos dinámicamente el tipo comercial según los datos ingresados
                            tipo_detectado = "EMPRESA/ORGANISMO" if tiene_razon_social else "CONSUMIDOR FINAL"
                            
                            nuevo_cliente = {
                                "Nombre": nombre.upper() if nombre else "N/A", 
                                "Apellido": apellido.upper() if apellido else "N/A",
                                "Razón Social": razon_social.upper() if razon_social else "",
                                "Telefono": telefono, 
                                "Direccion_1": dir1.upper(),
                                "Link_Direccion_1": link1,
                                "Zona": zona, 
                                "Tipo_Cliente": tipo_detectado
                            }
                            
                            # 1. Insertamos el cliente y capturamos la respuesta
                            resultado = db.table("CLIENTES").insert(nuevo_cliente).execute()
                            
                            # 2. Obtenemos el ID generado
                            id_cliente_generado = "N/A"
                            if resultado.data:
                                id_cliente_generado = resultado.data[0].get('ID_Cliente', resultado.data[0].get('id', 'N/A'))
                            
                            # 3. Recuperamos el usuario logueado
                            usuario_logueado = st.session_state.get('usuario_actual', 'Desconocido')
                            
                            # 4. 🔥 LOG DE AUDITORÍA ACTUALIZADO
                            log_auditoria(
                                tabla="CLIENTES",
                                accion="INSERT",
                                id_entidad=id_cliente_generado,
                                detalles={
                                    "operacion": "Alta de Cliente Rápida",
                                    "datos_cliente": {
                                        "nombre_completo": f"{apellido.upper()}, {nombre.upper()}" if tiene_datos_persona else "N/A",
                                        "razon_social": razon_social.upper() if razon_social else "N/A",
                                        "telefono": telefono,
                                        "zona": zona,
                                        "tipo_cliente": tipo_detectado,
                                        "direccion_principal": dir1.upper()
                                    }
                                },
                                usuario=usuario_logueado
                            )
                            
                            st.success("✅ Cliente guardado!")
                            st.rerun()
                            
                    except Exception as e:
                        st.error(f"Error al procesar la solicitud: {e}")
                        
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

    @st.dialog("➕ Nuevo Proveedor Rápido")
    def abrir_alta_proveedor_rapida():
        # Consultamos la base de datos dentro del diálogo para tener datos frescos
        try:
            response = db.table("PROVEEDORES").select("*").execute()
            df_prov = pd.DataFrame(response.data)
        except:
            df_prov = pd.DataFrame()
        
        with st.form("form_nuevo_proveedor_rapido", clear_on_submit=True):
            # Calculamos ID sugerido basándonos en la consulta actual
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
                        st.success("✅ ¡Proveedor cargado exitosamente!")
                        st.rerun() # Esto cierra el diálogo y refresca todo
                    except Exception as e:
                        st.error(f"Error al guardar: {e}")

    def resetear_compras():
        # Lista de claves específicas del módulo COMPRAS
        keys_a_limpiar = [
            'carrito_compra', 'oc_en_edicion', 'temp_prov', 
            'temp_pago', 'temp_punto', 'temp_nro', 'prod_compra_key'
        ]
        for key in keys_a_limpiar:
            if key in st.session_state:
                del st.session_state[key]
        
        # Aseguramos estados básicos
        st.session_state.carrito_compra = []
        st.session_state.txt_barcode = ""
        st.rerun()

    def log_auditoria(tabla, accion, id_entidad, detalles, usuario="Martin"):
        """
        Registra automáticamente el movimiento en la tabla de Auditoría.
        """
        try:
            db.table("AUDITORIA").insert({
                "Tabla_Afectada": tabla,
                "Accion": accion,
                "ID_Entidad": str(id_entidad),
                "Detalles": detalles,
                "Usuario": usuario
            }).execute()
        except Exception as e:
            # Forzamos a que Streamlit nos muestre el error real en pantalla si llega a fallar
            st.error(f"🚨 Error crítico al guardar en auditoría: {e}")

    def actualizar_estados_productos(db):
        """
        Regla:
        - INACTIVO: Si Stock_Min es NULL / None / 0 Y Stock_Actual es 0.
        - ACTIVO: Cualquier otro caso (incluye productos 'clavo' con stock > 0).
        """
        try:
            # Traemos todos los productos con los nombres reales de tus columnas
            prods = db.table("PRODUCTOS").select("ID_Producto, Stock_Actual, Stock_Min, Estado").execute().data
            
            prods_a_inactivar = []
            prods_a_activar = []
            
            for p in prods:
                cod = p.get("ID_Producto")
                stock = int(p.get("Stock_Actual") or 0)
                stock_min = p.get("Stock_Min")
                estado_actual = p.get("Estado", "ACTIVO")
                
                # Verificamos si Stock_Min es nulo o 0
                sin_stock_min = (stock_min is None or str(stock_min).strip() in ["", "0", "None"])
                
                if sin_stock_min and stock == 0:
                    if estado_actual != "INACTIVO":
                        prods_a_inactivar.append(cod)
                else:
                    if estado_actual != "ACTIVO":
                        prods_a_activar.append(cod)
                        
            # Actualizaciones en la base de datos
            for cod in prods_a_inactivar:
                db.table("PRODUCTOS").update({"Estado": "INACTIVO"}).eq("ID_Producto", cod).execute()
                
            for cod in prods_a_activar:
                db.table("PRODUCTOS").update({"Estado": "ACTIVO"}).eq("ID_Producto", cod).execute()
                
            st.success(f"✅ Estados actualizados: {len(prods_a_inactivar)} inhabilitados, {len(prods_a_activar)} reactivados.")
            
            # Limpiamos caché de sesión para refrescar las listas
            if 'df_prod' in st.session_state:
                del st.session_state['df_prod']
                
        except Exception as e:
            st.error(f"Error al actualizar estados: {e}")

    @st.cache_data(ttl=600)
    def cargar_puntos_reparto():
        """
        Recupera los puntos de reparto guardados en Supabase.
        Retorna un diccionario: {"Nombre del punto": (lat, lng), ...}
        """
        try:
            data = db.table("puntos_reparto").select("*").execute().data
            puntos = {}
            for p in data:
                puntos[p['nombre']] = (float(p['latitud']), float(p['longitud']))
            return puntos
        except Exception as e:
            st.error(f"Error al cargar puntos de reparto desde la base de datos: {e}")
            return {}

    @st.dialog("➕ Alta Rápida de Producto", width="large")
    def modal_alta_rapida_producto():
        # Obtener opciones globales de forma segura (para no romper si no están definidas)
        rubros_opt = LISTA_RUBROS if 'LISTA_RUBROS' in globals() else ["General", "OTROS"]
        prov_opt = lista_proveedores if 'lista_proveedores' in globals() else ["Genérico"]
    
        with st.form("form_alta_producto_rapido", clear_on_submit=False):
            c_alta1, c_alta2 = st.columns(2)
            
            with c_alta1:
                id_nuevo = st.text_input("Código / ID Producto*", key="alta_rap_id").strip()
                nombre_nuevo = st.text_input("Descripción / Nombre*", key="alta_rap_nom").strip()
                marca_nueva = st.text_input("Marca", key="alta_rap_marca").strip()
                rubro_nuevo = st.selectbox("Rubro", options=rubros_opt, key="alta_rap_rubro")
                prov_seleccionado = st.selectbox("Proveedor", options=prov_opt, key="alta_rap_prov")
                
            with c_alta2:
                stock_ini = st.number_input("Stock Inicial", min_value=0, value=0, step=1, key="alta_rap_stock")
                costo_ini = st.number_input("Precio Costo ($)", min_value=0.0, value=0.0, step=10.0, key="alta_rap_costo")
                p1 = st.number_input("Precio Lista 1 ($)*", min_value=0.0, value=0.0, step=10.0, key="alta_rap_p1")
                p2 = st.number_input("Precio Lista 2 ($)", min_value=0.0, value=0.0, step=10.0, key="alta_rap_p2")
                p3 = st.number_input("Precio Lista 3 ($)", min_value=0.0, value=0.0, step=10.0, key="alta_rap_p3")
                p4 = st.number_input("Precio Lista 4 ($)", min_value=0.0, value=0.0, step=10.0, key="alta_rap_p4")
                p5 = st.number_input("Precio Lista 5 ($)", min_value=0.0, value=0.0, step=10.0, key="alta_rap_p5")
    
            st.caption("* Campos obligatorios")
            # El botón ahora refleja que se añadirá directamente a la compra
            btn_guardar = st.form_submit_button("💾 Guardar y Añadir a la Compra")
    
        if btn_guardar:
            if not id_nuevo or not nombre_nuevo or p1 <= 0:
                st.error("Por favor, completa los campos obligatorios (ID, Nombre y Precio 1 > 0).")
            else:
                nuevo_prod = {
                    "ID_Producto": id_nuevo,
                    "Nombre": nombre_nuevo,
                    "Rubro": rubro_nuevo if rubro_nuevo != "" else "OTROS",
                    "Marca": marca_nueva if marca_nueva != "" else None,
                    "Stock_Actual": int(stock_ini),
                    "Precio_Costo": float(costo_ini),
                    "Precio_1": float(p1),
                    "Precio_2": float(p2),
                    "Precio_3": float(p3),
                    "Precio_4": float(p4),
                    "Precio_5": float(p5),
                    "ID_Proveedor": prov_seleccionado if prov_seleccionado != "Genérico" else None,
                    "Stock_Min": 0,
                    "Stock_Max": 0,
                    "Imagen": None,
                    "Estado": "ACTIVO" # Aseguramos que entre activo
                }
                
                try:
                    # 1. Guardar en Base de Datos
                    db.table("PRODUCTOS").insert(nuevo_prod).execute()
                    
                    # 2. Invalidar caché para que el buscador global se actualice luego
                    if 'df_prod' in st.session_state: 
                        del st.session_state['df_prod']
                    
                    # 3. Auto-Añadirlo al carrito transformando el dict a una pd.Series 
                    # (Para que tu función _agregar_al_carrito lo lea sin problemas)
                    pm_nuevo = pd.Series(nuevo_prod)
                    _agregar_al_carrito(pm_nuevo)
                    
                    st.success(f"🎉 ¡Producto guardado y añadido a la compra!")
                    
                    # 4. Cerrar el modal y refrescar
                    st.rerun()
                except Exception as e:
                    st.error(f"Error técnico al guardar: {e}")
    
    # --- CONFIGURACIÓN ESTÉTICA ---
    st.set_page_config(page_title="Pañalera Moldes - ERP", layout="wide")

    LISTA_RUBROS = [
        "ACEITE", "ACONDICIONADOR", "ALGODON", 
        "APOSITOS", "BAÑO LIQUIDO", "CAMBIADOR", "CHUPETE", 
        "COLONIA", "CREMA", "CUCHARAS", "DESCONGESTIONADORES NASALES", 
        "ESPONJA", "HIGIENE BUCAL", "HISOPOS", "JABON", 
        "LECHE", "LIMPIEZA ROPA", "MAMADERA", "MOCHILA MATERNAL", "MORDILLOS", 
        "OLEO CALCAREO", "PAÑALES", "PAÑALES ADULTOS", "PLATOS", "PROTECTOR MAMARIO", 
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
                "⚙️ Configuración Pagos", "📦 Productos",
                "📦 Stock", "🚚 Proveedores", "📦 Compras", "👥 Vendedores", 
                "⚙️ Auditoría", "📈 Reporte de Utilidades", "🚚 Gestión de Repartos", "📊 Reportes" # <--- AQUÍ LO AGREGAMOS
            ])
        elif st.session_state.rol == "Vendedor":
            opciones_disponibles.extend(["🛒 Punto de Venta", "🚚 Gestión de Repartos", "📦 Productos", "👥 Clientes"])
        
        menu = st.selectbox("Menú Principal", opciones_disponibles)
        
        st.divider()
        if st.button("🚪 Cerrar Sesión"):
            st.session_state.logeado = False
            st.rerun()

    # --- LÓGICA DE MÓDULOS ---

    # =====================================================================
    # MODULO: 💰 CAJA
    # =====================================================================
    if menu == "💰 Caja":
        st.title("💰 Gestión de Caja")
        
        # 1. Obtenemos estado
        turno_actual = obtener_turno_activo() 
        
        # Reducimos los tabs únicamente a las dos vistas útiles
        tab_turno, tab_explorar = st.tabs(["🕒 Turno Actual", "🔍 Explorador"])
        
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
                # 1. Agregamos order("Fecha", desc=True) para traerlos ordenados desde la base de datos
                res_caja = db.table("CAJA").select("*").order("Fecha", desc=True).execute()
                df_caja = pd.DataFrame(res_caja.data)
            except Exception:
                df_caja = pd.DataFrame()

            fecha_sel = st.date_input("Consultar fecha", datetime.now())
            
            if not df_caja.empty:
                df_caja['Fecha'] = pd.to_datetime(df_caja['Fecha'])
                # 2. Filtración y ordenamiento explícito (de más reciente a más antiguo)
                df_filtrado = df_caja[df_caja['Fecha'].dt.date == fecha_sel].sort_values(by="Fecha", ascending=False)
            else:
                df_filtrado = pd.DataFrame() # Tabla vacía si no hay datos

            # --- MOSTRAR MÉTRICAS (SOLO SALDO) ---
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
                    width='stretch', 
                    hide_index=True  # Esto oculta el número de fila a la izquierda
                )
            else:
                st.info("No hay movimientos registrados para la fecha seleccionada.")

            # --- REGISTRO MANUAL (Fuera del if para que siempre se vea) ---
            with st.expander("➕ Registrar Movimiento Manual"):
                conceptos_data = db.table("LISTA_CONCEPTOS").select("CONCEPTO").execute().data
                lista_c = [c['CONCEPTO'] for c in conceptos_data]
                
                with st.form("nuevo_movimiento", clear_on_submit=True):
                    concepto = st.selectbox("Concepto", lista_c)
                    tipo = st.radio("Tipo", ["Ingreso", "Egreso"])
                    importe = st.number_input("Importe", min_value=0.0)
                    forma_pago = st.selectbox("Forma de Pago", ["Efectivo", "Crédito", "Débito", "Transferencia"])
                    
                    if st.form_submit_button("Guardar"):
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
    # MODULO: 🛒 PUNTO DE VENTA
    # =====================================================================
    elif menu == "🛒 Punto de Venta":
        col_t1, col_t2 = st.columns([4, 1])
        col_t1.header("🚀 Venta Rápida - Pañalera Moldes")
        if col_t2.button("🧹 Limpiar Todo", type="secondary", width='stretch'):
            resetear_punto_venta()
        # 1.5. BOTÓN PARA VER PENDIENTES (Visual Horizontal Expandida y Ordenada Alfabéticamente)
        @st.dialog("Ventas Pendientes", width="large")
        def abrir_pendientes():
            import json
            import re
        
            def limpiar_monto(texto):
                """Extrae números de un string como 'Transferencia: $59,600'"""
                solo_numeros = re.sub(r"[^\d.]", "", texto.replace(",", ""))
                try:
                    return float(solo_numeros)
                except:
                    return 0.0
        
            try:
                # Traemos los vendedores para mapear el ID al Nombre en la visual
                vendedores_dict = {}
                if "df_vend" in locals() and not df_vend.empty:
                    vendedores_dict = dict(
                        zip(
                            df_vend["ID_Vendedor"].astype(str),
                            df_vend["Nombre"],
                        )
                    )
        
                # 1. Consultamos los datos crudos de Supabase
                datos_raw = db.table("VENTAS_PENDIENTES").select("*").execute().data
        
                if not datos_raw:
                    st.info("📭 No hay ventas pendientes registradas.")
                else:
                    # Ordenamos la lista alfabéticamente por el nombre del Cliente
                    pendientes = sorted(
                        datos_raw,
                        key=lambda x: str(x.get("Cliente", "")).strip().lower(),
                    )
        
                    st.markdown("### Listado de Espera (Ordenado por Cliente)")
        
                    for v in pendientes:
                        # Contenedor que aprovecha el ancho completo del diálogo grande
                        with st.container(border=True):
                            # Dividimos en 4 columnas horizontales
                            c_id, c_cliente, c_monto, c_acciones = st.columns(
                                [1.2, 2.0, 1.2, 1.2], vertical_alignment="center"
                            )
        
                            with c_id:
                                id_corto = v["ID_Pendiente"].replace("PEND-", "")
                                st.markdown(
                                    f"🆔 **#{id_corto}**\n\n📅 {v['Fecha']}"
                                )
        
                            with c_cliente:
                                id_vendedor_str = str(v.get("Vendedor", "1"))
                                nombre_vendedor = vendedores_dict.get(
                                    id_vendedor_str, f"Vendedor {id_vendedor_str}"
                                )
                                st.markdown(
                                    f"👤 **{v['Cliente']}**\n\n👔 {nombre_vendedor}"
                                )
        
                            with c_monto:
                                monto_numerico = limpiar_monto(
                                    v.get("Metodo_Pago", "0")
                                )
                                st.markdown(
                                    f"💰 **Total:**\n\n### ${monto_numerico:,.0f}"
                                )
        
                            with c_acciones:
                                btn_col1, btn_col2 = st.columns(2, gap="small")
        
                                with btn_col1:
                                    if st.button(
                                        "📥 Cargar",
                                        key=f"recup_{v['ID_Pendiente']}",
                                        use_container_width=True,
                                        type="primary",
                                    ):
                                        st.session_state.id_pendiente_cargado = v[
                                            "ID_Pendiente"
                                        ]
                                        st.session_state.carrito_vta = json.loads(
                                            v["Detalle_JSON"]
                                        )
                                        st.session_state.pagos_split = json.loads(
                                            v.get(
                                                "Pagos_JSON",
                                                '[{"metodo": "Efectivo", "monto": 0.0}]',
                                            )
                                        )
                                        st.session_state.cliente_recuperado = v[
                                            "Cliente"
                                        ]
                                        st.session_state.id_cliente_recuperado = (
                                            v.get("ID_Cliente_Pendiente", "0")
                                        )
        
                                        st.session_state.vendedor_recuperado = (
                                            id_vendedor_str
                                        )
        
                                        st.session_state.tipo_entrega = v.get(
                                            "Forma_Entrega", "Mostrador"
                                        )
                                        st.session_state.direccion_entrega = v.get(
                                            "Direccion_Entrega", "N/A"
                                        )
                                        st.session_state.link_maps_entrega = v.get(
                                            "Link_Maps_Entrega", "N/A"
                                        )
                                        st.session_state.fecha_reparto = v.get(
                                            "Fecha_Entrega",
                                            str(datetime.today().date()),
                                        )
                                        st.session_state.observaciones_entrega = v.get(
                                            "Observaciones", ""
                                        )
                                        st.rerun()
        
                                with btn_col2:
                                    if st.button(
                                        "🗑️",
                                        key=f"del_{v['ID_Pendiente']}",
                                        use_container_width=True,
                                        help="Eliminar de pendientes",
                                    ):
                                        db.table("VENTAS_PENDIENTES").delete().eq(
                                            "ID_Pendiente", v["ID_Pendiente"]
                                        ).execute()
                                        st.rerun()
        
            except Exception as e:
                st.error(f"Error al leer pendientes: {e}")
        
        
        # --- 🔥 BOTÓN PRINCIPAL CON CONTADOR DENTRO DEL PARÉNTESIS ---
        cant_pendientes = 0
        try:
            # Consulta ultra liviana a Supabase solo trayendo la columna ID para contar
            res_cant = db.table("VENTAS_PENDIENTES").select("ID_Pendiente").execute()
            if res_cant and res_cant.data:
                cant_pendientes = len(res_cant.data)
        except Exception:
            cant_pendientes = 0
        
        # Construimos el label con el formato exacto solicitado
        label_boton = f"📂 VER PENDIENTES ({cant_pendientes})"
        
        if st.button(label_boton, width="stretch"):
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
            # Cambiamos a 3 columnas: c1 (Cliente), c2 (+), c3 (Vendedor)
            c1, c2, c3 = st.columns([3, 0.5, 1.5])
        
            # --- HELPER AUXILIAR DE LIMPIEZA ---
            def limpiar_val(val):
                if (
                    pd.isna(val)
                    or val is None
                    or str(val).strip().upper() in ["NAN", "NONE", "NULL"]
                ):
                    return ""
                return str(val).strip()
        
            # --- 1. CREAR LA COLUMNA DISPLAY (CON RAZÓN SOCIAL INTEGRADA Y LIMPIA) ---
            def obtener_display_cliente(row):
                razon = limpiar_val(row.get("Razón Social"))
                nombre = limpiar_val(row.get("Nombre"))
                apellido = limpiar_val(row.get("Apellido"))
                tel = limpiar_val(row.get("Telefono"))
                id_c = row.get("ID_Cliente")
        
                # Formatear teléfono si existe
                txt_tel = f" ({tel})" if tel else ""
        
                # Si tiene Razón Social válida
                if razon:
                    nombre_completo = f"{nombre} {apellido}".strip()
                    if nombre_completo:
                        return f"{razon.upper()} | {nombre_completo.upper()}{txt_tel} - ID: {id_c}"
                    else:
                        return f"{razon.upper()}{txt_tel} - ID: {id_c}"
                else:
                    # Si no hay razón social (Persona / Consumidor Final)
                    nombre_completo = f"{nombre} {apellido}".strip()
                    if not nombre_completo:
                        nombre_completo = "SIN NOMBRE"
                    return f"{nombre_completo.upper()}{txt_tel} - ID: {id_c}"
        
            df_clie["Display"] = df_clie.apply(obtener_display_cliente, axis=1)
        
            # --- 2. AHORA SÍ: LÓGICA DE PERSISTENCIA ---
            valor_inicial = None
            if "id_cliente_recuperado" in st.session_state:
                candidatos = df_clie[
                    df_clie["ID_Cliente"].astype(str)
                    == str(st.session_state.id_cliente_recuperado)
                ]
                if not candidatos.empty:
                    valor_inicial = candidatos.iloc[0]["Display"]
        
            # --- 3. SELECTOR DE CLIENTE ---
            cliente_display = c1.selectbox(
                "👤 Buscar Cliente (Nombre, Apellido, Teléfono o Razón Social)",
                options=df_clie["Display"].tolist(),
                index=(
                    df_clie["Display"].tolist().index(valor_inicial)
                    if valor_inicial and valor_inicial in df_clie["Display"].tolist()
                    else None
                ),
                placeholder="Seleccione o busque un cliente...",
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
        
            # --- LÓGICA DE ASIGNACIÓN Y NOMBRE EN TICKET ---
            cliente_sel_row = None
            if cliente_display:
                cliente_sel_row = df_clie[df_clie["Display"] == cliente_display].iloc[0]
        
                razon_sel = limpiar_val(cliente_sel_row.get("Razón Social"))
                nombre_sel = limpiar_val(cliente_sel_row.get("Nombre"))
                apellido_sel = limpiar_val(cliente_sel_row.get("Apellido"))
        
                # 🔥 MEJORA DE FLUJO: Nombre comercial prioritario si existe Razón Social, sino Nombre + Apellido
                if razon_sel:
                    cliente_nombre_final = razon_sel.upper()
                else:
                    cliente_nombre_final = (
                        f"{nombre_sel} {apellido_sel}".strip().upper()
                    )
                    if not cliente_nombre_final:
                        cliente_nombre_final = "CONSUMIDOR FINAL"
        
                id_cliente_final = str(cliente_sel_row["ID_Cliente"])
                st.session_state.id_cliente_recuperado = id_cliente_final
            else:
                id_cliente_final = "0"
                cliente_nombre_final = "Consumidor Final"
                if "id_cliente_recuperado" in st.session_state:
                    del st.session_state.id_cliente_recuperado
        
            # --- 🔥 MAPEO DINÁMICO DE VENDEDOR EN C3 (MOVIDO DESDE C4) ---
            vendedor_id_final = "1"  # Fallback por defecto si no hay datos
            if "df_vend" in locals() and not df_vend.empty:
                # Creamos un diccionario {ID_Vendedor: "Nombre Apellido"}
                dict_vendedores = {
                    str(row["ID_Vendedor"]): f"{row['Nombre']} {row['Apellido']}"
                    for _, row in df_vend.iterrows()
                }
        
                lista_opciones = list(dict_vendedores.keys())
        
                # 🔥 Lógica para pre-seleccionar el vendedor recuperado
                idx_vendedor = 0
                if "vendedor_recuperado" in st.session_state:
                    id_recup = st.session_state.vendedor_recuperado
                    if id_recup in lista_opciones:
                        idx_vendedor = lista_opciones.index(id_recup)
                    # Lo eliminamos para que afecte solo a esta carga y no quede fijo en las próximas ventas
                    del st.session_state.vendedor_recuperado
        
                # El selectbox opera sobre los IDs (claves) pero muestra los nombres legibles
                vendedor_id_sel = c3.selectbox(
                    "👔 Vendedor",
                    options=lista_opciones,
                    format_func=lambda x: dict_vendedores[x],
                    index=idx_vendedor,  # <-- Forzamos el índice recuperado
                    key="pos_vendedor_selector_dinamico",
                )
                if vendedor_id_sel:
                    vendedor_id_final = str(vendedor_id_sel)
            else:
                # Fallback visual clásico si df_vend viniera vacío
                c3.selectbox(
                    "👔 Vendedor",
                    options=["1"],
                    format_func=lambda x: "Vendedor Genérico",
                )
        
        
        # 3. BUSCADOR DE PRODUCTOS
        st.divider()
        st.subheader("🔍 Añadir Productos")
        
        # --- FILTRO DE DISPONIBILIDAD ---
        # Filtramos: (Tiene stock positivo) O (Es un concepto financiero / No stockeable)
        df_disponible = df_prod[
            (df_prod["Stock_Actual"] > 0) | (df_prod["Es_Stockeable"] == False)
        ].copy()
        
        # Creamos la lista formateada con los resultados del filtro
        opciones_productos = (
            df_disponible["Nombre"] + " - " + df_disponible["ID_Producto"].astype(str)
        ).tolist()
        
        col_bus1, col_bus2 = st.columns([2, 1])
        
        col_bus1.selectbox(
            "Buscar por nombre o código",
            options=opciones_productos,
            index=None,
            placeholder="Escriba para buscar producto...",
            key="prod_manual_key",
            on_change=procesar_seleccion_manual,
        )
        
        # Aviso: Solo advertimos si un producto FÍSICO no tiene stock.
        if "prod_manual_key" in st.session_state and st.session_state.prod_manual_key:
            busqueda = st.session_state.prod_manual_key
            id_buscado = busqueda.split(" - ")[-1]
            prod_buscado = df_prod[
                df_prod["ID_Producto"].astype(str) == id_buscado
            ].iloc[0]
        
            if (
                prod_buscado["Stock_Actual"] <= 0
                and prod_buscado["Es_Stockeable"] == True
            ):
                st.warning(
                    f"⚠️ El producto '{prod_buscado['Nombre']}' no se puede agregar porque no cuenta con stock."
                )
        
        
        # 4. CARRITO (Versión sin Selector Global)
        if st.session_state.carrito_vta:
            st.write("### 🛒 Detalle de la Venta")
        
            for i, item in enumerate(st.session_state.carrito_vta):
                res_p = df_prod[df_prod["ID_Producto"].astype(str) == str(item["id"])]
                if res_p.empty:
                    continue
                p_data = res_p.iloc[0]
        
                c1, c2, c3, c4, c5, c6 = st.columns([2, 1.2, 0.8, 1.2, 1, 0.5])
        
                # Nombre y Código del producto
                with c1:
                    st.markdown(f"#### {p_data['Nombre']}")
                    st.markdown(
                        f"<p style='font-size:14px;'><strong>{p_data['ID_Producto']}</strong></p>",
                        unsafe_allow_html=True,
                    )
        
                # 1. Selector de Lista (Por defecto: "Automática (P1/P2)")
                lista_actual_producto = item.get("lista_local", "Automática (P1/P2)")
                opciones_lista = [
                    "Automática (P1/P2)",
                    "Lista 1",
                    "Lista 2",
                    "Lista 3",
                    "Lista 4",
                    "Lista 5",
                ]
        
                lista_item = c2.selectbox(
                    "Lista",
                    opciones_lista,
                    index=opciones_lista.index(lista_actual_producto),
                    key=f"L_{i}",
                )
        
                if lista_item != lista_actual_producto:
                    item["lista_local"] = lista_item
                    st.rerun()
        
                # 2. Cantidad
                n_cant = c3.number_input(
                    "Cant.", min_value=1, value=int(item["cantidad"]), key=f"Q_{i}"
                )
        
                # 3. Calcular el precio SUGERIDO
                if lista_item == "Automática (P1/P2)":
                    if n_cant == 1:
                        col_p = "Precio_1"
                    elif n_cant == 2:
                        col_p = "Precio_2"
                    else:
                        col_p = "Precio_3"
                else:
                    col_p = lista_item.replace("Lista ", "Precio_")
        
                precio_sugerido = float(p_data[col_p])
        
                # 4. Input Precio
                n_prec = c4.number_input(
                    "Precio",
                    value=precio_sugerido,
                    key=f"P_{i}_{lista_item}_{n_cant}_{precio_sugerido}",
                    format="%.2f",
                )
        
                # 5. Actualización
                sub = n_cant * n_prec
                st.session_state.carrito_vta[i].update(
                    {"cantidad": n_cant, "precio": n_prec, "subtotal": sub}
                )
        
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
            obs_recuperada = st.session_state.get("observaciones_entrega", "") # 👈 1. RECUPERAMOS NOTA DE SESSION_STATE

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

            # 👈 2. NUEVO CAMPO VISUAL DE OBSERVACIONES
            observaciones_vta = st.text_input(
                "📝 Observaciones / Notas para el Repartidor", 
                value=obs_recuperada,
                placeholder="Ej: Pasar antes de las 16hs, llamar al timbre 2B, cobro exacto..."
            )

            # Actualizamos el estado
            st.session_state.tipo_entrega = tipo_entrega
            st.session_state.fecha_reparto = str(fecha_reparto)
            st.session_state.direccion_entrega = direccion_elegida
            st.session_state.link_maps_entrega = link_elegido
            st.session_state.observaciones_entrega = observaciones_vta # 👈 3. GUARDAMOS EN SESSION_STATE

        # --- 6. BOTONES DE CIERRE (Solo visibles si hay productos en el carrito) ---
        if st.session_state.carrito_vta:
            st.divider()
            col_f1, col_f2 = st.columns(2)
        
            with col_f1:
                if st.button("🏁 FINALIZAR Y REGISTRAR VENTA", width='stretch', type="primary"):
                    # 0. Verificación de sumas de pago
                    suma_pagos = sum(float(p["monto"]) for p in st.session_state.pagos_split)
                    if abs(suma_pagos - total_final_vta) > 0.01:
                        st.error(f"¡Error! La suma de los pagos (${suma_pagos:.2f}) no coincide con el total (${total_final_vta:.2f})")
                    else:
                        # ---------------------------------------------------------
                        # 🔥 VALIDACIÓN DE STOCK REAL EN BASE DE DATOS
                        # ---------------------------------------------------------
                        hay_error_stock = False
                        for item in st.session_state.carrito_vta:
                            p_check = db.table("PRODUCTOS").select("Nombre", "Stock_Actual", "Es_Stockeable").eq("ID_Producto", str(item["id"])).single().execute()
                            if p_check.data:
                                es_stockeable = p_check.data.get("Es_Stockeable", True)
                                stock_actual = float(p_check.data.get("Stock_Actual", 0))
                                cant_solicitada = float(item["cantidad"])
        
                                if es_stockeable and cant_solicitada > stock_actual:
                                    nombre_prod = p_check.data.get("Nombre", "Desconocido")
                                    st.error(f'El artículo "{nombre_prod}" no posee esa cantidad para facturar, revisar código y stock')
                                    hay_error_stock = True
                                    break
        
                        # --- BLINDAJE DE SEGURIDAD PARA GIFT CARDS ---
                        hay_error_gc = False
                        if not hay_error_stock:
                            for pago in st.session_state.pagos_split:
                                if "Gift Card" in pago["metodo"]:
                                    gc_check = db.table("GIFT_CARDS") \
                                                 .select("Saldo_Actual") \
                                                 .eq("ID_GiftCard", st.session_state.get('gc_activa_id')) \
                                                 .single().execute()
                                    
                                    saldo_real = float(gc_check.data['Saldo_Actual']) if (gc_check and gc_check.data) else 0.0
                                    
                                    if pago["monto"] > saldo_real:
                                        st.error(f"❌ ¡Saldo insuficiente en Gift Card! Disponible: ${saldo_real:,.2f}")
                                        hay_error_gc = True
                                        break
        
                        # PROCESAR VENTA SOLO SI NO HAY ERRORES
                        if not hay_error_stock and not hay_error_gc:
                            try:
                                # 1. DEFINIR DATOS BÁSICOS
                                id_v = datetime.now().strftime("%Y%m%d%H%M%S")
                                f = datetime.now().strftime("%Y-%m-%d")
                                
                                # --- OBTENER TURNO Y NOMBRE DE USUARIO ---
                                turno_res = db.table("CONTROL_TURNOS").select("ID_Turno").eq("Estado", "Abierto").maybe_single().execute()
                                id_turno_val = turno_res.data['ID_Turno'] if (turno_res and turno_res.data) else "SIN_TURNO"
        
                                nombre_usuario_actual = st.session_state.get('usuario_nombre')
                                if not nombre_usuario_actual:
                                    u_res = db.table("USUARIOS").select("Nombre").eq("ID_Usuario", vendedor_id_final).single().execute()
                                    nombre_usuario_actual = u_res.data.get('Nombre') if (u_res and u_res.data) else str(vendedor_id_final)
        
                                # 2. Registrar Cabecera
                                desglose_pagos = " | ".join([f"{p['metodo']}: ${p['monto']:,.0f}" for p in st.session_state.pagos_split])
                                db.table("VENTAS_CABECERA").insert({
                                    "ID_Venta": id_v,
                                    "Fecha": f,
                                    "ID_Cliente": id_cliente_final,
                                    "ID_Vendedor": vendedor_id_final, 
                                    "Forma_Pago": desglose_pagos,
                                    "Total": total_final_vta,
                                    "Forma_Entrega": st.session_state.tipo_entrega,
                                    "Direccion_Entrega": st.session_state.direccion_entrega if st.session_state.tipo_entrega == "Reparto" else "N/A",
                                    "Observaciones": st.session_state.get('observaciones_entrega', '')
                                }).execute()
                                
                                # 3. Registrar Detalle y Actualizar Stock (Solo si es_stockeable)
                                for art in st.session_state.carrito_vta:
                                    prod_data = db.table("PRODUCTOS").select("Precio_Costo", "Nombre", "Stock_Actual", "Es_Stockeable").eq("ID_Producto", str(art['id'])).single().execute()
                                    
                                    costo_historico = prod_data.data.get('Precio_Costo', 0) if prod_data.data else 0
                                    nombre_prod = art.get('nombre') or (prod_data.data.get('Nombre') if prod_data.data else 'Artículo')
                                    es_stockeable = prod_data.data.get('Es_Stockeable', True) if prod_data.data else True
        
                                    db.table("VENTAS_DETALLE").insert({
                                        "ID_Venta": id_v,
                                        "ID_Producto": str(art['id']),
                                        "Cantidad": int(art['cantidad']),
                                        "Precio_Unitario": float(art['precio']),
                                        "Precio_Costo_Unitario": float(costo_historico),
                                        "Subtotal": float(art['subtotal'])
                                    }).execute()
                                    
                                    # Descontar stock SOLO si es producto stockeable
                                    if es_stockeable and prod_data.data:
                                        stock_actual = int(prod_data.data.get('Stock_Actual', 0))
                                        cantidad_vendida = int(art['cantidad'])
                                        stock_nuevo = stock_actual - cantidad_vendida
        
                                        db.table("PRODUCTOS").update({"Stock_Actual": stock_nuevo}).eq("ID_Producto", str(art['id'])).execute()
        
                                        db.table("MOVIMIENTOS_STOCK").insert({
                                            "id_producto": str(art['id']),
                                            "nombre_producto": nombre_prod,
                                            "tipo_movimiento": "VENTA",
                                            "cantidad": -cantidad_vendida,
                                            "stock_anterior": stock_actual,
                                            "stock_nuevo": stock_nuevo,
                                            "origen_referencia": f"Venta ID: {id_v}",
                                            "usuario": str(nombre_usuario_actual)
                                        }).execute()
        
                                # 4. Registrar Pagos en VENTAS_PAGOS
                                for pago in st.session_state.pagos_split:
                                    db.table("VENTAS_PAGOS").insert({
                                        "ID_Venta": id_v,
                                        "Metodo_Pago": pago["metodo"],
                                        "Monto": float(pago["monto"])
                                    }).execute()
        
                                # 5. Registrar en Caja y procesar Gift Cards
                                for pago in st.session_state.pagos_split:
                                    metodo = pago["metodo"]
                                    monto = float(pago["monto"])
                                    
                                    db.table("CAJA").insert({
                                        "ID_Turno": id_turno_val,
                                        "Fecha": datetime.now().isoformat(),
                                        "Tipo": "Ingreso",
                                        "Concepto": f"Venta {id_v} ({metodo})",
                                        "Monto": monto,
                                        "Forma_Pago": metodo
                                    }).execute()
        
                                    es_efectivo_reparto = (metodo == "Efectivo" and st.session_state.tipo_entrega == "Reparto")
                                    es_otro_metodo = (metodo != "Efectivo")
                                    
                                    if es_efectivo_reparto or es_otro_metodo:
                                        if "Gift Card" in metodo:
                                            gc_id = st.session_state.get('gc_activa_id')
                                            gc_curr = db.table("GIFT_CARDS").select("Saldo_Actual").eq("ID_GiftCard", gc_id).single().execute()
                                            saldo_base = float(gc_curr.data['Saldo_Actual']) if (gc_curr and gc_curr.data) else 0.0
                                            nuevo_saldo = saldo_base - monto
                                            
                                            db.table("GIFT_CARDS").update({"Saldo_Actual": float(nuevo_saldo)}).eq("ID_GiftCard", gc_id).execute()
                                            if nuevo_saldo <= 0:
                                                db.table("GIFT_CARDS").update({"Estado": False}).eq("ID_GiftCard", gc_id).execute()
        
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
                                st.session_state.observaciones_entrega = ""
                                st.rerun()
        
                            except Exception as e:
                                st.error(f"Error al registrar: {e}")
        
            with col_f2:
                if st.button("⏳ GUARDAR COMO PENDIENTE", width='stretch'):
                    import json
                    import re
                    
                    try:
                        lat, lng = None, None
                        link = st.session_state.link_maps_entrega
                        if link:
                            coords = re.findall(r'@(-?\d+\.\d+),(-?\d+\.\d+)', link)
                            if coords:
                                lat, lng = float(coords[0][0]), float(coords[0][1])
        
                        desglose_pagos = " | ".join([f"{p['metodo']}: ${p['monto']:,.0f}" for p in st.session_state.pagos_split])
        
                        data_to_save = {
                            "Fecha": datetime.now().strftime('%Y-%m-%d'),
                            "Hora": datetime.now().strftime('%H:%M:%S'),
                            "Cliente": cliente_nombre_final,
                            "ID_Cliente_Pendiente": id_cliente_final,
                            "Vendedor": vendedor_id_final,
                            "Metodo_Pago": desglose_pagos,
                            "Pagos_JSON": json.dumps(st.session_state.pagos_split),
                            "Detalle_JSON": json.dumps(st.session_state.carrito_vta),
                            "Forma_Entrega": st.session_state.tipo_entrega,
                            "Direccion_Entrega": st.session_state.direccion_entrega,
                            "Link_Maps_Entrega": link,
                            "Fecha_Entrega": st.session_state.fecha_reparto,
                            "Observaciones": st.session_state.get('observaciones_entrega', ''),
                            "Latitud": lat,
                            "Longitud": lng
                        }
        
                        if 'id_pendiente_cargado' in st.session_state and st.session_state.id_pendiente_cargado:
                            db.table("VENTAS_PENDIENTES") \
                              .update(data_to_save) \
                              .eq("ID_Pendiente", st.session_state.id_pendiente_cargado) \
                              .execute()
                            st.toast("Venta pendiente actualizada", icon="🔄")
                        else:
                            data_to_save["ID_Pendiente"] = f"PEND-{datetime.now().strftime('%Y%m%d%H%M%S')}"
                            db.table("VENTAS_PENDIENTES").insert(data_to_save).execute()
                            st.toast("Venta guardada como nuevo pendiente", icon="⏳")
        
                        st.session_state.carrito_vta = []
                        st.session_state.pagos_split = [{"metodo": "Efectivo", "monto": 0.0}]
                        st.session_state.observaciones_entrega = ""
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
    # MODULO: 👥 CLIENTES
    # =====================================================================
    elif menu == "👥 Clientes":
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
                    st.dataframe(df_clientes[mask], width='stretch')
                else:
                    st.dataframe(df_clientes, width='stretch')
            
        with tab_nuevo:
            with st.form("form_nuevo_cliente"):
                c1, c2 = st.columns(2)
                with c1:
                    nombre = st.text_input("Nombre*")
                    apellido = st.text_input("Apellido*")
                    dni = st.text_input("DNI", max_chars=8)
                    cuit = st.text_input("CUIT", max_chars=13)
                    # 🔥 NUEVO CAMPO: Agregado directamente al formulario de alta
                    razon_social = st.text_input("Razón Social")
                    telefono = st.text_input("Teléfono* (10 dígitos)", max_chars=10)
                with c2:
                    dir1 = st.text_input("Dirección 1*")
                    link1 = st.text_input("Link Dirección 1")
                    dir2 = st.text_input("Dirección 2")
                    link2 = st.text_input("Link Dirección 2")
                    dir3 = st.text_input("Dirección 3")
                    link3 = st.text_input("Link Dirección 3")
                    zona = st.selectbox("Zona*", ["NORTE", "SUR", "CENTRO", "ESTE", "OESTE", "SANLO CHICO"])
                    tipo = st.selectbox("Tipo Cliente", ["CONSUMIDOR FINAL", "MAYORISTA", "EMPRESA/ORGANISMO"])
                
                submitted = st.form_submit_button("Guardar Cliente")
                
                if submitted:
                    # 🔥 NUEVA VALIDACIÓN CONDICIONAL: (Nombre y Apellido) O (Razón Social)
                    tiene_datos_persona = bool(nombre and apellido)
                    tiene_razon_social = bool(razon_social)
                    
                    if not (tiene_datos_persona or tiene_razon_social):
                        st.error("⚠️ Debes completar obligatoriamente el 'Nombre y Apellido' o la 'Razón Social'.")
                    elif not all([telefono, dir1]):
                        st.error("⚠️ El 'Teléfono' y la 'Dirección 1' son campos obligatorios para cualquier cliente.")
                    elif telefono in df_clientes['Telefono'].astype(str).values:
                        st.error("⚠️ Ya existe un cliente con este teléfono!")
                    else:
                        # Proceder al guardado en Supabase...
                        nuevo_cliente = {
                            "Nombre": nombre.upper() if nombre else "N/A", # Si es empresa, guardamos N/A de forma limpia
                            "Apellido": apellido.upper() if apellido else "N/A",
                            "DNI": dni,
                            "CUIT": cuit, 
                            "Razón Social": razon_social.upper() if razon_social else "",
                            "Telefono": telefono, 
                            "Direccion_1": dir1.upper(),
                            "Direccion_2": dir2.upper(), 
                            "Direccion_3": dir3.upper(),
                            "Link_Direccion_1": link1, 
                            "Link_Direccion_2": link2,
                            "Link_Direccion_3": link3, 
                            "Zona": zona, 
                            "Tipo_Cliente": tipo
                        }
                        
                        try:
                            # 1. Insertamos el cliente y capturamos la respuesta de Supabase
                            resultado = db.table("CLIENTES").insert(nuevo_cliente).execute()
                            
                            id_cliente_generado = "N/A"
                            if resultado.data:
                                id_cliente_generado = resultado.data[0].get('ID_Cliente', resultado.data[0].get('id', 'N/A'))
                            
                            # 2. Recuperamos el usuario logueado
                            usuario_logueado = st.session_state.get('usuario_actual', 'Desconocido')
                            
                            # 3. 🔥 LOG DE AUDITORÍA (Alta de Cliente con Razón Social)
                            log_auditoria(
                                tabla="CLIENTES",
                                accion="INSERT",
                                id_entidad=id_cliente_generado,
                                detalles={
                                    "operacion": "Alta de Cliente",
                                    "datos_cliente": {
                                        "nombre_completo": f"{apellido.upper()}, {nombre.upper()}",
                                        "razon_social": razon_social.upper() if razon_social else "N/A",
                                        "telefono": telefono,
                                        "dni_cuit": cuit if cuit else dni,
                                        "zona": zona,
                                        "tipo_cliente": tipo,
                                        "direccion_principal": dir1.upper()
                                    }
                                },
                                usuario=usuario_logueado
                            )
                            
                            st.success("✅ Cliente cargado!")
                            st.rerun()
                            
                        except Exception as e:
                            st.error(f"Error al guardar el cliente o registrar auditoría: {e}")
                            
        if tab_modificar is not None:
            with tab_modificar:
                st.subheader("Modificar Cliente Existente")
            
                # Función auxiliar para limpiar NaNs de Pandas a string vacío o None
                def limpiar_val(val):
                    if pd.isna(val) or val is None or str(val).strip().upper() in ["NAN", "NONE", "NULL"]:
                        return ""
                    return str(val).strip()
            
                # --- 1. SELECTOR DINÁMICO (SOPORTA PERSONAS Y EMPRESAS) ---
                def obtener_etiqueta_cliente(row):
                    razon = limpiar_val(row.get('Razón Social'))
                    if razon:
                        return f"{razon} (ID: {row['ID_Cliente']})"
                    else:
                        nombre = limpiar_val(row.get('Nombre'))
                        apellido = limpiar_val(row.get('Apellido'))
                        return f"{nombre} {apellido}".strip() + f" (ID: {row['ID_Cliente']})"
            
                lista_clientes = df_clientes.apply(obtener_etiqueta_cliente, axis=1)
                
                seleccion = st.selectbox("Seleccione el cliente", [""] + lista_clientes.tolist(), key="sel_modificar")
                
                if seleccion:
                    id_modificar = seleccion.split("(ID: ")[1].replace(")", "")
                    fila = df_clientes[df_clientes['ID_Cliente'].astype(str) == id_modificar].iloc[0]
            
                    # Buscamos Gift Card activa
                    gc_data = db.table("GIFT_CARDS").select("*").eq("ID_Cliente", int(id_modificar)).eq("Estado", True).execute().data
                    
                    if gc_data:
                        gc = gc_data[0]
                        st.info(f"""
                        **Detalles de Gift Card Activa:**
                        - Saldo Inicial: ${gc['Saldo_Inicial']:,.2f}
                        - Saldo Actual: ${gc['Saldo_Actual']:,.2f}
                        - Pagada con: {gc['Forma_Pago_Adquisicion']}
                        """)
                    
                    if st.session_state.get('rol') == "Administrador":
                        if st.button("🎁 Gestionar Gift Card"):
                            razon_limpia = limpiar_val(fila.get('Razón Social'))
                            nombre_para_gc = razon_limpia.upper() if razon_limpia else f"{limpiar_val(fila.get('Nombre'))} {limpiar_val(fila.get('Apellido'))}".strip()
                            abrir_asignacion_gift_card(id_modificar, nombre_para_gc)
            
                    # 2. Formulario con valores de entrada sanitizados
                    with st.form("form_datos"):
                        c1, c2 = st.columns(2)
                        with c1:
                            nuevo_nombre = st.text_input("Nombre", value=limpiar_val(fila.get('Nombre')))
                            nuevo_apellido = st.text_input("Apellido", value=limpiar_val(fila.get('Apellido')))
                            nuevo_dni = st.text_input("DNI", value=limpiar_val(fila.get('DNI')))
                            nueva_razon = st.text_input("Razón Social", value=limpiar_val(fila.get('Razón Social')))
                            nuevo_cuit = st.text_input("CUIT", value=limpiar_val(fila.get('CUIT')))
                            nuevo_telefono = st.text_input("Teléfono", value=limpiar_val(fila.get('Telefono')), max_chars=10)
                        
                        with c2:
                            nuevo_dir1 = st.text_input("Dirección 1", value=limpiar_val(fila.get('Direccion_1')))
                            nuevo_link1 = st.text_input("Link Dirección 1", value=limpiar_val(fila.get('Link_Direccion_1')))
                            nuevo_dir2 = st.text_input("Dirección 2", value=limpiar_val(fila.get('Direccion_2')))
                            nuevo_link2 = st.text_input("Link Dirección 2", value=limpiar_val(fila.get('Link_Direccion_2')))
                            nuevo_dir3 = st.text_input("Dirección 3", value=limpiar_val(fila.get('Direccion_3')))
                            nuevo_link3 = st.text_input("Link Dirección 3", value=limpiar_val(fila.get('Link_Direccion_3')))
                        
                        nueva_obs = st.text_area("Observaciones", value=limpiar_val(fila.get('Observaciones')))
                        
                        zonas_lista = ["NORTE", "SUR", "CENTRO", "ESTE", "OESTE", "SANLO CHICO"]
                        idx_zona = zonas_lista.index(fila.get('Zona')) if fila.get('Zona') in zonas_lista else 0
                        input_zona = st.selectbox("Zona", zonas_lista, index=idx_zona)
                        
                        tipos_lista = ["CONSUMIDOR FINAL", "MAYORISTA", "EMPRESA/ORGANISMO"]
                        idx_tipo = tipos_lista.index(fila.get('Tipo_Cliente')) if fila.get('Tipo_Cliente') in tipos_lista else 0
                        input_tipo = st.selectbox("Tipo Cliente", tipos_lista, index=idx_tipo)
                        
                        guardar_btn = st.form_submit_button("Guardar Cambios")
            
                    # ACCIÓN DE GUARDAR (FUERA DEL FORM)
                    if guardar_btn:
                        usuario_logueado = st.session_state.get('usuario_actual', 'Desconocido')
                        
                        # Sanitizamos lo que ingresó el usuario antes de enviar a BD
                        razon_final = nueva_razon.strip().upper() if nueva_razon and nueva_razon.strip().upper() != "NAN" else None
                        nombre_final = nuevo_nombre.strip().upper() if nuevo_nombre and nuevo_nombre.strip().upper() != "NAN" else ""
                        apellido_final = nuevo_apellido.strip().upper() if nuevo_apellido and nuevo_apellido.strip().upper() != "NAN" else ""
            
                        try:
                            db.table("CLIENTES").update({
                                "Nombre": nombre_final,
                                "Apellido": apellido_final,
                                "DNI": nuevo_dni.strip(),
                                "Razón Social": razon_final,
                                "CUIT": nuevo_cuit.strip(),
                                "Telefono": nuevo_telefono.strip(),
                                "Direccion_1": nuevo_dir1.strip().upper(),
                                "Link_Direccion_1": nuevo_link1.strip(),
                                "Direccion_2": nuevo_dir2.strip().upper(),
                                "Link_Direccion_2": nuevo_link2.strip(),
                                "Direccion_3": nuevo_dir3.strip().upper(),
                                "Link_Direccion_3": nuevo_link3.strip(),
                                "Observaciones": nueva_obs.strip(),
                                "Zona": input_zona,
                                "Tipo_Cliente": input_tipo
                            }).eq("ID_Cliente", int(id_modificar)).execute()
                            
                            # Log de auditoría
                            razon_antigua = limpiar_val(fila.get('Razón Social'))
                            nombre_antiguo = razon_antigua if razon_antigua else f"{limpiar_val(fila.get('Apellido'))}, {limpiar_val(fila.get('Nombre'))}"
                            nombre_nuevo = razon_final if razon_final else f"{apellido_final}, {nombre_final}"
            
                            log_auditoria(
                                tabla="CLIENTES",
                                accion="UPDATE",
                                id_entidad=id_modificar,
                                detalles={
                                    "operacion": "Modificar Cliente",
                                    "antes": {
                                        "nombre_comercial_o_completo": nombre_antiguo,
                                        "telefono": fila.get('Telefono'),
                                        "zona": fila.get('Zona'),
                                        "direccion_1": fila.get('Direccion_1')
                                    },
                                    "despues": {
                                        "nombre_comercial_o_completo": nombre_nuevo,
                                        "telefono": nuevo_telefono,
                                        "zona": input_zona,
                                        "direccion_1": nuevo_dir1.strip().upper()
                                    }
                                },
                                usuario=usuario_logueado
                            )
                            
                            st.success("Guardado correctamente")
                            st.rerun()
                            
                        except Exception as e:
                            st.error(f"Error al modificar el cliente: {e}")
            
                    # ACCIÓN DE ELIMINAR
                    st.divider()
                    if st.session_state.get('rol') == "Administrador":
                        confirmar_del = st.checkbox("Confirmar eliminación", key="check_del_final")
                        if st.button("🗑️ Eliminar Cliente", key="btn_del_final"):
                            if confirmar_del:
                                usuario_logueado = st.session_state.get('usuario_actual', 'Desconocido')
                                
                                try:
                                    db.table("CLIENTES").delete().eq("ID_Cliente", int(id_modificar)).execute()
                                    
                                    razon_antigua = limpiar_val(fila.get('Razón Social'))
                                    cliente_identificador = razon_antigua if razon_antigua else f"{limpiar_val(fila.get('Apellido'))}, {limpiar_val(fila.get('Nombre'))}"
            
                                    log_auditoria(
                                        tabla="CLIENTES",
                                        accion="DELETE",
                                        id_entidad=id_modificar,
                                        details={
                                            "operacion": "Eliminar Cliente",
                                            "cliente_eliminado": cliente_identificador,
                                            "dni_cuit": fila.get('CUIT') if fila.get('CUIT') else fila.get('DNI'),
                                            "telefono": fila.get('Telefono')
                                        },
                                        usuario=usuario_logueado
                                    )
                                    
                                    st.success("🗑️ Cliente eliminado.")
                                    st.rerun()
                                    
                                except Exception as e:
                                    st.error(f"Error al eliminar el cliente: {e}")
                            else:
                                st.warning("⚠️ Debes marcar la casilla de confirmación.")

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
    # MODULO: 📦 PRODUCTOS
    # =====================================================================
    elif menu == "📦 Productos":
        st.title("📦 Gestión de Productos")
    
        # 1. CARGA INICIAL DE DATOS NECESARIOS (PARA TODOS)
        try:
            data = db.table("PRODUCTOS").select("*").execute().data
            df_prod = pd.DataFrame(data)
            
            # Carga de proveedores
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
    
        # 2. DEFINICIÓN DINÁMICA DE PESTAÑAS SEGÚN ROL (Se agrega "📋 Inventario")
        if st.session_state.rol == "Administrador":
            tabs = st.tabs(["🔍 Buscar", "➕ Alta", "✏️ Modificar", "🔄 Cambios", "✂️ Divisor", "📋 Inventario", "📥 Importar", "📜 Histórico"])
            tab_buscar, tab_alta, tab_modificar, tab_cambios, tab_divisor, tab_inventario, tab_importar, tab_historico = tabs
        else:
            tabs = st.tabs(["🔍 Buscar", "🔄 Cambios", "✂️ Divisor", "📋 Inventario"])
            tab_buscar, tab_cambios, tab_divisor, tab_inventario = tabs
            tab_alta, tab_modificar, tab_importar, tab_historico = None, None, None, None
    
        # --- PESTAÑA BUSCAR ---
        with tab_buscar:
            st.subheader("🔍 Buscador de Productos")
        
            # --- CONTROLES Y FILTROS RÁPIDOS ---
            c_chk1, c_chk2 = st.columns(2)
        
            # 1. Filtro de Stock > 0 (Tildado por defecto)
            solo_con_stock = c_chk1.checkbox("📦 Solo productos con Stock > 0", value=True, key="chk_solo_con_stock")
        
            # 2. Mostrar Inactivos (Solo disponible para Administradores)
            mostrar_inactivos = False
            if st.session_state.rol == "Administrador":
                mostrar_inactivos = c_chk2.checkbox("👁️ Mostrar productos INACTIVOS", value=False, key="chk_inactivos")
        
            busqueda_texto = st.text_input(
                "Escriba para filtrar por nombre o código:", 
                placeholder="Ej: pampers, toallitas, 779...",
                key="busqueda_tab_buscar"
            )
        
            c1, c2 = st.columns(2)
            rubros = ["Todos"] + [r for r in st.session_state.df_prod['Rubro'].dropna().unique().tolist() if r]
            marcas = ["Todos"] + [m for m in st.session_state.df_prod['Marca'].dropna().unique().tolist() if m]
        
            filtro_rubro = c1.selectbox("Filtrar por Rubro", rubros, key="filtro_rubro_tab")
            filtro_marca = c2.selectbox("Filtrar por Marca", marcas, key="filtro_marca_tab")
        
            df_filtrado = st.session_state.df_prod.copy()
        
            # -------------------------------------------------------------
            # 1️⃣ FILTRO DE PRODUCTOS INACTIVOS
            # -------------------------------------------------------------
            if 'Estado' in df_filtrado.columns and not mostrar_inactivos:
                df_filtrado = df_filtrado[df_filtrado['Estado'] != 'INACTIVO']
        
            # -------------------------------------------------------------
            # 2️⃣ FILTRO DE STOCK DISPONIBLE (Stock_Actual > 0)
            # -------------------------------------------------------------
            if solo_con_stock and 'Stock_Actual' in df_filtrado.columns:
                # Aseguramos que interprete el stock como número por seguridad
                df_filtrado['Stock_Actual'] = pd.to_numeric(df_filtrado['Stock_Actual'], errors='coerce').fillna(0)
                df_filtrado = df_filtrado[df_filtrado['Stock_Actual'] > 0]
        
            # -------------------------------------------------------------
            # 3️⃣ FILTROS DE BÚSQUEDA POR TEXTO, RUBRO Y MARCA
            # -------------------------------------------------------------
            if busqueda_texto:
                busqueda_texto = busqueda_texto.lower()
                mask = df_filtrado['Nombre'].str.lower().str.contains(busqueda_texto, na=False) | \
                       df_filtrado['ID_Producto'].astype(str).str.lower().str.contains(busqueda_texto, na=False)
                df_filtrado = df_filtrado[mask]
        
            if filtro_rubro != "Todos": 
                df_filtrado = df_filtrado[df_filtrado['Rubro'] == filtro_rubro]
            if filtro_marca != "Todos": 
                df_filtrado = df_filtrado[df_filtrado['Marca'] == filtro_marca]
        
            # -------------------------------------------------------------
            # 🔤 ORDENAR ALFABÉTICAMENTE POR NOMBRE
            # -------------------------------------------------------------
            if 'Nombre' in df_filtrado.columns:
                df_filtrado = df_filtrado.sort_values(by='Nombre', key=lambda col: col.str.lower(), ascending=True)
        
            # Guardamos una referencia para el generador antes de recortar columnas por rol
            df_para_wsp = df_filtrado.copy()
        
            # Ajuste de columnas visibles según el rol
            if st.session_state.rol != "Administrador":
                cols_vendedor = ['Nombre', 'Precio_1', 'Precio_2', 'Precio_3']
                df_filtrado = df_filtrado[[c for c in cols_vendedor if c in df_filtrado.columns]]
        
            st.dataframe(df_filtrado, use_container_width=True, hide_index=True)
        
            # -------------------------------------------------------------
            # 4️⃣ GENERADOR DE RESPUESTA PARA WHATSAPP
            # -------------------------------------------------------------
            st.markdown("---")
            if st.button("💬 Generar Respuesta para WhatsApp", type="primary", key="btn_wsp_precios_prod"):
                if df_para_wsp.empty:
                    st.warning("⚠️ No hay productos con los criterios seleccionados para generar la respuesta.")
                else:
                    lineas_mensaje = []
                    
                    for _, prod in df_para_wsp.iterrows():
                        nombre = str(prod.get('Nombre', '')).strip()
                        
                        try:
                            p1 = int(float(prod.get('Precio_1', 0)))
                            p2 = int(float(prod.get('Precio_2', 0)))
                            p3 = int(float(prod.get('Precio_3', 0)))
                        except (ValueError, TypeError):
                            p1, p2, p3 = 0, 0, 0
                        
                        # Reglas de precios
                        if p1 == p2:
                            # Caso 1: Precio_1 == Precio_2 (Precio único)
                            linea = f"• *{nombre}* ${p1}"
                        elif p1 != p2 and p2 == p3:
                            # Caso 2: Precio_1 != Precio_2 y Precio_2 == Precio_3
                            linea = f"• *{nombre}* ${p1} x1 o ${p2} cada uno llevando 2"
                        elif p2 != p3:
                            # Caso 3: Precio_2 != Precio_3
                            linea = f"• *{nombre}* ${p1} x1 o ${p3} cada uno llevando 3"
                        else:
                            linea = f"• *{nombre}* ${p1}"
                            
                        lineas_mensaje.append(linea)
                    
                    msg_precios_wsp = "\n".join(lineas_mensaje)
                    st.text_area("Copiar respuesta para WhatsApp:", value=msg_precios_wsp, height=250, key="txt_area_wsp_precios")
    
        # --- PESTAÑA CAMBIOS ---
        with tab_cambios:
            st.subheader("🔄 Gestión de Cambios y Devoluciones")
        
            if st.session_state.get('rol') == "Administrador":
                st.divider()
                st.subheader("🛡️ Panel de Supervisión (Admin)")
                pendientes = db.table("PRE_CAMBIOS").select("*").eq("Estado", "PENDIENTE").execute().data
        
                if pendientes:
                    for p in pendientes:
                        with st.container(border=True):
                            c1, c2 = st.columns([3, 1])
                            with c1:
                                st.markdown(f"**Producto:** {p['Nombre']} | **Usuario Solicitante:** {p['Usuario']}")
                                st.caption(f"Motivo original: {p['Descripción']}")
        
                            with st.form(f"form_admin_{p['id']}"):
                                col_a, col_b, col_c = st.columns(3)
                                new_cant = col_a.number_input("Cantidad:", value=max(p['Entra'], p['Sale']), key=f"cant_{p['id']}")
                                new_tipo = col_b.selectbox("Tipo:", ["ENTRA", "SALE"], index=0 if p['Entra'] > 0 else 1, key=f"tipo_{p['id']}")
                                new_desc = col_c.text_input("Motivo editado:", value=p['Descripción'], key=f"desc_{p['id']}")
        
                                btn_col1, btn_col2 = st.columns(2)
                                if btn_col1.form_submit_button("💾 Aprobar y Procesar", use_container_width=True):
                                    prod_data = db.table("PRODUCTOS").select("Stock_Actual", "Nombre").eq("ID_Producto", p['Código']).execute().data
        
                                    if prod_data:
                                        stock_viejo = int(prod_data[0]['Stock_Actual'])
                                        nombre_producto_kardex = prod_data[0].get('Nombre', p['Nombre'])
        
                                        cantidad_movimiento = int(new_cant) if new_tipo == 'ENTRA' else -int(new_cant)
                                        stock_nuevo = stock_viejo + cantidad_movimiento
        
                                        # 1. Actualización de Stock Actual del Producto
                                        db.table("PRODUCTOS").update({"Stock_Actual": stock_nuevo}).eq("ID_Producto", p['Código']).execute()
        
                                        try:
                                            # 2. Registrar en Tabla CAMBIOS
                                            db.table("CAMBIOS").insert({
                                                "Fecha": datetime.now().isoformat(),
                                                "Usuario": st.session_state.get('usuario_nombre') or st.session_state.get('usuario_actual', 'Administrador'),
                                                "Código": p['Código'],
                                                "Nombre": p['Nombre'],
                                                "Descripción": new_desc,
                                                "Entra": int(new_cant) if new_tipo == 'ENTRA' else 0,
                                                "Sale": int(new_cant) if new_tipo == 'SALE' else 0,
                                                "existencia_ant": stock_viejo,
                                                "existencia_actual": stock_nuevo
                                            }).execute()
        
                                            # 3. REGISTRAR EN MOVIMIENTOS_STOCK (KARDEX)
                                            usuario_admin = st.session_state.get('usuario_nombre') or st.session_state.get('usuario_actual', 'Administrador')
                                            tipo_mov_kardex = "DEVOLUCIÓN ENTRADA" if new_tipo == 'ENTRA' else "CAMBIO SALIDA"
        
                                            db.table("MOVIMIENTOS_STOCK").insert({
                                                "id_producto": str(p['Código']),
                                                "nombre_producto": nombre_producto_kardex,
                                                "tipo_movimiento": tipo_mov_kardex,
                                                "cantidad": cantidad_movimiento,
                                                "stock_anterior": stock_viejo,
                                                "stock_nuevo": stock_nuevo,
                                                "origen_referencia": f"Cambio/Devolución ID: {p['id']} - Motivo: {new_desc}",
                                                "usuario": str(usuario_admin)
                                            }).execute()
        
                                            # 4. Cambiar estado a PROCESADO
                                            db.table("PRE_CAMBIOS").update({"Estado": "PROCESADO"}).eq("id", p['id']).execute()
                                            st.success("✅ Stock y Kardex actualizados correctamente.")
                                            st.rerun()
        
                                        except Exception as e:
                                            st.error(f"Error al procesar el cambio: {e}")
        
                                if btn_col2.form_submit_button("❌ Rechazar", use_container_width=True):
                                    db.table("PRE_CAMBIOS").update({"Estado": "RECHAZADO"}).eq("id", p['id']).execute()
                                    st.rerun()
                else:
                    st.info("No hay cambios pendientes.")
                st.divider()
        
            if 'lista_cambios' not in st.session_state:
                st.session_state.lista_cambios = []
        
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
        
            if st.session_state.lista_cambios:
                st.write("Resumen del movimiento:")
                st.table(pd.DataFrame(st.session_state.lista_cambios))
        
                if st.button("❌ Limpiar lista"):
                    st.session_state.lista_cambios = []
                    st.rerun()
        
                motivo = st.text_input("Motivo del cambio:")
        
                if st.button("📤 Enviar Pre-cambio a Revisión"):
                    try:
                        usuario_solicitante = st.session_state.get('usuario_nombre') or st.session_state.get('usuario_actual', 'Desconocido')
                        for item in st.session_state.lista_cambios:
                            db.table("PRE_CAMBIOS").insert({
                                "Fecha": datetime.now().isoformat(),
                                "Código": item['ID'],
                                "Nombre": item['Producto'],
                                "Descripción": motivo,
                                "Entra": int(item['Cantidad']) if item['Tipo'] == 'ENTRA' else 0,
                                "Sale": int(item['Cantidad']) if item['Tipo'] == 'SALE' else 0,
                                "Estado": "PENDIENTE",
                                "Usuario": str(usuario_solicitante)
                            }).execute()
                        st.success("✅ Enviado a revisión.")
                        st.session_state.lista_cambios = []
                        st.rerun()
                    except Exception as e:
                        st.error(f"Error: {e}")
    
        # --- PESTAÑA DIVISOR ---
        with tab_divisor:
            st.subheader("✂️ Divisor de Fardos")
            
            # Notificación persistente después del st.rerun()
            if st.session_state.get('msg_division_exitosa'):
                st.success(st.session_state.msg_division_exitosa)
                st.toast("✅ ¡División de fardo procesada correctamente!", icon="🎉")
                del st.session_state['msg_division_exitosa']
        
            patrones_fardo = [r'\bx12\b', r'\bx24\b', r'\bx30\b', r'\bX12\b', r'\bX24\b', r'\bX30\b']
            regex_patron = '|'.join(patrones_fardo)
            
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
                    id_fardo = id_fardo_sel.split(" - ")[0]
                    st.session_state.id_fardo_temp = id_fardo 
                    st.session_state.fila_fardo_temp = df_filtrado_div[df_filtrado_div['ID_Producto'].astype(str) == id_fardo].iloc[0]
                    
                    st.info(f"Fardo: {st.session_state.fila_fardo_temp['Nombre']} | Stock actual: {st.session_state.fila_fardo_temp['Stock_Actual']} unidades")
                    
                    with st.form("form_divisor"):
                        c1, c2 = st.columns(2)
                        unidades = c1.number_input("¿Cuántas unidades trae el fardo?", min_value=1, value=24)
                        id_cajita = c2.text_input("Código de la Cajita Individual:")
                        
                        fila_fardo = st.session_state.fila_fardo_temp
                        id_fardo = st.session_state.id_fardo_temp
                        
                        costo_fardo = float(fila_fardo['Precio_Costo']) if fila_fardo['Precio_Costo'] else 0.0
                        costo_unitario = costo_fardo / unidades if unidades > 0 else 0
                        precio_sugerido = ((int((costo_unitario * 1.40) // 100) + 1) * 100)
                        
                        st.write(f"Costo unitario: `${costo_unitario:,.2f}` | Precio Sugerido: `${precio_sugerido:,.0f}`")
                        
                        # Botón de formulario solicita confirmación
                        btn_pre_confirmar = st.form_submit_button("🚀 Confirmar División")
        
                        if btn_pre_confirmar:
                            if int(fila_fardo['Stock_Actual']) <= 0:
                                st.error(f"⚠️ ¡Error! El fardo '{fila_fardo['Nombre']}' no cuenta con existencias para dividir.")
                            elif not id_cajita.strip():
                                st.error("⚠️ Debe ingresar el código de la cajita individual.")
                            else:
                                # Guardar parámetros validados para mostrar modal/pantalla de confirmación
                                st.session_state.pending_division = {
                                    "id_fardo": id_fardo,
                                    "fila_fardo": fila_fardo,
                                    "unidades": int(unidades),
                                    "id_cajita": id_cajita.strip()
                                }
                                st.rerun()
        
                    # --- POP-UP / DIÁLOGO DE CONFIRMACIÓN ---
                    if "pending_division" in st.session_state:
                        p = st.session_state.pending_division
                        
                        st.markdown("---")
                        st.warning(f"⚠️ **¿Confirmar división del fardo?**\n\n"
                                   f"- **Fardo:** {p['fila_fardo']['Nombre']} (Se descontará 1 unidad)\n"
                                   f"- **Cajita destino:** {p['id_cajita']} (Se sumarán {p['unidades']} unidades)")
                        
                        col_conf1, col_conf2 = st.columns(2)
                        
                        if col_conf1.button("✅ Sí, Ejecutar División", type="primary", key="btn_confirm_div_yes"):
                            try:
                                # 1. Verificar existencia de la cajita individual
                                prod_cajita = db.table("PRODUCTOS").select("Stock_Actual", "Nombre").eq("ID_Producto", p['id_cajita']).execute().data
                                if not prod_cajita:
                                    st.error("❌ ¡Error! El código de la cajita no existe en la base de datos.")
                                else:
                                    usuario_logueado = st.session_state.get('usuario_nombre') or st.session_state.get('usuario_actual', 'Desconocido')
        
                                    # 2. Descuento del Fardo
                                    stock_fardo_old = int(p['fila_fardo']['Stock_Actual'])
                                    nuevo_stock_fardo = stock_fardo_old - 1
                                    db.table("PRODUCTOS").update({"Stock_Actual": nuevo_stock_fardo}).eq("ID_Producto", p['id_fardo']).execute()
        
                                    # 3. Incremento de la Cajita Individual
                                    stock_cajita_old = int(prod_cajita[0]['Stock_Actual'])
                                    nombre_cajita = prod_cajita[0].get('Nombre', 'Cajita Individual')
                                    nuevo_stock_cajita = stock_cajita_old + p['unidades']
                                    db.table("PRODUCTOS").update({"Stock_Actual": nuevo_stock_cajita}).eq("ID_Producto", p['id_cajita']).execute()
        
                                    # 4. Registrar en Tabla CAMBIOS (Fardo)
                                    db.table("CAMBIOS").insert({
                                        "Fecha": datetime.now().isoformat(),
                                        "Usuario": str(usuario_logueado),
                                        "Código": p['id_fardo'],
                                        "Nombre": p['fila_fardo']['Nombre'],
                                        "Descripción": f"División de fardo: Se transformó en {p['unidades']} unidades de {p['id_cajita']}",
                                        "Entra": 0, "Sale": 1,
                                        "existencia_ant": stock_fardo_old,
                                        "existencia_actual": nuevo_stock_fardo
                                    }).execute()
        
                                    # 5. Registrar en Tabla CAMBIOS (Cajita)
                                    db.table("CAMBIOS").insert({
                                        "Fecha": datetime.now().isoformat(),
                                        "Usuario": str(usuario_logueado),
                                        "Código": p['id_cajita'],
                                        "Nombre": nombre_cajita,
                                        "Descripción": f"Ingreso por división de fardo {p['id_fardo']}",
                                        "Entra": p['unidades'], "Sale": 0,
                                        "existencia_ant": stock_cajita_old,
                                        "existencia_actual": nuevo_stock_cajita
                                    }).execute()
        
                                    # 6. REGISTRO EN MOVIMIENTOS_STOCK (KARDEX) - FARDO
                                    db.table("MOVIMIENTOS_STOCK").insert({
                                        "id_producto": str(p['id_fardo']),
                                        "nombre_producto": str(p['fila_fardo']['Nombre']),
                                        "tipo_movimiento": "DIVISIÓN FARDO (SALIDA)",
                                        "cantidad": -1,
                                        "stock_anterior": stock_fardo_old,
                                        "stock_nuevo": nuevo_stock_fardo,
                                        "origen_referencia": f"División en {p['unidades']} uds de Cajita (ID: {p['id_cajita']})",
                                        "usuario": str(usuario_logueado)
                                    }).execute()
        
                                    # 7. REGISTRO EN MOVIMIENTOS_STOCK (KARDEX) - CAJITA
                                    db.table("MOVIMIENTOS_STOCK").insert({
                                        "id_producto": str(p['id_cajita']),
                                        "nombre_producto": str(nombre_cajita),
                                        "tipo_movimiento": "DIVISIÓN FARDO (ENTRADA)",
                                        "cantidad": p['unidades'],
                                        "stock_anterior": stock_cajita_old,
                                        "stock_nuevo": nuevo_stock_cajita,
                                        "origen_referencia": f"Ingreso por división de Fardo (ID: {p['id_fardo']})",
                                        "usuario": str(usuario_logueado)
                                    }).execute()
        
                                    # 8. Log de Auditoría
                                    log_auditoria(
                                        tabla="PRODUCTOS",
                                        accion="UPDATE",
                                        id_entidad=p['id_fardo'],
                                        detalles={
                                            "operacion": "Divisor de Fardos",
                                            "fardo": {"id": p['id_fardo'], "nombre": p['fila_fardo']['Nombre'], "stock_nuevo": nuevo_stock_fardo},
                                            "cajita": {"id": p['id_cajita'], "nombre": nombre_cajita, "unidades_ingresadas": p['unidades'], "stock_nuevo": nuevo_stock_cajita}
                                        },
                                        usuario=usuario_logueado
                                    )
        
                                    # Guardar mensaje de éxito para mostrarlo post-rerun
                                    st.session_state.msg_division_exitosa = f"🎉 **¡División Exitosa!** Se descontó 1 fardo '{p['fila_fardo']['Nombre']}' y se acreditaron {p['unidades']} unidades a '{nombre_cajita}'."
                                    
                                    # Limpieza de estados y caché
                                    del st.session_state['pending_division']
                                    if 'df_prod' in st.session_state: 
                                        del st.session_state['df_prod']
                                    
                                    st.rerun()
        
                            except Exception as e:
                                st.error(f"Error al procesar la división: {e}")
        
                        if col_conf2.button("❌ Cancelar", key="btn_confirm_div_no"):
                            del st.session_state['pending_division']
                            st.rerun()
                    
        # --- PESTAÑA INVENTARIO (NUEVA) ---
        with tab_inventario:
            st.subheader("📋 Módulo de Toma de Inventarios")
            
            # -------------------------------------------------------------
            # CONFIGURACIÓN DE PESTAÑAS SEGÚN ROL
            # -------------------------------------------------------------
            if st.session_state.rol == "Administrador":
                tab_vendedor, tab_admin = st.tabs(["📝 Realizar Recuento (Vendedor)", "📊 Auditoría y Ajustes (Admin)"])
            else:
                tab_vendedor = st.container()
                tab_admin = None
            
            # =============================================================
            # VISTA 1: VENDEDOR (TOMA DE RECUENTO)
            # =============================================================
            with tab_vendedor:
                # ---------------------------------------------------------
                # CRONOGRAMA DE INVENTARIO SEMANAL (Ancho ajustado)
                # ---------------------------------------------------------
                with st.expander("📅 Ver Cronograma de Inventario Semanal", expanded=False):
                    try:
                        # Creamos 2 columnas: 60% para la imagen, 40% vacío a la derecha
                        col_img, col_vacia = st.columns([0.6, 0.4])
                        with col_img:
                            st.image("cronograma_inventario.jpg", use_container_width=True)
                    except Exception:
                        st.warning("⚠️ No se encontró la imagen 'cronograma_inventario.jpg' en el directorio del proyecto.")
            
                st.caption("Efectúe el recuento físico de la mercadería.")
                
                # Filtrar solo productos activos
                df_activos = st.session_state.df_prod.copy()
                if 'Estado' in df_activos.columns:
                    df_activos = df_activos[df_activos['Estado'] != 'INACTIVO']
                
                modo_conteo = st.radio(
                    "Seleccionar método de recuento:", 
                    ["Por Marca", "Muestreo al Azar"], 
                    horizontal=True, 
                    key="inv_modo"
                )
                
                productos_a_contar = pd.DataFrame()
                parametro_conteo = ""
                
                # RESETEO LIMPIO DE SELECTBOX DE MARCAS
                if st.session_state.get("reset_inventario", False):
                    st.session_state["inv_marca_sel"] = None
                    st.session_state["reset_inventario"] = False
                
                # --- OPCIÓN A: POR MARCA ---
                if modo_conteo == "Por Marca":
                    marcas_disp = sorted(df_activos['Marca'].dropna().unique().tolist()) if 'Marca' in df_activos.columns else []
                    
                    marca_sel = st.selectbox(
                        "Seleccionar Marca a auditar:", 
                        options=marcas_disp, 
                        index=None, 
                        placeholder="--- Seleccione una Marca ---",
                        key="inv_marca_sel"
                    )
                    
                    if marca_sel:
                        parametro_conteo = marca_sel
                        productos_a_contar = df_activos[df_activos['Marca'] == marca_sel].copy()
                
                # --- OPCIÓN B: MUESTREO AL AZAR ---
                else:
                    cant_items = st.number_input(
                        "Cantidad de artículos al azar:", 
                        min_value=1, 
                        max_value=max(1, len(df_activos)), 
                        value=min(10, max(1, len(df_activos))), 
                        step=5
                    )
                    parametro_conteo = f"{cant_items} Artículos al azar"
                    
                    if st.button("🎲 Generar Muestra Aleatoria", key="btn_generar_azar"):
                        st.session_state.muestra_azar = df_activos.sample(n=int(cant_items)).copy()
                    
                    if "muestra_azar" in st.session_state:
                        productos_a_contar = st.session_state.muestra_azar
                
                # --- FORMULARIO DE RECUENTO FÍSICO ---
                if not productos_a_contar.empty:
                    st.info(f"Mostrando **{len(productos_a_contar)}** productos para la recolección física.")
                    
                    with st.form("form_recuento_inv"):
                        vendedor_nombre = st.text_input(
                            "Nombre del Vendedor / Auditor:", 
                            value=st.session_state.get("usuario_actual", "")
                        )
                        conteos_usuario = {}
                        
                        st.markdown("---")
                        for idx, prod in productos_a_contar.iterrows():
                            col_info, col_input = st.columns([3, 1])
                            with col_info:
                                st.write(f"**{prod['Nombre']}**")
                                st.caption(f"Código: `{prod['ID_Producto']}` | Rubro: {prod.get('Rubro', 'N/A')}")
                            with col_input:
                                conteos_usuario[str(prod['ID_Producto'])] = st.number_input(
                                    "Contado", 
                                    min_value=0, 
                                    value=0, 
                                    key=f"inv_in_{prod['ID_Producto']}"
                                )
                            st.markdown("---")
                            
                        if st.form_submit_button("📩 Finalizar y Enviar Recuento", type="primary"):
                            if not vendedor_nombre.strip():
                                st.error("Por favor ingrese el nombre del auditor/vendedor antes de enviar.")
                            else:
                                try:
                                    # 1. Consultar Stock actual y Precio de costo en tiempo real desde Supabase
                                    ids_prod = list(conteos_usuario.keys())
                                    res_prods = db.table("PRODUCTOS").select("ID_Producto, Stock_Actual, Precio_Costo").in_("ID_Producto", ids_prod).execute().data
                                    df_live = pd.DataFrame(res_prods)
                                    
                                    stock_map = dict(zip(df_live['ID_Producto'].astype(str), df_live['Stock_Actual']))
                                    costo_map = dict(zip(df_live['ID_Producto'].astype(str), df_live['Precio_Costo']))
                                    
                                    # 2. Calcular Impacto Financiero y preparar el detalle
                                    impacto_financiero_total = 0.0
                                    detalles_insertar_temp = []
                                    
                                    for prod_id, cont_cant in conteos_usuario.items():
                                        stock_snap = float(stock_map.get(str(prod_id), 0) or 0)
                                        costo_unitario = float(costo_map.get(str(prod_id), 0) or 0)
                                        
                                        dif = float(cont_cant) - stock_snap
                                        impacto_item = dif * costo_unitario
                                        impacto_financiero_total += impacto_item
                                        
                                        # REGLA: Si no hay diferencia, se marca como SIN_DIFERENCIA automáticamente
                                        estado_inicial_item = "SIN_DIFERENCIA" if dif == 0 else "PENDIENTE"
                                        
                                        detalles_insertar_temp.append({
                                            "id_producto": str(prod_id),
                                            "stock_sistema_snap": stock_snap,
                                            "stock_contado": float(cont_cant),
                                            "diferencia": dif,
                                            "estado_item": estado_inicial_item
                                        })
                                    
                                    # 3. Insertar Registro Cabecera
                                    res_cabecera = db.table("INVENTARIOS_CABECERA").insert({
                                        "tipo": "MARCA" if modo_conteo == "Por Marca" else "AZAR",
                                        "parametro": parametro_conteo,
                                        "vendedor": vendedor_nombre.strip(),
                                        "estado": "ENVIADO",
                                        "impactofinanciero": round(impacto_financiero_total, 2)
                                    }).execute()
                                    
                                    id_inv = res_cabecera.data[0]['id']
                                    
                                    # 4. Asignar inventario_id e Insertar Detalles
                                    for det in detalles_insertar_temp:
                                        det["inventario_id"] = id_inv
                                    
                                    db.table("INVENTARIOS_DETALLE").insert(detalles_insertar_temp).execute()
                                    
                                    # 5. Limpieza de variables temporales de la sesión
                                    for prod_id in conteos_usuario.keys():
                                        key_input = f"inv_in_{prod_id}"
                                        if key_input in st.session_state:
                                            del st.session_state[key_input]
                                    
                                    if "muestra_azar" in st.session_state:
                                        del st.session_state["muestra_azar"]
                                    
                                    st.session_state["reset_inventario"] = True
                                    
                                    st.success(f"✅ Recuento enviado con éxito. Impacto estimado: ${impacto_financiero_total:,.2f}")
                                    st.rerun()
                                    
                                except Exception as e:
                                    st.error(f"Error al enviar recuento: {e}")
                
            # =============================================================
            # VISTA 2: ADMIN (AUDITORÍA Y AJUSTES)
            # =============================================================
            if st.session_state.rol == "Administrador" and tab_admin:
                with tab_admin:
                    st.caption("Supervisión, detección de inconsistencias y aplicación de diferencias de stock.")
                    
                    cabeceras_data = db.table("INVENTARIOS_CABECERA").select("*").eq("estado", "ENVIADO").order("created_at", desc=True).execute().data
                    
                    if not cabeceras_data:
                        st.info("No existen recuentos pendientes de revisión.")
                    else:
                        df_cabeceras = pd.DataFrame(cabeceras_data)
                        
                        opciones_inv = [
                            f"ID #{r['id']} - {r['vendedor']} ({r['parametro']}) - Impacto Est.: ${float(r.get('impactofinanciero') or 0):,.2f} - {str(r['created_at'])[:10]}" 
                            for _, r in df_cabeceras.iterrows()
                        ]
                        inv_seleccionado = st.selectbox("Seleccione recuento para auditar:", opciones_inv, key="rev_inv_sel")
                        
                        if inv_seleccionado:
                            id_cabecera = int(inv_seleccionado.split(" - ")[0].replace("ID #", ""))
                            detalle_data = db.table("INVENTARIOS_DETALLE").select("*").eq("inventario_id", id_cabecera).execute().data
                            
                            if detalle_data:
                                df_det = pd.DataFrame(detalle_data)
                                ids_det = df_det['id_producto'].astype(str).tolist()
                                
                                # Obtener información actual de los productos
                                df_prods_info = pd.DataFrame(
                                    db.table("PRODUCTOS")
                                    .select("ID_Producto, Nombre, Precio_Costo, Stock_Actual")
                                    .in_("ID_Producto", ids_det)
                                    .execute().data
                                )
                                
                                if not df_prods_info.empty:
                                    df_prods_info['ID_Producto'] = df_prods_info['ID_Producto'].astype(str)
                                    df_det['id_producto'] = df_det['id_producto'].astype(str)
                                    
                                    df_merged = pd.merge(df_det, df_prods_info, left_on="id_producto", right_on="ID_Producto", suffixes=('', '_actual'))
                                    
                                    # --- CÁLCULO DINÁMICO TOMANDO EL CONTEO DEL ADMIN ---
                                    def calcular_dif_admin(row):
                                        key_input = f"input_admin_stk_{row['id']}"
                                        # Si el Admin ya interactuó/ingresó un valor en el input, tomar ese valor; si no, tomar el del vendedor.
                                        val_admin = st.session_state.get(key_input, int(row['stock_contado']))
                                        return float(val_admin) - float(row['stock_sistema_snap'])
        
                                    df_merged['diferencia_efectiva'] = df_merged.apply(calcular_dif_admin, axis=1)
                                    
                                    total_dif = df_merged['diferencia_efectiva'].sum()
                                    impacto_dinero = (df_merged['diferencia_efectiva'] * df_merged['Precio_Costo'].fillna(0)).sum()
                                    
                                    m1, m2 = st.columns(2)
                                    m1.metric("Diferencia Total (Unidades)", f"{total_dif:.0f}")
                                    m2.metric("Impacto Financiero Estimado (Admin)", f"${impacto_dinero:,.2f}", delta_color="inverse")
                                    
                                    st.markdown("---")
                                    
                                    # RENDERIZAR ARTÍCULOS PARA REVISIÓN
                                    for idx, item in df_merged.iterrows():
                                        snap_val = float(item['stock_sistema_snap'])
                                        
                                        with st.container(border=True):
                                            c1, c2, c3, c4, c5, c6 = st.columns([2.5, 1.2, 1.2, 1.5, 1.5, 2])
                                            c1.write(f"**{item['Nombre']}** (`{item['id_producto']}`)")
                                            c2.write(f"Snap: **{snap_val:.0f}**")
                                            c3.write(f"Contado: **{item['stock_contado']:.0f}**")
                                            
                                            # Campo para re-conteo / confirmación del admin
                                            valor_admin = c4.number_input(
                                                "Re-conteo Admin", 
                                                min_value=0, 
                                                value=int(item['stock_contado']), 
                                                key=f"input_admin_stk_{item['id']}",
                                                label_visibility="collapsed"
                                            )
                                            
                                            # EVALUACIÓN DINÁMICA DE DIFERENCIA
                                            dif_efectiva = float(valor_admin) - snap_val
                                            
                                            if dif_efectiva == 0:
                                                color_status = "🟢 COINCIDE"
                                            elif dif_efectiva < 0:
                                                color_status = f"🔴 FALTANTE ({dif_efectiva:.0f})"
                                            else:
                                                color_status = f"🟡 SOBRANTE (+{dif_efectiva:.0f})"
                                            
                                            c5.write(f"Estado: **{color_status}**")
                                            
                                            with c6:
                                                # CONDICIÓN CLAVE: Solo se permite ajustar si hay diferencia real Y está pendiente
                                                if item['estado_item'] == 'PENDIENTE' and dif_efectiva != 0:
                                                    if st.button("✔️ Aplicar Ajuste", key=f"btn_aj_{item['id']}"):
                                                        try:
                                                            id_prod_str = str(item['id_producto']).strip()
                                                            nombre_prod_str = str(item['Nombre']).strip()
                                                            usuario_actual = st.session_state.get('usuario_actual', 'Admin')
                                                            
                                                            # 1. Consultar stock actual previo de Supabase
                                                            p_actual = db.table("PRODUCTOS").select("Stock_Actual").eq("ID_Producto", id_prod_str).execute().data
                                                            stock_previo = float(p_actual[0]['Stock_Actual'] or 0) if p_actual else 0.0
                                                            
                                                            nuevo_stock_int = int(valor_admin)
                                                            diferencia_real = nuevo_stock_int - stock_previo
                                                            
                                                            # 2. Actualizar stock final en la tabla PRODUCTOS
                                                            db.table("PRODUCTOS").update({
                                                                "Stock_Actual": nuevo_stock_int
                                                            }).eq("ID_Producto", id_prod_str).execute()
                                                            
                                                            # 3. Marcar ítem del inventario como ajustado
                                                            db.table("INVENTARIOS_DETALLE").update({
                                                                "estado_item": "AJUSTADO",
                                                                "diferencia": dif_efectiva
                                                            }).eq("id", int(item['id'])).execute()
                                                            
                                                            # 4. REGISTRAR MOVIMIENTO EN LA TABLA MOVIMIENTOS_STOCK (KARDEX)
                                                            if diferencia_real > 0:
                                                                tipo_mov = "AJUSTE INVENTARIO (ENTRADA)"
                                                            elif diferencia_real < 0:
                                                                tipo_mov = "AJUSTE INVENTARIO (SALIDA)"
                                                            else:
                                                                tipo_mov = "AJUSTE INVENTARIO (SIN CAMBIO)"
                                                            
                                                            db.table("MOVIMIENTOS_STOCK").insert({
                                                                "id_producto": id_prod_str,
                                                                "nombre_producto": nombre_prod_str,
                                                                "tipo_movimiento": tipo_mov,
                                                                "cantidad": int(abs(diferencia_real)),
                                                                "stock_anterior": float(stock_previo),
                                                                "stock_nuevo": float(nuevo_stock_int),
                                                                "origen_referencia": f"Ajuste por Inventario (ID Cabecera: {id_cabecera})",
                                                                "usuario": str(usuario_actual)
                                                            }).execute()
                                                            
                                                            # 5. Registrar auditoría general
                                                            if 'log_auditoria' in globals():
                                                                log_auditoria(
                                                                    tabla="PRODUCTOS",
                                                                    accion="UPDATE",
                                                                    id_entidad=id_prod_str,
                                                                    detalles={
                                                                        "operacion": "Ajuste Directo Conteo Admin",
                                                                        "conteo_vendedor": float(item['stock_contado']),
                                                                        "stock_anterior": stock_previo,
                                                                        "nuevo_stock": nuevo_stock_int,
                                                                        "diferencia": diferencia_real
                                                                    },
                                                                    usuario=usuario_actual
                                                                )
                                                            
                                                            st.success(f"✅ Stock fijado: {int(stock_previo)} ➔ {nuevo_stock_int} (Kardex registrado)")
                                                            st.rerun()
                                                            
                                                        except Exception as e:
                                                            st.error(f"Error al aplicar ajuste y registrar movimiento: {e}")
                                                            
                                                elif item['estado_item'] == 'AJUSTADO':
                                                    st.caption("✅ Ajustado")
                                                else:
                                                    st.caption("🟢 Sin diferencia")
                                            
                                    st.markdown("---")
                                    
                                    # --- BOTONES DE ACCIÓN GLOBAL DE AUDITORÍA ---
                                    col_accion1, col_accion2 = st.columns(2)
                                    
                                    with col_accion1:
                                        if st.button("🏁 Finalizar y Archivar Auditoría", type="primary", use_container_width=True, key="btn_cerrar_inv"):
                                            db.table("INVENTARIOS_CABECERA").update({"estado": "REVISADO"}).eq("id", id_cabecera).execute()
                                            st.success("✅ Inventario cerrado correctamente.")
                                            st.rerun()
                                            
                                    with col_accion2:
                                        with st.popover("🗑️ Eliminar Auditoría", use_container_width=True):
                                            st.warning("⚠️ **¿Está seguro de eliminar este recuento?**")
                                            st.caption("Esta acción borrará permanentemente la cabecera y sus detalles.")
                                            
                                            if st.button("💥 Confirmar y Borrar", type="primary", use_container_width=True, key="btn_del_inv_confirm"):
                                                try:
                                                    # 1. Borrar detalle
                                                    db.table("INVENTARIOS_DETALLE").delete().eq("inventario_id", id_cabecera).execute()
                                                    
                                                    # 2. Borrar cabecera
                                                    db.table("INVENTARIOS_CABECERA").delete().eq("id", id_cabecera).execute()
                                                    
                                                    st.success("🗑️ Registro de inventario eliminado.")
                                                    st.rerun()
                                                except Exception as e:
                                                    st.error(f"Error al eliminar auditoría: {e}")
    
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
                        rubro_nuevo = st.selectbox("Rubro", options=LISTA_RUBROS if 'LISTA_RUBROS' in globals() else ["General"])
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
                            "ID_Proveedor": None,
                            "Stock_Min": 0,
                            "Stock_Max": 0,
                            "Imagen": None
                        }
                        
                        try:
                            db.table("PRODUCTOS").insert(nuevo_prod).execute()
                            st.success(f"🎉 ¡Producto '{nombre_nuevo}' guardado!")
                            if 'df_prod' in st.session_state: del st.session_state['df_prod']
                            st.rerun()
                        except Exception as e:
                            st.error(f"Error técnico: {e}")
    
            # --- PESTAÑA MODIFICAR ---
            with tab_modificar:
                st.subheader("✏️ Modificar Producto Completo")
            
                # --- BOTÓN DE MANTENIMIENTO DE ESTADOS ---
                with st.expander("⚙️ Herramientas de Mantenimiento"):
                    st.caption("Inactiva productos sin stock y con Stock Mínimo en 0/NULL. Reactiva los que recuperen stock.")
                    if st.button("⚡ Actualizar Estados de Productos", use_container_width=True):
                        with st.spinner("Procesando estados en Supabase..."):
                            actualizar_estados_productos(db)
                            st.rerun()
                
                if not st.session_state.df_prod.empty:
                    opciones = (st.session_state.df_prod['ID_Producto'].astype(str) + " - " + st.session_state.df_prod['Nombre']).tolist()
                    prod_sel = st.selectbox("Seleccionar producto:", [""] + opciones)
                    
                    def get_safe(key, fila, default=0, is_float=False):
                        val = fila.get(key)
                        if val is None or (isinstance(val, float) and pd.isna(val)) or str(val).strip() == "":
                            return default
                        return float(val) if is_float else int(float(val))
                    
                    if prod_sel:
                        id_sel = prod_sel.split(" - ")[0]
                        fila = st.session_state.df_prod[st.session_state.df_prod['ID_Producto'].astype(str) == id_sel].iloc[0]
                        
                        val_stk = get_safe('Stock_Actual', fila, 0)
                        val_min = get_safe('Stock_Min', fila, 0)
                        val_max = get_safe('Stock_Max', fila, 0)
                        val_cos = get_safe('Precio_Costo', fila, 0.0, is_float=True)
                        
                        prov_actual = fila.get('ID_Proveedor')
                        if prov_actual is None or pd.isna(prov_actual):
                            prov_actual = "" 
                        
                        with st.form("form_mod_completo"):
                            c1, c2, c3 = st.columns(3)
                            with c1:
                                n_nom = st.text_input("Nombre", value=str(fila.get('Nombre', '')))
                                rubros_lista = LISTA_RUBROS if 'LISTA_RUBROS' in globals() else ["General"]
                                idx_rubro = rubros_lista.index(fila.get('Rubro')) if fila.get('Rubro') in rubros_lista else 0
                                n_rub = st.selectbox("Rubro", options=rubros_lista, index=idx_rubro)
                                n_mar = st.text_input("Marca", value=str(fila.get('Marca', '')))
                                idx_prov = lista_proveedores.index(prov_actual) if prov_actual in lista_proveedores else 0
                                n_prov = st.selectbox("Proveedor", options=lista_proveedores, index=idx_prov)
                            with c2:
                                n_stk = st.number_input("Stock Actual", value=val_stk)
                                n_min = st.number_input("Stock Min", value=val_min)
                                n_max = st.number_input("Stock Max", value=val_max)
                                n_img = st.text_input("URL Imagen", value=str(fila.get('Imagen', '')))
                            with c3:
                                n_cos = st.number_input("Costo", value=get_safe('Precio_Costo', fila, 0.0, True), format="%.2f")
                                n_p1 = st.number_input("Precio 1", value=get_safe('Precio_1', fila, 0.0, True), format="%.2f")
                                n_p2 = st.number_input("Precio 2", value=get_safe('Precio_2', fila, 0.0, True), format="%.2f")
                                n_p3 = st.number_input("Precio 3", value=get_safe('Precio_3', fila, 0.0, True), format="%.2f")
                                n_p4 = st.number_input("Precio 4", value=get_safe('Precio_4', fila, 0.0, True), format="%.2f")
                                n_p5 = st.number_input("Precio 5", value=get_safe('Precio_5', fila, 0.0, True), format="%.2f")
                            
                            if st.form_submit_button("✅ Guardar Todos los Cambios"):
                                def clean_text(val):
                                    if val is None or val == "" or str(val).lower() == "none":
                                        return None
                                    return str(val)
                    
                                def clean_num(val, is_float=False):
                                    try:
                                        if val in [None, '', 'None']: return 0.0 if is_float else 0
                                        return float(val) if is_float else int(val)
                                    except:
                                        return 0.0 if is_float else 0
                    
                                stock_nuevo = clean_num(n_stk)
                                nombre_producto_nuevo = str(n_nom) if n_nom else "Sin nombre"
            
                                datos_update = {
                                    "Nombre": nombre_producto_nuevo,
                                    "Rubro": clean_text(n_rub),
                                    "Marca": clean_text(n_mar),
                                    "ID_Proveedor": clean_num(n_prov),
                                    "Stock_Actual": stock_nuevo,
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
                                    # 1. Actualización del producto
                                    db.table("PRODUCTOS").update(datos_update).eq("ID_Producto", id_sel).execute()
                                    
                                    # 2. Obtenemos el nombre del usuario activo
                                    usuario_activo = st.session_state.get('usuario_nombre') or st.session_state.get('usuario_actual', 'Martin')
            
                                    # 3. VERIFICAMOS Y REGISTRAMOS CAMBIO EN STOCK (KARDEX)
                                    diferencia_stock = stock_nuevo - val_stk
                                    if diferencia_stock != 0:
                                        tipo_mov = "AJUSTE POSITIVO" if diferencia_stock > 0 else "AJUSTE NEGATIVO"
                                        db.table("MOVIMIENTOS_STOCK").insert({
                                            "id_producto": str(id_sel),
                                            "nombre_producto": nombre_producto_nuevo,
                                            "tipo_movimiento": tipo_mov,
                                            "cantidad": diferencia_stock,  # Puede ser positivo o negativo
                                            "stock_anterior": int(val_stk),
                                            "stock_nuevo": int(stock_nuevo),
                                            "origen_referencia": "Ajuste Manual en Edición de Producto",
                                            "usuario": str(usuario_activo)
                                        }).execute()
            
                                    # 4. Log de Auditoría
                                    log_auditoria(
                                        tabla="PRODUCTOS",
                                        accion="UPDATE",
                                        id_entidad=id_sel,
                                        detalles={
                                            "motivo": "Modificación manual desde formulario de edición",
                                            "valores_finales": datos_update
                                        },
                                        usuario=usuario_activo
                                    )
                    
                                    st.success("¡Producto actualizado exitosamente!")
                                    if 'df_prod' in st.session_state: del st.session_state['df_prod']
                                    st.rerun()
                                except Exception as e:
                                    st.error(f"Error al actualizar en Supabase: {e}")
                else:
                    st.info("No hay productos para modificar.")
    
            # --- PESTAÑA IMPORTAR ---
            with tab_importar:
                st.subheader("📥 Importación Masiva de Productos")
                st.markdown("Subí un archivo CSV (UTF-8 o Latin-1) o Excel.")
                
                archivo = st.file_uploader("Seleccioná el archivo", type=['csv', 'xlsx'])
    
                if archivo and st.button("🚀 Procesar e Importar"):
                    try:
                        if archivo.name.endswith('.csv'):
                            try:
                                df_i = pd.read_csv(archivo, encoding='utf-8')
                            except UnicodeDecodeError:
                                archivo.seek(0)
                                df_i = pd.read_csv(archivo, encoding='latin-1')
                        else:
                            df_i = pd.read_excel(archivo)
    
                        df_i = df_i.dropna(axis=1, how='all')
                        df_i['ID_Producto'] = df_i['ID_Producto'].astype(str)
                        
                        for col in df_i.columns:
                            if col in ['Stock_Actual', 'Stock_Min', 'Stock_Max']:
                                df_i[col] = pd.to_numeric(df_i[col], errors='coerce').fillna(0).astype(int)
                            elif 'Precio' in col:
                                df_i[col] = pd.to_numeric(df_i[col], errors='coerce').fillna(0.0)
                            else:
                                df_i[col] = df_i[col].fillna('')
    
                        data_to_upsert = df_i.to_dict(orient='records')
                        db.table("PRODUCTOS").upsert(data_to_upsert).execute()
    
                        st.success(f"✅ Importación exitosa: {len(df_i)} productos procesados.")
                        st.balloons()
                        if 'df_prod' in st.session_state: del st.session_state['df_prod']
                        st.rerun()
    
                    except Exception as e:
                        st.error(f"Error al procesar el archivo: {e}")
    
            # =====================================================================
            # --- PESTAÑA HISTÓRICO DE MOVIMIENTOS (KARDEX) (Solo Administrador) ---
            # =====================================================================
            with tab_historico:
                st.subheader("📜 Histórico de Movimientos de Inventario (Kardex)")
                
                # 1. Buscador opcional de producto
                if 'df_prod' in st.session_state and not st.session_state.df_prod.empty:
                    opciones_kardex = (
                        st.session_state.df_prod['ID_Producto'].astype(str) + " - " + st.session_state.df_prod['Nombre']
                    ).tolist()
                else:
                    opciones_kardex = []
                    
                prod_kardex_sel = st.selectbox(
                    "Filtrar por Producto (Dejar en 'Todos' para auditoría general):", 
                    ["Todos los Productos"] + opciones_kardex, 
                    key="kardex_prod_sel"
                )
                
                # 2. Filtro Rango de Fechas
                col_f1, col_f2 = st.columns(2)
                fecha_desde = col_f1.date_input("Fecha Desde:", value=datetime.now() - timedelta(days=30))
                fecha_hasta = col_f2.date_input("Fecha Hasta:", value=datetime.now())
            
                str_f_desde = datetime.combine(fecha_desde, datetime.min.time()).isoformat()
                str_f_hasta = datetime.combine(fecha_hasta, datetime.max.time()).isoformat()
            
                with st.spinner("Cargando movimientos de stock desde la base de datos..."):
                    try:
                        # Consulta a la tabla MOVIMIENTOS_STOCK
                        query = db.table("MOVIMIENTOS_STOCK").select("*")\
                            .gte("fecha", str_f_desde)\
                            .lte("fecha", str_f_hasta)
            
                        # Filtro opcional por producto
                        if prod_kardex_sel != "Todos los Productos":
                            id_kardex = prod_kardex_sel.split(" - ")[0]
                            query = query.eq("id_producto", str(id_kardex))
            
                        # Ordenar por fecha descendente
                        res_mov = query.order("fecha", desc=True).execute().data
            
                        if res_mov:
                            df_kardex = pd.DataFrame(res_mov)
            
                            # Formatear la columna 'fecha' a solo día/mes/año
                            df_kardex["Fecha_Corta"] = pd.to_datetime(
                                df_kardex["fecha"], errors='coerce'
                            ).dt.strftime("%d/%m/%Y")
            
                            # Mapeo y orden exacto de columnas solicitado:
                            # Fecha | ID Producto | Producto | Cantidad | Tipo Movimiento | Stock Ant. | Stock Nuevo | Origen / Referencia | Usuario
                            columnas_mostrar = {
                                "Fecha_Corta": "Fecha",
                                "id_producto": "ID Producto",
                                "nombre_producto": "Producto",
                                "cantidad": "Cantidad",
                                "tipo_movimiento": "Tipo Movimiento",
                                "stock_anterior": "Stock Ant.",
                                "stock_nuevo": "Stock Nuevo",
                                "origen_referencia": "Origen / Referencia",
                                "usuario": "Usuario"
                            }
            
                            # Seleccionar solo las columnas en el orden estricto
                            cols_existentes = {k: v for k, v in columnas_mostrar.items() if k in df_kardex.columns}
                            df_vista = df_kardex[list(cols_existentes.keys())].rename(columns=cols_existentes)
            
                            st.dataframe(
                                df_vista, 
                                use_container_width=True, 
                                hide_index=True
                            )
                        else:
                            st.info("ℹ️ No se registraron movimientos de stock para los criterios de búsqueda seleccionados.")
            
                    except Exception as e:
                        st.error(f"Error al obtener el historial de movimientos de stock: {e}")
    
    # =====================================================================
    # MODULO: 📦 STOCK
    # =====================================================================
    elif menu == "📦 Stock":
        st.header("📊 Gestión y Análisis de Stock")
        
        # Carga base de datos de productos y proveedores
        df_prod = pd.DataFrame(db.table("PRODUCTOS").select("*").execute().data)
        df_prov = pd.DataFrame(db.table("PROVEEDORES").select("*").execute().data)
        
        # Definición de pestañas
        tab_listado, tab_priorizacion = st.tabs([
            "📋 Listado y Buscador General", 
            "🎯 Priorización de Compras (ABC + Urgencia)"
        ])
        
        # -----------------------------------------------------------------
        # PESTAÑA 1: LISTADO Y BUSCADOR GENERAL (CÓDIGO ORIGINAL)
        # -----------------------------------------------------------------
        with tab_listado:
            st.subheader("🔍 Buscar Artículos")
            
            mostrar_inactivos = st.checkbox("👁️ Mostrar productos INACTIVOS", value=False, key="chk_inactivos_stock")
            
            busqueda_texto = st.text_input(
                "Escriba para filtrar por nombre o código:", 
                placeholder="Ej: babydry, pampers, 779...",
                key="busqueda_stock"
            )
            
            c1, c2, c3 = st.columns(3)
            rubros = ["Todos"] + [r for r in df_prod['Rubro'].dropna().unique().tolist() if r]
            marcas = ["Todos"] + [m for m in df_prod['Marca'].dropna().unique().tolist() if m]
            provs = ["Todos"] + [p for p in df_prov['Razon_Social'].dropna().unique().tolist() if p]
            
            filtro_rubro = c1.selectbox("Filtrar por Rubro", rubros, key="filtro_rubro_stock")
            filtro_marca = c2.selectbox("Filtrar por Marca", marcas, key="filtro_marca_stock")
            filtro_prov = c3.selectbox("Filtrar por Proveedor", provs, key="filtro_prov_stock")
            
            df_f = df_prod.copy()
            
            if 'Estado' in df_f.columns and not mostrar_inactivos:
                df_f = df_f[df_f['Estado'] != 'INACTIVO']
    
            if busqueda_texto:
                busqueda_texto = busqueda_texto.lower()
                mask = df_f['Nombre'].str.lower().str.contains(busqueda_texto, na=False) | \
                       df_f['ID_Producto'].astype(str).str.lower().str.contains(busqueda_texto, na=False)
                df_f = df_f[mask]
            
            if filtro_rubro != "Todos":
                df_f = df_f[df_f['Rubro'] == filtro_rubro]
                
            if filtro_marca != "Todos":
                df_f = df_f[df_f['Marca'] == filtro_marca]
                
            if filtro_prov != "Todos":
                if 'Proveedor' in df_f.columns:
                    df_f = df_f[df_f['Proveedor'] == filtro_prov]
                elif 'ID_Proveedor' in df_f.columns:
                    prov_sel = df_prov[df_prov['Razon_Social'] == filtro_prov]
                    if not prov_sel.empty:
                        id_prov_buscado = prov_sel.iloc[0]['ID_Proveedor']
                        df_f = df_f[df_f['ID_Proveedor'] == id_prov_buscado]
            
            df_f['Stock_Actual'] = pd.to_numeric(df_f['Stock_Actual'], errors='coerce').fillna(0)
            df_f['Stock_Min'] = pd.to_numeric(df_f['Stock_Min'], errors='coerce').fillna(0)
            df_f['Stock_Max'] = pd.to_numeric(df_f['Stock_Max'], errors='coerce').fillna(0)
    
            df_f['Faltante_Min'] = (df_f['Stock_Min'] - df_f['Stock_Actual']).clip(lower=0)
            df_f['Faltante_Max'] = (df_f['Stock_Max'] - df_f['Stock_Actual']).clip(lower=0)
            
            df_f['Pedir'] = False
            cols_mostrar = ['Pedir', 'Nombre', 'Stock_Actual', 'Stock_Min', 'Stock_Max', 'Faltante_Min', 'Faltante_Max']
            cols_presentes = [c for c in cols_mostrar if c in df_f.columns]
    
            st.caption("💡 Tildá únicamente los artículos que querés incluir en el mensaje de WhatsApp.")
    
            df_editado = st.data_editor(
                df_f[cols_presentes],
                column_config={
                    "Pedir": st.column_config.CheckboxColumn(
                        "📱 Pedir",
                        help="Marcar para incluir en el mensaje de WhatsApp",
                        default=False
                    )
                },
                disabled=['Nombre', 'Stock_Actual', 'Stock_Min', 'Stock_Max', 'Faltante_Min', 'Faltante_Max'],
                hide_index=True,
                use_container_width=True,
                key="editor_tabla_stock"
            )
    
            col_exp1, col_exp2 = st.columns(2)
            
            import io
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                df_f.drop(columns=['Pedir'], errors='ignore').to_excel(writer, index=False)
            
            col_exp1.download_button(
                label="📥 Exportar a Excel", 
                data=buffer.getvalue(), 
                file_name="reporte_stock.xlsx", 
                mime="application/vnd.ms-excel"
            )
            
            if col_exp2.button("💬 Generar Resumen para WhatsApp", key="btn_wsp_p1"):
                seleccionados = df_editado[df_editado['Pedir'] == True]
                
                if seleccionados.empty:
                    st.warning("⚠️ No has tildado ningún producto en la columna '📱 Pedir'. Seleccioná al menos uno en la tabla.")
                else:
                    mensaje = "🛒 *Pedido Sugerido (Faltantes a Mínimo):*\n"
                    for _, item in seleccionados.iterrows():
                        cant_pedir = int(item['Faltante_Min']) if item['Faltante_Min'] > 0 else 1
                        mensaje += f"- {item['Nombre']}: Faltan {cant_pedir}\n"
                    
                    st.text_area("Copia este mensaje para WhatsApp:", value=mensaje, height=200, key="txt_wsp_p1")
    
            st.divider()
            if st.button("🔄 RECALCULAR STOCK MÍNIMO/MÁXIMO", key="btn_recalc_p1"):
                ids_a_recalcular = df_f['ID_Producto'].astype(str).tolist() if 'ID_Producto' in df_f.columns else []
                cant_prods = len(ids_a_recalcular)
                with st.spinner(f"Calculando rotación de 60 días para {cant_prods} producto(s)..."):
                    if 'calcular_y_actualizar_stock_automatico' in globals() and calcular_y_actualizar_stock_automatico(ids_filtrados=ids_a_recalcular):
                        st.success(f"¡Stock mínimo y máximo actualizado para {cant_prods} productos!")
                        if 'df_prod' in st.session_state:
                            del st.session_state['df_prod']
                        st.rerun()
    
        # -----------------------------------------------------------------
        # PESTAÑA 2: PRIORIZACIÓN DE COMPRAS (ANÁLISIS ABC + URGENCIA)
        # -----------------------------------------------------------------
        with tab_priorizacion:
            st.subheader("🎯 Ranking de Priorización de Compras")
            st.caption("Clasifica productos según su relevancia comercial (ABC) y urgencia por faltante de stock mínimo.")
            
            # Buscador de texto combinado
            busqueda_abc = st.text_input(
                "🔍 Buscar por nombre o código en el ranking:", 
                placeholder="Ej: pampers, babydry, 779...",
                key="busqueda_texto_abc"
            )
        
            # Filtros de menú desplegable
            fa1, fa2, fa3 = st.columns(3)
            p_rubro = fa1.selectbox("Rubro", rubros, key="p_rubro_abc")
            p_marca = fa2.selectbox("Marca", marcas, key="p_marca_abc")
            p_prov = fa3.selectbox("Proveedor", provs, key="p_prov_abc")
            
            dias_analisis = st.slider("Días de historia de ventas para scoring:", min_value=15, max_value=90, value=60, step=15, key="slider_dias_abc")
        
            # Cargar detalles de ventas
            res_vd = db.table("VENTAS_DETALLE").select("ID_Producto, Cantidad, Subtotal, Precio_Costo_Unitario").execute().data
            df_vd = pd.DataFrame(res_vd) if res_vd else pd.DataFrame()
        
            df_ranking = df_prod.copy()
            
            # 1. Filtro: Productos Inactivos
            if 'Estado' in df_ranking.columns:
                df_ranking = df_ranking[df_ranking['Estado'] != 'INACTIVO']
        
            # 2. Filtro: Solo productos stockeables (Es_Stockeable == True)
            if 'Es_Stockeable' in df_ranking.columns:
                df_ranking = df_ranking[df_ranking['Es_Stockeable'] == True]
        
            # 3. Filtro de negocio: Rubro LECHE solo con presentaciones bulto/pack (" x12", " x24", " x30", " x400", " x800", " x1000", " x1200")
            if 'Rubro' in df_ranking.columns and 'Nombre' in df_ranking.columns:
                es_leche = df_ranking['Rubro'].astype(str).str.upper() == 'LECHE'
                contiene_bulto = df_ranking['Nombre'].astype(str).str.contains(' x12| x24| x30| x400| x800| x1000| x1200', case=False, na=False)
                df_ranking = df_ranking[~es_leche | contiene_bulto]
        
            # Limpieza de numéricos
            df_ranking['Stock_Actual'] = pd.to_numeric(df_ranking['Stock_Actual'], errors='coerce').fillna(0)
            df_ranking['Stock_Min'] = pd.to_numeric(df_ranking['Stock_Min'], errors='coerce').fillna(0)
            df_ranking['Stock_Max'] = pd.to_numeric(df_ranking['Stock_Max'], errors='coerce').fillna(0)
        
            # 4. Aplicación del Buscador por Texto
            if busqueda_abc:
                b_txt = busqueda_abc.lower()
                mask_abc = df_ranking['Nombre'].astype(str).str.lower().str.contains(b_txt, na=False) | \
                           df_ranking['ID_Producto'].astype(str).str.lower().str.contains(b_txt, na=False)
                df_ranking = df_ranking[mask_abc]
        
            # 5. Aplicación de filtros interactivos del usuario (Rubro, Marca, Proveedor)
            if p_rubro != "Todos":
                df_ranking = df_ranking[df_ranking['Rubro'] == p_rubro]
            if p_marca != "Todos":
                df_ranking = df_ranking[df_ranking['Marca'] == p_marca]
            if p_prov != "Todos":
                if 'Proveedor' in df_ranking.columns:
                    df_ranking = df_ranking[df_ranking['Proveedor'] == p_prov]
                elif 'ID_Proveedor' in df_ranking.columns:
                    prov_sel_abc = df_prov[df_prov['Razon_Social'] == p_prov]
                    if not prov_sel_abc.empty:
                        id_p_abc = prov_sel_abc.iloc[0]['ID_Proveedor']
                        df_ranking = df_ranking[df_ranking['ID_Proveedor'] == id_p_abc]
        
            if df_ranking.empty:
                st.info("No se encontraron productos con los criterios, texto y filtros seleccionados.")
            else:
                # Procesar ventas si existen registros
                if not df_vd.empty:
                    df_vd['Cantidad'] = pd.to_numeric(df_vd['Cantidad'], errors='coerce').fillna(0)
                    df_vd['Subtotal'] = pd.to_numeric(df_vd['Subtotal'], errors='coerce').fillna(0)
                    df_vd['Precio_Costo_Unitario'] = pd.to_numeric(df_vd['Precio_Costo_Unitario'], errors='coerce').fillna(0)
                    df_vd['Ganancia_Real'] = df_vd['Subtotal'] - (df_vd['Cantidad'] * df_vd['Precio_Costo_Unitario'])
        
                    # Agrupado por producto
                    agrupado = df_vd.groupby('ID_Producto').agg({
                        'Cantidad': 'sum',
                        'Subtotal': 'sum',
                        'Ganancia_Real': 'sum'
                    }).reset_index().rename(columns={
                        'Cantidad': 'Rotacion_Unid',
                        'Subtotal': 'Facturacion_Total',
                        'Ganancia_Real': 'Ganancia_Total'
                    })
                    
                    df_ranking['ID_Producto'] = df_ranking['ID_Producto'].astype(str)
                    agrupado['ID_Producto'] = agrupado['ID_Producto'].astype(str)
                    
                    df_ranking = pd.merge(df_ranking, agrupado, on='ID_Producto', how='left')
                else:
                    df_ranking['Rotacion_Unid'] = 0
                    df_ranking['Facturacion_Total'] = 0.0
                    df_ranking['Ganancia_Total'] = 0.0
        
                df_ranking['Rotacion_Unid'] = df_ranking['Rotacion_Unid'].fillna(0)
                df_ranking['Facturacion_Total'] = df_ranking['Facturacion_Total'].fillna(0.0)
                df_ranking['Ganancia_Total'] = df_ranking['Ganancia_Total'].fillna(0.0)
        
                # Normalización y Cálculo de Score Multi-criterio
                max_rot = df_ranking['Rotacion_Unid'].max()
                max_fact = df_ranking['Facturacion_Total'].max()
                max_gan = df_ranking['Ganancia_Total'].max()
        
                norm_rot = (df_ranking['Rotacion_Unid'] / max_rot * 100) if max_rot > 0 else 0
                norm_fact = (df_ranking['Facturacion_Total'] / max_fact * 100) if max_fact > 0 else 0
                norm_gan = (df_ranking['Ganancia_Total'] / max_gan * 100) if max_gan > 0 else 0
        
                # Score = 40% Facturación + 35% Rotación + 25% Ganancia
                df_ranking['Score_Comercial'] = (0.40 * norm_fact) + (0.35 * norm_rot) + (0.25 * norm_gan)
        
                # Asignación de Categoría ABC
                def asignar_categoria(score, p70, p30):
                    if score >= p70 and score > 0:
                        return "🟢 Categoría A"
                    elif score >= p30 and score > 0:
                        return "🟡 Categoría B"
                    else:
                        return "🔴 Categoría C"
        
                p70 = df_ranking['Score_Comercial'].quantile(0.70)
                p30 = df_ranking['Score_Comercial'].quantile(0.30)
                
                df_ranking['Categoria_ABC'] = df_ranking['Score_Comercial'].apply(lambda x: asignar_categoria(x, p70, p30))
        
                # Cálculo de Urgencia y Faltantes
                df_ranking['Faltante_Min'] = (df_ranking['Stock_Min'] - df_ranking['Stock_Actual']).clip(lower=0)
                
                def calc_urgencia(row):
                    if row['Stock_Min'] > 0 and row['Faltante_Min'] > 0:
                        return (row['Faltante_Min'] / row['Stock_Min']) * 100
                    return 0.0
        
                df_ranking['Urgencia_%'] = df_ranking.apply(calc_urgencia, axis=1)
        
                # Ordenamiento: Primero Categoría ABC (A->B->C) y luego Urgencia descendente
                df_ranking['Orden_Cat'] = df_ranking['Categoria_ABC'].map({
                    "🟢 Categoría A": 1,
                    "🟡 Categoría B": 2,
                    "🔴 Categoría C": 3
                })
                
                df_ranking = df_ranking.sort_values(
                    by=['Orden_Cat', 'Urgencia_%', 'Score_Comercial'], 
                    ascending=[True, False, False]
                ).reset_index(drop=True)
        
                # Selección por defecto: Tildar automáticamente los productos con Urgencia > 0%
                df_ranking['Pedir'] = df_ranking['Urgencia_%'] > 0
                
                cols_abc_mostrar = ['Pedir', 'Categoria_ABC', 'Nombre', 'Urgencia_%', 'Stock_Actual', 'Stock_Min', 'Faltante_Min', 'Score_Comercial']
                cols_abc_presentes = [c for c in cols_abc_mostrar if c in df_ranking.columns]
        
                st.markdown("---")
                st.caption("📌 Los artículos con stock por debajo del mínimo vienen tildados automáticamente. Podés destildar o sumar los que desees.")
        
                df_abc_editado = st.data_editor(
                    df_ranking[cols_abc_presentes],
                    column_config={
                        "Pedir": st.column_config.CheckboxColumn("📱 Pedir", default=False),
                        "Categoria_ABC": st.column_config.TextColumn("Categoría"),
                        "Nombre": st.column_config.TextColumn("Producto"),
                        "Urgencia_%": st.column_config.NumberColumn("Urgencia (%)", format="%.0f%%"),
                        "Stock_Actual": st.column_config.NumberColumn("Stock Act."),
                        "Stock_Min": st.column_config.NumberColumn("Mínimo"),
                        "Faltante_Min": st.column_config.NumberColumn("Faltante a Mín."),
                        "Score_Comercial": st.column_config.NumberColumn("Score", format="%.1f pts")
                    },
                    disabled=['Categoria_ABC', 'Nombre', 'Urgencia_%', 'Stock_Actual', 'Stock_Min', 'Faltante_Min', 'Score_Comercial'],
                    hide_index=True,
                    use_container_width=True,
                    key="editor_tabla_abc"
                )
        
                col_abc_wsp, col_abc_exp = st.columns(2)
                
                if col_abc_wsp.button("💬 Generar WhatsApp Priorizado", type="primary", key="btn_wsp_abc"):
                    sel_abc = df_abc_editado[df_abc_editado['Pedir'] == True]
                    
                    if sel_abc.empty:
                        st.warning("⚠️ No seleccionaste ningún producto. Tildá las casillas en la columna '📱 Pedir'.")
                    else:
                        msg_abc = ""
                        for _, r in sel_abc.iterrows():
                            cant_comprar = int(r['Faltante_Min']) if r['Faltante_Min'] > 0 else 1
                            
                            rubro_prod = str(r.get('Rubro', '')).strip().upper()
                            
                            if rubro_prod == "LECHE":
                                unid_texto = "fardo" if cant_comprar == 1 else "fardos"
                            else:
                                unid_texto = "unidad" if cant_comprar == 1 else "unidades"
                            
                            msg_abc += f"{cant_comprar} {unid_texto} *{r['Nombre']}*\n"
                        
                        st.text_area("Copiar mensaje para proveedor:", value=msg_abc, height=220, key="txt_area_abc")
        
                # ==========================================
                # 📥 GENERACIÓN DE EXCEL (SOLO URGENCIA > 0%)
                # ==========================================
                df_export_excel = df_ranking[df_ranking['Urgencia_%'] > 0].drop(columns=['Pedir', 'Orden_Cat'], errors='ignore')
        
                if not df_export_excel.empty:
                    buffer_abc = io.BytesIO()
                    with pd.ExcelWriter(buffer_abc, engine='xlsxwriter') as writer_abc:
                        df_export_excel.to_excel(writer_abc, index=False)
                    
                    col_abc_exp.download_button(
                        label=f"📥 Exportar Recomendados ({len(df_export_excel)}) a Excel",
                        data=buffer_abc.getvalue(),
                        file_name="productos_recomendados_compra.xlsx",
                        mime="application/vnd.ms-excel",
                        key="btn_exp_excel_abc"
                    )
                else:
                    col_abc_exp.info("🟢 No hay productos con urgencia mayor al 0% para exportar.")

    # =====================================================================
    # MODULO: 🚚 PROVEEDORES
    # =====================================================================
    elif menu == "🚚 Proveedores":
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
            st.dataframe(df_filtrado, width='stretch')
            
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
        # Layout superior idéntico al Punto de Venta
        col_t1, col_t2 = st.columns([4, 1])
        col_t1.header("📦 Registro de Compras (Entrada de Mercadería)")
        
        if col_t2.button("🧹 Limpiar Todo", type="secondary", width='stretch'):
            resetear_compras()

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
                
                if not df_hist.empty:
                    # Aseguramos el formato adecuado para las columnas necesarias
                    if 'Fecha' in df_hist.columns:
                        df_hist['Fecha_dt'] = pd.to_datetime(df_hist['Fecha'], errors='coerce')
                    else:
                        df_hist['Fecha_dt'] = pd.NaT
        
                    df_hist['Total_Compra_num'] = pd.to_numeric(df_hist['Total_Compra'], errors='coerce').fillna(0)
        
                    # ==========================================
                    # 🔍 SECCIÓN DE FILTROS (EXPANDER)
                    # ==========================================
                    with st.expander("🔍 Filtros de Búsqueda Avanzada", expanded=False):
                        col_f1, col_f2, col_f3 = st.columns(3)
                        
                        with col_f1:
                            filtro_id = st.text_input("ID Compra contiene:", value="")
                            
                            # Filtro de Rango de Fechas
                            min_date = df_hist['Fecha_dt'].min()
                            max_date = df_hist['Fecha_dt'].max()
                            
                            if pd.notna(min_date) and pd.notna(max_date):
                                filtro_fecha = st.date_input(
                                    "Rango de Fechas:",
                                    value=(min_date.date(), max_date.date()),
                                    key="f_fecha_facturas"
                                )
                            else:
                                filtro_fecha = None
        
                        with col_f2:
                            proveedores_unique = ["Todos"] + list(df_hist['Proveedor'].dropna().unique())
                            filtro_prov = st.selectbox("Proveedor:", proveedores_unique)
                            
                            filtro_nro_fact = st.text_input("Nro Factura contiene:", value="")
        
                        with col_f3:
                            metodos_unique = ["Todos"] + list(df_hist['Metodo_Pago'].dropna().unique())
                            filtro_metodo = st.selectbox("Método de Pago:", metodos_unique)
                            
                            val_min_tot = float(df_hist['Total_Compra_num'].min())
                            val_max_tot = float(df_hist['Total_Compra_num'].max())
                            
                            if val_min_tot < val_max_tot:
                                filtro_total = st.slider(
                                    "Rango de Total ($):",
                                    min_value=val_min_tot,
                                    max_value=val_max_tot,
                                    value=(val_min_tot, val_max_tot)
                                )
                            else:
                                filtro_total = (val_min_tot, val_max_tot)
        
                    # ==========================================
                    # 🧹 APLICACIÓN DE FILTROS AL DATAFRAME
                    # ==========================================
                    df_filtrado = df_hist.copy()
        
                    # 1. Filtro ID
                    if filtro_id.strip():
                        df_filtrado = df_filtrado[df_filtrado['ID_Compra'].astype(str).str.contains(filtro_id.strip(), case=False, na=False)]
        
                    # 2. Filtro Fecha
                    if filtro_fecha and isinstance(filtro_fecha, (tuple, list)) and len(filtro_fecha) == 2:
                        f_inicio, f_fin = filtro_fecha
                        df_filtrado = df_filtrado[
                            (df_filtrado['Fecha_dt'].dt.date >= f_inicio) & 
                            (df_filtrado['Fecha_dt'].dt.date <= f_fin)
                        ]
        
                    # 3. Filtro Proveedor
                    if filtro_prov != "Todos":
                        df_filtrado = df_filtrado[df_filtrado['Proveedor'] == filtro_prov]
        
                    # 4. Filtro Nro Factura
                    if filtro_nro_fact.strip():
                        df_filtrado = df_filtrado[df_filtrado['Nro_Factura'].astype(str).str.contains(filtro_nro_fact.strip(), case=False, na=False)]
        
                    # 5. Filtro Método de Pago
                    if filtro_metodo != "Todos":
                        df_filtrado = df_filtrado[df_filtrado['Metodo_Pago'] == filtro_metodo]
        
                    # 6. Filtro Total
                    df_filtrado = df_filtrado[
                        (df_filtrado['Total_Compra_num'] >= filtro_total[0]) & 
                        (df_filtrado['Total_Compra_num'] <= filtro_total[1])
                    ]
        
                    # ==========================================
                    # 📊 RENDERIZADO DE LA TABLA
                    # ==========================================
                    st.caption(f"Mostrando **{len(df_filtrado)}** de **{len(df_hist)}** facturas encontradas.")
        
                    if not df_filtrado.empty:
                        # Encabezados estilo tabla
                        h1, h2, h3, h4, h5, h6, h7 = st.columns([2, 2, 2, 2, 2, 2, 2])
                        h1.markdown("**ID Compra**")
                        h2.markdown("**Fecha**")
                        h3.markdown("**Proveedor**")
                        h4.markdown("**Nro Factura**")
                        h5.markdown("**Método Pago**")
                        h6.markdown("**Total**")
                        h7.markdown("**Acción**")
                        st.divider()
        
                        # Renderizamos cada compra filtrada como una fila interactiva
                        for _, row in df_filtrado.iterrows():
                            id_compra = str(row['ID_Compra'])
                            
                            with st.container():
                                c1, c2, c3, c4, c5, c6, c7 = st.columns([2, 2, 2, 2, 2, 2, 2])
                                
                                c1.write(f"`{id_compra}`")
                                c2.write(str(row.get('Fecha', '')))
                                c3.write(str(row.get('Proveedor', '')))
                                c4.write(str(row.get('Nro_Factura', '')))
                                c5.write(str(row.get('Metodo_Pago', '')))
                                c6.write(f"${float(row.get('Total_Compra', 0) or 0):,.2f}")
                                
                                # Botón directo al lado de la fila
                                if c7.button("📲 Copiar", key=f"btn_copiar_{id_compra}"):
                                    try:
                                        # Obtenemos productos de la factura
                                        det_compra = db.table("DETALLE_COMPRAS").select("ID_Producto").eq("ID_Compra", id_compra).execute().data
                                        
                                        if det_compra:
                                            ids_prods = [str(x['ID_Producto']) for x in det_compra]
                                            
                                            # Obtenemos catálogo
                                            prods_db = db.table("PRODUCTOS").select("ID_Producto, Nombre, Precio_1, Precio_2, Precio_3").in_("ID_Producto", ids_prods).execute().data
                                            
                                            if prods_db:
                                                texto_wsp = f"📦 *¡INGRESO DE MERCADERÍA!* 📦\n\n"
                                                for p in prods_db:
                                                    nom = p.get('Nombre', 'Producto Sin Nombre')
                                                    p1 = float(p.get('Precio_1', 0) or 0)
                                                    p2 = float(p.get('Precio_2', 0) or 0)
                                                    p3 = float(p.get('Precio_3', 0) or 0)
                                                    
                                                    texto_wsp += f"🔹 *{nom}*\n"
                                                    texto_wsp += f"   • P1: ${p1:,.0f} | P2: ${p2:,.0f} | P3: ${p3:,.0f}\n\n"
                                                
                                                st.session_state["texto_vendedor_activo"] = texto_wsp
                                                st.session_state["factura_activa_id"] = id_compra
                                            else:
                                                st.error("No se encontraron los productos en el catálogo.")
                                        else:
                                            st.error("Esta factura no tiene detalle de productos asociado.")
                                    except Exception as e:
                                        st.error(f"Error al obtener el detalle: {e}")
        
                        # Si se presionó el botón de alguna fila, mostramos el cuadro copiable abajo
                        if "texto_vendedor_activo" in st.session_state:
                            st.divider()
                            st.success(f"✅ Texto generado para la factura `{st.session_state.get('factura_activa_id')}`:")
                            st.text_area(
                                "📋 Copiar para WhatsApp (Ctrl + C / Ctrl + V):", 
                                value=st.session_state["texto_vendedor_activo"], 
                                height=220
                            )
                    else:
                        st.warning("No se encontraron facturas que coincidan con los filtros aplicados.")
                else:
                    st.info("No hay facturas.")

            with tab_ordenes:
                df_oc = pd.DataFrame(db.table("ORDENES_COMPRA").select("*").execute().data)
                if not df_oc.empty:
                    opciones_oc = df_oc['ID_Compra'].astype(str) + " - " + df_oc['Proveedor']
                    oc_sel = st.selectbox("¿Qué orden procesar?", ["-- Seleccionar --"] + opciones_oc.tolist())
                    if oc_sel != "-- Seleccionar --":
                        id_oc = oc_sel.split(" - ")[0]
                        det_oc = pd.DataFrame(db.table("DETALLE_ORDENES").select("*").eq("ID_Compra", id_oc).execute().data)
                        st.dataframe(det_oc, width='stretch')
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

        # --- FUNCIÓN ADICIONAL: Agregar al carrito de compras ---
        def _agregar_al_carrito(pm):
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
        
        # --- POP-UP / DIÁLOGO DE CONFIRMACIÓN PARA PRODUCTOS INACTIVOS ---
        @st.dialog("⚠️ Producto Inactivo")
        def confirmar_activacion_producto(pm):
            st.warning(
                f"El producto **{pm['Nombre']}** (Código: `{pm['ID_Producto']}`) "
                "se encuentra marcado como **INACTIVO**."
            )
            st.write("¿Deseas cambiar su estado a **ACTIVO** y agregarlo a la compra?")
            
            col1, col2 = st.columns(2)
            
            with col1:
                if st.button("SÍ, Activar y Agregar", type="primary", use_container_width=True):
                    # 1. Actualizar estado en la base de datos (Supabase)
                    db.table("PRODUCTOS").update({"Estado": "ACTIVO"}).eq("ID_Producto", pm['ID_Producto']).execute()
                    
                    # 2. Actualizar DataFrame en memoria local
                    idx = df_prod[df_prod['ID_Producto'] == pm['ID_Producto']].index
                    if not idx.empty:
                        df_prod.loc[idx, 'Estado'] = 'ACTIVO'
                    
                    # 3. Agregar al carrito de compras
                    _agregar_al_carrito(pm)
                    
                    # 4. Limpiar estado auxiliar y recargar interfaz
                    st.session_state.prod_inactivo_pendiente = None
                    st.rerun()
                    
            with col2:
                if st.button("NO, Cancelar", use_container_width=True):
                    st.session_state.prod_inactivo_pendiente = None
                    st.rerun()
        
        
        # --- FUNCIÓN DE ESCANEO ---
        def procesar_escaneo():
            barcode = st.session_state.txt_barcode
            if barcode != "":
                res = df_prod[df_prod['ID_Producto'].astype(str) == barcode]
                if not res.empty:
                    p = res.iloc[0]
                    # Validar si el estado es INACTIVO (ajusta 'Estado' al nombre exacto de tu columna si difiere)
                    estado_prod = str(p.get('Estado', 'ACTIVO')).upper()
                    if estado_prod == 'INACTIVO':
                        st.session_state.prod_inactivo_pendiente = p
                    else:
                        _agregar_al_carrito(p)
            st.session_state.txt_barcode = ""
        
        
        # --- 3. SECCIÓN: DATOS DE FACTURA ---
        with st.expander("📄 Datos de la Factura Actual", expanded=True):
            df_hist_check = pd.DataFrame(db.table("COMPRAS_CABECERA").select("Nro_Factura").execute().data)
            facturas_existentes = df_hist_check['Nro_Factura'].tolist() if not df_hist_check.empty else []
            
            c1, c1_btn, c2, c3 = st.columns([1, 0.2, 1.5, 1])
            
            with c1:
                prov_sel = st.selectbox("Proveedor", lista_proveedores, 
                                        index=lista_proveedores.index(st.session_state.get("temp_prov", lista_proveedores[0])) 
                                        if st.session_state.get("temp_prov") in lista_proveedores else 0,
                                        key="prov_main")
                fecha_factura = st.date_input("Fecha de Factura")
            
            with c1_btn:
                st.write("")
                st.write("")
                if st.button("➕", help="Agregar nuevo proveedor"):
                    abrir_alta_proveedor_rapida()
            
            with c2:
                f1, _, f2 = st.columns([1, 0.2, 2])
                f_punto = f1.text_input("00000", value=st.session_state.get("temp_punto", ""), max_chars=5)
                f_nro = f2.text_input("00000000", value=st.session_state.get("temp_nro", ""), max_chars=8)
                
                if not f_punto and not f_nro:
                    nro_fact_completo = "00000-00000000"
                else:
                    nro_fact_completo = f"{f_punto.zfill(5)}-{f_nro.zfill(8)}"
                    if nro_fact_completo != "00000-00000000" and nro_fact_completo in facturas_existentes:
                        st.error(f"⚠️ La factura {nro_fact_completo} ya existe.")
                        nro_fact_completo = "DUPLICADA"
                        
            with c3:
                pago_compra = st.selectbox("Método de Pago", ["Contado", "Transferencia", "Cuenta Corriente"], key="pago_main")
        
        
        # --- SECCIÓN: BUSCADOR UNIFICADO ---
        st.subheader("🔍 Añadir Productos a la Compra")
        
        def procesar_seleccion_compra():
            seleccion = st.session_state.prod_compra_key
            if seleccion:
                id_seleccionado = seleccion.split(" - ")[-1]
                pm = df_prod[df_prod['ID_Producto'].astype(str) == id_seleccionado].iloc[0]
                
                # Verificar estado del producto
                estado_prod = str(pm.get('Estado', 'ACTIVO')).upper()
                if estado_prod == 'INACTIVO':
                    st.session_state.prod_inactivo_pendiente = pm
                else:
                    _agregar_al_carrito(pm)
                    
                st.session_state.prod_compra_key = None 
        
        # 2. Preparamos las opciones de búsqueda
        opciones_busqueda = (df_prod['Nombre'] + " - " + df_prod['ID_Producto'].astype(str)).tolist()
        
        # 3. Interfaz única (CON COLUMNAS PARA EL BOTÓN +)
        col_busc, col_btn = st.columns([11, 1], vertical_alignment="bottom")
        
        with col_busc:
            st.selectbox(
                "Buscar por nombre o código", 
                options=opciones_busqueda, 
                index=None,
                placeholder="Escriba para buscar producto o escanee...",
                key="prod_compra_key",
                on_change=procesar_seleccion_compra
            )
            
        with col_btn:
            # Botón disparador del st.dialog
            if st.button("➕", help="Dar de alta un nuevo producto"):
                modal_alta_rapida_producto()
        
        # --- DISPARADOR DEL POP-UP (Inactivos) ---
        # Si hay un producto inactivo pendiente de confirmación, se abre el diálogo modal
        if st.session_state.get("prod_inactivo_pendiente") is not None:
            confirmar_activacion_producto(st.session_state.prod_inactivo_pendiente)
        
        
        # --- MOSTRAR CARRITO Y EDICIÓN DE PRECIOS ---
        if st.session_state.carrito_compra:
            st.subheader("🛒 Detalle de Items")
            for i, item in enumerate(st.session_state.carrito_compra):
                p_info = df_prod[df_prod['ID_Producto'].astype(str) == str(item['id'])]
                rubro = p_info.iloc[0]['Rubro'] if not p_info.empty else "OTROS"
                margenes = MARGENES_RUBROS.get(rubro, [0.3, 0.2, 0.1, 0.05, 0.0])
        
                with st.container(border=True):
                    c_head, c_btn = st.columns([6, 1])
                    nombre_limpio = item['nombre'].strip()
                    c_head.write(f"**{nombre_limpio}** `{item['id']}` | Rubro: {rubro}")
                    
                    if c_btn.button("🗑️ Eliminar", key=f"del_final_{i}"):
                        st.session_state.carrito_compra.pop(i)
                        st.rerun()
        
                    cols = st.columns([1, 1, 5])
                    n_cant = cols[0].number_input("Cant", min_value=1, value=int(item['cantidad']), key=f"q_{i}")
                    n_costo = cols[1].number_input("Costo $", value=float(item['costo']), key=f"p_{i}")
                    
                    cols_p = st.columns(5)
                    nuevos_precios = {}
                    
                    for j in range(5):
                        sugerido = n_costo * (1 + margenes[j])
                        precio_inicial = item.get(f'Precio_{j+1}')
                        if precio_inicial is None:
                            precio_inicial = p_info.iloc[0][f'Precio_{j+1}'] if not p_info.empty else sugerido
                        
                        nuevos_precios[f'Precio_{j+1}'] = cols_p[j].number_input(
                            f"P{j+1} (S:${sugerido:.0f})", 
                            value=float(precio_inicial or 0),
                            key=f"p{j+1}_{i}"
                        )
                    
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
                    usuario_logueado = st.session_state.get("usuario_actual", "Admin")
                    
                    # 1. Guardar Cabecera de Compra
                    db.table("COMPRAS_CABECERA").insert({
                        "ID_Compra": id_c, 
                        "Fecha": str(fecha_factura), 
                        "Proveedor": prov_sel, 
                        "Nro_Factura": nro_fact, 
                        "Metodo_Pago": pago_compra, 
                        "Total_Compra": float(total_final)
                    }).execute()
                    
                    # 2. Guardar Detalle, Actualizar Stock, Precios y registrar KARDEX
                    for item in st.session_state.carrito_compra:
                        id_p_str = str(item['id'])
                        cant_comprada = int(item['cantidad'])
                        
                        data_update = {
                            "Precio_Costo": float(item['costo']),
                            "Precio_1": float(item.get('Precio_1', 0)),
                            "Precio_2": float(item.get('Precio_2', 0)),
                            "Precio_3": float(item.get('Precio_3', 0)),
                            "Precio_4": float(item.get('Precio_4', 0)),
                            "Precio_5": float(item.get('Precio_5', 0))
                        }
                        
                        # Obtener datos del producto desde df_prod
                        prod_info = df_prod[df_prod['ID_Producto'].astype(str) == id_p_str]
                        
                        es_stockeable = False
                        stock_anterior = 0
                        stock_nuevo = 0
                        nombre_producto = item.get('nombre', '')
            
                        if not prod_info.empty:
                            fila_p = prod_info.iloc[0]
                            es_stockeable = fila_p.get('Es_Stockeable', False) == True
                            stock_anterior = int(fila_p.get('Stock_Actual', 0) or 0)
                            if not nombre_producto:
                                nombre_producto = str(fila_p.get('Nombre', ''))
            
                        if es_stockeable:
                            stock_nuevo = stock_anterior + cant_comprada
                            data_update["Stock_Actual"] = stock_nuevo
                        
                        # Ejecutamos el update en la tabla PRODUCTOS
                        db.table("PRODUCTOS").update(data_update).eq("ID_Producto", id_p_str).execute()
            
                        # B. Guardar Detalle (en la tabla DETALLE_COMPRAS)
                        db.table("DETALLE_COMPRAS").insert({
                            "ID_Compra": id_c,
                            "ID_Producto": id_p_str,
                            "Cantidad": cant_comprada,
                            "Precio_Costo_Unitario": float(item['costo']),
                            "Subtotal": float(item['subtotal'])
                        }).execute()
            
                        # C. REGISTRO EN MOVIMIENTOS_STOCK (KARDEX - ENTRADA POR COMPRA)
                        # Solo registramos el movimiento de stock si el producto incrementa inventario
                        if es_stockeable:
                            db.table("MOVIMIENTOS_STOCK").insert({
                                "id_producto": id_p_str,
                                "nombre_producto": str(nombre_producto),
                                "tipo_movimiento": "COMPRA (ENTRADA)",
                                "cantidad": cant_comprada,
                                "stock_anterior": float(stock_anterior),
                                "stock_nuevo": float(stock_nuevo),
                                "origen_referencia": f"Ingreso por Compra (ID: {id_c} - Factura: {nro_fact})",
                                "usuario": str(usuario_logueado)
                            }).execute()
            
                    # --- Limpieza de Órdenes en Edición ---
                    if 'oc_en_edicion' in st.session_state:
                        id_a_borrar = st.session_state.oc_en_edicion
                        db.table("DETALLE_ORDENES").delete().eq("ID_Compra", id_a_borrar).execute()
                        db.table("ORDENES_COMPRA").delete().eq("ID_Compra", id_a_borrar).execute()
                        del st.session_state.oc_en_edicion
                    
                    st.success("¡Compra registrada, stock cargado y Kardex actualizado correctamente!")
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
            st.dataframe(df_activos, width='stretch', hide_index=True)
            
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
    # MODULO: ⚙️ AUDITORÍA
    # =====================================================================
    elif menu == "⚙️ Auditoría":
        st.title("🛡️ Auditoría del Sistema")
        st.subheader("Historial de Modificaciones y Eventos")
        
        # --- FILTROS DE BÚSQUEDA ---
        c1, c2, c3, c4 = st.columns(4)
        with c1: 
            # Agregamos "Todas" para no obligar al usuario a filtrar por una sola tabla
            tabla_f = st.selectbox("Tabla Afectada", ["Todas", "PRODUCTOS", "CAJA", "VENDEDORES", "COMPRAS_CABECERA"], key="sel_tabla")
        with c2: 
            accion_f = st.selectbox("Acción", ["Todas", "INSERT", "UPDATE", "DELETE"], key="sel_accion")
        with c3: 
            user_f = st.text_input("Usuario (Filtro parcial)", key="input_user")
        with c4: 
            id_f = st.text_input("ID Entidad Exacto", key="input_id")
        
        # --- CONSTRUCCIÓN DINÁMICA DE QUERY ---
        query = db.table("AUDITORIA").select("*")
        
        if tabla_f != "Todas":
            query = query.eq("Tabla_Afectada", tabla_f)
        if accion_f != "Todas":
            query = query.eq("Accion", accion_f)
        if user_f:
            query = query.ilike("Usuario", f"%{user_f}%")
        if id_f:
            query = query.eq("ID_Entidad", id_f)
        
        # --- EJECUCIÓN Y RENDERIZADO ---
        try:
            # Ordenamos siempre por el evento más reciente y limitamos para proteger la memoria de la app
            res = query.order("Fecha_Hora", desc=True).limit(100).execute()
            
            if res.data:
                df_auditoria = pd.DataFrame(res.data)
                
                # Formateamos la fecha para que sea más legible en pantalla
                df_auditoria['Fecha_Hora'] = pd.to_datetime(df_auditoria['Fecha_Hora']).dt.strftime('%Y-%m-%d %H:%M:%S')
                
                # Reordenamos columnas para una vista limpia
                columnas_ordenadas = ['Fecha_Hora', 'Usuario', 'Tabla_Afectada', 'Accion', 'ID_Entidad', 'Detalles']
                df_render = df_auditoria[columnas_ordenadas]
                
                # Renderizado usando la configuración de columnas de Streamlit para el campo JSON
                st.dataframe(
                    df_render,
                    hide_index=True,
                    use_container_width=True,
                    column_config={
                        "Detalles": st.column_config.JsonColumn("Datos/Cambios 🔍", help="Historial de campos modificados")
                    }
                )
            else:
                st.info("No se encontraron registros que coincidan con los criterios de búsqueda.")
                
        except Exception as e:
            st.error(f"Error al consultar la tabla de auditoría: {e}")

    # =====================================================================
    # MODULO: 📈 REPORTE DE UTILIDADES
    # =====================================================================
    elif menu == "📈 Reporte de Utilidades":
        mostrar_reporte_utilidad()

    # =====================================================================
    # MODULO: 🚚 GESTION DE REPARTOS
    # =====================================================================
    elif menu == "🚚 Gestión de Repartos":
        
        # Cargar puntos guardados desde Supabase
        puntos_db = cargar_puntos_reparto()
    
        # Obtenemos ventas pendientes de reparto
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
            
            total_general = len(df)
            st.markdown(f"## 🗺️ Planificación de Repartos ({total_general})")
            st.divider()
            
            rol_usuario = st.session_state.get('rol', 'Vendedor')
            
            for fecha, grupo in df.groupby('Fecha_Entrega'):
                st.subheader(f"📅 {fecha} ({len(grupo)})")
                
                if rol_usuario == "Administrador":
                    with st.expander(f"⚙️ Configurar Origen y Destino para {fecha}"):
                        c_origen, c_destino = st.columns(2)
                        
                        # --- CONFIGURACIÓN ORIGEN ---
                        with c_origen:
                            st.markdown("**📍 Punto de Partida**")
                            # Armamos las opciones mezclando los puntos de la BD + la opción de Link
                            opciones_origen = {**puntos_db, "Otro (Link de Maps)": "link"}
                            
                            sel_origen = st.selectbox(
                                "¿Desde dónde sale el reparto?", 
                                list(opciones_origen.keys()), 
                                key=f"sel_orig_{fecha}"
                            )
                            
                            if sel_origen == "Otro (Link de Maps)":
                                link_orig = st.text_input("Pega el link de origen:", key=f"link_orig_{fecha}")
                                if link_orig:
                                    coords_orig = extraer_coords_desde_link(link_orig)
                                    if coords_orig:
                                        st.success(f"Origen: {coords_orig}")
                                        punto_partida = coords_orig
                                    else:
                                        st.error("No se pudo leer el link. Se usará el Local por defecto.")
                                        punto_partida = list(puntos_db.values())[0]
                                else:
                                    punto_partida = list(puntos_db.values())[0]
                            else:
                                punto_partida = opciones_origen[sel_origen]
    
                        # --- CONFIGURACIÓN DESTINO FINAL ---
                        with c_destino:
                            st.markdown("**🏁 Punto de Finalización**")
                            opciones_destino = {**puntos_db, "Otro (Link de Maps)": "link"}
                            
                            # 1. Definimos dinámicamente el nombre y las coordenadas del punto por defecto (primer punto de la BD)
                            nombre_defecto = list(puntos_db.keys())[0] if puntos_db else "Punto Principal"
                            coords_defecto = list(puntos_db.values())[0] if puntos_db else None
                        
                            # Preseleccionamos por defecto la primera opción de la BD (índice 0)
                            index_def = 0
                        
                            sel_destino = st.selectbox(
                                "¿Dónde termina la ruta?", 
                                list(opciones_destino.keys()), 
                                index=index_def,
                                key=f"sel_dest_{fecha}"
                            )
                            
                            if sel_destino == "Otro (Link de Maps)":
                                link_dest = st.text_input("Pega el link de destino:", key=f"link_dest_{fecha}")
                                if link_dest:
                                    coords_dest = extraer_coords_desde_link(link_dest)
                                    if coords_dest:
                                        st.success(f"Destino: {coords_dest}")
                                        punto_llegada = coords_dest
                                    else:
                                        # 2. Mensaje y fallback totalmente dinámicos
                                        st.error(f"No se pudo leer el link. Se usará {nombre_defecto} por defecto.")
                                        punto_llegada = coords_defecto
                                else:
                                    punto_llegada = coords_defecto
                            else:
                                punto_llegada = opciones_destino[sel_destino]
    
                    # Botón de optimización
                    if st.button(f"🚀 Generar Diagrama Optimizado para {fecha}", key=f"btn_{fecha}"):
                        st.session_state[f"mostrar_diagrama_{fecha}"] = True
                        st.session_state[f"p_partida_{fecha}"] = punto_partida
                        st.session_state[f"p_llegada_{fecha}"] = punto_llegada
                    
                    # Si la bandera es True, mostramos el mapa interactivo
                    if st.session_state.get(f"mostrar_diagrama_{fecha}", False):
                        p_partida = st.session_state.get(f"p_partida_{fecha}", punto_partida)
                        p_llegada = st.session_state.get(f"p_llegada_{fecha}", punto_llegada)
                        
                        generar_diagrama_optimizada(grupo, p_partida, fecha, punto_destino=p_llegada)
                
                # Iteramos sobre los repartos del día
                for _, v in grupo.iterrows():
                    with st.container(border=True):
                        c1, c2, c3 = st.columns([2, 2, 1])
                        c1.write(f"👤 **Cliente:** {v['Cliente']}")
                        c2.write(f"📍 **Dir:** {v['Direccion_Entrega']}")
                        
                        if v.get('Link_Maps_Entrega'):
                            c3.link_button("📍 Maps", v['Link_Maps_Entrega'])
                        
                        obs_entrega = v.get('Observaciones', '')
                        if pd.notna(obs_entrega) and str(obs_entrega).strip() and str(obs_entrega).strip().lower() not in ["nan", "none"]:
                            st.info(f"📝 **Nota para el repartidor:** {obs_entrega}", icon="📌")
                        
                        st.caption(f"💰 {v['Metodo_Pago']}")

    # =====================================================================
    # MODULO: 📊 REPORTES
    # =====================================================================
    elif menu == "📊 Reportes":
        reportes.render_reportes(db)
