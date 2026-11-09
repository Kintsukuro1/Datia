"""Validaciones del upload que no dependen de PostgreSQL.

Cubren dos huecos: la URL de mantenimiento no escapaba la clave (rompia con
`@`, `:` o `/`) y el servicio aceptaba `.xls` aunque `openpyxl` no lo lee,
ademas de copiar sin limite de tamano.
"""
import asyncio
import io
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy.engine import make_url
from starlette.datastructures import UploadFile

from app.core.config import settings
from app.modules.catalog.services.connector_service import ConnectorDomainService


def _upload(filename: str, content: bytes):
    return UploadFile(file=io.BytesIO(content), filename=filename)


class TestConnectorUrls(unittest.TestCase):

    def test_url_escapes_password_with_special_characters(self):
        with patch.object(settings, "POSTGRES_PASSWORD", "p@ss:word/123"):
            url = ConnectorDomainService._maintenance_url()
        self.assertEqual(make_url(url).password, "p@ss:word/123")

    def test_dataset_url_uses_the_requested_database(self):
        url = ConnectorDomainService._dataset_url("dataset_123")
        self.assertEqual(make_url(url).database, "dataset_123")


class TestUploadValidation(unittest.TestCase):

    def test_legacy_xls_is_rejected_with_a_clear_message(self):
        file = _upload("datos.xls", b"not-an-excel")
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(ConnectorDomainService.upload_database_file(None, file))
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn(".xlsx", ctx.exception.detail)

    def test_upload_over_the_server_limit_is_rejected(self):
        file = _upload("datos.csv", b"x" * 2048)
        with patch.object(settings, "MAX_UPLOAD_BYTES", 1024):
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(ConnectorDomainService.upload_database_file(None, file))
        self.assertEqual(ctx.exception.status_code, 413)
        self.assertIn("límite", ctx.exception.detail)


if __name__ == "__main__":
    unittest.main()
