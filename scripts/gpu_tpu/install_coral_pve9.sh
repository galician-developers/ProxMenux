#!/bin/bash
# ProxMenux - Coral TPU Installer (PVE 9.x)
# =========================================
# Author      : MacRimi
# License     : MIT
# Version     : 1.5 (feranick fork primary; kernel 6.12+ support; broken-pkg recovery)
# Last Updated: 17/04/2026
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


# ============================================================
# Clean up a broken gasket-dkms package state.
#
# Context: if the user had gasket-dkms installed as a .deb (typical after
# following the Coral docs or via libedgetpu1-std's dependency chain), a
# kernel upgrade on PVE 9 triggers dkms autoinstall → compile fails on
# kernel 6.12+ → dpkg leaves the package half-configured. That broken state
# blocks further apt operations (including our own `apt-get install` below)
# with "E: Sub-process /usr/bin/dpkg returned an error code (1)".
# ============================================================
cleanup_broken_gasket_dkms() {
  # dpkg status codes of interest (first column of `dpkg -l`):
  #   iF  half-configured
  #   iU  unpacked (not configured)
  #   iH  half-installed
  # Also catch the case where the package is installed but the DKMS source tree
  # is broken (kernel upgrade without rebuild).
  local pkg_state
  pkg_state=$(dpkg -l gasket-dkms 2>/dev/null | awk '/^[a-zA-Z][a-zA-Z]/ {print $1}' | tail -1)

  if [[ -z "$pkg_state" ]]; then
    return 0  # package not present, nothing to clean
  fi

  # Any state other than "ii" (installed+configured cleanly) or "rc"
  # (removed but config remaining) warrants proactive cleanup.
  case "$pkg_state" in
    ii|rc)
      # Even when state is "ii", a stale DKMS module may exist — drop it to
      # ensure our fresh build replaces the old one.
      msg_info "$(translate 'Removing any pre-existing gasket-dkms package...')"
      dpkg -r gasket-dkms >>"$LOG_FILE" 2>&1 || true
      dkms remove gasket/1.0 --all >>"$LOG_FILE" 2>&1 || true
      msg_ok "$(translate 'Pre-existing gasket-dkms package removed.')"
      ;;
    *)
      msg_warn "$(translate 'Detected broken gasket-dkms package state:') ${pkg_state}. $(translate 'Forcing removal...')"
      dpkg --remove --force-remove-reinstreq gasket-dkms >>"$LOG_FILE" 2>&1 || true
      dpkg --purge --force-all gasket-dkms >>"$LOG_FILE" 2>&1 || true
      dkms remove gasket/1.0 --all >>"$LOG_FILE" 2>&1 || true
      # apt-get install -f resolves any remaining dependency issues left by
      # the forced removal above.
      apt-get install -f -y >>"$LOG_FILE" 2>&1 || true
      msg_ok "$(translate 'Broken gasket-dkms package state recovered.')"
      ;;
  esac
}


# ============================================================
# Clone the gasket driver sources.
#
# Primary:  feranick/gasket-driver  — community fork, actively maintained,
#                                     already carries patches for kernel
#                                     6.10 / 6.12 / 6.13.  Preferred.
# Fallback: google/gasket-driver    — upstream, stale.  Requires the manual
#                                     compatibility patches applied below.
#
# Sets GASKET_SOURCE_USED to "feranick" or "google" so downstream steps know
# whether to apply the local patches.
# ============================================================
clone_gasket_sources() {
  local FERANICK_URL="https://github.com/feranick/gasket-driver.git"
  local GOOGLE_URL="https://github.com/google/gasket-driver.git"

  cd /tmp || exit 1
  rm -rf gasket-driver >>"$LOG_FILE" 2>&1

  msg_info "$(translate 'Cloning Coral driver repository (feranick fork)...')"
  if git clone --depth=1 "$FERANICK_URL" gasket-driver >>"$LOG_FILE" 2>&1; then
    GASKET_SOURCE_USED="feranick"
    msg_ok "$(translate 'feranick/gasket-driver cloned (actively maintained, kernel 6.12+ ready).')"
    return 0
  fi

  msg_warn "$(translate 'feranick fork unreachable. Falling back to google/gasket-driver...')"
  rm -rf gasket-driver >>"$LOG_FILE" 2>&1
  if git clone --depth=1 "$GOOGLE_URL" gasket-driver >>"$LOG_FILE" 2>&1; then
    GASKET_SOURCE_USED="google"
    msg_ok "$(translate 'google/gasket-driver cloned (fallback — will apply local patches).')"
    return 0
  fi

  msg_error "$(translate 'Could not clone any gasket-driver repository. Check your internet connection and /tmp/coral_install.log')"
  exit 1
}


