import os
import uuid

from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

# Carpeta montada como volumen en el compose, para que las imágenes sobrevivan
# al redeploy del contenedor.
UPLOAD_DIR = "/app/uploads/unit-images"

# Ruta pública con la que el frontend pide la imagen. El backend la sirve y
# nginx ya hace proxy de /units/* al api, así que no hay que tocar nginx.
PUBLIC_PREFIX = "/units/images"

ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}
MAX_BYTES = 2 * 1024 * 1024  # 2 MB


def _extension(filename: str) -> str | None:
    if "." not in filename:
        return None
    ext = filename.rsplit(".", 1)[1].lower()
    return ext if ext in ALLOWED_EXTENSIONS else None


def save_unit_image(file: FileStorage) -> tuple[dict | None, dict | None]:
    """Guarda la imagen en el volumen y devuelve su ruta pública.

    Retorna (data, error) siguiendo el patrón del módulo.
    """
    if not file or not file.filename:
        return None, {"code": "NO_FILE", "message": "No se recibió ninguna imagen"}

    ext = _extension(secure_filename(file.filename))
    if not ext:
        return None, {
            "code": "INVALID_TYPE",
            "message": "Formato no permitido. Usa JPG, PNG o WEBP.",
        }

    # Validar tamaño leyendo el stream sin cargarlo entero en memoria.
    file.seek(0, os.SEEK_END)
    size = file.tell()
    file.seek(0)
    if size > MAX_BYTES:
        return None, {
            "code": "TOO_LARGE",
            "message": "La imagen supera el límite de 2 MB.",
        }

    os.makedirs(UPLOAD_DIR, exist_ok=True)

    # Nombre único para no pisar imágenes de otras unidades ni filtrar el
    # nombre original del archivo del usuario.
    nombre = f"{uuid.uuid4().hex}.{ext}"
    file.save(os.path.join(UPLOAD_DIR, nombre))

    return {"ruta": f"{PUBLIC_PREFIX}/{nombre}"}, None


def get_unit_image_path(nombre: str) -> str | None:
    """Devuelve la ruta en disco de una imagen, o None si el nombre es inválido
    o el archivo no existe. secure_filename evita path traversal."""
    seguro = secure_filename(nombre)
    if not seguro:
        return None

    ruta = os.path.join(UPLOAD_DIR, seguro)
    return ruta if os.path.isfile(ruta) else None
