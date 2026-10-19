"""
DATIA - Setup & Full Migration to PostgreSQL
Creates databases, initializes corporate enterprise schema with rich business data,
migrates/seeds metadata, configures PostgreSQL CorporateConnection, RBAC, and Catalog.
"""

import os
import sys
import logging
from datetime import datetime, timedelta
import random

# Ensure backend root is on Python path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, text
from app.core.config import settings
from app.core.database import SessionLocal
from app.core.security import encrypt_credential
from app.modules.admin_catalog.models import CorporateConnection
from app.db.init_db import init_db

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
logger = logging.getLogger("setup_postgres_full")

PG_HOST = settings.POSTGRES_SERVER
PG_PORT = settings.POSTGRES_PORT
PG_USER = settings.POSTGRES_USER
PG_PASSWORD = settings.POSTGRES_PASSWORD
METADATA_DB = settings.POSTGRES_DB  # democratizacion_metadatos
BUSINESS_DB = "democratizacion_empresa"

MAINTENANCE_URL = f"postgresql+psycopg://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/postgres"
BUSINESS_URL = f"postgresql+psycopg://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{BUSINESS_DB}"

def create_database_if_not_exists(dbname: str):
    """Connects to maintenance DB and creates target DB if it does not exist."""
    m_engine = create_engine(MAINTENANCE_URL, isolation_level="AUTOCOMMIT", pool_pre_ping=True)
    with m_engine.connect() as conn:
        exists = conn.execute(text("SELECT 1 FROM pg_database WHERE datname = :dbname"), {"dbname": dbname}).scalar()
        if not exists:
            logger.info(f"Creando base de datos PostgreSQL '{dbname}'...")
            conn.execute(text(f'CREATE DATABASE "{dbname}" OWNER "{PG_USER}"'))
            logger.info(f"Base de datos '{dbname}' creada exitosamente.")
        else:
            logger.info(f"Base de datos '{dbname}' ya existe.")
    m_engine.dispose()

