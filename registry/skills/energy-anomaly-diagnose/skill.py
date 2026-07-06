definition = {
    "name": "energy-anomaly-diagnose",
    "description": "Diagnose likely causes of energy anomalies from telemetry, events, and baseline comparisons.",
}


DEFAULT_THRESHOLDS = {
    "spike_ratio": 1.15,
    "drop_ratio": 0.85,
    "min_samples": 3,
}


def execute(params):
    entity_ref = str(params.get("entity_ref") or "").strip()
    metric_name = str(params.get("metric_name") or "").strip()
    current_window = params.get("current_window")
    if not entity_ref:
        raise ValueError("entity_ref is required")
    if not metric_name:
        raise ValueError("metric_name is required")
    if not isinstance(current_window, dict):
        raise ValueError("current_window must be an object")

    baseline_window = params.get("baseline_window") if isinstance(params.get("baseline_window"), dict) else {}
    context = params.get("context") if isinstance(params.get("context"), dict) else {}
    thresholds = dict(DEFAULT_THRESHOLDS)
    if isinstance(params.get("thresholds"), dict):
        thresholds.update(params["thresholds"])

    current_series = _extract_series(current_window)
    baseline_series = _extract_series(baseline_window)
    current_stats = _stats(current_series)
    baseline_stats = _stats(baseline_series)
    event_rows = _extract_events(current_window) + _extract_events(baseline_window)
    findings = _build_findings(metric_name, current_stats, baseline_stats, event_rows, context, thresholds)
    severity = _severity_from_findings(findings, current_stats, baseline_stats, thresholds)
    evidence_chain = _build_evidence_chain(current_series, baseline_series, event_rows, context)
    return {
        "success": True,
        "entity_ref": entity_ref,
        "metric_name": metric_name,
        "severity": severity,
        "current_stats": current_stats,
        "baseline_stats": baseline_stats,
        "delta": _delta(current_stats, baseline_stats),
        "findings": findings,
        "likely_causes": [item["cause"] for item in findings if item.get("cause")],
        "evidence_chain": evidence_chain,
        "recommended_next_checks": _recommended_next_checks(findings, context),
        "confidence": _confidence(current_stats, baseline_stats, findings, thresholds),
    }


def _extract_series(window):
    series = window.get("timeseries")
    if isinstance(series, list):
        return [item for item in series if isinstance(item, dict)]
    return []


def _extract_events(window):
    events = window.get("events")
    if isinstance(events, list):
        return [item for item in events if isinstance(item, dict)]
    return []


def _stats(series):
    numeric_values = []
    ts_values = []
    for row in series:
        value = row.get("value")
        if isinstance(value, (int, float)):
            numeric_values.append(float(value))
        else:
            try:
                numeric_values.append(float(value))
            except (TypeError, ValueError):
                continue
        ts = str(row.get("ts") or row.get("start_time") or "").strip()
        if ts:
            ts_values.append(ts)
    if not numeric_values:
        return {
            "sample_count": 0,
            "min": None,
            "max": None,
            "avg": None,
            "sum": None,
            "start_time": ts_values[0] if ts_values else "",
            "end_time": ts_values[-1] if ts_values else "",
        }
    total = sum(numeric_values)
    return {
        "sample_count": len(numeric_values),
        "min": min(numeric_values),
        "max": max(numeric_values),
        "avg": total / len(numeric_values),
        "sum": total,
        "start_time": ts_values[0] if ts_values else "",
        "end_time": ts_values[-1] if ts_values else "",
    }


