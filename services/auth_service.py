import hashlib
import logging
import bcrypt
from db.connection import get_db_connection, release_db_connection
from utils.jwt_handler import generate_jwt

# Logger del módulo — reemplaza print() para tener trazabilidad real en producción
logger = logging.getLogger(__name__)


# ── Constantes de hashing ──────────────────────────────────────────────────────
# Número de rondas de bcrypt. 12 es el mínimo recomendado para producción:
# suficientemente lento para resistir fuerza bruta, suficientemente rápido
# para no impactar la experiencia del usuario (~300ms por login).
BCRYPT_ROUNDS = 12

# Prefijo que identifica un hash bcrypt — los hashes MD5 legacy no lo tienen
BCRYPT_PREFIX = "$2b$"


def _es_hash_bcrypt(stored_hash: str) -> bool:
    """
    Detecta si un hash almacenado fue generado con bcrypt o con MD5 legacy.

    Los hashes bcrypt siempre empiezan con '$2b$' (o '$2a$' en versiones antiguas).
    Los hashes MD5 son 32 caracteres hexadecimales sin prefijo.

    Args:
        stored_hash: Hash almacenado en la base de datos.

    Returns:
        True si es bcrypt, False si es MD5 legacy.
    """
    return stored_hash.startswith(BCRYPT_PREFIX) or stored_hash.startswith("$2a$")


def _verificar_password(password: str, stored_hash: str) -> bool:
    """
    Verifica la contraseña contra el hash almacenado, soportando
    tanto bcrypt (nuevo) como MD5 (legacy del sistema PHP).

    Args:
        password: Contraseña en texto plano ingresada por el usuario.
        stored_hash: Hash almacenado en la base de datos.

    Returns:
        True si la contraseña es correcta, False en caso contrario.
    """
    if _es_hash_bcrypt(stored_hash):
        # Ruta nueva: comparación bcrypt segura
        return bcrypt.checkpw(password.encode("utf-8"), stored_hash.encode("utf-8"))

    # Ruta legacy: MD5 heredado del sistema PHP
    md5_calculado = hashlib.md5(password.encode("utf-8")).hexdigest()
    return md5_calculado == stored_hash


def _migrar_a_bcrypt(cursor, connection, user_id: int, password: str) -> None:
    """
    Re-hashea la contraseña con bcrypt y actualiza la BD silenciosamente.

    Esta función se llama solo cuando un usuario hace login exitoso con
    un hash MD5 legacy. La migración es transparente — el usuario no
    nota ningún cambio en su experiencia.

    Estrategia de migración progresiva:
      - Login con MD5 correcto → re-hashear y guardar bcrypt → próximo login ya usa bcrypt
      - En 30-60 días todos los usuarios activos habrán migrado automáticamente

    Args:
        cursor: Cursor de la conexión activa (para no abrir una nueva).
        connection: Conexión activa (para hacer commit).
        user_id: ID del usuario a migrar.
        password: Contraseña en texto plano (ya verificada como correcta).
    """
    try:
        nuevo_hash = bcrypt.hashpw(
            password.encode("utf-8"),
            bcrypt.gensalt(rounds=BCRYPT_ROUNDS),
        ).decode("utf-8")

        cursor.execute(
            "UPDATE t_usuarios SET clave = %s WHERE id = %s",
            (nuevo_hash, user_id),
        )
        connection.commit()

        logger.info("Contraseña migrada a bcrypt para usuario id=%s", user_id)

    except Exception as exc:
        # Si la migración falla, NO bloquear el login — el usuario ya autenticó.
        # El próximo login intentará migrar de nuevo.
        connection.rollback()
        logger.error(
            "Error al migrar contraseña a bcrypt para usuario id=%s: %s",
            user_id,
            repr(exc),
        )


def _load_user_permissions(
    cursor,
    user_id: int,
    id_rol: int,
    id_empresa: int | None,
) -> list[str]:
    """
    Carga los permisos efectivos de un usuario en una empresa específica.

    El conjunto efectivo es la UNIÓN de:
      1. Permisos heredados del rol (r_rol_permisos): comunes a todos los
         usuarios que comparten el rol, asignados por el sudo_erp.
      2. Permisos específicos del usuario en esa empresa (r_usuario_permisos):
         extras que el sudo_erp o el admin_empresa le asignó individualmente.

    La unión se hace con UNION en SQL (deduplica claves automáticamente)
    para que si un permiso está en ambas fuentes aparezca solo una vez
    en el resultado.

    Solo se devuelven permisos con `status = 1` en t_permisos (permite
    "desactivar" un permiso globalmente sin borrar datos históricos).

    Si id_empresa es None (caso sudo_erp sin empresa asignada), solo se
    consultan los permisos heredados del rol — r_usuario_permisos requiere
    filtrar por empresa y sin ella no aplica.

    Args:
        cursor:      Cursor activo sobre la conexión del login (reutilizado
                     para no abrir una nueva conexión del pool).
        user_id:     ID del usuario autenticado.
        id_rol:      ID del rol del usuario (para heredar de r_rol_permisos).
        id_empresa:  Empresa activa del usuario, o None si es sudo_erp.

    Returns:
        Lista ordenada y deduplicada de claves de permiso (ej: ["on", "cund1"]).
    """
    if id_empresa is not None:
        # Caso común: usuario con empresa — unir permisos del rol + específicos
        cursor.execute(
            """
            SELECT DISTINCT p.clave
            FROM t_permisos p
            WHERE p.status = 1
              AND (
                  p.id_permiso IN (
                      SELECT id_permiso FROM r_rol_permisos WHERE id_rol = %s
                  )
                  OR
                  p.id_permiso IN (
                      SELECT id_permiso FROM r_usuario_permisos
                       WHERE id_usuario = %s AND id_empresa = %s
                  )
              )
            ORDER BY p.clave
            """,
            (id_rol, user_id, id_empresa),
        )
    else:
        # Caso sudo_erp sin empresa: solo permisos heredados del rol
        cursor.execute(
            """
            SELECT p.clave
            FROM t_permisos p
            INNER JOIN r_rol_permisos rp ON rp.id_permiso = p.id_permiso
            WHERE rp.id_rol = %s
              AND p.status  = 1
            ORDER BY p.clave
            """,
            (id_rol,),
        )

    return [row[0] for row in cursor.fetchall()]