# ============================================================
# On a failed DKMS build, surface the most relevant lines of make.log
# on-screen so the user (and bug reports) have immediate context without
# having to open the log file manually.
# ============================================================
show_dkms_build_failure() {
  local make_log="/var/lib/dkms/gasket/1.0/build/make.log"
  echo "" >&2
  msg_warn "$(translate 'DKMS build failed. Last lines of make.log:')"
  if [[ -f "$make_log" ]]; then
    # Also append the full log to our install log for post-mortem.
    {
      echo "---- /var/lib/dkms/gasket/1.0/build/make.log ----"
      cat "$make_log"
    } >>"$LOG_FILE" 2>&1
    tail -n 50 "$make_log" >&2
  else
    echo "$(translate '(make.log not found — DKMS may have failed before invoking make)')" >&2
  fi
  echo "" >&2
  echo -e "${TAB}${BL}$(translate 'Full log:')${CL} /tmp/coral_install.log" >&2
  echo "" >&2
}

install_coral_host() {
  show_proxmenux_logo
  : >"$LOG_FILE"

  # Detect running kernel and parse major/minor for conditional patches
  local KVER KMAJ KMIN
  KVER=$(uname -r)
  KMAJ=$(echo "$KVER" | cut -d. -f1)
  KMIN=$(echo "$KVER" | cut -d. -f2 | cut -d+ -f1 | cut -d- -f1)

  # Recover from a broken gasket-dkms package state (typical after a kernel
  # upgrade on PVE 9) before attempting any apt operations.
  cleanup_broken_gasket_dkms

  msg_info "$(translate 'Installing build dependencies...')"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq >>"$LOG_FILE" 2>&1
  if ! apt-get install -y git dkms build-essential "proxmox-headers-${KVER}" >>"$LOG_FILE" 2>&1; then
    msg_error "$(translate 'Error installing build dependencies. Check /tmp/coral_install.log')"; exit 1
  fi
  msg_ok "$(translate 'Build dependencies installed.')"

  # Clone sources (feranick fork preferred, google fallback).
  # Sets GASKET_SOURCE_USED.
  clone_gasket_sources

  cd /tmp/gasket-driver || exit 1

  # Apply compatibility patches ONLY when using the stale google/gasket-driver
  # fallback. feranick/gasket-driver already has equivalent fixes upstream, so
  # re-applying them would double-edit (and in some cases break) the sources.
  if [[ "$GASKET_SOURCE_USED" == "google" ]]; then
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
  else
    msg_info2 "$(translate 'Skipping manual patches — feranick fork already supports this kernel.')"
  fi


  msg_info "$(translate 'Preparing DKMS source tree...')"
  local GASKET_SRC="/usr/src/gasket-1.0"
  # Remove any leftover manual DKMS tree from a previous run (package-level
  # cleanup was already handled by cleanup_broken_gasket_dkms above).
  dkms remove gasket/1.0 --all >>"$LOG_FILE" 2>&1 || true
  rm -rf "$GASKET_SRC"
  cp -r /tmp/gasket-driver/. "$GASKET_SRC"
  if ! dkms add "$GASKET_SRC" >>"$LOG_FILE" 2>&1; then
    msg_error "$(translate 'DKMS add failed. Check /tmp/coral_install.log')"; exit 1
  fi
  msg_ok "$(translate 'DKMS source tree prepared.')"


  msg_info "$(translate 'Compiling Coral TPU drivers for current kernel...')"
  if ! dkms build gasket/1.0 -k "$KVER" >>"$LOG_FILE" 2>&1; then
    show_dkms_build_failure
    msg_error "$(translate 'DKMS build failed.')"
    exit 1
  fi
  if ! dkms install gasket/1.0 -k "$KVER" >>"$LOG_FILE" 2>&1; then
    show_dkms_build_failure
    msg_error "$(translate 'DKMS install failed.')"
    exit 1
  fi
  msg_ok "$(translate 'Drivers compiled and installed via DKMS.') (source: ${GASKET_SOURCE_USED})"


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
