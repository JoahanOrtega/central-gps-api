"""
ttl_cache.py — Cache en memoria con expiración por tiempo.

────────────────────────────────────────────────────────────────────────────────
¿Por qué un cache TTL y no functools.lru_cache?
────────────────────────────────────────────────────────────────────────────────
`functools.lru_cache` guarda valores indefinidamente hasta que se desaloja
por límite de tamaño. Para valores que pueden cambiar en BD (como
`vel_max` de una unidad, que un operador puede editar desde el catálogo),
un LRU sin TTL puede servir datos obsoletos durante horas.

Este módulo ofrece un cache de tipo "get or compute" con un TTL configurable
por llamada. La estructura es deliberadamente simple: un dict en memoria
+ timestamp + lock para thread-safety (gunicorn con threads expone el
cache a múltiples hilos).

Trade-offs asumidos:
  - Sin eviction por tamaño: si se usa para algo con cardinalidad alta,
    la memoria crece sin control. Para `vel_max` con ~200 unidades por
    empresa no es problema.
  - TTL simple: no hay "stale-while-revalidate". Cuando un valor expira,
    el siguiente get paga el costo del fetch.
  - No se persiste: en reinicio del worker, el cache se vacía. Es por
    diseño, evita inconsistencias tras un despliegue.
  - Posible "thundering herd" benigno: si N hilos llegan simultáneamente
    a una clave expirada/ausente, los N pueden ejecutar `compute` antes
    de que alguno termine. No corrompe datos (last-write-wins), solo
    desperdicia fetches. Para casos donde esto importe, considerar un
    lock por clave en el futuro.

Uso:

    from utils.ttl_cache import TTLCache
    _cache = TTLCache(ttl_seconds=300)

    def get_vel_max(imei: str) -> float:
        return _cache.get_or_compute(
            key=imei,
            compute=lambda: _query_vel_max_from_db(imei),
        )
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Generic, TypeVar

T = TypeVar("T")


class TTLCache(Generic[T]):
    """
    Cache genérico clave→valor con expiración por tiempo.

    Thread-safe: usa un RLock para permitir que `compute` (dentro del
    `get_or_compute`) llame de nuevo al cache sin deadlock.
    """

    def __init__(self, ttl_seconds: float) -> None:
        """
        Args:
            ttl_seconds: Segundos de vida de cada entrada. Pasado ese tiempo,
                         el próximo `get` recalculará el valor.
        """
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds debe ser > 0")

        self._ttl = ttl_seconds
        self._store: dict[str, tuple[T, float]] = {}
        self._lock = threading.RLock()

    def get_or_compute(self, key: str, compute: Callable[[], T]) -> T:
        """
        Retorna el valor cacheado si está vigente. Si no, ejecuta `compute`,
        guarda el resultado y lo retorna.

        Args:
            key:     Identificador único del valor.
            compute: Función sin argumentos que calcula el valor si no está
                     en cache o está expirado.

        Returns:
            El valor (del cache o recién calculado).
        """
        now = time.monotonic()

        # Fast path: intento de hit sin lock largo
        with self._lock:
            entry = self._store.get(key)
            if entry is not None:
                value, expires_at = entry
                if expires_at > now:
                    return value

        # Miss o expirado: recalcular (fuera del lock para no bloquear otras claves)
        fresh_value = compute()

        with self._lock:
            self._store[key] = (fresh_value, now + self._ttl)

        return fresh_value

    def invalidate(self, key: str) -> None:
        """
        Invalida manualmente una entrada. Útil cuando se sabe que el valor
        cambió en BD (p. ej. tras un PATCH de la unidad).
        """
        with self._lock:
            self._store.pop(key, None)

    def clear(self) -> None:
        """Vacía todo el cache. Principalmente para tests."""
        with self._lock:
            self._store.clear()
