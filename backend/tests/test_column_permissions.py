"""Politicas de columna: la CLS deja de depender de un seed que adivina nombres.

Estos tests verifican el contrato de `GET/PUT /permissions/columns` contra el
mismo guardarrail que aplica el chat, no solo que el endpoint responda 200:

- un dataset subido nace sin filas CLS, asi que el admin tiene que poder marcar
  una columna MASKED/BLOCKED sin esperar un seed;
- `ALLOWED` explicito levanta el bloqueo sembrado (la unica via para que un rol
  de RRHH creado a mano vea remuneraciones);
- el cambio se refleja YA en el prompt cacheado (invalidacion);
- toda mutacion de permisos deja rastro en `audit_logs`.

Se usa una base PostgreSQL desechable como fuente porque el universo de columnas
sale de la introspeccion fisica.
"""

import unittest
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import text

from conftest import (
    create_temp_pg_database, drop_temp_pg_database,
    make_auth_headers, pg_source_conn_attrs, pg_source_engine,
)

from main import app

from app.core.constants import ROLE_ADMINISTRADOR
from app.db.init_db import init_db
from app.modules.admin_catalog.models import (
    ColumnPermissionType, CorporateConnection, RoleColumnPermission,
    RoleTablePermission,
)
from app.modules.auth.models import Role
from app.modules.chat_engine.dynamic_schema import DynamicSchemaPruningService
from app.modules.chat_engine.governance_guard import GovernanceGuard
from app.modules.telemetry_audit.models import AuditLog

COLUMNS_URL = "/api/v1/permissions/columns"
TABLE_URL = "/api/v1/permissions"
TABLE = "cls_clientes"
SALARY = "sueldo_mensual"
RUT = "rut_dni_cliente"


