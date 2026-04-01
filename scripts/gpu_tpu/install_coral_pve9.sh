#!/bin/bash
# ProxMenux - Coral TPU Installer (PVE 9.x)
# =========================================
# Author      : MacRimi
# License     : MIT
# Version     : 1.4 (kernel-conditional patches, direct DKMS, no debuild)
# Last Updated: 01/04/2026
# =========================================

LOCAL_SCRIPTS="/usr/local/share/proxmenux/scripts"
BASE_DIR="/usr/local/share/proxmenux"
UTILS_FILE="$BASE_DIR/utils.sh"
LOG_FILE="/tmp/coral_install.log"

if [[ -f "$UTILS_FILE" ]]; then
  source "$UTILS_FILE"
fi


load_language
initialize_cache




ensure_apex_group_and_udev() {
  msg_info "Ensuring apex group and udev rules..."


  if ! getent group apex >/dev/null; then
    groupadd --system apex || true
    msg_ok "System group 'apex' created"
  else
    msg_ok "System group 'apex' already exists"
  fi


  cat >/etc/udev/rules.d/99-coral-apex.rules <<'EOF'
# Coral / Google APEX TPU (M.2 / PCIe)
# Assign group "apex" and safe permissions to device nodes
KERNEL=="apex_*", GROUP="apex", MODE="0660"
SUBSYSTEM=="apex", GROUP="apex", MODE="0660"
EOF


  if [[ -f /usr/lib/udev/rules.d/60-gasket-dkms.rules ]]; then
    sed -i 's/GROUP="[^"]*"/GROUP="apex"/g' /usr/lib/udev/rules.d/60-gasket-dkms.rules || true
  fi


  udevadm control --reload-rules
  udevadm trigger --subsystem-match=apex || true

  msg_ok "apex group and udev rules are in place"


  if ls -l /dev/apex_* 2>/dev/null | grep -q ' apex '; then
    msg_ok "Coral TPU device nodes detected with correct group (apex)"
  else
    msg_warn "apex device node not found yet; a reboot may be required"
  fi
}




pre_install_prompt() {
  if ! dialog --title "$(translate 'Coral TPU Installation')" --yesno \
    "\n$(translate 'Installing Coral TPU drivers requires rebooting the server after installation. Do you want to proceed?')" 10 70; then

    exit 0
  fi
}

install_coral_host() {
  show_proxmenux_logo
  : >"$LOG_FILE"

  # Detect running kernel and parse major/minor for conditional patches
  local KVER KMAJ KMIN
  KVER=$(uname -r)
  KMAJ=$(echo "$KVER" | cut -d. -f1)
  KMIN=$(echo "$KVER" | cut -d. -f2 | cut -d+ -f1 | cut -d- -f1)


  msg_info "$(translate 'Installing build dependencies...')"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq >>"$LOG_FILE" 2>&1
  if ! apt-get install -y git dkms build-essential "proxmox-headers-${KVER}" >>"$LOG_FILE" 2>&1; then
    msg_error "$(translate 'Error installing build dependencies. Check /tmp/coral_install.log')"; exit 1
  fi
  msg_ok "$(translate 'Build dependencies installed.')"


  cd /tmp || exit 1
  rm -rf gasket-driver >>"$LOG_FILE" 2>&1
  msg_info "$(translate 'Cloning Google Coral driver repository...')"
  if ! git clone https://github.com/google/gasket-driver.git >>"$LOG_FILE" 2>&1; then
    msg_error "$(translate 'Could not clone the repository. Check /tmp/coral_install.log')"; exit 1
  fi
  msg_ok "$(translate 'Repository cloned successfully.')"


  cd /tmp/gasket-driver || exit 1
  msg_info "$(translate 'Patching source for kernel compatibility...')"

  # Patch 1: no_llseek was removed in kernel 6.5 — replace with noop_llseek
  if [[ "$KMAJ" -gt 6 ]] || [[ "$KMAJ" -eq 6 && "$KMIN" -ge 5 ]]; then
    sed -i 's/\.llseek = no_llseek/\.llseek = noop_llseek/' src/gasket_core.c
  fi

  # Patch 2: MODULE_IMPORT_NS changed to string-literal syntax in kernel 6.13.
  # IMPORTANT: applying this patch on kernel < 6.13 causes a compile error.
  if [[ "$KMAJ" -gt 6 ]] || [[ "$KMAJ" -eq 6 && "$KMIN" -ge 13 ]]; then
    sed -i 's/^MODULE_IMPORT_NS(DMA_BUF);/MODULE_IMPORT_NS("DMA_BUF");/' src/gasket_page_table.c
  fi

  msg_ok "$(translate 'Source patched successfully.') (kernel ${KVER})"


  msg_info "$(translate 'Preparing DKMS source tree...')"
  local GASKET_SRC="/usr/src/gasket-1.0"
  # Remove any previous installation (package or manual) to avoid conflicts
  dpkg -r gasket-dkms >>"$LOG_FILE" 2>&1 || true
  dkms remove gasket/1.0 --all >>"$LOG_FILE" 2>&1 || true
  rm -rf "$GASKET_SRC"
  cp -r /tmp/gasket-driver/. "$GASKET_SRC"
  if ! dkms add "$GASKET_SRC" >>"$LOG_FILE" 2>&1; then
    msg_error "$(translate 'DKMS add failed. Check /tmp/coral_install.log')"; exit 1
  fi
  msg_ok "$(translate 'DKMS source tree prepared.')"


  msg_info "$(translate 'Compiling Coral TPU drivers for current kernel...')"
  if ! dkms build gasket/1.0 -k "$KVER" >>"$LOG_FILE" 2>&1; then
    sed -n '1,200p' /var/lib/dkms/gasket/1.0/build/make.log >>"$LOG_FILE" 2>&1 || true
    msg_error "$(translate 'DKMS build failed. Check /tmp/coral_install.log')"; exit 1
  fi
  if ! dkms install gasket/1.0 -k "$KVER" >>"$LOG_FILE" 2>&1; then
    msg_error "$(translate 'DKMS install failed. Check /tmp/coral_install.log')"; exit 1
  fi
  msg_ok "$(translate 'Drivers compiled and installed via DKMS.')"


  ensure_apex_group_and_udev

  msg_info "$(translate 'Loading modules...')"
  modprobe gasket >>"$LOG_FILE" 2>&1 || true
  modprobe apex   >>"$LOG_FILE" 2>&1 || true
  if lsmod | grep -q '\bapex\b'; then
    msg_ok "$(translate 'Modules loaded.')"
    msg_success "$(translate 'Coral TPU drivers installed and loaded successfully.')"
  else
    msg_warn "$(translate 'Installation finished but drivers are not loaded. Please check dmesg and /tmp/coral_install.log')"
  fi

  echo "---- dmesg | grep -i apex (last lines) ----" >>"$LOG_FILE"
  dmesg | grep -i apex | tail -n 20 >>"$LOG_FILE" 2>&1
}

restart_prompt() {
  if whiptail --title "$(translate 'Coral TPU Installation')" --yesno \
    "$(translate 'The installation requires a server restart to apply changes. Do you want to restart now?')" 10 70; then
    msg_warn "$(translate 'Restarting the server...')"
    reboot
  else
    msg_success "$(translate 'Completed. Press Enter to return to menu...')"
    read -r
  fi
}


pre_install_prompt
install_coral_host
restart_prompt
