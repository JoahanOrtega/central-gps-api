# central-gps-api

`central-gps-api` es una API REST desarrollada con Flask y PostgreSQL, diseñada para centralizar y atender las peticiones del sistema CentralGPS. Su propósito es servir como backend para la consulta y gestión de información relacionada con usuarios, transportes y otros módulos del sistema.

Actualmente, el proyecto ya cuenta con estructura modular, conexión a base de datos PostgreSQL, rutas para consulta de usuarios y autenticación inicial mediante login.

---

## Características actuales

- API REST construida con Flask
- Conexión a PostgreSQL
- Configuración mediante variables de entorno
- Estructura modular separada por rutas, servicios y conexión a base de datos
- Endpoint de verificación de estado
- Endpoint para consulta de usuarios
- Endpoint para autenticación de usuarios

---

## Tecnologías utilizadas

- Python 3.13.5
- Flask 3.1.3
- PostgreSQL
- Flask-CORS
- psycopg2-binary
- python-dotenv

---

## Dependencias principales

Estas son las librerías utilizadas actualmente en el proyecto:

- `Flask==3.1.3`
- `flask-cors==6.0.2`
- `psycopg2-binary==2.9.11`
- `python-dotenv==1.2.2`
- `Werkzeug==3.1.6`
- `Jinja2==3.1.6`
- `itsdangerous==2.2.0`
- `MarkupSafe==3.0.3`
- `click==8.3.1`
- `blinker==1.9.0`
- `colorama==0.4.6`

---

## Estructura del proyecto

```bash
central-gps-api/
├── db/
│   └── connection.py
├── routes/
│   ├── __init__.py
│   ├── auth_routes.py
│   └── user_routes.py
├── services/
│   └── auth_services.py
├── .env
├── .env.example
├── .gitignore
├── app.py
├── config.py
├── README.md
└── requirements.txt
```
