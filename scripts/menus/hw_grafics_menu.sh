#!/bin/bash
# ==========================================================
# ProxMenux - GPU and TPU Menu
# ==========================================================
# Author      : MacRimi
# Copyright   : (c) 2024 MacRimi
# License     : MIT
# Version     : 2.0
# Last Updated: 01/04/2026
# ==========================================================

# Configuration
LOCAL_SCRIPTS="/usr/local/share/proxmenux/scripts"
BASE_DIR="/usr/local/share/proxmenux"
UTILS_FILE="$BASE_DIR/utils.sh"
VENV_PATH="/opt/googletrans-env"

if [[ -f "$UTILS_FILE" ]]; then
    source "$UTILS_FILE"
fi

load_language
initialize_cache

# ==========================================================

while true; do
    OPTION=$(dialog --colors --backtitle "ProxMenux" \
        --title "$(translate "GPUs and Coral-TPU Menu")" \
        --menu "\n$(translate "Select an option:")" 25 80 15 \
            ""  "\Z4──────────────────────── $(translate "HOST") ─────────────────────────\Zn" \
            "1"         "$(translate "Install NVIDIA Drivers on Host")" \
            "2"         "$(translate "Update NVIDIA Drivers (Host + LXC)")" \
            "3"         "$(translate "Install/Update Coral TPU on Host")" \
            ""  "\Z4──────────────────────── $(translate "LXC") ──────────────────────────\Zn" \
            "4"         "$(translate "Add GPU to LXC   (Intel / AMD / NVIDIA)")" \
            "5"         "$(translate "Add Coral TPU to LXC")" \
            ""          "" \
            "0"         "$(translate "Return to Main Menu")" \
            2>&1 >/dev/tty
    ) || { exec bash "$LOCAL_SCRIPTS/menus/main_menu.sh"; }

    case "$OPTION" in
        1)
            bash "$LOCAL_SCRIPTS/gpu_tpu/nvidia_installer.sh"
            ;;
        2)
            bash "$LOCAL_SCRIPTS/gpu_tpu/nvidia_update.sh"
            ;;
        3)
            bash "$LOCAL_SCRIPTS/gpu_tpu/install_coral_pve9.sh"
            ;;
        4)
            bash "$LOCAL_SCRIPTS/gpu_tpu/add_gpu_lxc.sh"
            ;;
        5)
            bash "$LOCAL_SCRIPTS/gpu_tpu/install_coral_lxc.sh"
            ;;
        0)
            exec bash "$LOCAL_SCRIPTS/menus/main_menu.sh"
            ;;
        *)
            exec bash "$LOCAL_SCRIPTS/menus/main_menu.sh"
            ;;
    esac
done
