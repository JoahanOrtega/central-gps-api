"""
password_service.py — Lógica de negocio del cambio de contraseña.

────────────────────────────────────────────────────────────────────────────────
¿Por qué un módulo separado de auth_service?
────────────────────────────────────────────────────────────────────────────────
auth_service.py tiene una responsabilidad clara: AUTENTICAR (verificar
credenciales y emitir un JWT). El cambio de contraseña es un dominio
relacionado pero distinto:
  - Cambia datos del usuario (write), no solo lee.
  - Tiene side effects (revocación de tokens, auditoría) que no deben
    contaminar el path crítico del login.
  - Su lógica crece con el tiempo (políticas de rotación, prevención
    de reutilización de N últimas, etc.) y mezclarla en auth_service
    haría ese archivo difícil de mantener.

Separarlo respeta SRP y permite testear cada flujo de manera aislada.

────────────────────────────────────────────────────────────────────────────────
Reutilización de lógica existente
────────────────────────────────────────────────────────────────────────────────
NO duplicamos las funciones de hashing — importamos las helpers privadas
(_verificar_password, _es_hash_bcrypt, BCRYPT_ROUNDS) desde auth_service.
Si en el futuro cambia el algoritmo (p.ej. argon2), un solo cambio en
auth_service propaga automáticamente a este módulo.
"""

import logging
import bcrypt

from db.connection import get_db_connection, release_db_connection
from services.auth_service import (
    BCRYPT_ROUNDS,
    _verificar_password,
)
from services.refresh_token_service import revoke_all_user_tokens

logger = logging.getLogger(__name__)


# ─── Constantes de errores ────────────────────────────────────────────────────
# Mensajes centralizados por dos razones:
#   1. Consistencia: el mismo error siempre dice exactamente lo mismo,
#      sin variaciones por copy-paste.
#   2. UX: el frontend puede compararlos como strings si necesita mostrar
#      iconografía distinta por tipo de error (aunque actualmente solo los
#      muestra como texto).
ERROR_USUARIO_NO_ENCONTRADO = "Usuario no encontrado o inactivo"
ERROR_PASSWORD_ACTUAL_INCORRECTA = "La contraseña actual es incorrecta"


