"""Dependency-free native terminal menu, included in wheels."""


def run():
    from .cli import main

    actions = {
        "1": ("Iniciar sesión y elegir materias", ["init"]),
        "2": ("Diagnóstico", ["doctor"]),
        "3": ("Ver materias", ["courses"]),
        "4": ("Elegir materias", ["courses", "--select"]),
        "5": ("Sincronizar", ["sync"]),
        "6": ("Configurar Claude Code", ["configure", "claude"]),
        "7": ("Configurar Codex", ["configure", "codex"]),
        "8": ("Configurar Claude Desktop", ["configure", "claude-desktop"]),
    }
    while True:
        print("\ne-FICH · Plataforma educativa · MCP\nby @juanmabdu\n")
        for key, (label, _) in actions.items():
            print(f"  {key}. {label}")
        print("  0. Salir")
        try:
            answer = input("Elegí una opción: ").strip()
        except (EOFError, KeyboardInterrupt):
            return
        if answer == "0":
            return
        if answer not in actions:
            print("Opción inválida.")
            continue
        main(actions[answer][1])
