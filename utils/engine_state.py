"""
engine_state.py — Fuente única de verdad para el estado del motor de una unidad.

────────────────────────────────────────────────────────────────────────────────
¿Por qué este módulo existe?
────────────────────────────────────────────────────────────────────────────────
En t_data hay DOS fuentes de información sobre el estado del motor:

  1. `tipo_alerta`  → evento explícito reportado por el AVL:
                        33 = encendido de motor
                        34 = apagado  de motor
                      Es la fuente MÁS CONFIABLE porque representa una
                      transición real notificada por el dispositivo.

  2. `status` (9 bits) → bit 1 = ignición (1=ON, 0=OFF)
                         Es un snapshot que puede fallar si el AVL pierde
                         señal momentáneamente y reporta ceros.

El legacy PHP y varios módulos de este backend los combinaban de forma
ad-hoc (y a veces inconsistente). Este módulo centraliza la regla:

    Prioridad:
      tipo_alerta ∈ {33, 34}  →  gana (es un evento, no un snapshot)
      en cualquier otro caso  →  se cae al bit 1 del `status`

De esta forma, un único cambio aquí se propaga a todo el backend
y (vía el campo `engine_state` en la respuesta) también al frontend.
"""

from __future__ import annotations

from typing import Literal, Final

# ── Constantes de tipo_alerta ──────────────────────────────────────────────────
#
# Estos códigos los emite el dispositivo AVL cuando detecta una transición
# de encendido/apagado real del motor. Son fieles al legacy PHP.
TIPO_ALERTA_ENCENDIDO: Final[int] = 33
TIPO_ALERTA_APAGADO: Final[int] = 34

# ── Constantes de status (9 bits) ──────────────────────────────────────────────
#
# El campo `status` es un string de 9 caracteres ('0' o '1') donde:
#   bit 1 = ignición, bits 2-5 = inputs, bits 6-9 = outputs
STATUS_ON: Final[str] = "100000000"
STATUS_OFF: Final[str] = "000000000"

# ── Tipo del estado del motor ─────────────────────────────────────────────────
#
# Se expone como string literal para que sea serializable directamente a JSON
# y para que el frontend pueda usar el mismo tipo sin conversiones.
EngineState = Literal["on", "off", "unknown"]

# Re-exporte conveniente: valores individuales usados en comparaciones externas.
ENGINE_STATE_ON: Final[EngineState] = "on"
ENGINE_STATE_OFF: Final[EngineState] = "off"
ENGINE_STATE_UNKNOWN: Final[EngineState] = "unknown"


# ── Helpers atómicos ──────────────────────────────────────────────────────────


def _ignition_from_status(status: str | None) -> EngineState:
    """
    Deriva el estado del motor a partir del campo `status` crudo.

    Regla: si el primer carácter del string es '1' → motor encendido,
           si es exactamente '0' → motor apagado,
           en cualquier otro caso (None, "", basura) → desconocido.

    Se mantiene estricto con "1"/"0" para no inventar información:
    un status vacío NO es lo mismo que un status apagado.
    """
    if not status:
        return ENGINE_STATE_UNKNOWN

    first_char = status.strip()[:1]
    if first_char == "1":
        return ENGINE_STATE_ON
    if first_char == "0":
        return ENGINE_STATE_OFF
    return ENGINE_STATE_UNKNOWN


def _engine_state_from_tipo_alerta(tipo_alerta: int | None) -> EngineState | None:
    """
    Deriva el estado del motor a partir de `tipo_alerta`.

    Retorna:
      "on"  si tipo_alerta == 33
      "off" si tipo_alerta == 34
      None  si tipo_alerta no es un evento de motor (cualquier otro valor).

    El retorno de None es intencional — significa "este campo no aporta
    información sobre el motor, hay que caer al fallback de `status`".
    """
    if tipo_alerta == TIPO_ALERTA_ENCENDIDO:
        return ENGINE_STATE_ON
    if tipo_alerta == TIPO_ALERTA_APAGADO:
        return ENGINE_STATE_OFF
    return None


# ── API pública ───────────────────────────────────────────────────────────────


def resolve_engine_state(
    tipo_alerta: int | None,
    status: str | None,
) -> EngineState:
    """
    Resuelve el estado del motor combinando `tipo_alerta` y `status`.

    Prioridad:
      1. Si `tipo_alerta` es 33 o 34 → gana (es un evento explícito del AVL).
      2. En caso contrario → se usa el bit 1 de `status`.
      3. Si ambos son inconclusos → "unknown".

    Esta es la ÚNICA función que debe usarse en todo el backend para
    responder "¿está encendida esta unidad?".

    Args:
        tipo_alerta: Valor crudo de la columna t_data.tipo_alerta.
        status:      Valor crudo de la columna t_data.status (9 bits).

    Returns:
        "on" | "off" | "unknown" — serializable directo a JSON.
    """
    from_alert = _engine_state_from_tipo_alerta(tipo_alerta)
    if from_alert is not None:
        return from_alert
    return _ignition_from_status(status)


def is_engine_off_point(
    tipo_alerta: int | None,
    status: str | None,
    speed_kmh: float,
    min_moving_speed: float = 1.0,
) -> bool:
    """
    Indica si un punto específico de t_data corresponde a "motor apagado
    en reposo" — criterio usado para cortar recorridos.

    Prioridad de decisión (las reglas se evalúan en orden):
      1. tipo_alerta == 34 → corte DURO (apagado explícito, siempre corta).
      2. tipo_alerta == 33 → NO corta (encendido explícito del motor gana
         sobre cualquier status, típico cuando status snapshot está en 0
         por transición momentánea en el primer paquete post-ignición).
      3. status en OFF Y velocidad < min_moving_speed → corta (fallback
         para AVLs que no envían tipo_alerta).
      4. Cualquier otro caso (incluye status OFF pero con velocidad alta,
         que normalmente es un paquete con pérdida de señal): NO corta.

    Args:
        tipo_alerta:      Valor de t_data.tipo_alerta para ese punto.
        status:           Valor de t_data.status para ese punto.
        speed_kmh:        Velocidad del punto en km/h (ya saneada a float).
        min_moving_speed: Umbral bajo el cual la unidad se considera detenida.

    Returns:
        True si el punto debe considerarse "apagado del motor" para cortar.
    """
    # Regla 1: evento explícito de apagado → corte duro
    if tipo_alerta == TIPO_ALERTA_APAGADO:
        return True

    # Regla 2: evento explícito de encendido → nunca corta
    # (veta el fallback de status, que podría estar en 0 por snapshot viejo)
    if tipo_alerta == TIPO_ALERTA_ENCENDIDO:
        return False

    # Regla 3: fallback por status + velocidad
    state_from_status = _ignition_from_status(status)
    if state_from_status == ENGINE_STATE_OFF and speed_kmh < min_moving_speed:
        return True

    return False
