# Contribuir a fich-mcp

Gracias por el interés. Esto es un proyecto chico y personal, pero las contribuciones son bienvenidas.

## Entorno de desarrollo

```bash
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
```

## Antes de abrir un PR

```bash
python -m pytest -q
python -m ruff check .
```

Ambos comandos corren en CI; un PR con `ruff` o tests en rojo no se va a mergear.

## Reglas de contenido

- **Nunca** subas datos reales de FICH: nombres de cursos, materiales, anuncios, IDs de usuario,
  tokens de sesión o capturas de pantalla con información de una cuenta real. Usá datos ficticios en
  issues, PRs y tests.
- Los tests usan mocks y fixtures locales — no hay integración contra el e-FICH real en CI.
- Si tocás `src/fich_mcp/clients.py` (registro de clientes MCP) o `src/fich_mcp/security.py`
  (almacenamiento del token), agregá tests para las tres plataformas soportadas cuando el cambio
  dependa del sistema operativo.

## Estilo

- Python 3.12+, tipado donde aporte claridad.
- `ruff` con la configuración de `pyproject.toml` (no se negocia línea por línea).
- Commits en inglés, formato `tipo: descripción` (`feat`, `fix`, `refactor`, `docs`, `test`, `chore`).
