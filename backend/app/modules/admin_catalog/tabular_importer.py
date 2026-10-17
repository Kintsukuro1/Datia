from typing import List, Optional
from .importers.base import sanitize_identifier
from .importers.postgres_importer import (
    infer_postgres_type,
    import_csv_to_postgres,
    import_excel_to_postgres,
    import_sqlite_to_postgres,
    import_sql_script_to_postgres,
)

__all__ = [
    "sanitize_identifier",
    "convert_uploaded_file_to_postgres",
    "infer_postgres_type",
    "import_csv_to_postgres",
    "import_excel_to_postgres",
    "import_sqlite_to_postgres",
    "import_sql_script_to_postgres",
]

def convert_uploaded_file_to_postgres(source_path: str, ext: str, target_engine, table_name: Optional[str] = None) -> List[str]:
    """
    Orchestrates the conversion and loading of any supported file format (.csv, .xlsx, .xls, .sql, .sqlite, .db)
    directly into a target PostgreSQL engine (democratizacion_empresa) and returns the created table names.
    """
    clean_ext = ext.lower().strip()

    if clean_ext in [".csv", ".txt", ".tsv"]:
        return import_csv_to_postgres(source_path, target_engine, table_name=table_name)

    elif clean_ext in [".xlsx", ".xlsm", ".xltx", ".xls"]:
        return import_excel_to_postgres(source_path, target_engine)

    elif clean_ext in [".sqlite", ".db", ".sqlite3"]:
        return import_sqlite_to_postgres(source_path, target_engine)

    elif clean_ext == ".sql":
        raise ValueError(
            "Los scripts .sql no se aceptan: ejecutan DDL/DML arbitrario contra el "
            "PostgreSQL destino sin pasar por el validador AST. Sube el dataset como "
            ".csv, .xlsx o .sqlite."
        )

    else:
        raise ValueError(f"Formato no soportado: {clean_ext}. Formatos permitidos: .sqlite, .db, .sqlite3, .csv, .xlsx, .xls, .sql")