def change_password(
    user_id: int,
    current_password: str,
    new_password: str,
    ip_origen: str | None = None,
) -> tuple[bool, str | None]:
    """
    Cambia la contraseña de un usuario autenticado.

    Flujo:
      1. Buscar al usuario activo por id (viene del JWT, ya verificado).
      2. Verificar que la contraseña ACTUAL coincide con el hash en BD.
         Soporta tanto bcrypt como MD5 legacy — reusa _verificar_password
         de auth_service para no duplicar la lógica.
      3. Hashear la nueva contraseña con bcrypt.
      4. Actualizar t_usuarios.clave + auditar (id_usuario_cambio,
         fecha_cambio).
      5. Revocar TODOS los refresh tokens del usuario para forzar
         re-login en otros dispositivos. El access token actual sigue
         siendo válido hasta que expire (15 min) — esa ventana es un
         compromiso aceptado: el usuario que acaba de cambiar la
         contraseña sigue trabajando sin interrupción, los OTROS
         dispositivos quedarán fuera al expirar su access token.

    Decisión de diseño — auditoría:
      Por ahora solo se actualizan id_usuario_cambio y fecha_cambio en
      t_usuarios (ya existen). NO insertamos en t_auditoria desde aquí
      porque ese módulo se está usando para entidades de negocio
      (unidades, pois, etc.) y mezclar eventos de auth puede saturar
      consultas ERP. Si en el futuro se decide auditar cambios de
      contraseña, el lugar correcto es agregar una entidad
      'auth.password_change' a t_auditoria con un patrón consistente.

    Seguridad:
      - El user_id viene SIEMPRE del JWT (decorador @jwt_required en
        el route). Nunca confiar en un user_id que venga del body —
        permitiría cambiar la contraseña de otro usuario.
      - Si la verificación de la contraseña actual falla, NO revelar
        si el usuario existe. Retornar el mismo mensaje genérico.
      - Si la migración a bcrypt falla a mitad de transacción, hacemos
        rollback completo: o se cambia y se revocan tokens, o no se
        cambia nada. Estado consistente garantizado.

    Args:
        user_id:          ID del usuario (extraído del JWT por el route).
        current_password: Contraseña actual en texto plano.
        new_password:     Contraseña nueva en texto plano (ya validada
                          por el schema marshmallow — longitud mínima,
                          distinta a la actual, etc.).
        ip_origen:        IP del cliente para logging (no se persiste
                          en una tabla aparte aún — solo aparece en
                          los logs de la app).

    Returns:
        Tupla (success, error_message):
          (True,  None)         → contraseña cambiada y tokens revocados.
          (False, str)          → error con mensaje listo para devolver.
    """
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        # 1. Buscar al usuario activo. Filtrar por status=1 evita que un
        #    usuario suspendido cambie su contraseña antes de que un admin
        #    lo reactive — defensa en profundidad: el JWT podría seguir
        #    siendo válido los últimos minutos antes de que el admin lo
        #    suspenda, este filtro tapa esa ventana.
        cursor.execute(
            """
            SELECT id, clave
              FROM t_usuarios
             WHERE id     = %s
               AND status = 1
            """,
            (user_id,),
        )
        row = cursor.fetchone()

        if not row:
            logger.warning(
                "Cambio de contraseña rechazado: usuario id=%s no existe o está inactivo",
                user_id,
            )
            return False, ERROR_USUARIO_NO_ENCONTRADO

        _, stored_hash = row

        # 2. Verificar la contraseña actual. _verificar_password() acepta
        #    tanto bcrypt como MD5 legacy — el mismo método que usa el
        #    login. Si el usuario tenía MD5 antes, nos da igual: aquí
        #    vamos a sobrescribir con bcrypt de todos modos.
        if not _verificar_password(current_password, stored_hash):
            logger.warning(
                "Cambio de contraseña rechazado: contraseña actual incorrecta "
                "para usuario id=%s desde ip=%s",
                user_id,
                ip_origen,
            )
            return False, ERROR_PASSWORD_ACTUAL_INCORRECTA

        # 3. Hashear la nueva contraseña con bcrypt. gensalt() genera
        #    un salt aleatorio nuevo — NO reutilizar el del hash anterior
        #    aunque haya sido bcrypt: el cambio de contraseña debe ser
        #    indistinguible de un usuario nuevo.
        nuevo_hash = bcrypt.hashpw(
            new_password.encode("utf-8"),
            bcrypt.gensalt(rounds=BCRYPT_ROUNDS),
        ).decode("utf-8")

        # 4. Actualizar la BD. id_usuario_cambio = user_id porque el
        #    cambio es self-service: el mismo usuario se modifica.
        #    fecha_cambio queda con CURRENT_TIMESTAMP del servidor para
        #    que el reloj sea siempre el de Postgres y no el del cliente.
        cursor.execute(
            """
            UPDATE t_usuarios
               SET clave              = %s,
                   id_usuario_cambio  = %s,
                   fecha_cambio       = CURRENT_TIMESTAMP
             WHERE id     = %s
               AND status = 1
            """,
            (nuevo_hash, user_id, user_id),
        )

        connection.commit()

        logger.info(
            "Contraseña cambiada para usuario id=%s desde ip=%s",
            user_id,
            ip_origen,
        )

        # 5. Revocar todos los refresh tokens del usuario.
        #    Lo hacemos DESPUÉS del commit porque revoke_all_user_tokens
        #    abre su propia conexión — si lo metiéramos antes del commit,
        #    estaríamos en dos transacciones distintas y podríamos
        #    quedar con la contraseña vieja + tokens revocados (peor
        #    estado posible: el usuario no puede entrar con ninguno).
        #
        #    Si ESTA llamada falla, el usuario sí podrá entrar con su
        #    contraseña nueva pero los otros dispositivos seguirán con
        #    sesión activa unos minutos. Trade-off aceptado: el riesgo
        #    de no revocar es mucho menor que el de bloquear al usuario.
        revoke_all_user_tokens(user_id)

        return True, None

    except Exception as exc:
        if connection:
            connection.rollback()
        logger.error(
            "Error en change_password para usuario id=%s: %s",
            user_id,
            repr(exc),
            exc_info=True,
        )
        # Re-lanzar para que el route responda 500 — un cambio de
        # contraseña que falla por error interno NO debe disfrazarse
        # de "credenciales inválidas".
        raise

    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_connection(connection)
