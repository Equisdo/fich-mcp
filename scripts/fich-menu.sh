#!/usr/bin/env bash
# Guided installer and operator menu for the FICH MCP companion.
#
# Uses gum (https://github.com/charmbracelet/gum) when available and falls back
# to a plain-bash menu otherwise, so it runs on a machine with nothing extra.
#
# Credentials are never read here: `init` owns the HTTP warning, the consent
# prompt and the password prompt. A wrapper that collected them would bypass
# that gate and leave them in a shell variable.

set -uo pipefail

AZUL="#38BDF8"
AZUL2="#0EA5E9"
GRIS="#6B7280"
VERDE="#4ADE80"
AMBAR="#FBBF24"
ROJO="#F87171"

C_AZUL=$'\033[38;5;39m'
C_CELESTE=$'\033[38;5;51m'
C_GRIS=$'\033[38;5;245m'
C_VERDE=$'\033[38;5;114m'
C_AMBAR=$'\033[38;5;221m'
C_ROJO=$'\033[38;5;203m'
C_BOLD=$'\033[1m'
C_RESET=$'\033[0m'

# FICH_MENU_NO_GUM=1 forces the plain-bash menu (useful over a dumb terminal).
HAS_GUM=0
[[ -z "${FICH_MENU_NO_GUM:-}" ]] && command -v gum >/dev/null 2>&1 && HAS_GUM=1

# ---------------------------------------------------------------- presentation

# `clear` needs TERM; fall back to the ANSI erase sequence when it is unset.
wipe() { clear 2>/dev/null || printf '\033[H\033[2J\033[3J'; }

banner() {
    wipe
    printf '%s' "$C_AZUL"
    cat <<'ART'

   ██████╗       ███████╗ ██╗  ██████╗ ██╗  ██╗
 ██╔════██╗      ██╔════╝ ██║ ██╔════╝ ██║  ██║
 █████████║█████╗█████╗   ██║ ██║      ███████║
 ██╔══════╝╚════╝██╔══╝   ██║ ██║      ██╔══██║
 ╚███████╗       ██║      ██║ ╚██████╗ ██║  ██║
  ╚══════╝       ╚═╝      ╚═╝  ╚═════╝ ╚═╝  ╚═╝

ART
    printf '%s' "$C_RESET"
    if (( HAS_GUM )); then
        gum style --foreground "$AZUL" --bold --align center --width 50 "e-FICH"
        gum style --foreground "$GRIS" --align center --width 50 "Plataforma educativa · MCP"
        gum style --foreground "$GRIS" --align center --width 50 "by @juanmabdu"
    else
        printf '                    %s%se-FICH%s\n' "$C_BOLD" "$C_AZUL" "$C_RESET"
        printf '           %sPlataforma educativa · MCP%s\n' "$C_GRIS" "$C_RESET"
        printf '                %sby @juanmabdu%s\n' "$C_GRIS" "$C_RESET"
    fi
    echo
    printf ' %s──────────────────────────────────────────────%s\n\n' "$C_GRIS" "$C_RESET"
}

title() { if (( HAS_GUM )); then gum style --bold --foreground "$AZUL" "$1"; else printf '%s%s%s%s\n' "$C_BOLD" "$C_AZUL" "$1" "$C_RESET"; fi; }
dim()   { if (( HAS_GUM )); then gum style --foreground "$GRIS"  "$1"; else printf '%s%s%s\n' "$C_GRIS"  "$1" "$C_RESET"; fi; }
ok()    { if (( HAS_GUM )); then gum style --foreground "$VERDE" "$1"; else printf '%s%s%s\n' "$C_VERDE" "$1" "$C_RESET"; fi; }
warn()  { if (( HAS_GUM )); then gum style --foreground "$AMBAR" "$1"; else printf '%s%s%s\n' "$C_AMBAR" "$1" "$C_RESET"; fi; }
err()   { if (( HAS_GUM )); then gum style --foreground "$ROJO"  "$1"; else printf '%s%s%s\n' "$C_ROJO"  "$1" "$C_RESET"; fi; }