def _build_findings(metric_name, current_stats, baseline_stats, event_rows, context, thresholds):
    findings = []
    if current_stats["sample_count"] < int(thresholds.get("min_samples") or 3):
        findings.append(
            {
                "type": "data_gap",
                "cause": "insufficient_current_samples",
                "message": "Current window has too few telemetry samples for a stable diagnosis.",
                "confidence": "low",
            }
        )
        return findings

    if baseline_stats["sample_count"] == 0:
        findings.append(
            {
                "type": "baseline_gap",
                "cause": "missing_baseline",
                "message": "No baseline window was provided, so anomaly judgment falls back to local variation and event context.",
                "confidence": "medium",
            }
        )
    else:
        ratio = _safe_ratio(current_stats["avg"], baseline_stats["avg"])
        if ratio is not None and ratio >= float(thresholds.get("spike_ratio") or 1.15):
            findings.append(
                {
                    "type": "level_shift_up",
                    "cause": "energy_or_power_spike_vs_baseline",
                    "message": f"{metric_name} average is {ratio:.2f}x the baseline window.",
                    "confidence": "high" if ratio >= 1.3 else "medium",
                }
            )
        elif ratio is not None and ratio <= float(thresholds.get("drop_ratio") or 0.85):
            findings.append(
                {
                    "type": "level_shift_down",
                    "cause": "lower_than_baseline",
                    "message": f"{metric_name} average is {ratio:.2f}x the baseline window.",
                    "confidence": "medium",
                }
            )

    if current_stats["avg"] is not None and current_stats["min"] is not None and current_stats["max"] is not None:
        local_spread_ratio = _safe_ratio(current_stats["max"], current_stats["avg"])
        if local_spread_ratio is not None and local_spread_ratio >= 1.2:
            findings.append(
                {
                    "type": "spike_pattern",
                    "cause": "intermittent_spike_or_load_instability",
                    "message": f"Current window peak is {local_spread_ratio:.2f}x the current average.",
                    "confidence": "medium",
                }
            )

    alarm_events = [item for item in event_rows if str(item.get("event_type") or "").lower() in {"alarm", "event"} or str(item.get("event_code") or "").strip()]
    if alarm_events:
        severity_levels = {str(item.get("severity") or "").lower() for item in alarm_events}
        findings.append(
            {
                "type": "event_correlation",
                "cause": "alarms_or_events_overlap_anomaly_window",
                "message": f"Detected {len(alarm_events)} relevant event(s) with severity levels: {sorted(level for level in severity_levels if level)}.",
                "confidence": "high" if any(level in {"major", "critical", "3", "4"} for level in severity_levels) else "medium",
            }
        )

    related_entities = context.get("related_entities")
    if isinstance(related_entities, list) and any(str(item.get("relation", {}).get("predicate") or "") == "measuredBy" for item in related_entities if isinstance(item, dict)):
        findings.append(
            {
                "type": "measurement_context",
                "cause": "measured_through_bound_points",
                "message": "The target entity has explicit measurement points in the ontology, so the diagnosis is grounded in mapped runtime signals.",
                "confidence": "medium",
            }
        )

    if not findings:
        findings.append(
            {
                "type": "no_strong_signal",
                "cause": "no_clear_anomaly_signal",
                "message": "No strong anomaly signal or correlated event was detected in the supplied windows.",
                "confidence": "medium",
            }
        )
    return findings


def _severity_from_findings(findings, current_stats, baseline_stats, thresholds):
    if current_stats["sample_count"] == 0:
        return "unknown"
    finding_types = {item.get("type") for item in findings}
    if "event_correlation" in finding_types and "level_shift_up" in finding_types:
        return "high"
    if "level_shift_up" in finding_types or "spike_pattern" in finding_types:
        return "medium"
    if "baseline_gap" in finding_types and current_stats["sample_count"] >= int(thresholds.get("min_samples") or 3):
        return "medium"
    if "no_strong_signal" in finding_types:
        return "low"
    return "low"


def _build_evidence_chain(current_series, baseline_series, event_rows, context):
    evidence = []
    if current_series:
        evidence.append(
            {
                "kind": "timeseries_current",
                "sample_count": len(current_series),
                "first_sample": current_series[0],
                "last_sample": current_series[-1],
            }
        )
    if baseline_series:
        evidence.append(
            {
                "kind": "timeseries_baseline",
                "sample_count": len(baseline_series),
                "first_sample": baseline_series[0],
                "last_sample": baseline_series[-1],
            }
        )
    if event_rows:
        evidence.append(
            {
                "kind": "events",
                "count": len(event_rows),
                "preview": event_rows[:5],
            }
        )
    if context:
        evidence.append(
            {
                "kind": "ontology_context",
                "keys": sorted(context.keys()),
            }
        )
    return evidence


def _recommended_next_checks(findings, context):
    recommendations = []
    finding_types = {item.get("type") for item in findings}
    if "baseline_gap" in finding_types:
        recommendations.append("Collect or query a comparable baseline window for the same entity, shift, and product context.")
    if "event_correlation" in finding_types:
        recommendations.append("Inspect alarm, maintenance, and work-order context around the event timestamps.")
    if "spike_pattern" in finding_types:
        recommendations.append("Check load transitions, start-stop actions, and control setpoint changes near the peak samples.")
    if "level_shift_up" in finding_types:
        recommendations.append("Compare current operating state, throughput, and recipe or grade against the baseline window.")
    if not recommendations:
        recommendations.append("Expand the observation window or compare with a peer line or machine for stronger contrast.")
    metric_definitions = context.get("metric_definitions")
    if isinstance(metric_definitions, list) and metric_definitions:
        recommendations.append("Verify that the metric semantic definition and aggregation rule match the intended anomaly KPI.")
    return recommendations[:5]


def _confidence(current_stats, baseline_stats, findings, thresholds):
    if current_stats["sample_count"] < int(thresholds.get("min_samples") or 3):
        return "low"
    if baseline_stats["sample_count"] > 0 and any(item.get("type") == "event_correlation" for item in findings):
        return "high"
    if baseline_stats["sample_count"] > 0:
        return "medium"
    return "low"


def _delta(current_stats, baseline_stats):
    if baseline_stats["avg"] in (None, 0) or current_stats["avg"] is None:
        return {
            "avg_delta": None,
            "avg_ratio": None,
        }
    avg_delta = current_stats["avg"] - baseline_stats["avg"]
    return {
        "avg_delta": avg_delta,
        "avg_ratio": current_stats["avg"] / baseline_stats["avg"],
    }


def _safe_ratio(a, b):
    if a is None or b in (None, 0):
        return None
    return float(a) / float(b)
