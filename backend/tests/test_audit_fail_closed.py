"""La auditoria de una consulta que devuelve datos no es best-effort.

Antes, `_persist_audit_log` se tragaba cualquier error y el chat devolvia filas
sin registro. Ahora el camino de datos pasa `required=True`: si el INSERT falla,
la respuesta se cancela. Los eventos de auth siguen siendo best-effort.
"""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.api.deps import (
    AUDIT_PROMPT_MAX_CHARS,
    AUDIT_SNAPSHOT_MAX_CHARS,
    AuditWriteError,
    _persist_audit_log,
)
from app.core.database import SessionLocal
from app.modules.auth.models import User
from app.modules.chat_engine import router as chat_router
from app.modules.chat_engine.schemas import QueryRequest


class _CapturingDb:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.entry = None
        self.rolled_back = False

    def add(self, entry):
        self.entry = entry

    def commit(self):
        if self.fail:
            raise RuntimeError("metadata caida")

    def rollback(self):
        self.rolled_back = True

    def refresh(self, _entry):
        return None


def _call(db, *, required: bool, prompt: str = "pregunta", snapshot=None):
    return _persist_audit_log(
        db=db,
        user_id=1,
        username="admin",
        user_role="Administrador",
        question_prompt=prompt,
        sql_generated="SELECT 1",
        validation_status="APROBADO",
        target_database="empresa",
        result_snapshot=snapshot,
        required=required,
    )


class TestAuditFailClosed(unittest.TestCase):

    def test_required_audit_raises_when_insert_fails(self):
        db = _CapturingDb(fail=True)
        with self.assertRaises(AuditWriteError):
            _call(db, required=True)
        self.assertTrue(db.rolled_back)

    def test_best_effort_audit_returns_none_when_insert_fails(self):
        db = _CapturingDb(fail=True)
        self.assertIsNone(_call(db, required=False))

    def test_prompt_is_bounded_and_oversized_snapshot_is_omitted(self):
        db = _CapturingDb()
        _call(
            db,
            required=True,
            prompt="x" * (AUDIT_PROMPT_MAX_CHARS + 1),
            snapshot="y" * (AUDIT_SNAPSHOT_MAX_CHARS + 1),
        )
        self.assertLessEqual(len(db.entry.question_prompt), AUDIT_PROMPT_MAX_CHARS + 30)
        self.assertIn("truncado", db.entry.question_prompt)
        self.assertIsNone(db.entry.result_snapshot)

    def test_chat_response_persists_audit_as_required(self):
        response = SimpleNamespace(
            traceability=None,
            data_rows=[{"id": 1}],
            response_type="data_analysis",
            summary_text="ok",
            audit_log_id=None,
            model_dump_json=lambda: "{}",
        )
        with patch.object(chat_router, "_persist_audit_log", return_value=7) as persist:
            chat_router._audit_response(
                db=object(),
                response=response,
                user_id=1,
                username="admin",
                user_role="Administrador",
                question_prompt="pregunta",
                target_database="empresa",
            )

        self.assertTrue(persist.call_args.kwargs["required"])
        self.assertEqual(response.audit_log_id, 7)

    def test_chat_query_does_not_return_if_required_audit_fails(self):
        """El endpoint real no devuelve la respuesta si la auditoría no se pudo guardar."""
        db = SessionLocal()
        try:
            user = db.query(User).filter(User.username == "admin").first()
            response = SimpleNamespace(
                traceability=None,
                data_rows=[{"id": 1}],
                response_type="data_analysis",
                summary_text="ok",
                audit_log_id=None,
                model_dump_json=lambda: "{}",
            )

            async def _fake_execute_query(**_kwargs):
                return response

            def _fake_persist(**kwargs):
                if kwargs.get("required"):
                    raise AuditWriteError("sin auditoria")
                return None

            with patch.object(chat_router.QueryEngine, "execute_query", _fake_execute_query), \
                 patch.object(chat_router, "_persist_audit_log", _fake_persist):
                with self.assertRaises(AuditWriteError):
                    asyncio.run(chat_router.process_chat_query(
                        QueryRequest(question="pregunta", connection_id=1),
                        current_user=user,
                        db=db,
                    ))
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
