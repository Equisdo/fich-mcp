# Windows nativo

## Instalación

Requisitos: Windows 10/11, Python 3.12/3.13 x64, almacenamiento local NTFS con
ACLs. No guardar configuración/cache en una unidad compartida, FAT/exFAT ni
carpetas sincronizadas. No requiere WSL2, Bash, gum ni permisos de administrador
para ejecutar la aplicación.

La sección Windows del README instala desde ZIP sin Git ni activación de venv.
Mientras el cambio no esté integrado en main, usar esta URL en pip:
`https://github.com/Equisdo/fich-mcp/archive/refs/heads/fix/windows-native.zip`.

Alternativa para desarrollar desde Git, en PowerShell:

```powershell
winget install --exact --id Python.Python.3.13
winget install --exact --id Git.Git
```

Cerrar y abrir PowerShell para recargar PATH. No depender del launcher `py`:

```powershell
python --version
git --version
git clone --branch fix/windows-native https://github.com/Equisdo/fich-mcp.git
cd fich-mcp
python -m venv .venv
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -e .
$env:Path = "$PWD\.venv\Scripts;" + $env:Path
fich-mcp doctor
fich-mcp tui
```

Si no resuelve Python, comprobar `Get-Command python -All` y ejecutar
`& "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe" -m venv .venv`.
Si no resuelve Git, comprobar `& 'C:\Program Files\Git\cmd\git.exe' --version`.
No instalar paquetes en otro Python: usar siempre el Python del venv con `-m pip`.

Sin login, doctor debe devolver JSON con `authentication_required`, no un
traceback. El menú permite iniciar sesión, seleccionar materias, sincronizar y
configurar clientes. Las credenciales se solicitan solamente en `init`.
`configure` necesita que el cliente correspondiente esté instalado y en PATH.

## OCR opcional

PDFs con texto nativo funcionan sin Poppler/Tesseract. Para imágenes y escaneos
instalar Poppler (pdftoppm.exe) y Tesseract con idiomas spa+eng y agregar sus
directorios bin al PATH. Si ya se usa Chocolatey:

```powershell
choco install poppler tesseract --yes
```

Reabrir PowerShell y comprobar:

```powershell
pdftoppm -v
tesseract --list-langs
fich-mcp doctor
```

Si falta español, instalar `spa.traineddata` de tessdata_fast oficial en el
directorio tessdata de Tesseract. No es necesario instalar OCR para arrancar
el menú, listar cursos ni consultar calendario. Las páginas sin OCR disponible
se reportan como gaps (`ocr_unavailable`), nunca como extracciones completas.

## Auditoría y garantías

Se revisaron todos los módulos Python, tests, script Bash, pyproject y CI del
commit base 80d98444e4951f7c59445117cfed18807b841543.

| Límite anterior | Implementación |
| --- | --- |
| store.py: único import/uso de fcntl, flock EX+NB en writer.lock | platforms.exclusive_writer: flock en POSIX; CreateFile sin compartir en Windows. Contención devuelve sync_busy, otros errores no se confunden con contención. |
| Lock de Service.sync | Cubre toda la sincronización (varias transacciones SQL y archivos), no sustituye las transacciones SQLite. Se libera por close, excepción o muerte. Nunca se elimina writer.lock. No es reentrante. |
| O_NOFOLLOW | Conservado en POSIX; Windows abre el reparse point y rechaza ese atributo en el handle. |
| getuid/chmod/modos de credenciales | POSIX sin cambios; Windows exige propietario actual y DACL privada, sin herencia amplia. Rechaza credenciales con ACL nula o ajena. |
| ZoneInfo Buenos Aires | tzdata declarado para toda plataforma sin base IANA del sistema; test fuerza TZPATH vacío. |
| Bash fuera del paquete instalado | Menú Python incluido en wheel; menú Bash/gum conservado como opción POSIX en checkout. |
| resource en pdf_worker | Import condicional. Job Objects Windows conservan límite de memoria 768 MiB y CPU 12 s; si falla la asignación no se procesa el PDF. |
| RLIMIT_FSIZE para salidas OCR | POSIX conserva 24 MiB por archivo. Windows evita archivos de salida: PNG/texto via pipes, lectura de máximo 24 MiB + 1, rechazo y terminación si excede. |
| killpg/SIGKILL | Windows Job Object mata el árbol RPC completo antes de recoger salida. El hijo espera stdin hasta quedar asignado al job. |
| Codificaciones implícitas | JSON/TOML/texto OCR se leen explícitamente como UTF-8. |
| Dependencias | httpx, mcp, pypdf son portables; pywin32 se instala solo en Windows. Poppler/Tesseract son externos y opcionales. |
| Tests/CI Linux exclusivos | Matriz Linux/macOS/Windows × Python 3.12/3.13, OCR instalado y wheel probado fuera del checkout. |

La privacidad protege frente a otras cuentas ordinarias, no frente al mismo
usuario ni administradores. El lock coordina procesos cooperantes en disco local;
SQLite sigue siendo responsable de su integridad transaccional. No se usa un
lock basado solamente en existencia de archivo ni un timeout que admita dos writers.

## Verificación

```powershell
& .\.venv\Scripts\python.exe -m pip install -e '.[dev]'
& .\.venv\Scripts\python.exe -m pytest -q
& .\.venv\Scripts\python.exe -m ruff check .
fich-mcp doctor
"0" | fich-mcp tui
```

Los tests Windows ejercitan ACLs reales, exclusión entre procesos, liberación
tras matar al propietario y terminación de descendientes. La suite general
incluye extracción nativa, OCR, MCP stdio y configuración de clientes con mocks.
El inicio de sesión y la sincronización contra Moodle real requieren credenciales
y quedan fuera de CI. Consultar la PR para resultados efectivos, no interpretar
la presencia de un workflow como evidencia de que pasó.

### Limitación preexistente en macOS ARM

El runner macOS 26 ARM rechaza `setrlimit(RLIMIT_AS, 768 MiB)` con
`ValueError: current limit exceeds maximum limit`. Es la misma llamada del
extractor original. Se conserva el comportamiento seguro: no procesar el PDF
si el sistema no admite los límites. Los tests prueban expresamente ese rechazo
sin salida y omiten solo las integraciones PDF que necesitan dicho límite.
No se presenta macOS como validado para PDF/OCR en ese entorno. El resto de la
suite y los comandos doctor/tui se ejecutan normalmente. Linux y Windows no
omiten las integraciones PDF por esta condición.