box() {
    if (( HAS_GUM )); then
        gum style --border rounded --border-foreground "$AZUL" --padding "1 3" --foreground "$VERDE" --bold "$1"
    else
        printf '%s ┌%s┐\n │ %s%s%s │\n └%s┘%s\n' \
            "$C_AZUL" "$(printf '─%.0s' $(seq 1 $((${#1} + 2))))" \
            "$C_VERDE$C_BOLD" "$1" "$C_RESET$C_AZUL" \
            "$(printf '─%.0s' $(seq 1 $((${#1} + 2))))" "$C_RESET"
    fi
}

pause() { echo; dim "Enter para volver al menú."; read -r _; }

# One choice out of N. Prints the chosen label; empty output means cancelled.
ui_menu() {
    local header="$1"; shift
    if (( HAS_GUM )); then
        gum choose --cursor="❯ " --cursor.foreground="$AZUL" \
            --selected.foreground="$AZUL" --header="$header" "$@"
        return
    fi
    printf '%s%s%s\n\n' "$C_GRIS" "$header" "$C_RESET" >&2
    local i=1 opt
    for opt in "$@"; do
        printf '  %s%s%d%s  %s%s%s\n' "$C_BOLD" "$C_AZUL" "$i" "$C_RESET" "$C_CELESTE" "$opt" "$C_RESET" >&2
        i=$((i + 1))
    done
    printf '\n %s❯%s ' "$C_CELESTE" "$C_RESET" >&2
    local answer; read -r answer
    [[ "$answer" =~ ^[0-9]+$ ]] && (( answer >= 1 && answer <= $# )) && printf '%s\n' "${!answer}"
}

# Zero or more choices out of N, one per output line. $2 is a comma-separated
# list of labels that start selected, so opening the picker cannot silently drop
# what is already chosen. A non-zero status means the user cancelled.
ui_multi() {
    local header="$1" preselected="$2"; shift 2
    if (( HAS_GUM )); then
        gum choose --no-limit --height 14 --cursor="❯ " --cursor.foreground="$AZUL" \
            --selected="$preselected" --selected.foreground="$AZUL2" \
            --header="$header" "$@"
        return
    fi
    printf '%s%s%s\n\n' "$C_GRIS" "$header" "$C_RESET" >&2
    local i=1 opt
    for opt in "$@"; do
        printf '  %s%s%2d%s  %s%s%s\n' "$C_BOLD" "$C_AZUL" "$i" "$C_RESET" "$C_CELESTE" "$opt" "$C_RESET" >&2
        i=$((i + 1))
    done
    printf '\n %sNúmeros separados por coma (vacío = ninguna)%s\n %s❯%s ' \
        "$C_GRIS" "$C_RESET" "$C_CELESTE" "$C_RESET" >&2
    local answer; read -r answer
    local n
    for n in ${answer//,/ }; do
        [[ "$n" =~ ^[0-9]+$ ]] && (( n >= 1 && n <= $# )) && printf '%s\n' "${!n}"
    done
}

ui_confirm() {
    if (( HAS_GUM )); then
        gum confirm --affirmative="Sí" --negative="Cancelar" "$1"
        return
    fi
    printf ' %s%s%s [s/N] ' "$C_CELESTE" "$1" "$C_RESET"
    local answer; read -r answer
    [[ "${answer,,}" == s* ]]
}

# Same question, but Enter cancels: for anything that can destroy existing state.
ui_confirm_danger() {
    if (( HAS_GUM )); then
        gum confirm --default=false --affirmative="Sí, continuar" --negative="Cancelar" "$1"
        return
    fi
    printf ' %s%s%s [s/N] ' "$C_AMBAR" "$1" "$C_RESET"
    local answer; read -r answer
    [[ "${answer,,}" == s* ]]
}

spin() {
    local label="$1"; shift
    if (( HAS_GUM )); then
        gum spin --spinner dot --spinner.foreground="$AZUL" --title="$label" -- "$@"
    else
        dim "$label" >&2
        "$@"
    fi
}

# ------------------------------------------------------------------ fich-mcp

REPO="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
FICH=()
FICH_SOURCE=""
PY=""

resolve_cli() {
    # `configure claude` registers whatever `which fich-mcp` resolves to, so put
    # the venv's executable on PATH when nothing else provides one.
    if ! command -v fich-mcp >/dev/null 2>&1 && [[ -x "$REPO/.venv/bin/fich-mcp" ]]; then
        export PATH="$REPO/.venv/bin:$PATH"
    fi
    # Prefer the repository source: the venv can hold an older installed copy,
    # and PYTHONPATH puts src/ ahead of site-packages.
    if [[ -x "$REPO/.venv/bin/python" && -f "$REPO/src/fich_mcp/cli.py" ]]; then
        export PYTHONPATH="$REPO/src${PYTHONPATH:+:$PYTHONPATH}"
        FICH=("$REPO/.venv/bin/python" -m fich_mcp)
        PY="$REPO/.venv/bin/python"
        FICH_SOURCE="código del repo ($REPO/src)"
    elif command -v fich-mcp >/dev/null 2>&1; then
        FICH=(fich-mcp)
        PY="$(command -v python3 || command -v python)"
        FICH_SOURCE="paquete instalado ($(command -v fich-mcp))"
    else
        err "No encuentro fich-mcp."
        dim "Corré el menú desde el repo, o instalá el paquete con: pip install ."
        exit 1
    fi
}

fich() { "${FICH[@]}" "$@"; }

# --------------------------------------------------------------------- acciones

accion_estado() {
    banner
    title "Estado"
    dim "Ejecutando: fich-mcp doctor"
    echo
    local salida
    salida="$(fich doctor 2>&1)" || true
    printf '%s\n' "$salida" | sed 's/^/  /'
    echo
    dim "Origen del código: $FICH_SOURCE"
    pause
}

accion_login() {
    banner
    title "Iniciar sesión en e-FICH"
    echo
    warn "e-FICH usa HTTP, no HTTPS."
    dim "Usuario, contraseña y token pueden ser leídos o modificados en tránsito."
    dim "Este menú no toca tus credenciales: las pide fich-mcp init, que además"
    dim "te va a pedir aceptar ese riesgo de forma explícita."
    echo
    ui_confirm "¿Continuar con el login?" || return
    echo
    # init owns stdin: the warning, the consent and the password prompt are its own.
    fich init
    echo
    if [[ -s "${XDG_CONFIG_HOME:-$HOME/.config}/fich-mcp/account.json" ]]; then
        ok "✓ Sesión guardada."
    else
        err "No quedó ninguna sesión guardada."
    fi
    pause
}

accion_materias() {
    banner
    title "Elegir materias"
    dim "Solo las materias seleccionadas se sincronizan y quedan visibles en el MCP."
    echo
    local json
    json="$(spin "Consultando materias en e-FICH..." "${FICH[@]}" courses 2>/dev/null)"
    if [[ "${json:0:1}" != "[" ]]; then
        err "No pude traer las materias desde e-FICH."
        dim "Si nunca iniciaste sesión, usá primero «Iniciar sesión»."
        pause; return
    fi

    # A comma in a course name would break gum's --selected list, so the label
    # keeps the id in field 2 and uses a middle dot instead.
    mapfile -t opciones < <(printf '%s' "$json" | "$PY" -c '
import json, sys
for course in json.load(sys.stdin):
    mark = "\u25cf" if course.get("selected") else "\u25cb"
    name = course.get("fullname", "").replace(",", " \u00b7")
    print(mark, course["id"], "\u00b7", name)
')
    if (( ${#opciones[@]} == 0 )); then
        warn "La cuenta no tiene materias accesibles."
        pause; return
    fi

    local previas
    previas="$(printf '%s\n' "${opciones[@]}" | grep '^●' | paste -sd, -)"

    echo
    dim "● ya seleccionada   ○ no seleccionada"
    echo
    local elegidas_archivo estado
    elegidas_archivo="$(mktemp)"
    ui_multi "¿Qué materias querés disponibles?" "$previas" "${opciones[@]}" > "$elegidas_archivo"
    estado=$?
    mapfile -t elegidas < "$elegidas_archivo"
    rm -f "$elegidas_archivo"

    if (( estado != 0 )); then
        echo; dim "Cancelado: la selección quedó como estaba."
        pause; return
    fi

    local ids=() linea
    for linea in "${elegidas[@]}"; do
        [[ -n "$linea" ]] && ids+=("$(printf '%s' "$linea" | awk '{print $2}')")
    done

    echo
    if (( ${#ids[@]} == 0 )); then
        warn "⚠ No seleccionaste ninguna materia."
        dim "Guardar así deja el MCP sin contenido y descarta la selección actual."
        ui_confirm_danger "¿Guardar igual una selección vacía?" || {
            echo; dim "Cancelado: la selección quedó como estaba."; pause; return
        }
    else
        for linea in "${elegidas[@]}"; do ok "  ✓ ${linea#* }"; done
        echo
        ui_confirm "¿Guardar esta selección?" || {
            echo; dim "Cancelado: la selección quedó como estaba."; pause; return
        }
    fi

    # `courses --select` prints the catalogue and its own English prompt, then reads
    # one comma-separated line; report what it actually stored, not what was asked.
    local respuesta salida guardado
    respuesta="$(IFS=,; echo "${ids[*]}")"
    echo
    salida="$(printf '%s\n' "$respuesta" | "${FICH[@]}" courses --select 2>/dev/null)"
    guardado="$(printf '%s' "$salida" | grep -o '{"selected": \[[^]]*\]}' | tail -1)"
    if [[ -z "$guardado" ]]; then
        err "No se pudo guardar la selección."
    else
        local cuantas
        cuantas="$(printf '%s' "$guardado" | grep -o '[0-9]\+' | wc -l)"
        ok "✓ Guardadas $cuantas materia(s)."
    fi
    pause
}

accion_sync() {
    banner
    title "Sincronizar"
    dim "Descarga metadatos e indexa los PDF en lotes acotados; podés cortarlo con Ctrl-C"
    dim "y retomar: el trabajo hecho queda guardado."
    echo
    local modo
    modo="$(ui_menu "¿Qué tipo de sincronización?" \
        "Normal — trae lo nuevo y sigue lo pendiente" \
        "Reintentar fallas — vuelve a bajar todo y reintenta páginas rotas" \
        "Volver")"
    case "$modo" in
        Normal*) echo; fich sync | tail -25 ;;
        Reintentar*)
            echo
            warn "Vuelve a descargar todos los PDF desde e-FICH. Puede tardar varios minutos."
            ui_confirm_danger "¿Seguir?" || { pause; return; }
            echo; fich sync --force-refresh | tail -25 ;;
        *) return ;;
    esac
    echo
    ok "✓ Sincronización terminada."
    pause
}

accion_cliente() {
    banner
    title "Conectar un cliente MCP"
    echo
    local cliente
    cliente="$(ui_menu "¿Dónde querés usar e-FICH?" \
        "Claude Code — registro automático" \
        "Otro cliente — configuración manual" \
        "Volver")"
    case "$cliente" in
        "Claude Code"*)
            echo
            if ! command -v fich-mcp >/dev/null 2>&1; then
                warn "El registro apunta al ejecutable instalado, y no hay ninguno en el PATH."
                dim "Instalalo primero desde «Instalar el paquete en el venv»."
                pause; return
            fi
            local salida
            salida="$(fich configure claude 2>&1)"
            case "$salida" in
                configured)         ok "✓ Registrado en Claude Code (scope de usuario)." ;;
                already_configured) ok "✓ Ya estaba registrado y apunta al ejecutable correcto." ;;
                *)                  err "No se pudo registrar: $salida" ;;
            esac
            ;;
        "Otro cliente"*)
            echo
            dim "Agregá este servidor stdio a la configuración de tu cliente:"
            echo
            local binario
            binario="$(command -v fich-mcp || true)"
            [[ -z "$binario" && -x "$REPO/.venv/bin/fich-mcp" ]] && binario="$REPO/.venv/bin/fich-mcp"
            [[ -z "$binario" ]] && binario="<instalá el paquete: opción «Instalar el paquete en el venv»>"
            printf '  %s serve\n' "$binario"
            echo
            dim "En formato JSON:"
            cat <<JSON | sed 's/^/  /'
{
  "mcpServers": {
    "fich": {
      "command": "$binario",
      "args": ["serve"]
    }
  }
}
JSON
            ;;
        *) return ;;
    esac
    pause
}

accion_instalar_paquete() {
    banner
    title "Instalar el paquete en el venv"
    dim "Deja el ejecutable fich-mcp disponible y sincronizado con el código del repo."
    dim "Es lo que necesita el registro automático en Claude Code."
    echo
    if [[ ! -x "$REPO/.venv/bin/pip" ]]; then
        err "No hay un venv en $REPO/.venv"
        dim "Crealo con: python3 -m venv .venv"
        pause; return
    fi
    ui_confirm "¿Instalar en modo editable (pip install -e .)?" || { pause; return; }
    echo
    (cd "$REPO" && "$REPO/.venv/bin/pip" install -e . 2>&1 | tail -5)
    echo
    if [[ -x "$REPO/.venv/bin/fich-mcp" ]]; then
        ok "✓ Instalado: $REPO/.venv/bin/fich-mcp"
        dim "Si no está en tu PATH, agregá $REPO/.venv/bin"
    else
        err "La instalación no dejó el ejecutable."
    fi
    pause
}

accion_guiada() {
    banner
    title "Instalación guiada"
    dim "Cuatro pasos: sesión, materias, cliente y primera sincronización."
    echo
    ui_confirm "¿Empezamos?" || return

    local estado
    estado="$(fich doctor 2>/dev/null | "$PY" -c 'import json,sys; print(json.load(sys.stdin)["authentication"])' 2>/dev/null)"
    if [[ "$estado" != "valid" ]]; then
        accion_login
    else
        banner; title "Instalación guiada"; ok "✓ Ya hay una sesión válida."; sleep 1
    fi

    accion_materias
    accion_cliente

    banner
    title "Primera sincronización"
    dim "Puede tardar: baja e indexa los PDF de las materias elegidas."
    echo
    if ui_confirm "¿Sincronizar ahora?"; then
        echo
        fich sync | tail -20
    fi
    echo
    box "✓ e-FICH MCP listo"
    echo
    dim "by @juanmabdu"
    pause
}

# ------------------------------------------------------------------------ main

resolve_cli

while true; do
    banner
    (( HAS_GUM )) || dim "Sugerencia: instalá gum para una interfaz con flechas."
    (( HAS_GUM )) || echo
    opcion="$(ui_menu "¿Qué querés hacer?" \
        "Instalación guiada" \
        "Iniciar sesión en e-FICH" \
        "Elegir materias" \
        "Sincronizar" \
        "Conectar un cliente MCP" \
        "Instalar el paquete en el venv" \
        "Estado" \
        "Salir")"
    case "$opcion" in
        "Instalación guiada")            accion_guiada ;;
        "Iniciar sesión en e-FICH")      accion_login ;;
        "Elegir materias")               accion_materias ;;
        "Sincronizar")                   accion_sync ;;
        "Conectar un cliente MCP")       accion_cliente ;;
        "Instalar el paquete en el venv") accion_instalar_paquete ;;
        "Estado")                        accion_estado ;;
        "Salir"|"")                      wipe; exit 0 ;;
    esac
done