def setup_business_database():
    """Initializes schema and seeds realistic data into democratizacion_empresa."""
    logger.info(f"Configurando esquema y datos en PostgreSQL: '{BUSINESS_DB}'...")
    b_engine = create_engine(BUSINESS_URL, isolation_level="AUTOCOMMIT", pool_pre_ping=True)

    ddl_sql = """
    CREATE TABLE IF NOT EXISTS dim_categorias (
        id_categoria SERIAL PRIMARY KEY,
        nombre_categoria VARCHAR(100) NOT NULL UNIQUE,
        descripcion TEXT
    );

    CREATE TABLE IF NOT EXISTS dim_productos (
        id_producto SERIAL PRIMARY KEY,
        nombre_producto VARCHAR(150) NOT NULL,
        id_categoria INT REFERENCES dim_categorias(id_categoria),
        precio_unitario NUMERIC(12, 2) NOT NULL,
        costo_unitario NUMERIC(12, 2) NOT NULL,
        stock_disponible INT DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS dim_clientes (
        id_cliente SERIAL PRIMARY KEY,
        nombre_empresa VARCHAR(150) NOT NULL,
        rut_dni_cliente VARCHAR(20) NOT NULL,
        email_contacto VARCHAR(150),
        telefono_contacto VARCHAR(50),
        tarjeta_credito_token VARCHAR(50),
        sector_industria VARCHAR(100),
        nivel_riesgo_crediticio VARCHAR(20) DEFAULT 'BAJO',
        fecha_alta DATE DEFAULT CURRENT_DATE
    );

    CREATE TABLE IF NOT EXISTS dim_empleados (
        id_empleado SERIAL PRIMARY KEY,
        nombre_completo VARCHAR(150) NOT NULL,
        cargo VARCHAR(100) NOT NULL,
        departamento VARCHAR(100) NOT NULL,
        sueldo_mensual NUMERIC(12, 2) NOT NULL,
        fecha_ingreso DATE NOT NULL,
        activo BOOLEAN DEFAULT TRUE
    );

    CREATE TABLE IF NOT EXISTS fact_ventas (
        id_venta SERIAL PRIMARY KEY,
        fecha_venta DATE NOT NULL,
        id_producto INT REFERENCES dim_productos(id_producto),
        id_cliente INT REFERENCES dim_clientes(id_cliente),
        cantidad INT NOT NULL,
        monto_total NUMERIC(14, 2) NOT NULL,
        costo_total NUMERIC(14, 2) NOT NULL,
        margen_ganancia NUMERIC(14, 2) NOT NULL,
        metodo_pago VARCHAR(50) DEFAULT 'TRANSFERENCIA',
        estado VARCHAR(30) DEFAULT 'COMPLETADO'
    );

    CREATE TABLE IF NOT EXISTS fact_ingresos_costos (
        id_registro SERIAL PRIMARY KEY,
        mes VARCHAR(20) NOT NULL,
        anio INT NOT NULL,
        categoria_financiera VARCHAR(100) NOT NULL,
        ingreso_bruto NUMERIC(14, 2) NOT NULL,
        costo_operativo NUMERIC(14, 2) NOT NULL,
        utilidad_neta NUMERIC(14, 2) NOT NULL
    );

    CREATE TABLE IF NOT EXISTS dim_servidores (
        id_servidor SERIAL PRIMARY KEY,
        nombre_host VARCHAR(100) NOT NULL UNIQUE,
        ip_interna VARCHAR(45) NOT NULL,
        sistema_operativo VARCHAR(100),
        datacenter VARCHAR(50),
        capacidad_ram_gb INT
    );

    CREATE TABLE IF NOT EXISTS fact_incidentes_ti (
        id_incidente SERIAL PRIMARY KEY,
        fecha_incidente TIMESTAMP NOT NULL,
        id_servidor INT REFERENCES dim_servidores(id_servidor),
        tipo_falla VARCHAR(100) NOT NULL,
        nivel_prioridad VARCHAR(20) CHECK (nivel_prioridad IN ('BAJA', 'MEDIA', 'ALTA', 'CRITICA')),
        horas_resolucion NUMERIC(6, 2),
        estado VARCHAR(30) DEFAULT 'RESUELTO'
    );

    CREATE TABLE IF NOT EXISTS fact_consumo_recursos (
        id_consumo SERIAL PRIMARY KEY,
        fecha_hora TIMESTAMP NOT NULL,
        id_servidor INT REFERENCES dim_servidores(id_servidor),
        porcentaje_cpu NUMERIC(5, 2),
        uso_ram_gb NUMERIC(6, 2),
        trafico_red_mb NUMERIC(10, 2)
    );
    """

    with b_engine.connect() as conn:
        for stmt in ddl_sql.split(";"):
            stmt_clean = stmt.strip()
            if stmt_clean:
                conn.execute(text(stmt_clean))

        # Check if already seeded
        ventas_count = conn.execute(text("SELECT COUNT(*) FROM fact_ventas")).scalar()
        if ventas_count and ventas_count > 0:
            logger.info(f"'{BUSINESS_DB}' ya contiene {ventas_count} registros en fact_ventas. Se conservan los datos.")
            b_engine.dispose()
            return

        logger.info("Insertando datos iniciales de negocio en PostgreSQL...")

        # 1. Categorias
        conn.execute(text("""
        INSERT INTO dim_categorias (id_categoria, nombre_categoria, descripcion) VALUES
        (1, 'Software Empresarial', 'Licencias de software corporativo y sistemas ERP/CRM'),
        (2, 'Hardware & Redes', 'Servidores, switches, laptops corporativas y perifericos'),
        (3, 'Servicios Cloud', 'Subscripciones a infraestructura de nube privada e híbrida'),
        (4, 'Consultoría & Soporte', 'Horas de servicios profesionales y soporte especializado')
        ON CONFLICT (id_categoria) DO NOTHING;
        """))

        # 2. Productos
        conn.execute(text("""
        INSERT INTO dim_productos (id_producto, nombre_producto, id_categoria, precio_unitario, costo_unitario, stock_disponible) VALUES
        (101, 'Licencia ERP Core Enterprise', 1, 25000.00, 12000.00, 50),
        (102, 'Servidor Rack 2U Dual Xeon', 2, 8500.00, 5800.00, 15),
        (103, 'Instancia Cloud VPC Dedicada', 3, 3200.00, 1800.00, 100),
        (104, 'Paquete 50 Hrs Consultoría BI', 4, 7500.00, 4200.00, 30),
        (105, 'Switch Gestionable 48 Puertos 10G', 2, 4200.00, 2900.00, 25),
        (106, 'Suite Ciberseguridad Endpoint EDR', 1, 15000.00, 7500.00, 80),
        (107, 'Almacenamiento SAN All-Flash 40TB', 2, 32000.00, 21000.00, 8),
        (108, 'Migración Cloud & Arquitectura AWS', 4, 18500.00, 9500.00, 20)
        ON CONFLICT (id_producto) DO NOTHING;
        """))

        # 3. Clientes
        conn.execute(text("""
        INSERT INTO dim_clientes (id_cliente, nombre_empresa, rut_dni_cliente, email_contacto, telefono_contacto, tarjeta_credito_token, sector_industria, nivel_riesgo_crediticio, fecha_alta) VALUES
        (1, 'Banco de Comercio y Crédito', '76.123.456-7', 'contacto@bancocomercio.cl', '+56911223344', 'TOK-VISA-9821-XYZ', 'Banca & Servicios Financieros', 'BAJO', '2025-01-15'),
        (2, 'Retail Corporativo Global', '78.987.654-3', 'finanzas@retailglobal.com', '+56922334455', 'TOK-MC-4412-ABC', 'Retail & Gran Consumo', 'BAJO', '2025-02-10'),
        (3, 'Logística & Transportes del Norte', '77.456.789-1', 'operaciones@logisticanorte.cl', '+56933445566', 'TOK-AMEX-1029-DEF', 'Logística & Cadena de Suministro', 'MEDIO', '2025-03-05'),
        (4, 'Clínica Salud Integral', '79.111.222-9', 'ti@saludintegral.cl', '+56944556677', 'TOK-VISA-5561-GHI', 'Salud & Salud Privada', 'BAJO', '2025-04-12'),
        (5, 'Minería Andina del Cobre', '76.555.444-K', 'abastecimiento@mineriandina.cl', '+56955667788', 'TOK-VISA-7788-JKL', 'Minería & Recursos Naturales', 'BAJO', '2025-05-20'),
        (6, 'Agroindustrias del Valle Central', '81.234.567-8', 'gerencia@agrovalle.cl', '+56966778899', 'TOK-MC-9900-MNO', 'Agricultura & Exportaciones', 'MEDIO', '2025-06-01')
        ON CONFLICT (id_cliente) DO NOTHING;
        """))

        # 4. Empleados
        conn.execute(text("""
        INSERT INTO dim_empleados (id_empleado, nombre_completo, cargo, departamento, sueldo_mensual, fecha_ingreso, activo) VALUES
        (1, 'Carlos Mendoza Rivera', 'Director de Tecnología (CTO)', 'Tecnología & TI', 6500000.00, '2023-01-10', TRUE),
        (2, 'Valentina Soto Morales', 'Gerente de Finanzas (CFO)', 'Economía & Finanzas', 6200000.00, '2023-03-15', TRUE),
        (3, 'Felipe Valenzuela Silva', 'Lead Data Scientist', 'Tecnología & TI', 4800000.00, '2023-06-01', TRUE),
        (4, 'Andrea Lagos Pizarro', 'Analista Financiero Senior', 'Economía & Finanzas', 3200000.00, '2024-02-01', TRUE),
        (5, 'Rodrigo Castro Araya', 'Ingeniero DevOps & Cloud', 'Tecnología & TI', 3600000.00, '2024-04-15', TRUE),
        (6, 'Daniela Fuentes Rojas', 'Account Executive Enterprise', 'Operaciones & Comercial', 3400000.00, '2024-05-01', TRUE)
        ON CONFLICT (id_empleado) DO NOTHING;
        """))

        # 5. Ventas (Rich dataset)
        random.seed(42)
        ventas_rows = [
            ("2026-07-05", 101, 1, 2, 50000.00, 24000.00, 26000.00, "TRANSFERENCIA", "COMPLETADO"),
            ("2026-07-12", 102, 3, 4, 34000.00, 23200.00, 10800.00, "TRANSFERENCIA", "COMPLETADO"),
            ("2026-07-20", 103, 2, 5, 16000.00, 9000.00, 7000.00, "TRANSFERENCIA", "COMPLETADO"),
            ("2026-08-02", 104, 4, 2, 15000.00, 8400.00, 6600.00, "TARJETA_CREDITO", "COMPLETADO"),
            ("2026-08-10", 101, 2, 1, 25000.00, 12000.00, 13000.00, "TRANSFERENCIA", "COMPLETADO"),
            ("2026-08-14", 105, 1, 3, 12600.00, 8700.00, 3900.00, "TRANSFERENCIA", "COMPLETADO"),
            ("2026-08-18", 106, 5, 4, 60000.00, 30000.00, 30000.00, "TRANSFERENCIA", "COMPLETADO"),
            ("2026-08-25", 107, 1, 1, 32000.00, 21000.00, 11000.00, "TRANSFERENCIA", "COMPLETADO"),
            ("2026-08-28", 108, 6, 2, 37000.00, 19000.00, 18000.00, "TRANSFERENCIA", "COMPLETADO"),
            ("2026-09-01", 103, 4, 8, 25600.00, 14400.00, 11200.00, "TARJETA_CREDITO", "COMPLETADO"),
            ("2026-09-03", 101, 5, 3, 75000.00, 36000.00, 39000.00, "TRANSFERENCIA", "COMPLETADO"),
            ("2026-09-05", 105, 3, 5, 21000.00, 14500.00, 6500.00, "TRANSFERENCIA", "COMPLETADO"),
        ]

        # Generate 40 more realistic sales
        base_date = datetime(2026, 1, 1)
        prods = [
            (101, 25000.0, 12000.0), (102, 8500.0, 5800.0), (103, 3200.0, 1800.0),
            (104, 7500.0, 4200.0), (105, 4200.0, 2900.0), (106, 15000.0, 7500.0),
            (107, 32000.0, 21000.0), (108, 18500.0, 9500.0)
        ]
        clients = [1, 2, 3, 4, 5, 6]
        methods = ["TRANSFERENCIA", "TRANSFERENCIA", "TARJETA_CREDITO", "ORDEN_COMPRA"]

        for i in range(40):
            d = base_date + timedelta(days=random.randint(0, 240))
            p_id, p_price, p_cost = random.choice(prods)
            c_id = random.choice(clients)
            qty = random.randint(1, 6)
            total = p_price * qty
            c_total = p_cost * qty
            margin = total - c_total
            m_pago = random.choice(methods)
            ventas_rows.append((
                d.strftime("%Y-%m-%d"), p_id, c_id, qty, total, c_total, margin, m_pago, "COMPLETADO"
            ))

        for v in ventas_rows:
            conn.execute(text("""
            INSERT INTO fact_ventas (fecha_venta, id_producto, id_cliente, cantidad, monto_total, costo_total, margen_ganancia, metodo_pago, estado)
            VALUES (:f, :p, :c, :q, :m, :ct, :mg, :mp, :st)
            """), {
                "f": v[0], "p": v[1], "c": v[2], "q": v[3], "m": v[4],
                "ct": v[5], "mg": v[6], "mp": v[7], "st": v[8]
            })

        # 6. Ingresos & Costos Mensuales
        fin_rows = [
            ('Enero', 2026, 'Ventas Software & Cloud', 180000.00, 105000.00, 75000.00),
            ('Febrero', 2026, 'Ventas Software & Cloud', 210000.00, 120000.00, 90000.00),
            ('Marzo', 2026, 'Ventas Software & Cloud', 245000.00, 135000.00, 110000.00),
            ('Abril', 2026, 'Ventas Software & Cloud', 195000.00, 115000.00, 80000.00),
            ('Mayo', 2026, 'Ventas Software & Cloud', 230000.00, 128000.00, 102000.00),
            ('Junio', 2026, 'Ventas Software & Cloud', 280000.00, 145000.00, 135000.00),
            ('Julio', 2026, 'Ventas Software & Cloud', 310000.00, 160000.00, 150000.00),
            ('Agosto', 2026, 'Ventas Software & Cloud', 295000.00, 152000.00, 143000.00),
        ]
        for f in fin_rows:
            conn.execute(text("""
            INSERT INTO fact_ingresos_costos (mes, anio, categoria_financiera, ingreso_bruto, costo_operativo, utilidad_neta)
            VALUES (:m, :a, :cat, :ing, :cost, :util)
            """), {
                "m": f[0], "a": f[1], "cat": f[2], "ing": f[3], "cost": f[4], "util": f[5]
            })

        # 7. Servidores TI
        conn.execute(text("""
        INSERT INTO dim_servidores (id_servidor, nombre_host, ip_interna, sistema_operativo, datacenter, capacidad_ram_gb) VALUES
        (1, 'srv-db-prod-01', '10.0.1.45', 'Ubuntu Server 22.04 LTS', 'DC-Santiago-Primary', 128),
        (2, 'srv-app-core-02', '10.0.1.46', 'Red Hat Enterprise Linux 9', 'DC-Santiago-Primary', 64),
        (3, 'srv-cloud-proxy-03', '10.0.2.10', 'Debian 12', 'DC-AWS-Cloud-UsEast', 32),
        (4, 'srv-backup-node-04', '10.0.3.15', 'Windows Server 2022', 'DC-Valparaiso-Backup', 256),
        (5, 'srv-analytics-ai-05', '10.0.2.25', 'Ubuntu Server 24.04 LTS', 'DC-Santiago-Primary', 512)
        ON CONFLICT (id_servidor) DO NOTHING;
        """))

        # 8. Incidentes TI
        inc_rows = [
            ('2026-08-01 04:15:00', 1, 'Alta latencia en disco SSD NVMe', 'ALTA', 2.5, 'RESUELTO'),
            ('2026-08-03 14:22:00', 2, 'Pico de consumo de memoria RAM (>95%)', 'CRITICA', 1.0, 'RESUELTO'),
            ('2026-08-08 09:10:00', 3, 'Reinicio inesperado de daemon de red BGP', 'MEDIA', 0.8, 'RESUELTO'),
            ('2026-08-12 18:45:00', 4, 'Falla en tarea cron de respaldo nocturno', 'BAJA', 4.0, 'RESUELTO'),
            ('2026-08-20 11:30:00', 5, 'Sobrecarga de GPU CUDA en pipeline de IA', 'ALTA', 1.5, 'RESUELTO'),
            ('2026-08-27 16:05:00', 2, 'Timeout en pool de conexiones de microservicio', 'MEDIA', 0.5, 'RESUELTO'),
            ('2026-09-02 08:20:00', 1, 'Degradación de réplica sincrónica PostgreSQL', 'CRITICA', 1.2, 'RESUELTO')
        ]
        for inc in inc_rows:
            conn.execute(text("""
            INSERT INTO fact_incidentes_ti (fecha_incidente, id_servidor, tipo_falla, nivel_prioridad, horas_resolucion, estado)
            VALUES (:fi, :srv, :tf, :np, :hr, :st)
            """), {
                "fi": inc[0], "srv": inc[1], "tf": inc[2], "np": inc[3], "hr": inc[4], "st": inc[5]
            })

        # 9. Consumo de Recursos TI
        recurso_rows = [
            ('2026-09-01 12:00:00', 1, 45.2, 84.5, 1250.0),
            ('2026-09-01 12:00:00', 2, 78.4, 52.1, 4500.0),
            ('2026-09-01 12:00:00', 3, 22.0, 14.2, 890.0),
            ('2026-09-01 12:00:00', 4, 12.5, 98.0, 150.0),
            ('2026-09-01 12:00:00', 5, 88.5, 420.0, 6800.0),
            ('2026-09-05 18:00:00', 1, 52.0, 92.0, 1400.0),
            ('2026-09-05 18:00:00', 2, 65.0, 48.0, 3900.0),
            ('2026-09-05 18:00:00', 5, 94.0, 480.0, 8500.0),
        ]
        for rec in recurso_rows:
            conn.execute(text("""
            INSERT INTO fact_consumo_recursos (fecha_hora, id_servidor, porcentaje_cpu, uso_ram_gb, trafico_red_mb)
            VALUES (:fh, :srv, :cpu, :ram, :net)
            """), {
                "fh": rec[0], "srv": rec[1], "cpu": rec[2], "ram": rec[3], "net": rec[4]
            })

        # Reset serial sequences for auto-increment PKs
        for tbl, pk in [
            ("dim_categorias", "id_categoria"),
            ("dim_productos", "id_producto"),
            ("dim_clientes", "id_cliente"),
            ("dim_empleados", "id_empleado"),
            ("fact_ventas", "id_venta"),
            ("fact_ingresos_costos", "id_registro"),
            ("dim_servidores", "id_servidor"),
            ("fact_incidentes_ti", "id_incidente"),
            ("fact_consumo_recursos", "id_consumo"),
        ]:
            try:
                conn.execute(text(f"""
                SELECT setval(pg_get_serial_sequence('{tbl}', '{pk}'), COALESCE((SELECT MAX({pk}) FROM {tbl}), 1));
                """))
            except Exception as seq_err:
                logger.warning(f"Aviso al actualizar secuencia {tbl}: {seq_err}")

    b_engine.dispose()
    logger.info(f"Base de datos de negocio '{BUSINESS_DB}' poblada exitosamente.")

