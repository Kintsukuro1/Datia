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
| **004** | [`004-reduccion-de-sobreingenieria.md`](004-reduccion-de-sobreingenieria.md) | ✅ **Fases 1–3 ejecutadas y verificadas (19/10/2026)** | Auditoría de sobreingeniería del 18/10/2026 (commit `456e6f8`): 87 archivos, `+669/−1624` (−955 netas). Diferidos con causa dentro del plan: `H1`, `F-05`, `H15`, `DUP-05/08/09`, ítems Docker/deps. Suite verde al commitear: pytest `686 passed` + 102 subtests, tsc exit 0, vitest 14/95, build OK. Fase 4 (decisiones de producto) espera OK explícito. |

---

## 📌 Planes pendientes

No quedan planes de **seguridad de datos** pendientes: **002** quedó
implementado el 19/10/2026 (chat solo en memoria; escritura física únicamente
por el endpoint administrativo). El único trabajo abierto es la **Fase 4 de
004** (decisiones de producto), que espera OK explícito.

---

## 📖 Instrucciones para el Ejecutor

1. Comprueba `git rev-parse --short HEAD` para detectar posible deriva (`drift`).
2. Ejecuta `cd backend && pytest` para tener la línea base real.
3. Sigue estrictamente los límites de alcance y los criterios de aceptación
   especificados en el documento del plan.