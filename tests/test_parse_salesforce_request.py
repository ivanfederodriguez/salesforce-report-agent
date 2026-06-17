from sf_report_agent.models.task import ExternalTask
from sf_report_agent.salesforce.field_mapper import parse_salesforce_request


def test_parses_micaela_request_deterministically(micaela_task: ExternalTask) -> None:
    result = parse_salesforce_request(micaela_task)

    assert result.report_type == "altas_por_campaña"
    assert result.year == 2026
    assert result.campaign_ids == [
        "7011W000001buEh",
        "701Pe00000VtQrK",
        "701Pe00000QysD4IAJ",
    ]
    assert result.campaign_names == [
        "[IND] Campañas Pauta Digital",
        "[IND] Redes Sociales",
        "[IND] Redes Sociales - Instagram",
    ]
    assert result.origin_sources == ["amplify", "orgánico web"]
    assert result.person_fields == [
        "nombre_y_apellido",
        "fecha_nacimiento_o_edad",
        "lugar_de_residencia",
    ]
    assert result.donation_fields == [
        "fecha_establecida",
        "estado",
        "monto",
        "fecha_de_finalizacion",
        "campaña",
    ]
    assert result.missing_information == []