def setup_metadata_database():
    """Siembra la metadata con el unico seeder de la aplicacion (`init_db`).

    Antes esta funcion era un SEGUNDO seeder (dominios, roles, usuarios,
    conector, matriz RBAC, permisos de columna y catalogo) que corria antes que
    `init_db` en cada arranque, y divergia de el: sembraba 5 usuarios en vez de
    10, una matriz de 3 roles en vez de 8, reglas de columnas distintas y un
    conector con otro nombre que podia terminar duplicado. Ahora identidad,
    permisos y conector tienen una sola fuente; este script aporta lo suyo:
    crear las bases y sembrar la base de negocio de la demo.

    La conexion de plataforma se refresca con los settings actuales: es lo que
    evita que un cambio de `POSTGRES_*` en `.env` deje la demo apuntando a
    credenciales viejas.
    """
    logger.info(f"Inicializando base de datos de gobernanza y metadatos: '{METADATA_DB}'...")
    db = SessionLocal()
    try:
        init_db(db)
        if settings.DEMO_MODE_EFFECTIVE:
            platform_conn = db.query(CorporateConnection).filter(
                CorporateConnection.is_uploaded == False
            ).order_by(CorporateConnection.id.asc()).first()
            if platform_conn:
                platform_conn.host = PG_HOST
                platform_conn.port = PG_PORT
                platform_conn.database_name = BUSINESS_DB
                platform_conn.username = PG_USER
                platform_conn.encrypted_password = encrypt_credential(PG_PASSWORD)
                db.commit()
        logger.info(f"Metadata de plataforma lista en '{METADATA_DB}'.")
    finally:
        db.close()

