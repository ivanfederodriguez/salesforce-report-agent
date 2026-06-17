CREATE TABLE tasks (
    id INTEGER PRIMARY KEY,
    created_at TEXT,
    channel_id TEXT,
    message_ts TEXT,
    user_id TEXT,
    sender_label TEXT,
    conversation_label TEXT,
    summary TEXT,
    requested_action TEXT,
    priority TEXT,
    category TEXT,
    status TEXT,
    classification_json TEXT,
    public_request_text TEXT,
    thread_ts TEXT,
    requester_label TEXT,
    updated_at TEXT
);

CREATE TABLE message_links (
    id INTEGER PRIMARY KEY,
    channel_id TEXT,
    message_ts TEXT,
    url TEXT,
    url_type TEXT,
    title TEXT,
    metadata_json TEXT
);

INSERT INTO tasks VALUES (
    123,
    '2026-06-17T12:00:00+00:00',
    'D123',
    '1718600000.000100',
    'U123',
    'Micaela Magliola',
    'DM con Micaela Magliola',
    'Informe de altas 2026 por campaña',
    'Armar un informe de altas en 2026 por campaña principal/campaña de origen, con datos personales y de donación.',
    'medium',
    'salesforce',
    'new',
    '{"summary":"Informe de altas 2026 por campaña"}',
    'Campañas: [IND] Campañas Pauta Digital, amplify, orgánico web, [IND] Redes Sociales, [IND] Redes Sociales - Instagram. Persona: nombre y apellido, fecha de nacimiento o edad, lugar de residencia. Donación: fecha establecida, estado, monto, fecha de finalización, campaña.',
    '1718600000.000100',
    'Micaela Magliola',
    '2026-06-17T12:00:00+00:00'
);

INSERT INTO message_links VALUES
    (1, 'D123', '1718600000.000100', 'https://example.lightning.force.com/lightning/r/Campaign/7011W000001buEh/view', 'salesforce', '[IND] Campañas Pauta Digital', '{"salesforce_id":"7011W000001buEh"}'),
    (2, 'D123', '1718600000.000100', 'https://example.lightning.force.com/lightning/r/Campaign/701Pe00000VtQrK/view', 'salesforce', '[IND] Redes Sociales', '{"salesforce_id":"701Pe00000VtQrK"}'),
    (3, 'D123', '1718600000.000100', 'https://example.lightning.force.com/lightning/r/Campaign/701Pe00000QysD4IAJ/view', 'salesforce', '[IND] Redes Sociales - Instagram', '{"salesforce_id":"701Pe00000QysD4IAJ"}');
