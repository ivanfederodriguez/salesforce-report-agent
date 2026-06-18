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

## Salesforce con MFA / OAuth

OAuth es el modo recomendado y el default. Si Salesforce exige MFA y no permite resetear el security token, creá una Connected App con Authorization Code Flow y configurá:

- callback URL: `http://localhost:8765/callback`;
- scopes: `api refresh_token offline_access`;
- una política de refresh token compatible con ejecuciones posteriores.

En `.env`:

```dotenv
SALESFORCE_AUTH_MODE=oauth
SALESFORCE_CLIENT_ID=consumer-key-de-la-connected-app
SALESFORCE_CLIENT_SECRET=consumer-secret-de-la-connected-app
SALESFORCE_REDIRECT_URI=http://localhost:8765/callback
SALESFORCE_TOKEN_PATH=.salesforce_token.json
SALESFORCE_DOMAIN=login
SF_READ_ONLY=true
```

Para autenticar y probar:

```bash
python -m sf_report_agent.main sf-oauth-login
python -m sf_report_agent.main sf-auth-status
python -m sf_report_agent.main sf-doctor
python -m sf_report_agent.main sf-doctor
```

`sf-oauth-login` abre o imprime la URL de Salesforce, recibe el callback local después del MFA y guarda los tokens en `.salesforce_token.json`. El archivo está ignorado por Git, se crea con permisos `0600` y sus secretos nunca se imprimen. Las siguientes ejecuciones refrescan el access token automáticamente; correr `sf-doctor` dos veces seguidas no debe abrir el navegador ni pedir MFA mientras el refresh token siga vigente.

También se puede proveer `SALESFORCE_REFRESH_TOKEN` y `SALESFORCE_INSTANCE_URL` por entorno. `SALESFORCE_ACCESS_TOKEN` se reconoce como configuración, pero las corridas normales exigen y refrescan un refresh token desde entorno o desde `SALESFORCE_TOKEN_PATH`.

Si la organización bloquea Connected Apps no aprobadas, un administrador puede tener que autorizar la app y sus políticas OAuth.

## Salesforce CLI mode (login web + MFA)

Si ya podés entrar con login web y MFA, pero no tenés una Connected App, el agente puede reutilizar una autenticación administrada por Salesforce CLI. Primero autorizá la org y asignale un alias:

```bash
sf org login web --instance-url https://login.salesforce.com --alias techo
```

Luego configurá `.env`:

```dotenv
SALESFORCE_AUTH_MODE=sf_cli
SALESFORCE_CLI_ALIAS=techo
SF_READ_ONLY=true
```

No hacen falta `SALESFORCE_CLIENT_ID`, `SALESFORCE_CLIENT_SECRET`, contraseña ni security token. Para verificar la sesión:

```bash
python -m sf_report_agent.main sf-auth-status
python -m sf_report_agent.main sf-doctor
```

En cada ejecución, el agente pide a `sf org display --target-org techo --json` los datos de la org. Si Salesforce CLI entrega el token oculto, lo recupera con `sf org auth show-access-token --target-org techo --json`; no requiere `SF_TEMP_SHOW_SECRETS=true`. El access token se usa solo en memoria: no se imprime, no se copia a `.env` y no se guarda en artifacts. Si la sesión dejó de ser válida, repetí `sf org login web --instance-url https://login.salesforce.com --alias techo`.

Este modo no reduce los permisos del usuario autenticado en Salesforce; la garantía read-only sigue estando en el cliente y los validadores del agente.

## Password mode legacy

En `.env`:

```dotenv
SALESFORCE_AUTH_MODE=password
SALESFORCE_USERNAME=usuario@example.org
SALESFORCE_PASSWORD=contraseña
SALESFORCE_SECURITY_TOKEN=token
SALESFORCE_DOMAIN=login
SF_READ_ONLY=true
```

Este modo conserva el flujo anterior y requiere security token. Para sandbox usá el dominio que corresponda a la autenticación de la org. Las credenciales no se imprimen ni se guardan en artifacts. `SF_READ_ONLY=false` es rechazado al iniciar.

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

El resolver describe objetos candidatos y solo usa campos visibles. El mapping aporta pistas semánticas; `describe` es la verdad técnica para API names, tipos, labels y relaciones. El ejemplo está en [`config/field_mapping.example.json`](config/field_mapping.example.json). Para usar un mapping revisado:

```dotenv
FIELD_MAPPING_PATH=config/field_mapping.json
```

Un valor `null` produce una advertencia y, si el campo fue solicitado, una aclaración; el agente no inventa campos. Las fuentes de origen también requieren mapping explícito, por ejemplo `"campaña_origen": "LeadSource"` dentro de `donation.fields`.

El objeto de donación puede definir `date_field` para el filtro anual y uno o más `campaign_filter_fields`. Si metadata expone varios lookups seguros a Campaign, el planner genera una variante por lookup y otra combinada con `OR`; no frena por una ambigüedad read-only y acotada. `campaign_relationships` permite incluir el nombre relacionado en el `SELECT`, sin asumir `CampaignId`, `CloseDate` ni nombres de relaciones universales. El mapping real NPSP está en [`config/field_mapping.json`](config/field_mapping.json), donde `npe03__Date_Established__c` representa la fecha de alta.