def authenticate_user(username: str, password: str):
    """
    Autentica a un usuario y genera su JWT.

    Flujo:
      1. Buscar usuario activo por nombre de usuario
      2. Verificar contraseña (bcrypt o MD5 legacy)
      3. Si es MD5, migrar silenciosamente a bcrypt
      4. Obtener empresa activa del usuario
      5. Generar y retornar el JWT

    Seguridad:
      - Siempre retorna el mismo mensaje de error para usuario no encontrado
        y para contraseña incorrecta — previene user enumeration.
      - Los hashes MD5 se migran a bcrypt de forma transparente en el primer
        login exitoso posterior a este cambio.

    Args:
        username: Nombre de usuario ingresado.
        password: Contraseña en texto plano ingresada.

    Returns:
        Tupla (user_data, token, error_message).
        En éxito: (dict, str, None).
        En error: (None, None, str).
    """
    # Mensaje de error unificado — no revelar si el usuario existe o no.
    # Respuesta diferente para "usuario no encontrado" vs "contraseña incorrecta"
    # permite a un atacante construir una lista de usuarios válidos del sistema.
    ERROR_CREDENCIALES = "Credenciales inválidas"

    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        # 1. Buscar usuario activo y obtener su rol normalizado
        cursor.execute(
            """
            SELECT
                u.id,
                u.usuario,
                u.clave,
                u.nombre,
                u.perfil,
                u.id_rol,
                r.clave     AS rol
            FROM t_usuarios u
            LEFT JOIN t_roles r ON r.id_rol = u.id_rol
            WHERE u.usuario = %s
              AND u.status  = 1
            """,
            (username,),
        )
        row = cursor.fetchone()

        # Mismo mensaje si el usuario no existe o si la contraseña es incorrecta —
        # ambos casos son "credenciales inválidas" desde la perspectiva del cliente
        if not row:
            return None, None, ERROR_CREDENCIALES

        user_id, db_username, stored_hash, nombre, perfil, id_rol, rol = row

        # 2. Verificar contraseña — soporta bcrypt (nuevo) y MD5 (legacy PHP)
        if not _verificar_password(password, stored_hash):
            return None, None, ERROR_CREDENCIALES

        # 3. Migración silenciosa: si el hash es MD5, actualizarlo a bcrypt ahora.
        #    La contraseña ya fue verificada como correcta, así que es seguro hacerlo.
        if not _es_hash_bcrypt(stored_hash):
            _migrar_a_bcrypt(cursor, connection, user_id, password)

        # 4. Obtener empresa activa del usuario
        #    El sudo_erp no tiene empresa asignada en r_empresa_usuarios
        id_empresa = None
        nombre_empresa = None
        es_admin_empresa = False

        if rol != "sudo_erp":
            cursor.execute(
                """
                SELECT
                    e.id_empresa,
                    e.nombre,
                    reu.es_admin_empresa
                FROM t_empresas e
                INNER JOIN r_empresa_usuarios reu ON reu.id_empresa = e.id_empresa
                WHERE reu.id_usuario = %s
                  AND reu.status     = 1
                  AND e.status       = 1
                ORDER BY reu.es_admin_empresa DESC, e.nombre
                LIMIT 1
                """,
                (user_id,),
            )
            company_row = cursor.fetchone()

            if company_row:
                id_empresa = company_row[0]
                nombre_empresa = company_row[1]
                es_admin_empresa = bool(company_row[2])

        # 5. Cargar permisos efectivos del usuario en su empresa activa.
        #    Es la UNIÓN de permisos heredados del rol + específicos del usuario.
        #    Si la BD no tiene el rol cargado (id_rol=None), lista vacía.
        permisos: list[str] = []
        if id_rol is not None:
            permisos = _load_user_permissions(cursor, user_id, id_rol, id_empresa)

        # 6. Construir payload del usuario para el JWT
        user = {
            "id": user_id,
            "username": db_username,
            "nombre": nombre,
            "perfil": perfil,  # Legacy — se mantiene por compatibilidad con PHP
            "rol": rol,  # 'sudo_erp' | 'admin_empresa' | 'usuario'
            "id_empresa": id_empresa,
            "nombre_empresa": nombre_empresa,
            "es_admin_empresa": es_admin_empresa,
            # Lista de claves de permisos efectivos (rol + específicos).
            # El decorador @permiso_required valida contra este campo.
            "permisos": permisos,
        }

        token = generate_jwt(user)
        return user, token, None

    except Exception as exc:
        logger.error("Error en authenticate_user para '%s': %s", username, repr(exc))
        # Re-lanzar para que el route lo capture y retorne 500 al cliente
        raise

    finally:
        if cursor:
            cursor.close()
        if connection:
            # Devolver al pool — connection.close() destruye la conexión
            # en lugar de reutilizarla, agotando el pool con alta carga
            release_db_connection(connection)
