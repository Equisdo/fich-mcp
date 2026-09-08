"""Cross-platform guided terminal UI shipped inside the Python package.

The repository also keeps the original Bash/gum frontend. This module mirrors
its presentation and workflows so an installed wheel -- notably on Windows --
does not fall back to a reduced menu.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

BANNER = r"""
   ██████╗       ███████╗ ██╗  ██████╗ ██╗  ██╗
 ██╔════██╗      ██╔════╝ ██║ ██╔════╝ ██║  ██║
 █████████║█████╗█████╗   ██║ ██║      ███████║
 ██╔══════╝╚════╝██╔══╝   ██║ ██║      ██╔══██║
 ╚███████╗       ██║      ██║ ╚██████╗ ██║  ██║
  ╚══════╝       ╚═╝      ╚═╝  ╚═════╝ ╚═╝  ╚═╝
"""


class UI:
    """Dependency-free UI with arrows on a terminal and a pipe-friendly fallback."""

    BLUE = "\033[38;5;39m"
    CYAN = "\033[38;5;51m"
    GRAY = "\033[38;5;245m"
    GREEN = "\033[38;5;114m"
    AMBER = "\033[38;5;221m"
    RED = "\033[38;5;203m"
    BOLD = "\033[1m"
    RESET = "\033[0m"

    def __init__(self, stdin=None, stdout=None):
        self.stdin = stdin or sys.stdin
        self.stdout = stdout or sys.stdout
        self.interactive = bool(self.stdin.isatty() and self.stdout.isatty())
        self.color = self.interactive and "NO_COLOR" not in os.environ
        if os.name == "nt" and self.interactive:
            self._enable_windows_ansi()

    @staticmethod
    def _enable_windows_ansi():
        try:
            import ctypes

            kernel = ctypes.windll.kernel32
            handle = kernel.GetStdHandle(-11)
            mode = ctypes.c_uint()
            if kernel.GetConsoleMode(handle, ctypes.byref(mode)):
                kernel.SetConsoleMode(handle, mode.value | 0x0004)
        except (AttributeError, OSError):
            pass

    def paint(self, text, *codes):
        return "".join(codes) + text + self.RESET if self.color else text

    def write(self, text=""):
        print(text, file=self.stdout)

    def clear(self):
        if self.interactive:
            self.stdout.write("\033[H\033[2J\033[3J")
            self.stdout.flush()

    def banner(self):
        self.clear()
        self.write(self.paint(BANNER, self.BLUE))
        self.write("                    " + self.paint("e-FICH", self.BOLD, self.BLUE))
        self.write("           " + self.paint("Plataforma educativa · MCP", self.GRAY))
        self.write("                " + self.paint("by @juanmabdu", self.GRAY))
        self.write()
        self.write(" " + self.paint("─" * 46, self.GRAY))
        self.write()

    def title(self, text):
        self.write(self.paint(text, self.BOLD, self.BLUE))

    def dim(self, text):
        self.write(self.paint(text, self.GRAY))

    def ok(self, text):
        self.write(self.paint(text, self.GREEN))

    def warn(self, text):
        self.write(self.paint(text, self.AMBER))

    def error(self, text):
        self.write(self.paint(text, self.RED))

    def box(self, text):
        edge = "─" * (len(text) + 2)
        self.write(self.paint(f" ┌{edge}┐", self.BLUE))
        line = self.paint(" │ ", self.BLUE)
        line += self.paint(text, self.GREEN, self.BOLD)
        line += self.paint(" │", self.BLUE)
        self.write(line)
        self.write(self.paint(f" └{edge}┘", self.BLUE))

    def pause(self):
        if not self.interactive:
            return
        self.write()
        try:
            input(self.paint("Enter para volver al menú.", self.GRAY))
        except (EOFError, KeyboardInterrupt):
            pass

    def _key(self):
        if os.name == "nt":
            import msvcrt

            key = msvcrt.getwch()
            if key in ("\x00", "\xe0"):
                return {"H": "up", "P": "down"}.get(msvcrt.getwch(), "other")
            return {
                "\r": "enter",
                " ": "space",
                "\x03": "cancel",
                "\x1b": "cancel",
            }.get(key, key)

        import termios
        import tty

        fd = self.stdin.fileno()
        previous = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            key = self.stdin.read(1)
            if key == "\x1b":
                tail = self.stdin.read(2)
                return {"[A": "up", "[B": "down"}.get(tail, "cancel")
            return {
                "\r": "enter",
                "\n": "enter",
                " ": "space",
                "\x03": "cancel",
            }.get(key, key)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, previous)

    def menu(self, header, options):
        if not self.interactive:
            self.banner()
            self.dim(header)
            for number, option in enumerate(options, 1):
                self.write(f"  {number}. {option}")
            try:
                answer = input("\n❯ ").strip()
            except (EOFError, KeyboardInterrupt):
                return None
            if answer.isdigit() and 1 <= int(answer) <= len(options):
                return options[int(answer) - 1]
            return None

        selected = 0
        while True:
            self.banner()
            self.write(self.paint(header, self.BOLD, self.BLUE))
            self.write()
            for index, option in enumerate(options):
                cursor = "❯" if index == selected else " "
                codes = (self.CYAN,) if index == selected else ()
                self.write(self.paint(f"{cursor} {option}", *codes))
            self.write()
            self.dim("↑↓ navegar · enter elegir · esc volver")
            key = self._key()
            if key == "up":
                selected = (selected - 1) % len(options)
            elif key == "down":
                selected = (selected + 1) % len(options)
            elif key == "enter":
                return options[selected]
            elif key == "cancel":
                return None

    def multi(self, header, options, selected_ids):
        if not self.interactive:
            self.banner()
            self.dim(header)
            for number, (course_id, label) in enumerate(options, 1):
                mark = "●" if course_id in selected_ids else "○"
                self.write(f"  {number}. {mark} {label}")
            try:
                raw = input("Números separados por coma (vacío = ninguna): ").strip()
            except (EOFError, KeyboardInterrupt):
                return None
            indexes = {int(value) - 1 for value in raw.split(",") if value.strip().isdigit()}
            return {options[index][0] for index in indexes if 0 <= index < len(options)}

        chosen = set(selected_ids)
        cursor = 0
        while True:
            self.banner()
            self.write(self.paint(header, self.BOLD, self.BLUE))
            self.write()
            for index, (course_id, label) in enumerate(options):
                pointer = "❯" if cursor == index else " "
                mark = "●" if course_id in chosen else "○"
                codes = (self.CYAN,) if cursor == index else ()
                self.write(self.paint(f"{pointer} {mark} {label}", *codes))
            self.write()
            self.dim("↑↓ navegar · espacio marcar · enter guardar · esc cancelar")
            key = self._key()
            if key == "up":
                cursor = (cursor - 1) % len(options)
            elif key == "down":
                cursor = (cursor + 1) % len(options)
            elif key == "space":
                chosen.symmetric_difference_update({options[cursor][0]})
            elif key == "enter":
                return chosen
            elif key == "cancel":
                return None

    def confirm(self, question, danger=False):
        color = self.AMBER if danger else self.CYAN
        try:
            answer = input(self.paint(f"{question} [s/N] ", color)).strip().casefold()
        except (EOFError, KeyboardInterrupt):
            return False
        return answer.startswith("s")


class App:
    def __init__(self, ui=None):
        self.ui = ui or UI()

    def status(self, pause=True):
        from .cli import doctor
        from .security import Paths

        self.ui.banner()
        self.ui.title("Estado")
        self.ui.dim("Ejecutando: fich-mcp doctor")
        self.ui.write()
        self.ui.write(json.dumps(doctor(Paths.default()), ensure_ascii=False, indent=2))
        if pause:
            self.ui.pause()

    def login(self, pause=True):
        from .cli import main

        self.ui.banner()
        self.ui.title("Iniciar sesión en e-FICH")
        self.ui.write()
        self.ui.warn("e-FICH usa HTTP, no HTTPS.")
        self.ui.dim("Usuario, contraseña y token pueden ser leídos o modificados en tránsito.")
        self.ui.dim("Las credenciales las pide fich-mcp init y nunca quedan en esta interfaz.")
        self.ui.write()
        if self.ui.confirm("¿Continuar con el login?"):
            self.ui.write()
            if main(["init"]) == 0:
                self.ui.ok("✓ Sesión guardada.")
            else:
                self.ui.error("No quedó ninguna sesión guardada.")
        if pause:
            self.ui.pause()

    def courses(self, pause=True):
        from .api import application

        self.ui.banner()
        self.ui.title("Elegir materias")
        self.ui.dim("Solo las materias seleccionadas se sincronizan y quedan visibles en el MCP.")
        self.ui.write()
        with application() as service:
            service.refresh_courses()
            courses = service.store.courses()
            options = [
                (item["id"], item["fullname"].replace(",", " ·")) for item in courses
            ]
            selected = {item["id"] for item in courses if item.get("selected")}
            if not options:
                self.ui.warn("La cuenta no tiene materias accesibles.")
            else:
                chosen = self.ui.multi("¿Qué materias querés disponibles?", options, selected)
                if chosen is None:
                    self.ui.dim("Cancelado: la selección quedó como estaba.")
                elif not chosen and not self.ui.confirm(
                    "¿Guardar una selección vacía? El MCP quedará sin contenido.",
                    danger=True,
                ):
                    self.ui.dim("Cancelado: la selección quedó como estaba.")
                else:
                    service.store.select(sorted(chosen))
                    self.ui.ok(f"✓ Guardadas {len(chosen)} materia(s).")
        if pause:
            self.ui.pause()

    def sync(self, pause=True):
        from .cli import main

        self.ui.banner()
        self.ui.title("Sincronizar")
        self.ui.dim("Descarga metadatos e indexa PDF en lotes; Ctrl-C permite cortar y retomar.")
        self.ui.write()
        choice = self.ui.menu(
            "¿Qué tipo de sincronización?",
            [
                "Normal — trae lo nuevo y sigue lo pendiente",
                "Reintentar fallas — vuelve a bajar todo y reintenta páginas rotas",
                "Volver",
            ],
        )
        if choice and choice.startswith("Normal"):
            result = main(["sync"])
            message = (
                "✓ Sincronización terminada."
                if result == 0
                else "La sincronización terminó con errores."
            )
            (self.ui.ok if result == 0 else self.ui.error)(message)
        elif choice and choice.startswith("Reintentar"):
            self.ui.warn("Esto vuelve a descargar los PDF y puede tardar varios minutos.")
            if self.ui.confirm("¿Seguir?", danger=True):
                result = main(["sync", "--force-refresh"])
                message = (
                    "✓ Sincronización terminada."
                    if result == 0
                    else "La sincronización terminó con errores."
                )
                (self.ui.ok if result == 0 else self.ui.error)(message)
        if pause:
            self.ui.pause()

    def client(self, pause=True):
        from .cli import main

        self.ui.banner()
        self.ui.title("Conectar un cliente MCP")
        self.ui.write()
        choices = {
            "Claude Code — registro automático": "claude",
            "ChatGPT desktop / Codex CLI — registro automático": "codex",
            "Claude Desktop — registro automático": "claude-desktop",
            "Los tres — registro automático": "all",
        }
        choice = self.ui.menu(
            "¿Dónde querés usar e-FICH?",
            [*choices, "Otro cliente — configuración manual", "Volver"],
        )
        if choice in choices:
            self.ui.write()
            result = main(["configure", choices[choice]])
            message = "✓ Configuración terminada." if result == 0 else "No se pudo registrar."
            (self.ui.ok if result == 0 else self.ui.error)(message)
        elif choice and choice.startswith("Otro cliente"):
            filename = "fich-mcp.exe" if os.name == "nt" else "fich-mcp"
            executable = shutil.which("fich-mcp") or str(Path(sys.executable).with_name(filename))
            self.ui.dim("Servidor stdio:")
            self.ui.write(f"  {executable} serve")
            self.ui.write()
            self.ui.dim("JSON:")
            config = {
                "mcpServers": {
                    "fich": {"command": executable, "args": ["serve"]}
                }
            }
            self.ui.write(json.dumps(config, ensure_ascii=False, indent=2))
            self.ui.write()
            self.ui.dim("TOML:")
            self.ui.write(
                f'[mcp_servers.fich]\ncommand = {json.dumps(executable)}\nargs = ["serve"]'
            )
        if pause:
            self.ui.pause()

    def install_package(self):
        repo = Path(__file__).resolve().parents[2]
        self.ui.banner()
        self.ui.title("Instalar o actualizar el paquete")
        self.ui.write()
        if not (repo / "pyproject.toml").is_file():
            self.ui.ok(f"✓ fich-mcp ya está instalado en {Path(sys.executable).parent}")
            self.ui.dim("Para actualizarlo, repetí el comando pip de la guía de Windows.")
        elif self.ui.confirm("¿Instalar este código en modo editable en el entorno actual?"):
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "-e", str(repo)]
            )
            message = "✓ Paquete instalado." if result.returncode == 0 else "Falló la instalación."
            (self.ui.ok if result.returncode == 0 else self.ui.error)(message)
        self.ui.pause()

    def guided(self):
        from .cli import doctor
        from .security import Paths

        self.ui.banner()
        self.ui.title("Instalación guiada")
        self.ui.dim("Cuatro pasos: sesión, materias, cliente y primera sincronización.")
        self.ui.write()
        if not self.ui.confirm("¿Empezamos?"):
            return
        if doctor(Paths.default())["authentication"] != "valid":
            self.login(pause=False)
        else:
            self.ui.ok("✓ Ya hay una sesión válida.")
        if doctor(Paths.default())["authentication"] != "valid":
            self.ui.error("La instalación guiada necesita una sesión válida.")
            self.ui.pause()
            return
        self.courses(pause=False)
        self.client(pause=False)
        self.ui.banner()
        self.ui.title("Primera sincronización")
        self.ui.dim("Puede tardar: baja e indexa los PDF de las materias elegidas.")
        self.ui.write()
        if self.ui.confirm("¿Sincronizar ahora?"):
            from .cli import main

            main(["sync"])
        self.ui.write()
        self.ui.box("✓ e-FICH MCP listo")
        self.ui.write()
        self.ui.dim("by @juanmabdu")
        self.ui.pause()

    def run(self):
        from .security import FichError

        options = [
            "Instalación guiada",
            "Iniciar sesión en e-FICH",
            "Elegir materias",
            "Sincronizar",
            "Conectar un cliente MCP",
            "Instalar o actualizar el paquete",
            "Estado",
            "Salir",
        ]
        actions = {
            options[0]: self.guided,
            options[1]: self.login,
            options[2]: self.courses,
            options[3]: self.sync,
            options[4]: self.client,
            options[5]: self.install_package,
            options[6]: self.status,
        }
        while True:
            choice = self.ui.menu("¿Qué querés hacer?", options)
            if choice in (None, "Salir"):
                self.ui.clear()
                return
            try:
                actions[choice]()
            except FichError as exc:
                self.ui.error(f"Error: {exc.code}")
                if exc.code == "authentication_required":
                    self.ui.dim("Primero elegí «Iniciar sesión en e-FICH».")
                self.ui.pause()
            except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
                self.ui.error("Error: operation_failed.")
                self.ui.pause()


def run():
    App().run()
