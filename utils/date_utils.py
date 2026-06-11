from datetime import datetime


def fmt_dt(dt: datetime | None) -> str | None:
    """
    Serializa un datetime naive de BD (UTC-6) a ISO 8601 con offset.
    "2026-06-11 13:30:00" → "2026-06-11T13:30:00-06:00"

    Uso único para serializar fechas de BD al frontend.
    Reemplaza .isoformat() en todos los servicios.
    """
    if dt is None:
        return None
    return dt.strftime("%Y-%m-%dT%H:%M:%S-06:00")
