import io

from fich_mcp.tui import UI


class TTY(io.StringIO):
    def isatty(self):
        return True


def test_arrow_menu_chooses_highlighted_option(monkeypatch):
    output = TTY()
    ui = UI(stdin=TTY(), stdout=output)
    keys = iter(["down", "enter"])
    monkeypatch.setattr(ui, "_key", lambda: next(keys))

    assert ui.menu("¿Qué querés hacer?", ["Instalación guiada", "Salir"]) == "Salir"
    rendered = output.getvalue()
    assert "e-FICH" in rendered
    assert "by @juanmabdu" in rendered
    assert "↑↓ navegar" in rendered


def test_course_picker_preserves_current_selection(monkeypatch):
    ui = UI(stdin=TTY(), stdout=TTY())
    monkeypatch.setattr(ui, "_key", lambda: "enter")

    assert ui.multi("Materias", [(1, "AED"), (2, "Laboratorio de Datos")], {2}) == {2}


def test_course_picker_toggles_with_space(monkeypatch):
    ui = UI(stdin=TTY(), stdout=TTY())
    keys = iter(["space", "down", "space", "enter"])
    monkeypatch.setattr(ui, "_key", lambda: next(keys))

    assert ui.multi("Materias", [(1, "AED"), (2, "Laboratorio de Datos")], {1}) == {2}
