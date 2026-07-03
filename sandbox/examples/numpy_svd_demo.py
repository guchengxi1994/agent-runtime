example_execution_policy = {
    "packages": [
        "numpy==2.1.3",
    ]
}


definition = {
    "name": "numpy_svd_demo",
    "description": "使用 numpy 随机生成方阵并计算奇异值分解，返回结构化结果",
    "category": "demo",
    "parameters": [
        {
            "name": "size",
            "type": "integer",
            "description": "方阵维度，建议 2 到 8 之间，避免返回结果过大",
            "required": False,
            "default": 4,
        },
        {
            "name": "seed",
            "type": "integer",
            "description": "随机种子，便于复现实验结果",
            "required": False,
            "default": 7,
        },
        {
            "name": "distribution",
            "type": "string",
            "description": "随机分布类型",
            "required": False,
            "default": "normal",
            "enum": ["normal", "uniform"],
        },
        {
            "name": "scale",
            "type": "number",
            "description": "随机值缩放系数；normal 时作为标准差，uniform 时作为上下界绝对值",
            "required": False,
            "default": 1.0,
        },
        {
            "name": "round_digits",
            "type": "integer",
            "description": "返回结果保留的小数位数",
            "required": False,
            "default": 6,
        },
    ],
}


def execute(params: dict):
    import numpy as np

    recommended_packages = [
        "numpy==2.1.3",
    ]

    size = int(params.get("size", 4) or 4)
    seed = int(params.get("seed", 7) or 7)
    distribution = str(params.get("distribution", "normal") or "normal").strip().lower()
    scale = float(params.get("scale", 1.0) or 1.0)
    round_digits = int(params.get("round_digits", 6) or 6)

    if size < 2 or size > 8:
        raise ValueError("size must be between 2 and 8")
    if distribution not in {"normal", "uniform"}:
        raise ValueError("distribution must be one of: normal, uniform")
    if scale <= 0:
        raise ValueError("scale must be greater than 0")
    if round_digits < 0 or round_digits > 12:
        raise ValueError("round_digits must be between 0 and 12")

    rng = np.random.default_rng(seed)
    print(f"generating {size}x{size} matrix, distribution={distribution}, seed={seed}")

    if distribution == "uniform":
        matrix = rng.uniform(-scale, scale, size=(size, size))
    else:
        matrix = rng.normal(loc=0.0, scale=scale, size=(size, size))

    print("running numpy.linalg.svd")
    u, singular_values, vt = np.linalg.svd(matrix, full_matrices=True)
    sigma = np.diag(singular_values)
    reconstructed = u @ sigma @ vt

    max_abs_error = float(np.max(np.abs(matrix - reconstructed)))
    fro_error = float(np.linalg.norm(matrix - reconstructed, ord="fro"))
    fro_norm = float(np.linalg.norm(matrix, ord="fro"))
    spectral_norm = float(singular_values[0])
    smallest_singular = float(singular_values[-1])
    condition_number = float(spectral_norm / smallest_singular) if smallest_singular > 0 else None

    energy = singular_values ** 2
    total_energy = float(np.sum(energy))
    explained_energy_ratio = (energy / total_energy).tolist() if total_energy > 0 else []

    def round_scalar(value: float) -> float:
        return round(float(value), round_digits)

    def round_matrix(value) -> list[list[float]]:
        return np.round(value, round_digits).tolist()

    is_orthonormal_u = bool(
        np.allclose(u.T @ u, np.eye(size), atol=10 ** (-(min(round_digits, 8))))
    )
    is_orthonormal_v = bool(
        np.allclose(vt @ vt.T, np.eye(size), atol=10 ** (-(min(round_digits, 8))))
    )

    return {
        "matrix_generation": {
            "size": size,
            "seed": seed,
            "distribution": distribution,
            "scale": scale,
            "dtype": str(matrix.dtype),
        },
        "matrix": round_matrix(matrix),
        "svd": {
            "u_shape": list(u.shape),
            "singular_values_shape": [int(singular_values.shape[0])],
            "vt_shape": list(vt.shape),
            "u": round_matrix(u),
            "singular_values": [round_scalar(value) for value in singular_values.tolist()],
            "vt": round_matrix(vt),
            "rank": int(np.linalg.matrix_rank(matrix)),
            "condition_number": round_scalar(condition_number) if condition_number is not None else None,
            "spectral_norm": round_scalar(spectral_norm),
            "frobenius_norm": round_scalar(fro_norm),
            "explained_energy_ratio": [
                round_scalar(value) for value in explained_energy_ratio
            ],
            "reconstruction_error": {
                "max_abs_error": round_scalar(max_abs_error),
                "frobenius_error": round_scalar(fro_error),
            },
            "orthogonality_check": {
                "u_columns_orthonormal": is_orthonormal_u,
                "v_rows_orthonormal": is_orthonormal_v,
            },
        },
        "recommended_packages": recommended_packages,
    }
