example_execution_policy = {
    "packages": [
        "pandas==2.2.3",
    ]
}


definition = {
    "name": "pandas_sales_analytics_demo",
    "description": "使用 pandas 对销售明细做聚合分析，需要 pandas",
    "category": "demo",
    "parameters": [
        {
            "name": "rows",
            "type": "array",
            "description": "销售明细数组，每项包含 region/category/product/units/price",
            "required": True,
        }
    ],
}


def execute(params: dict):
    import pandas as pd

    recommended_packages = [
        "pandas==2.2.3",
    ]
    rows = params.get("rows")
    if not isinstance(rows, list) or not rows:
        raise ValueError("rows must be a non-empty array")

    df = pd.DataFrame(rows)
    required_columns = {"region", "category", "product", "units", "price"}
    missing = sorted(required_columns - set(df.columns))
    if missing:
        raise ValueError(f"rows missing columns: {missing}")

    df["units"] = pd.to_numeric(df["units"], errors="coerce").fillna(0)
    df["price"] = pd.to_numeric(df["price"], errors="coerce").fillna(0)
    df["revenue"] = df["units"] * df["price"]

    print(f"loaded rows={len(df)}")

    overall = {
        "rows": int(len(df)),
        "total_units": float(df["units"].sum()),
        "total_revenue": round(float(df["revenue"].sum()), 2),
        "avg_price": round(float(df["price"].mean()), 2),
    }

    by_region = (
        df.groupby("region", as_index=False)
        .agg(total_units=("units", "sum"), total_revenue=("revenue", "sum"))
        .sort_values("total_revenue", ascending=False)
    )

    by_category = (
        df.groupby("category", as_index=False)
        .agg(total_units=("units", "sum"), total_revenue=("revenue", "sum"))
        .sort_values("total_revenue", ascending=False)
    )

    top_products = (
        df.groupby(["category", "product"], as_index=False)
        .agg(total_units=("units", "sum"), total_revenue=("revenue", "sum"))
        .sort_values(["total_revenue", "total_units"], ascending=[False, False])
        .head(10)
    )

    return {
        "overall": overall,
        "by_region": by_region.round(2).to_dict(orient="records"),
        "by_category": by_category.round(2).to_dict(orient="records"),
        "top_products": top_products.round(2).to_dict(orient="records"),
        "recommended_packages": recommended_packages,
    }