def check_postgres_alive() -> bool:
    """Verifies that PostgreSQL server is reachable before attempting bootstrap."""
    try:
        m_engine = create_engine(MAINTENANCE_URL, isolation_level="AUTOCOMMIT", pool_pre_ping=True)
        with m_engine.connect() as conn:
            conn.execute(text("SELECT 1;"))
        m_engine.dispose()
        return True
    except Exception as e:
        logger.warning(f"PostgreSQL server ({PG_HOST}:{PG_PORT}) is not reachable: {e}")
        return False

def run() -> bool:
    logger.info("==================================================")
    logger.info("DATIA POSTGRESQL AUTO-BOOTSTRAP & HEALTH CHECK")
    logger.info("==================================================")
    logger.info(f"Host: {PG_HOST}:{PG_PORT} | Usuario: {PG_USER}")

    if not check_postgres_alive():
        logger.warning("No se pudo contactar al servidor PostgreSQL. Omitiendo auto-inicialización.")
        return False

    # 1. La metadata es plataforma: se crea siempre.
    create_database_if_not_exists(METADATA_DB)

    # 2. La base de negocio es el dataset de la demo. Con DEMO_MODE apagado no
    #    se crea ni se carga: en una instalacion real el cliente trae la suya y
    #    estos datos son de demostracion.
    if settings.DEMO_MODE_EFFECTIVE:
        create_database_if_not_exists(BUSINESS_DB)
        setup_business_database()

    # 3. Setup metadata database with governance & active connection
    setup_metadata_database()

    logger.info("==================================================")
    logger.info("POSTGRESQL COMPLETAMENTE VERIFICADO Y SINCRONIZADO")
    logger.info("==================================================")
    return True

if __name__ == "__main__":
    success = run()
    sys.exit(0 if success else 1)
