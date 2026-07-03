example_execution_policy = {
    "packages": [
        "PyYAML==6.0.2",
        "pydantic==2.11.4",
    ]
}


definition = {
    "name": "yaml_contract_validator_demo",
    "description": "解析 YAML 并用 Pydantic 做契约校验，需要 PyYAML + pydantic",
    "category": "demo",
    "parameters": [
        {
            "name": "yaml_text",
            "type": "string",
            "description": "待校验的 YAML 文本",
            "required": True,
        }
    ],
}


def execute(params: dict):
    import yaml
    from pydantic import BaseModel, Field, ValidationError, field_validator

    recommended_packages = [
        "PyYAML==6.0.2",
        "pydantic==2.11.4",
    ]

    class ServiceConfig(BaseModel):
        name: str = Field(min_length=2, max_length=50)
        owner: str = Field(min_length=2, max_length=50)
        replicas: int = Field(ge=1, le=20)
        cpu_limit: float = Field(gt=0, le=8)
        memory_mb: int = Field(ge=128, le=16384)
        tags: list[str] = Field(default_factory=list)
        env: dict[str, str] = Field(default_factory=dict)

        @field_validator("tags")
        @classmethod
        def validate_tags(cls, value: list[str]) -> list[str]:
            unique = []
            seen = set()
            for item in value:
                normalized = item.strip().lower()
                if not normalized or normalized in seen:
                    continue
                seen.add(normalized)
                unique.append(normalized)
            return unique

    yaml_text = str(params.get("yaml_text", "")).strip()
    if not yaml_text:
        raise ValueError("yaml_text is required")

    parsed = yaml.safe_load(yaml_text)
    if not isinstance(parsed, dict):
        raise ValueError("yaml_text must parse to an object")

    print("yaml parsed, validating contract")

    try:
        config = ServiceConfig.model_validate(parsed)
    except ValidationError as exc:
        return {
            "success": False,
            "errors": exc.errors(),
            "recommended_packages": recommended_packages,
        }

    normalized = config.model_dump()
    warnings = []
    if normalized["replicas"] >= 10 and normalized["cpu_limit"] >= 4:
        warnings.append("High replica count with high CPU limit may be expensive.")
    if "prod" not in normalized["tags"]:
        warnings.append("tags does not include 'prod'; treat this as non-production by default.")

    return {
        "success": True,
        "normalized": normalized,
        "warnings": warnings,
        "recommended_packages": recommended_packages,
    }
