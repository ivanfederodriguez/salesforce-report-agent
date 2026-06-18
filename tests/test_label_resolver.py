import json
from datetime import UTC, datetime
from pathlib import Path

import openpyxl
import pandas as pd

from sf_report_agent.reports.exporters import export_report
from sf_report_agent.salesforce.label_resolver import SalesforceLabelResolver


def _snapshot() -> dict[str, object]:
    return {
        "objects": {
            "Donation__c": {
                "fields": [
                    {"name": "Amount__c", "label": "Importe", "type": "currency"},
                    {"name": "First_Name__c", "label": "Nombre", "type": "string"},
                    {"name": "Other_Name__c", "label": "Nombre", "type": "string"},
                    {
                        "name": "Contact__c",
                        "label": "Contacto",
                        "type": "reference",
                        "referenceTo": ["Contact"],
                        "relationshipName": "Contact__r",
                    },
                ]
            },
            "Contact": {
                "fields": [
                    {"name": "Name", "label": "Nombre", "type": "string"},
                    {
                        "name": "Birthdate",
                        "label": "Fecha de nacimiento",
                        "type": "date",
                    },
                ]
            },
        }
    }


def test_resolves_direct_and_relationship_labels_and_disambiguates_duplicates() -> None:
    resolver = SalesforceLabelResolver(_snapshot())

    labels = resolver.resolve(
        "Donation__c",
        ["Amount__c", "Contact__r.Birthdate", "First_Name__c", "Other_Name__c"],
    )

    assert labels["Amount__c"] == "Importe"
    assert labels["Contact__r.Birthdate"] == "Contacto: Fecha de nacimiento"
    assert labels["First_Name__c"] != labels["Other_Name__c"]
    assert labels["First_Name__c"].startswith("Nombre (")


def test_csv_xlsx_use_readable_headers_and_metadata_keeps_api_mapping(tmp_path: Path) -> None:
    dataframe = pd.DataFrame(
        [{"Amount__c": 100, "Contact__r.Birthdate": "1990-01-01"}]
    )
    labels = SalesforceLabelResolver(_snapshot()).resolve(
        "Donation__c", list(dataframe.columns)
    )
    metadata = {"api_name_to_label": labels}

    paths = export_report(
        dataframe.rename(columns=labels),
        task_id=1,
        title="Reporte con labels",
        artifacts_dir=tmp_path,
        output_formats=["csv", "xlsx"],
        metadata=metadata,
        warnings=[],
        generated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    csv_path = next(path for path in paths if path.suffix == ".csv")
    xlsx_path = next(path for path in paths if path.suffix == ".xlsx")
    assert csv_path.read_text(encoding="utf-8").splitlines()[0] == (
        "Importe,Contacto: Fecha de nacimiento"
    )
    workbook = openpyxl.load_workbook(xlsx_path, read_only=True)
    assert [cell.value for cell in next(workbook["datos"].iter_rows())] == [
        "Importe",
        "Contacto: Fecha de nacimiento",
    ]
    metadata_values = [row[1].value for row in workbook["metadata"].iter_rows(min_row=2)]
    assert json.dumps(labels, ensure_ascii=False) in metadata_values
