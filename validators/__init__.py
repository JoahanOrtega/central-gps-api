# ── Validators de payloads ────────────────────────────────────────────────────
# Punto único de importación para todos los schemas de marshmallow.
#
# Uso en routes:
#   from validators import LoginSchema, CreateUnitSchema, CreatePoiSchema
#
# Nota: la carpeta se llama 'validators' en lugar de 'schemas' para evitar
# colisiones con el módulo interno de marshmallow en Python/Windows.

from validators.auth_validators import LoginSchema, SwitchCompanySchema
from validators.unit_validators import CreateUnitSchema, UpdateUnitSchema
from validators.poi_validators import CreatePoiSchema, CreatePoiGroupSchema
from validators.erp_validators import CreateEmpresaAdminSchema

__all__ = [
    "LoginSchema",
    "SwitchCompanySchema",
    "CreateUnitSchema",
    "UpdateUnitSchema",
    "CreatePoiSchema",
    "CreatePoiGroupSchema",
    "CreateEmpresaAdminSchema",
]
