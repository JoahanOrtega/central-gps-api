# ── Validators de payloads ────────────────────────────────────────────────────
# Punto único de importación para todos los schemas de marshmallow.
#
# Uso en routes:
#   from validators import LoginSchema, CreateUnitSchema, CreatePoiSchema
#
# Nota: la carpeta se llama 'validators' en lugar de 'schemas' para evitar
# colisiones con el módulo interno de marshmallow en Python/Windows.

from validators.auth_validators import LoginSchema, SwitchCompanySchema
from validators.unit_validators import CreateUnitSchema
from validators.poi_validators import CreatePoiSchema, CreatePoiGroupSchema

__all__ = [
    "LoginSchema",
    "SwitchCompanySchema",
    "CreateUnitSchema",
    "CreatePoiSchema",
    "CreatePoiGroupSchema",
]