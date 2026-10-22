"""`_schema_field_for_role` era fail-closed para tablas y fail-OPEN para columnas.

Ante cualquier excepcion devolvia `set()`. Para `allowed_tables` un set vacio
deniega, pero para `blocked_columns` y `masked_columns` un set vacio significa
"ninguna columna bloqueada / ninguna enmascarada": la request seguia y el valor
sensible salia en claro. El mismo `set()` tambien disfrazaba de RBAC un fallo de
infraestructura: la matriz no se pudo resolver, no es que el perfil no tenga
permisos.

La politica nueva es explicita: si la matriz RBAC no se puede resolver, la
request falla con 503. Una `HTTPException` deliberada de la capa de abajo (un 404
de conexion, por ejemplo) se re-lanza sin reescribirla.
"""

import unittest
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

from app.core.constants import ROLE_ANALISTA_FINANCIERO
from app.modules.chat_engine.dynamic_schema import DynamicSchemaPruningService
from app.modules.chat_engine.governance_guard import GovernanceGuard


class TestSchemaFieldFailClosed(unittest.TestCase):
    def _getters(self):
        return (
            ("allowed_tables", GovernanceGuard.get_allowed_tables_for_role),
            ("blocked_columns", GovernanceGuard.get_blocked_columns_for_role),
            ("masked_columns", GovernanceGuard.get_masked_columns_for_role),
        )

    def test_un_fallo_de_la_matriz_lanza_503_en_los_tres_campos(self):
        for campo, getter in self._getters():
            with self.subTest(campo=campo):
                with patch.object(
                    DynamicSchemaPruningService,
                    "get_authorized_schema_prompt",
                    side_effect=RuntimeError("metadata de permisos caida"),
                ):
                    with self.assertRaises(HTTPException) as ctx:
                        getter(ROLE_ANALISTA_FINANCIERO, False, db=MagicMock(),
                               role_id=2, connection_id=1)

                self.assertEqual(
                    ctx.exception.status_code, 503,
                    f"{campo} no fallo cerrado: devolvio {ctx.exception}",
                )
                self.assertIn("matriz RBAC", str(ctx.exception.detail))

    def test_el_detalle_no_filtra_el_error_interno(self):
        with patch.object(
            DynamicSchemaPruningService,
            "get_authorized_schema_prompt",
            side_effect=RuntimeError("metadata de permisos caida"),
        ):
            with self.assertRaises(HTTPException) as ctx:
                GovernanceGuard.get_blocked_columns_for_role(
                    ROLE_ANALISTA_FINANCIERO, False, db=MagicMock(),
                    role_id=2, connection_id=1,
                )
        self.assertNotIn("metadata de permisos caida", str(ctx.exception.detail))

    def test_una_http_exception_deliberada_se_relanza_sin_reescribir(self):
        """El 404 de conexion no es un fallo de RBAC y no puede convertirse en 503."""
        with patch.object(
            DynamicSchemaPruningService,
            "get_authorized_schema_prompt",
            side_effect=HTTPException(status_code=404, detail="Conexión corporativa 9 no encontrada."),
        ):
            with self.assertRaises(HTTPException) as ctx:
                GovernanceGuard.get_masked_columns_for_role(
                    ROLE_ANALISTA_FINANCIERO, False, db=MagicMock(),
                    role_id=2, connection_id=9,
                )
        self.assertEqual(ctx.exception.status_code, 404)
        self.assertIn("no encontrada", str(ctx.exception.detail))

    def test_sin_sesion_y_sin_pool_tambien_falla_cerrado(self):
        with patch("app.core.database.SessionLocal", side_effect=RuntimeError("sin pool")):
            with self.assertRaises(HTTPException) as ctx:
                GovernanceGuard.get_masked_columns_for_role(
                    ROLE_ANALISTA_FINANCIERO, False, db=None, role_id=2, connection_id=1,
                )
        self.assertEqual(ctx.exception.status_code, 503)

    def test_un_resultado_vacio_legitimo_sigue_siendo_un_set(self):
        """Un perfil que de verdad no tiene columnas restringidas no es un error."""
        with patch.object(
            DynamicSchemaPruningService,
            "get_authorized_schema_prompt",
            return_value={"blocked_columns": set(), "masked_columns": set(),
                          "allowed_tables": {"fact_ventas"}},
        ):
            self.assertEqual(
                GovernanceGuard.get_blocked_columns_for_role(
                    ROLE_ANALISTA_FINANCIERO, False, db=MagicMock(), role_id=2, connection_id=1,
                ),
                set(),
            )


if __name__ == "__main__":
    unittest.main()
