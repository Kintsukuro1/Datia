# 📋 Planes de Implementación - Proyecto Datia

**Origen:** auditoría del commit `c5f1af4` (25 de Septiembre de 2026)  
**Estado revisado:** contra el árbol actual, no contra `c5f1af4`.

> Las cifras de línea base del documento de auditoría (`103 passed, 1 failed`)
> quedaron desactualizadas: la suite de `backend/tests/` tiene hoy del orden de
> 500 tests en 65 archivos. Verificar con `cd backend && pytest` antes de
> fiarse de cualquier plan.

---

## 📊 Estado de cada plan

| ID | Plan | Estado | Qué dice el código hoy |
|---|---|---|---|
| **001** | [`001-fix-unpack-crash-and-remediation-banner.md`](001-fix-unpack-crash-and-remediation-banner.md) | ✅ **Implementado** | `build_dynamic_visualization` devuelve 5 elementos también con `rows`/`columns` vacíos (`kpi_calculator.py:155-168`); `engine.py:25` importa `logger`; el banner se antepone con `if is_remediation:` y no depende de que `conversational` sea truthy (`engine.py:530-532`). |
| **002** | [`002-enforce-read-only-in-memory-null-remediation.md`](002-enforce-read-only-in-memory-null-remediation.md) | ✅ **Implementado** | El chat remedia solo en memoria (`apply_in_memory_remediation`) y ya no llama `apply_null_policy`. La mutación física queda únicamente en el endpoint administrativo `/catalog/connections/{id}/nulls-policy` (`admin_catalog/router.py`), que exige `get_current_admin`, `confirm=true` server-side para las políticas destructivas y deja registro en `audit_logs`. La inmutabilidad de la base está cubierta por `test_null_policy_and_visualizations.py::test_prompt2_remediation_answers_original_question`. |
| **003** | [`003-connection-engine-lifecycle-and-dispose.md`](003-connection-engine-lifecycle-and-dispose.md) | ✅ **Implementado con otro diseño** | El plan pedía un `ConnectorEngineRegistry` con caché. Lo que hay es `connector_engine()` (`core/database.py:276-292`), un contextmanager que hace `dispose()` al salir, y `sql_executor.py:39-41` lo usa con `with`. Sin caché, a propósito (ver el docstring). El fallo que motiva el plan —pools huérfanos— está resuelto. |
| **004** | [`004-reduccion-de-sobreingenieria.md`](004-reduccion-de-sobreingenieria.md) | ✅ **Fases 1–4 ejecutadas y verificadas (19/10/2026)** | Auditoría de sobreingeniería del 18/10/2026 (commit `456e6f8`) y cierre del 19/10: Fase 4 completa (`INV-1` saca `holt_damped` y 222,4 MB de dependencias; `F-04`, `F-17`, `F-16`, `F-18` borrados/reemplazados; `F-12` decidido: el límite sigue siendo el literal), `F-05` (resolución única de la conexión del pedido; un `connection_id` explícito inexistente es 404 y no un fallback a otra base) y `H1` (un solo seeder de metadata, `init_db`). `DUP-05`/`DUP-08` ejecutados; `ESTADO-01` cerrado como no-go (`field-sizing` recién es Baseline 2026-06). Suite verde: pytest `689 passed` + 102 subtests, tsc exit 0, vitest 14/95, build OK. Pendiente declarado: lote Docker/deps/scripts (`DK-01..03`, `DEP-BE-04/05`, `SC-R-01`, `BS-04/05`, exige build limpio), `DUP-09` (nav/tabs sin tests de render) y `H15`. |

---

## 📌 Planes pendientes

No quedan planes de **seguridad de datos** pendientes: **002** quedó
implementado el 19/10/2026 (chat solo en memoria; escritura física únicamente
por el endpoint administrativo). De **004** quedan abiertos solo el lote de
Docker/deps/scripts (`DK-01..03`, `DEP-BE-04/05`, `SC-R-01`, `BS-04/05`), que
exige un build limpio de Docker para verificarse, y dos ítems menores con causa
declarada: `DUP-09` y `H15`.

---

## 📖 Instrucciones para el Ejecutor

1. Comprueba `git rev-parse --short HEAD` para detectar posible deriva (`drift`).
2. Ejecuta `cd backend && pytest` para tener la línea base real.
3. Sigue estrictamente los límites de alcance y los criterios de aceptación
   especificados en el documento del plan.