Antes de exportar, los API names se renombran con los labels reales de Salesforce. Para relaciones se combina el label del lookup y el campo relacionado, por ejemplo `Contacto: Fecha de nacimiento`. Cada metadata JSON conserva el diccionario `api_name_to_label` para auditoría.

Los datos personales sobre un objeto de donación solo se agregan cuando el mapping incluye una relación inequívoca, por ejemplo:

```json
{
  "relationships": {
    "person_from_donation": "npsp__Primary_Contact__r"
  }
}
```

Según el modelo de la org, `person_from_donation` podría ser `npsp__Primary_Contact__r`, `Contact` u otra relación custom. Confirmala con `inspect-schema`; no es un nombre universal. Con `ALLOW_REPORT_WITHOUT_PERSON_FIELDS=false` (default), si el pedido requiere datos personales y la relación falta o no es visible, la corrida termina en `needs_clarification` con preguntas para Iván. En `true`, el reporte puede continuar sin esos campos y deja una advertencia.

## CLI

Con el entorno virtual activo:

```bash
python -m sf_report_agent.main doctor
python -m sf_report_agent.main sf-oauth-login
python -m sf_report_agent.main sf-auth-status
python -m sf_report_agent.main sf-doctor
python -m sf_report_agent.main inspect-schema --object Opportunity
python -m sf_report_agent.main inspect-schema --object Contact --filter campaign
python -m sf_report_agent.main list-tasks --limit 20
python -m sf_report_agent.main run-task --task-id 123 --dry-run
python -m sf_report_agent.main run-task --task-id 123
python -m sf_report_agent.main run-once --dry-run
python -m sf_report_agent.main run-once
```

`doctor` valida SQLite, DB propia, artifacts, Ollama y presencia del modelo. `sf-auth-status` verifica el modo y, en OAuth, prueba el refresh; en `sf_cli`, valida la sesión del alias sin mostrar el access token. `sf-doctor` usa password, refresh token o la sesión de Salesforce CLI según `SALESFORCE_AUTH_MODE`, muestra el modo/instance URL, intenta `describe`, `SELECT ... LIMIT 1`, campos visibles y las tres Campaign IDs del fixture; guarda el resultado en `artifacts/permission_reports/`.

`inspect-schema` lista label, API name, tipo y `referenceTo` de los campos visibles. Guarda cada inspección en `artifacts/schema/<object>_describe_<timestamp>.json`.

El dry-run no crea cliente Salesforce: interpreta, planifica, valida y exporta un dataset vacío con SOQL limitado a 200 filas. Una ejecución real exige credenciales y limita la exportación a `MAX_EXPORT_ROWS`.

## Flujo LangGraph

```text
START -> load_task -> parse_request -> resolve_salesforce_schema
      -> check_permissions -> build_report_plan -> validate_plan
      -> [needs_clarification] compose_clarification_response -> persist_result
      -> [valid] build_soql -> validate_soql -> execute_query
      -> transform_dataset -> quality_checks -> export_report
      -> compose_response -> persist_result -> END
```

Cada corrida y cada variante quedan auditadas en `salesforce_report_agent.db`. Los reportes se escriben en:

- `artifacts/reports/task_<id>_<slug>_<timestamp>.csv`;
- `artifacts/reports/task_<id>_<slug>_<timestamp>.xlsx`;
- `artifacts/runs/task_<id>_<variant>_<timestamp>.json`.

El XLSX contiene `datos`, `metadata` y, cuando corresponde, `warnings`.

## Seguridad y PII

- La API pública del cliente solo expone operaciones de lectura.
- El validador admite únicamente una sentencia `SELECT`, sin comentarios ni keywords destructivas.
- Los Campaign IDs y nombres de API se validan antes de construir SOQL.
- `LOG_PII=false` evita dumps de registros; ninguna contraseña o token entra en logs/metadata.
- `.salesforce_token.json` está excluido de Git y nunca se imprime en consola.
- Los access tokens obtenidos desde Salesforce CLI se usan solo en memoria.
- `REQUIRE_HUMAN_APPROVAL_FOR_PII=true` deja el resultado como `done_pending_approval`.
- `ALLOW_REPORT_WITHOUT_PERSON_FIELDS=false` evita generar silenciosamente un reporte incompleto.
- `UPDATE_SOURCE_TASK=false` mantiene la SQLite fuente intacta.
- `ALLOW_SALESFORCE_REPORT_CREATE=false` deshabilita por defecto la creación opcional de Reports en Salesforce. Si se habilita y la operación falla, los CSV/XLSX locales se conservan y la corrida termina con una advertencia.
- Nunca hay envío automático a Slack.

## Tests y calidad

```bash
pytest
ruff check .
mypy src
```

La suite cubre extracción de IDs, parsing, bundles de planes, variantes SOQL seguras, labels de metadata, exports múltiples, persistencia por variante, permission doctor y el grafo completo con Salesforce simulado y artifacts reales.
