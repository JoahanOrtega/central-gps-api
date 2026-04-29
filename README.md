# central-gps-api

`central-gps-api` es una API REST desarrollada con Flask y PostgreSQL, actualmente en esta versión cuenta con la autenticación de usuarios.

Actualmente, el proyecto ya cuenta con rutas para consulta de usuarios y autenticación inicial mediante login.

---

## Características actuales

- API REST construida con Flask
- Conexión a PostgreSQL
- Configuración mediante variables de entorno
- Endpoint de verificación de estado
- Endpoint para consulta de usuarios
- Endpoint para autenticación de usuarios

---

## Requisitos

- Python 3.10+
- PostgreSQL
- pip

## Instalación

```bash
pip install -r requirements.txt
```


# 2. Aplicar la migración SQL en tu BD
psql -U <tu_usuario> -d <tu_db> -f migrations/001_add_status_to_pois.sql