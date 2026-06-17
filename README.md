# Salesforce Report Agent

Worker local que convierte tareas generadas por `slack-automatizacion` en reportes auditables de Salesforce. Lee el intake desde SQLite, interpreta el pedido con reglas determinísticas y Ollama, descubre el schema real de la org, valida permisos, construye SOQL read-only, exporta CSV/XLSX y deja una respuesta en español lista para aprobación.

No lee Slack ni envía mensajes. Tampoco crea, modifica o elimina registros de Salesforce.

## Relación con el agente de Slack

`slack-automatizacion` sigue siendo responsable del intake, clasificación, SQLite y flujo de aprobación. Este repositorio es un worker separado y solo consume:

- `tasks`, para el pedido clasificado;
- `message_links`, para URLs e IDs de Campaign;
- opcionalmente `tasks.status`, únicamente si `UPDATE_SOURCE_TASK=true` y la corrida real terminó bien.

Si el checkout local del agente fuente se llama `slack-personal-agent`, configurá `SOURCE_DB_PATH=../slack-personal-agent/slack_agent.db`.

## Instalación

Requiere Python 3.12 o superior.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
cp .env.example .env
```

## Ollama

El MVP usa exclusivamente un modelo local. Instalá Ollama, descargá/iniciá el modelo y dejá el servicio escuchando en localhost:

```bash
ollama run gemma4:e2b-mlx
```

Defaults:

```dotenv
MODEL_PROVIDER=ollama
OLLAMA_MODEL=gemma4:e2b-mlx
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_TEMPERATURE=0
```

El parser extrae primero Campaign IDs, año, campañas/fuentes y campos mediante reglas confiables. Solo consulta Ollama cuando necesita completar información faltante, y nunca permite que el modelo reemplace IDs ya extraídos.

## Configuración de Salesforce

En `.env`:

```dotenv
SALESFORCE_USERNAME=usuario@example.org
SALESFORCE_PASSWORD=contraseña
SALESFORCE_SECURITY_TOKEN=token
SALESFORCE_DOMAIN=login
SF_READ_ONLY=true
```

Para sandbox usá el dominio que corresponda a la autenticación de la org. Las credenciales no se imprimen ni se guardan en artifacts. `SF_READ_ONLY=false` es rechazado al iniciar.

Permisos mínimos recomendados:

- sistema `API Enabled`;
- lectura de `Campaign` y, si se usa, `CampaignMember`;
- lectura de `Contact` y/o `Account`;
- lectura del objeto real de donaciones (`Opportunity`, NPSP u objeto custom);
- Field-Level Security visible para campos personales y de donación;
- acceso a los registros de las campañas pedidas;
- autorización de Connected App si la autenticación de la org la requiere.

No se requieren ni se recomiendan permisos de escritura.

## Schema y field mapping

El resolver describe objetos candidatos y solo usa campos visibles. El ejemplo está en [`config/field_mapping.example.json`](config/field_mapping.example.json). Para usar un mapping revisado:

```dotenv
FIELD_MAPPING_PATH=config/field_mapping.json
```

Un valor `null` produce una advertencia; el agente no inventa campos. Los datos personales sobre un objeto de donación solo se agregan cuando el mapping incluye una relación inequívoca, por ejemplo:

```json
{
  "relationships": {
    "person_from_donation": "npsp__Primary_Contact__r"
  }
}
```

## CLI

Con el entorno virtual activo:

```bash
python -m sf_report_agent.main doctor
python -m sf_report_agent.main sf-doctor
python -m sf_report_agent.main list-tasks --limit 20
python -m sf_report_agent.main run-task --task-id 123 --dry-run
python -m sf_report_agent.main run-task --task-id 123
python -m sf_report_agent.main run-once --dry-run
python -m sf_report_agent.main run-once
```

`doctor` valida SQLite, DB propia, artifacts, Ollama y presencia del modelo. `sf-doctor` intenta login, `describe`, `SELECT ... LIMIT 1`, campos visibles y las tres Campaign IDs del fixture; guarda el resultado en `artifacts/permission_reports/`.

El dry-run no crea cliente Salesforce: interpreta, planifica, valida y exporta un dataset vacío con SOQL limitado a 200 filas. Una ejecución real exige credenciales y limita la exportación a `MAX_EXPORT_ROWS`.

## Flujo LangGraph

```text
START -> load_task -> parse_request -> resolve_salesforce_schema
      -> check_permissions -> build_report_plan -> validate_plan
      -> build_soql -> validate_soql -> execute_query
      -> transform_dataset -> quality_checks -> export_report
      -> compose_response -> persist_result -> END
```

Cada corrida queda auditada en `salesforce_report_agent.db`. Los reportes se escriben en:

- `artifacts/reports/task_<id>_<slug>_<timestamp>.csv`;
- `artifacts/reports/task_<id>_<slug>_<timestamp>.xlsx`;
- `artifacts/runs/task_<id>_<timestamp>.json`.

El XLSX contiene `datos`, `metadata` y, cuando corresponde, `warnings`.

## Seguridad y PII

- La API pública del cliente solo expone operaciones de lectura.
- El validador admite únicamente una sentencia `SELECT`, sin comentarios ni keywords destructivas.
- Los Campaign IDs y nombres de API se validan antes de construir SOQL.
- `LOG_PII=false` evita dumps de registros; ninguna contraseña o token entra en logs/metadata.
- `REQUIRE_HUMAN_APPROVAL_FOR_PII=true` deja el resultado como `done_pending_approval`.
- `UPDATE_SOURCE_TASK=false` mantiene la SQLite fuente intacta.
- Nunca hay envío automático a Slack.

## Tests y calidad

```bash
pytest
ruff check .
mypy src
```

La suite cubre extracción de IDs, parsing del pedido de Micaela, SOQL seguro, permission doctor y el grafo completo con Salesforce simulado y artifacts reales.
