# Pipeline Contract

Use this reference when the user needs a consistent capability contract, canonical object vocabulary, or release checklist across ontology pipeline stages.

## Capability contract

Every ontology engineering capability should preserve these fields in its outputs:

| Field | Meaning |
| --- | --- |
| `input_objects` | Source artifacts accepted by the capability |
| `output_objects` | New ontology artifacts emitted by the capability |
| `prerequisites` | Required earlier stages or preconditions |
| `ai_capabilities` | LLM, embedding, rule, or deterministic logic involved |
| `human_review` | `none`, `recommended`, or `required` |
| `rollback` | Whether the output can be reverted without destructive manual work |
| `version` | Capability contract version |
| `composable` | Whether the output can be used as a workflow node |

## Canonical industrial object families

Start from reusable object families before adding site-specific variants:

- `Factory`, `Workshop`, `ProductionLine`, `ProcessSegment`
- `Machine`, `EquipmentModule`, `Sensor`, `Actuator`, `Alarm`
- `Product`, `MaterialLot`, `Recipe`, `WorkOrder`, `Batch`, `Shift`
- `Operator`, `Team`, `MaintenanceTask`, `InspectionRecord`
- `UtilitySystem`, `Meter`, `EmissionPoint`, `QualitySample`
- `Document`, `SOP`, `Manual`, `MaintenanceRecord`, `Image`, `Video`

## Canonical relation verbs

Prefer typed verbs instead of generic `relatedTo`:

- `contains`
- `belongsTo`
- `partOf`
- `uses`
- `produces`
- `consumes`
- `generatedBy`
- `measuredBy`
- `controlledBy`
- `maintainedBy`
- `documents`
- `appliesTo`

## Naming and unit rules

- Use PascalCase for ontology object names.
- Use camelCase for properties and behaviors.
- Preserve raw aliases exactly as observed in source systems.
- Normalize units to engineering names such as `celsius`, `bar`, `rpm`, `kWh`, `Nm3`.
- Separate display labels from canonical identifiers.
- Reserve `status` for business state, not transport or connector health.

## Release artifact checklist

Do not release an ontology increment without:

1. Scope and boundary statement.
2. Candidate-to-canonical merge rationale.
3. Relation evidence notes.
4. Mapping table with source ownership and read/write direction.
5. Behavior safety notes for write operations.
6. Validation report with open issues.
7. Version number, change summary, and rollback note.
8. Evolution trigger describing what future drift should be rescanned.
