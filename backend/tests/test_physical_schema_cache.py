"""La introspeccion fisica del esquema se cachea; el prompt del LLM no.

Por que este cache existe
-------------------------
`get_physical_db_tables` y `get_physical_table_columns` abren una conexion nueva
(o un `create_engine` + `dispose()` contra el Postgres del cliente) EN CADA
llamada. Medido en un `POST /chat/query` con 10 tablas permitidas: ~30
aperturas, y todo dentro de un `async def`, o sea bloqueando el event loop.

Lo que NO se puede cachear
--------------------------
`_schema_cache` (el prompt ya armado) tiene el texto de la pregunta en la key, y
eso es intencional: el ranking de tablas depende de la pregunta. Aqui lo que se
cachea es la introscpeccion, que no depende de ella.

Lo que este test protege
------------------------
1. La entrada cacheada devuelve lo mismo que la calculada, y `include_samples`
   NO se confunde entre si (el prompt pide muestras, el ranking no).
2. `invalidate_schema_cache` limpia la introscpeccion: un DDL nuevo o una tabla
   subida tienen que verse, no esperar 10 minutos.
3. Un resultado VACIO no se cachea. Sin esto, un Postgres caido 30 s se
   convierte en 10 minutos de "esta base no tiene tablas" para todo el mundo.
   Es el mismo criterio de `SchemaIntrospectionError`: no se pudo leer != vacio.
"""

import unittest
from unittest.mock import patch

from sqlalchemy import text

from conftest import (
    create_temp_pg_database,
    drop_temp_pg_database,
    pg_source_conn_attrs,
    pg_source_engine,
)

from app.modules.chat_engine.dynamic_schema import DynamicSchemaPruningService as S


class _Conn:
    """Target minimo: `_physical_key` y `connector_engine` leen estos campos."""

    def __init__(self, db_name):
        for key, value in pg_source_conn_attrs(db_name).items():
            setattr(self, key, value)


class TestPhysicalSchemaCache(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Fuente desechable: base PostgreSQL real con tablas y filas. Con filas
        # porque `include_samples=True` lee `SELECT * ... LIMIT 20`, y una tabla
        # vacia no distingue el cache de "no hay cache": las dos dan [].
        cls.pg_db = create_temp_pg_database("cache")
        eng = pg_source_engine(cls.pg_db)
        try:
            with eng.begin() as c:
                c.execute(text("CREATE TABLE ventas (id INTEGER, monto REAL, region TEXT)"))
                c.execute(text("CREATE TABLE precios (sku TEXT, valor REAL)"))
                c.execute(text(
                    "INSERT INTO ventas (id, monto, region) VALUES "
                    "(1, 100.0, 'Norte'), (2, 250.5, 'Sur'), (3, 75.25, 'Norte')"
                ))
        finally:
            eng.dispose()

    @classmethod
    def tearDownClass(cls):
        drop_temp_pg_database(cls.pg_db)

    def setUp(self):
        self.conn = _Conn(self.pg_db)
        S.invalidate_schema_cache()

    def tearDown(self):
        S.invalidate_schema_cache()

    def test_tables_cache_returns_same_result_and_invalidates(self):
        first = S.get_physical_db_tables(self.conn)
        self.assertEqual(first, {"ventas", "precios"})

        # El hit de cache tiene que devolver un set NUEVO: el llamador lo usa
        # como `physical_tables` y lo deriva con operaciones de conjunto, y un
        # set cacheado mutado en el sitio envenena a todos los request
        # siguientes.
        cached = S.get_physical_db_tables(self.conn)
        self.assertEqual(first, cached)
        self.assertIsNot(first, cached)
        cached.add("tabla_inventada")
        self.assertNotIn("tabla_inventada", S.get_physical_db_tables(self.conn))

        # Un DDL nuevo no espera al TTL.
        eng = pg_source_engine(self.pg_db)
        try:
            with eng.begin() as c:
                c.execute(text("CREATE TABLE clientes (rut TEXT)"))
        finally:
            eng.dispose()
        self.assertNotIn("clientes", S.get_physical_db_tables(self.conn))
        S.invalidate_schema_cache()
        self.assertIn("clientes", S.get_physical_db_tables(self.conn))

    def test_columns_cache_respects_include_samples(self):
        sin_muestras = S.get_physical_table_columns("ventas", self.conn, include_samples=False)
        con_muestras = S.get_physical_table_columns("ventas", self.conn, include_samples=True)

        # Los nombres coinciden; el contenido de `samples` NO puede cruzarse.
        # El prompt del LLM pide muestras y el ranking de tablas no: si
        # compartieran entrada, el prompt recibiria valores que el llamador pidio
        # no leer.
        self.assertEqual([c["name"] for c in sin_muestras], ["id", "monto", "region"])
        self.assertEqual([c["name"] for c in con_muestras], ["id", "monto", "region"])
        self.assertEqual([c["samples"] for c in sin_muestras], [[], [], []])
        monto = next(c for c in con_muestras if c["name"] == "monto")
        # La introspeccion no lleva ORDER BY: se compara como conjunto.
        self.assertEqual(sorted(monto["samples"]), sorted(["100.0", "250.5", "75.25"]))

        # El dict de cada columna es copia propia, no la entrada del cache.
        con_muestras[0]["name"] = "CORROMPIDO"
        self.assertEqual(S.get_physical_table_columns("ventas", self.conn)[0]["name"], "id")

    def test_empty_result_is_not_cached(self):
        """Una base que no se puede leer no puede quedar cacheada como vacia.

        Se cuenta el trabajo REAL (la introscpeccion) en vez de espiar la
        conexion, porque hay varias rutas que devuelven vacio sin conectar
        siquiera: una `CorporateConnection` de Postgres que no responde traga la
        excepcion en el `connector_engine`. Espiar el `connect` solo comprobaria
        una de ellas.
        """
        with patch.object(S, "_introspect_tables", return_value=set()) as spy:
            self.assertEqual(S.get_physical_db_tables(self.conn), set())
            self.assertEqual(S.get_physical_db_tables(self.conn), set())
            # La segunda llamada VOLVIO a preguntar: un vacio cacheado habria
            # respondido sin tocar el introspector, y el Postgres caido se
            # convertiria en diez minutos de "esta base no tiene tablas".
            self.assertEqual(spy.call_count, 2)

    def test_key_ignores_orm_object_identity(self):
        """La key sale de los DATOS de la conexion, no de `id(obj)`.

        `target` es un objeto ORM detached distinto en cada request. Con
        `id(target)` en la key el cache nunca acertaria (y el id se reutiliza
        entre objetos, o sea que tampoco serviria como identidad).
        """
        from app.modules.admin_catalog.models import DatabaseType

        class ConnFalso:
            def __init__(self, host):
                self.db_type = DatabaseType.POSTGRESQL
                self.host = host
                self.port = 5432
                self.database_name = "corp"

        a, b = ConnFalso("h1"), ConnFalso("h1")
        self.assertIsNot(a, b)
        self.assertEqual(S._physical_key(a), S._physical_key(b))
        self.assertNotEqual(S._physical_key(a), S._physical_key(ConnFalso("h2")))


if __name__ == "__main__":
    unittest.main()