class TestColumnPermissions(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.pg_db = create_temp_pg_database("colperm")
        eng = pg_source_engine(cls.pg_db)
        try:
            with eng.begin() as c:
                c.execute(text(f"DROP TABLE IF EXISTS {TABLE}"))
                c.execute(text(
                    f"CREATE TABLE {TABLE} ("
                    "id INTEGER PRIMARY KEY, "
                    f"{RUT} TEXT, "
                    f"{SALARY} NUMERIC)"
                ))
                c.execute(text(
                    f"INSERT INTO {TABLE} (id, {RUT}, {SALARY}) "
                    "VALUES (1, '12.345.678-9', 1500000)"
                ))
        finally:
            eng.dispose()

    @classmethod
    def tearDownClass(cls):
        drop_temp_pg_database(cls.pg_db)

    def setUp(self):
        from app.core.database import SessionLocal

        self.client = TestClient(app)
        self.db = SessionLocal()
        self.suffix = uuid.uuid4().hex[:10]
        self.role_name = f"Rol CLS {self.suffix}"
        self.admin_headers = make_auth_headers(self.db, "admin")

        self.role = Role(name=self.role_name, description="politica de columna de prueba")
        self.db.add(self.role)
        self.db.commit()
        self.db.refresh(self.role)

        self.conn = CorporateConnection(
            name=f"TEST cls {self.suffix}",
            is_active=False,
            is_uploaded=True,
            **pg_source_conn_attrs(self.pg_db),
        )
        self.db.add(self.conn)
        self.db.commit()
        self.db.refresh(self.conn)

        # La tabla tiene que estar concedida: el prompt del chat solo renderiza
        # columnas de tablas que el rol puede leer.
        self.db.add(RoleTablePermission(
            role_id=self.role.id,
            connection_id=self.conn.id,
            schema_name="public",
            table_name=TABLE,
            is_allowed=True,
            granted_by_admin=True,
        ))
        self.db.commit()

    def tearDown(self):
        self.db.query(RoleColumnPermission).filter(
            RoleColumnPermission.role_id == self.role.id
        ).delete(synchronize_session=False)
        self.db.query(RoleTablePermission).filter(
            RoleTablePermission.role_id == self.role.id
        ).delete(synchronize_session=False)
        self.db.query(AuditLog).filter(
            AuditLog.question_prompt.like(f"%{self.role_name}%")
        ).delete(synchronize_session=False)
        self.db.query(CorporateConnection).filter(
            CorporateConnection.id == self.conn.id
        ).delete(synchronize_session=False)
        self.db.query(Role).filter(Role.id == self.role.id).delete(synchronize_session=False)
        self.db.commit()
        self.db.close()

    # --- helpers ---------------------------------------------------------

    def _put(self, permission_type, columns=None, connection_id=None, role_id=None, headers=None):
        return self.client.put(
            COLUMNS_URL,
            json={
                "connection_id": connection_id if connection_id is not None else self.conn.id,
                "role_id": role_id if role_id is not None else self.role.id,
                "permission_type": permission_type,
                "columns": columns or [{"table_name": TABLE, "column_name": RUT}],
            },
            headers=headers or self.admin_headers,
        )

    def _delete(self, table_name=TABLE, column_name=RUT, connection_id=None, role_id=None, headers=None):
        return self.client.delete(
            COLUMNS_URL,
            params={
                "connection_id": connection_id if connection_id is not None else self.conn.id,
                "role_id": role_id if role_id is not None else self.role.id,
                "table_name": table_name,
                "column_name": column_name,
            },
            headers=headers or self.admin_headers,
        )

    def _prompt(self):
        return DynamicSchemaPruningService.get_authorized_schema_prompt(
            db=self.db,
            role_id=self.role.id,
            connection_id=self.conn.id,
            is_admin=False,
            user_role=self.role_name,
            query=f"listado de {TABLE}",
        )

    # --- autenticacion y validacion --------------------------------------

    def test_requiere_admin(self):
        """Un no-admin no puede leer ni escribir politicas de columna."""
        headers = make_auth_headers(self.db, "economista")
        self.assertEqual(self.client.get(COLUMNS_URL, headers=headers).status_code, 403)
        self.assertEqual(self._put("BLOCKED", headers=headers).status_code, 403)

    def test_tipo_de_politica_invalido_es_422(self):
        resp = self._put("LOQUESEA")
        self.assertEqual(resp.status_code, 422, resp.text[:300])

    def test_conexion_o_rol_inexistente_es_404(self):
        self.assertEqual(self._put("BLOCKED", connection_id=999999).status_code, 404)
        self.assertEqual(self._put("BLOCKED", role_id=999999).status_code, 404)

    def test_rol_admin_rechazado(self):
        """El admin esta exento de CLS: una regla suya no la aplica nadie."""
        admin_role = self.db.query(Role).filter(Role.name == ROLE_ADMINISTRADOR).first()
        self.assertIsNotNone(admin_role)
        resp = self._put("BLOCKED", role_id=admin_role.id)
        self.assertEqual(resp.status_code, 400, resp.text[:300])

    # --- upsert y efecto real -------------------------------------------

    def test_put_es_idempotente_y_el_get_lo_devuelve(self):
        """Repetir el PUT actualiza la fila; no acumula duplicados."""
        first = self._put("MASKED")
        self.assertEqual(first.status_code, 200, first.text[:300])
        self.assertEqual(first.json()["permissions"][0]["permission_type"], "MASKED")

        second = self._put("BLOCKED")
        self.assertEqual(second.status_code, 200, second.text[:300])

        rows = self.db.query(RoleColumnPermission).filter(
            RoleColumnPermission.role_id == self.role.id,
            RoleColumnPermission.connection_id == self.conn.id,
        ).all()
        self.assertEqual(len(rows), 1, "el upsert duplico la politica en vez de actualizarla")
        self.assertEqual(rows[0].permission_type, ColumnPermissionType.BLOCKED)

        listed = self.client.get(
            COLUMNS_URL,
            params={"connection_id": self.conn.id, "role_id": self.role.id},
            headers=self.admin_headers,
        )
        self.assertEqual(listed.status_code, 200, listed.text[:300])
        data = listed.json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["column_name"], RUT)
        self.assertEqual(data[0]["permission_type"], "BLOCKED")
        self.assertEqual(data[0]["role_name"], self.role_name)

    def test_bloqueada_desaparece_del_prompt_y_el_cache_se_invalida(self):
        """El admin bloquea y el chat deja de ver la columna en la misma corrida.

        Se pide el prompt ANTES y DESPUES con la misma pregunta para que peguen
        en la misma key del cache: si la invalidacion faltara, el segundo prompt
        seria el cacheado y la columna seguiria ahi.
        """
        antes = self._prompt()
        self.assertIn(RUT, antes["schema_prompt"])
        self.assertNotIn(RUT, antes["blocked_columns"])

        resp = self._put("BLOCKED")
        self.assertEqual(resp.status_code, 200, resp.text[:300])

        despues = self._prompt()
        self.assertNotIn(RUT, despues["schema_prompt"])
        self.assertIn(RUT, despues["blocked_columns"])

    def test_enmascarada_sigue_consultable_pero_marcada(self):
        resp = self._put("MASKED")
        self.assertEqual(resp.status_code, 200, resp.text[:300])

        despues = self._prompt()
        self.assertIn(RUT, despues["schema_prompt"])
        self.assertIn(RUT, despues["masked_columns"])
        self.assertNotIn(RUT, despues["blocked_columns"])
        # Una muestra real de la columna enmascarada no puede viajar al prompt.
        self.assertNotIn("12.345.678-9", despues["schema_prompt"])

    def test_allowed_explicito_levanta_el_bloqueo_sembrado(self):
        """La excepcion del admin sobrevive al seed: es el camino de RRHH manual.

        `init_db` siembra BLOCKED sobre remuneraciones para todo rol no-RRHH y
        solo inserta cuando no existe fila. Un ALLOWED explicito del admin, por
        tanto, se conserva y le devuelve la columna al rol.
        """
        std = self.db.query(CorporateConnection).filter(
            CorporateConnection.is_uploaded == False  # noqa: E712
        ).first()
        self.assertIsNotNone(std, "falta el conector de plataforma sembrado")

        init_db(self.db)

        sembrada = self.db.query(RoleColumnPermission).filter(
            RoleColumnPermission.role_id == self.role.id,
            RoleColumnPermission.connection_id == std.id,
            RoleColumnPermission.table_name == "dim_empleados",
            RoleColumnPermission.column_name == SALARY,
        ).first()
        self.assertIsNotNone(sembrada, "el seed no bloqueo remuneraciones al rol nuevo")
        self.assertEqual(sembrada.permission_type, ColumnPermissionType.BLOCKED)

        resp = self._put(
            "ALLOWED",
            columns=[{"table_name": "dim_empleados", "column_name": SALARY}],
            connection_id=std.id,
        )
        self.assertEqual(resp.status_code, 200, resp.text[:300])

        # El PUT corre en la sesion del request; esta sesion ya habia leido la
        # fila sembrada y SQLAlchemy devolveria el objeto cacheado (BLOCKED).
        self.db.expire_all()

        blocked = GovernanceGuard.get_blocked_columns_for_role(
            self.role_name, False, db=self.db, role_id=self.role.id, connection_id=std.id,
        )
        self.assertNotIn(SALARY, blocked)
        # La exencion es de sueldo, no del RUT: el resto de la CLS sigue igual.
        masked = GovernanceGuard.get_masked_columns_for_role(
            self.role_name, False, db=self.db, role_id=self.role.id, connection_id=std.id,
        )
        self.assertIn(RUT, masked)

        # Y el siguiente arranque no la vuelve a bloquear.
        init_db(self.db)
        bloqueada_otra_vez = self.db.query(RoleColumnPermission).filter(
            RoleColumnPermission.role_id == self.role.id,
            RoleColumnPermission.connection_id == std.id,
            RoleColumnPermission.table_name == "dim_empleados",
            RoleColumnPermission.column_name == SALARY,
        ).first()
        self.assertEqual(bloqueada_otra_vez.permission_type, ColumnPermissionType.ALLOWED)

    # --- reset a "sin politica" ------------------------------------------

    def test_delete_devuelve_la_columna_a_sin_politica(self):
        """Borrar la fila es la unica forma de deshacer una excepcion ALLOWED.

        `ALLOWED` no equivale a "sin politica": sobrevive al seed. Si el admin se
        equivoca al conceder, sin DELETE la columna queda legible para siempre.
        """
        self.assertEqual(self._put("BLOCKED").status_code, 200)
        self.assertIn(RUT, self._prompt()["blocked_columns"])

        borrado = self._delete()
        self.assertEqual(borrado.status_code, 200, borrado.text[:300])
        self.assertTrue(borrado.json()["deleted"])

        rows = self.db.query(RoleColumnPermission).filter(
            RoleColumnPermission.role_id == self.role.id,
            RoleColumnPermission.connection_id == self.conn.id,
        ).all()
        self.assertEqual(rows, [])

        # El cache del prompt se invalida: la columna vuelve a estar disponible
        # en la misma corrida, no diez minutos despues.
        despues = self._prompt()
        self.assertIn(RUT, despues["schema_prompt"])
        self.assertNotIn(RUT, despues["blocked_columns"])

        listado = self.client.get(
            COLUMNS_URL,
            params={"connection_id": self.conn.id, "role_id": self.role.id},
            headers=self.admin_headers,
        )
        self.assertEqual(listado.status_code, 200, listado.text[:300])
        self.assertEqual(listado.json(), [])

    def test_delete_es_idempotente_y_no_audita_el_no_op(self):
        """Repetir el reset no es un error y no inventa un evento de auditoria."""
        primero = self._delete()
        self.assertEqual(primero.status_code, 200, primero.text[:300])
        self.assertFalse(primero.json()["deleted"])

        self.assertEqual(self._put("MASKED").status_code, 200)
        self.assertEqual(self._delete().json()["deleted"], True)

        segundo = self._delete()
        self.assertEqual(segundo.status_code, 200, segundo.text[:300])
        self.assertFalse(segundo.json()["deleted"])

        auditorias = self.db.query(AuditLog).filter(
            AuditLog.validation_status == "PERMISOS_COLUMNA",
            AuditLog.question_prompt.like("%eliminada%"),
            AuditLog.question_prompt.like(f"%{self.role_name}%"),
        ).all()
        self.assertEqual(
            len(auditorias), 1,
            "el reset sin fila dejo un evento que no corresponde a ningun cambio",
        )

    def test_delete_valida_alcance_como_el_put(self):
        headers = make_auth_headers(self.db, "economista")
        self.assertEqual(self._delete(headers=headers).status_code, 403)

        admin_role = self.db.query(Role).filter(Role.name == ROLE_ADMINISTRADOR).first()
        self.assertIsNotNone(admin_role)
        self.assertEqual(self._delete(role_id=admin_role.id).status_code, 400)
        self.assertEqual(self._delete(connection_id=999999).status_code, 404)
        self.assertEqual(self._delete(role_id=999999).status_code, 404)
        self.assertEqual(self._delete(column_name="  ").status_code, 400)

    def test_delete_deja_el_detalle_en_auditoria(self):
        self.assertEqual(self._put("BLOCKED").status_code, 200)
        self.assertEqual(self._delete().status_code, 200)

        auditoria = self.db.query(AuditLog).filter(
            AuditLog.validation_status == "PERMISOS_COLUMNA",
            AuditLog.question_prompt.like("%eliminada%"),
            AuditLog.question_prompt.like(f"%{self.role_name}%"),
        ).first()
        self.assertIsNotNone(auditoria)
        self.assertEqual(auditoria.target_database, self.conn.name)
        self.assertIn(f"{TABLE}.{RUT}", auditoria.result_snapshot)
        self.assertIn("eliminar_politica_columna", auditoria.result_snapshot)

    # --- trazabilidad ----------------------------------------------------

    def test_toda_mutacion_de_permisos_deja_auditoria(self):
        """Conceder tablas y marcar columnas tienen que dejar autor y detalle.

        Antes, `PUT /permissions` no escribia ninguna fila: un acceso otorgado por
        error no tenia ni fecha ni responsable en el export de cumplimiento.
        """
        grant = self.client.put(
            TABLE_URL,
            params={
                "connection_id": self.conn.id,
                "role_id": self.role.id,
                "table_names": [TABLE],
                "is_allowed": True,
            },
            headers=self.admin_headers,
        )
        self.assertEqual(grant.status_code, 200, grant.text[:300])

        policy = self._put("BLOCKED")
        self.assertEqual(policy.status_code, 200, policy.text[:300])

        auditorias = self.db.query(AuditLog).filter(
            AuditLog.username == "admin",
            AuditLog.validation_status.in_(["PERMISOS_TABLA", "PERMISOS_COLUMNA"]),
            AuditLog.question_prompt.like(f"%{self.role_name}%"),
        ).all()
        self.assertEqual(sorted(a.validation_status for a in auditorias),
                         ["PERMISOS_COLUMNA", "PERMISOS_TABLA"])

        tabla = next(a for a in auditorias if a.validation_status == "PERMISOS_TABLA")
        self.assertEqual(tabla.target_database, self.conn.name)
        self.assertIn(TABLE, tabla.result_snapshot)

        columna = next(a for a in auditorias if a.validation_status == "PERMISOS_COLUMNA")
        self.assertIn(f"{TABLE}.{RUT}", columna.result_snapshot)
        self.assertIn("BLOCKED", columna.result_snapshot)

        # El detalle tiene que salir por la API, no solo existir en la tabla: si
        # el inspector no muestra QUE columna se toco, el rastro es inutil para
        # quien audita desde la UI.
        listado = self.client.get(
            "/api/v1/audit",
            params={"validation_status": "PERMISOS_COLUMNA", "username": "admin", "page_size": 50},
            headers=self.admin_headers,
        )
        self.assertEqual(listado.status_code, 200, listado.text[:300])
        filas = listado.json()["items"]
        mi_fila = next(
            (f for f in filas if self.role_name in (f["question_prompt"] or "")),
            None,
        )
        self.assertIsNotNone(mi_fila, "el evento de permisos no aparecio en el listado de auditoria")
        self.assertIn(f"{TABLE}.{RUT}", mi_fila["result_snapshot"])
        self.assertIn("BLOCKED", mi_fila["result_snapshot"])

        # Y el snapshot de una consulta NO viaja al listado: pesa hasta 100 KB y
        # ya esta representado por SQL/filas. Sin este corte, el campo nuevo
        # inflaria cada pagina de auditoria.
        ruido = AuditLog(
            username="admin",
            question_prompt=f"consulta de prueba {self.role_name}",
            validation_status="APROBADO",
            target_database=self.conn.name,
            result_snapshot='{"filas": "' + "x" * 5000 + '"}',
        )
        self.db.add(ruido)
        self.db.commit()

        listado_completo = self.client.get(
            "/api/v1/audit",
            params={"username": "admin", "page_size": 50},
            headers=self.admin_headers,
        )
        self.assertEqual(listado_completo.status_code, 200, listado_completo.text[:300])
        fila_ruido = next(
            f for f in listado_completo.json()["items"]
            if f["question_prompt"] == ruido.question_prompt
        )
        self.assertIsNone(fila_ruido["result_snapshot"],
                          "el snapshot de una consulta viajo al listado de auditoria")


if __name__ == "__main__":
    unittest.main()
