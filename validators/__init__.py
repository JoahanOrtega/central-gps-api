# ── Validators de payloads ────────────────────────────────────────────────────
# Punto único de importación para todos los schemas de marshmallow.
#
# Uso en routes:
#   from validators import LoginSchema, CreateUserSchema, UpdateUserSchema
#
# Nota: la carpeta se llama 'validators' en lugar de 'schemas' para evitar
# colisiones con el módulo interno de marshmallow en Python/Windows.

from validators.auth_validators import LoginSchema, SwitchCompanySchema
from validators.password_validators import ChangePasswordSchema
from validators.unit_validators import CreateUnitSchema, UpdateUnitSchema
from validators.poi_validators import (
    CreatePoiSchema,
    CreatePoiGroupSchema,
    UpdatePoiSchema,
)
from validators.erp_validators import CreateEmpresaAdminSchema

# user_validators reemplaza al antiguo usuario_validators (que se elimina)
# tras esta migración. CreateUserSchema sustituye a CreateUsuarioCompletoSchema.
from validators.user_validators import (
    CreateUserSchema,
    UpdateUserSchema,
    StatusUserSchema,
)

__all__ = [
    "LoginSchema",
    "SwitchCompanySchema",
    "ChangePasswordSchema",
    "CreateUnitSchema",
    "UpdateUnitSchema",
    "CreatePoiSchema",
    "CreatePoiGroupSchema",
    "UpdatePoiSchema",
    "CreateEmpresaAdminSchema",
    "CreateUserSchema",
    "UpdateUserSchema",
    "StatusUserSchema",
]
