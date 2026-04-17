#!/bin/bash
# ProxMenux - NVIDIA Driver Updater (Host + LXC)
# ================================================
# Author      : MacRimi
# License     : MIT
# Version     : 1.0
# Last Updated: 01/04/2026
# ================================================

LOCAL_SCRIPTS="/usr/local/share/proxmenux/scripts"
BASE_DIR="/usr/local/share/proxmenux"
UTILS_FILE="$BASE_DIR/utils.sh"
LOG_FILE="/tmp/nvidia_update.log"

NVIDIA_BASE_URL="https://download.nvidia.com/XFree86/Linux-x86_64"
NVIDIA_WORKDIR="/opt/nvidia"

if [[ -f "$UTILS_FILE" ]]; then
  source "$UTILS_FILE"
fi

load_language
initialize_cache


# ============================================================
# GPU passthrough guard — block update when GPU is in VM passthrough mode
# ============================================================
check_gpu_not_in_vm_passthrough() {
  local dev vendor driver vfio_list=""
  for dev in /sys/bus/pci/devices/*; do
    vendor=$(cat "$dev/vendor" 2>/dev/null)
    [[ "$vendor" != "0x10de" ]] && continue
    if [[ -L "$dev/driver" ]]; then
      driver=$(basename "$(readlink "$dev/driver")")
      if [[ "$driver" == "vfio-pci" ]]; then
        vfio_list+="  • $(basename "$dev")\n"
      fi
    fi
  done

  [[ -z "$vfio_list" ]] && return 0

  local msg
  msg="\n$(translate "One or more NVIDIA GPUs are currently configured for VM passthrough (vfio-pci):")\n\n"
  msg+="${vfio_list}\n"
  msg+="$(translate "Updating host drivers while the GPU is assigned to a VM could break passthrough and destabilize the system.")\n\n"
  msg+="$(translate "To update host drivers, first remove the GPU from VM passthrough configuration and reboot.")"

  dialog --backtitle "ProxMenux" \
    --title "$(translate "GPU in VM Passthrough Mode")" \
    --msgbox "$msg" 16 78
  exit 0
}


# ============================================================
# Host NVIDIA state detection
# ============================================================
detect_host_nvidia() {
  HOST_NVIDIA_VERSION=""
  HOST_NVIDIA_READY=false

  if lsmod | grep -q "^nvidia " && command -v nvidia-smi >/dev/null 2>&1; then
    HOST_NVIDIA_VERSION=$(nvidia-smi --query-gpu=driver_version \
      --format=csv,noheader 2>/dev/null | head -n1 | tr -d '[:space:]')
    [[ -n "$HOST_NVIDIA_VERSION" ]] && HOST_NVIDIA_READY=true
  fi

  if ! $HOST_NVIDIA_READY; then
    dialog --backtitle "ProxMenux" \
      --title "$(translate 'NVIDIA Not Found')" \
      --msgbox "\n$(translate 'No NVIDIA driver is currently loaded on this host.')\n\n$(translate 'Please install NVIDIA drivers first using the option:')\n\n  $(translate 'Install NVIDIA Drivers on Host')\n\n$(translate 'from this same GPU and TPU menu.')" \
      13 72
    exit 0
  fi
}


# ============================================================
# LXC containers with NVIDIA passthrough
# ============================================================
find_nvidia_containers() {
  NVIDIA_CONTAINERS=()
  for conf in /etc/pve/lxc/*.conf; do
    [[ -f "$conf" ]] || continue
    if grep -qiE "dev[0-9]+:.*nvidia" "$conf"; then
      NVIDIA_CONTAINERS+=("$(basename "$conf" .conf)")
    fi
  done
}

get_lxc_nvidia_version() {
  local ctid="$1"
  local version=""

  # Prefer nvidia-smi when the container is running (works with .run-installed drivers)
  if pct status "$ctid" 2>/dev/null | grep -q "running"; then
    version=$(pct exec "$ctid" -- nvidia-smi \
      --query-gpu=driver_version --format=csv,noheader 2>/dev/null \
      | head -1 | tr -d '[:space:]' || true)
  fi

  # Fallback: dpkg status for apt-installed libcuda1 (dir-type storage, no start needed)
  if [[ -z "$version" ]]; then
    local rootfs="/var/lib/lxc/${ctid}/rootfs"
    if [[ -f "${rootfs}/var/lib/dpkg/status" ]]; then
      version=$(grep -A5 "^Package: libcuda1$" "${rootfs}/var/lib/dpkg/status" \
        | grep "^Version:" | head -1 | awk '{print $2}' | cut -d- -f1)
    fi
  fi

  echo "${version:-$(translate 'not installed')}"
}


# ============================================================
# Version list from NVIDIA servers
# ============================================================
list_available_versions() {
  local html
  html=$(curl -s --connect-timeout 15 "${NVIDIA_BASE_URL}/" 2>/dev/null) || true

  if [[ -z "$html" ]]; then
    echo ""
    return 1
  fi

  echo "$html" \
    | grep -o 'href=[^ >]*' \
    | awk -F"'" '{print $2}' \
    | grep -E '^[0-9]' \
    | sed 's/\/$//' \
    | sed "s/^[[:space:]]*//;s/[[:space:]]*$//" \
    | sort -Vr \
    | uniq
}

get_latest_version() {
  local latest_line
  latest_line=$(curl -fsSL --connect-timeout 15 "${NVIDIA_BASE_URL}/latest.txt" 2>/dev/null) || true
  echo "$latest_line" | awk '{print $1}' | tr -d '[:space:]'
}


# ============================================================
# Version selection menu
# ============================================================
select_target_version() {
  show_proxmenux_logo
  msg_title "$(translate 'NVIDIA Driver Update')"
  msg_info "$(translate 'Fetching available NVIDIA versions...')"
  local latest versions_list
  latest=$(get_latest_version 2>/dev/null)
  versions_list=$(list_available_versions 2>/dev/null)
  msg_ok "$(translate 'Version list retrieved.')"
  sleep 1
  
  if [[ -z "$latest" && -z "$versions_list" ]]; then
    dialog --backtitle "ProxMenux" \
      --title "$(translate 'Error')" \
      --msgbox "\n$(translate 'Could not retrieve versions from NVIDIA. Please check your internet connection.')" \
      8 72
    exit 1
  fi

  [[ -z "$latest" && -n "$versions_list" ]] && latest=$(echo "$versions_list" | head -1)
  [[ -z "$versions_list" ]] && versions_list="$latest"
  latest=$(echo "$latest" | tr -d '[:space:]')

  local choices=()
  choices+=("latest" "$(translate 'Latest available') (${latest:-?})")
  choices+=("" "")

  while IFS= read -r ver; do
    ver=$(echo "$ver" | tr -d '[:space:]')
    [[ -z "$ver" ]] && continue
    choices+=("$ver" "$ver")
  done <<< "$versions_list"

  local menu_text
  menu_text="\n$(translate 'Current host version:') ${HOST_NVIDIA_VERSION}\n"
  menu_text+="$(translate 'Select the target version to install on host and all affected LXCs:')"

  TARGET_VERSION=$(dialog --backtitle "ProxMenux" \
    --title "$(translate 'NVIDIA Driver Version')" \
    --menu "$menu_text" 26 80 16 \
    "${choices[@]}" \
    2>&1 >/dev/tty) || exit 0

  [[ -z "$TARGET_VERSION" ]] && exit 0

  if [[ "$TARGET_VERSION" == "latest" ]]; then
    TARGET_VERSION="$latest"
  fi
  TARGET_VERSION=$(echo "$TARGET_VERSION" | tr -d '[:space:]')
}


# ============================================================
# Update NVIDIA userspace libs inside a single LXC
# ============================================================
update_lxc_nvidia() {
  local ctid="$1"
  local version="$2"
  local was_running=false

  # Capture old version before update
  local old_version
  old_version=$(get_lxc_nvidia_version "$ctid")

  if pct status "$ctid" 2>/dev/null | grep -q "running"; then
    was_running=true
  else
    msg_info "$(translate 'Starting container') ${ctid}..."
    pct start "$ctid" >>"$LOG_FILE" 2>&1 || true
    local ready=false
    for _ in {1..15}; do
      sleep 2
      pct exec "$ctid" -- true >/dev/null 2>&1 && ready=true && break
    done
    if ! $ready; then
      msg_warn "$(translate 'Container') ${ctid} $(translate 'did not start. Skipping.')"
      return 1
    fi
    msg_ok "$(translate 'Container') ${ctid} $(translate 'started.')"
  fi

  msg_info "$(translate 'Updating NVIDIA libs in container') ${ctid}..."

  local run_file="${NVIDIA_WORKDIR}/NVIDIA-Linux-x86_64-${version}.run"

  if [[ ! -f "$run_file" ]]; then
    msg_warn "$(translate 'Installer not found:') ${run_file} — $(translate 'skipping container') ${ctid}"
    if [[ "$was_running" == "false" ]]; then pct stop "$ctid" >>"$LOG_FILE" 2>&1 || true; fi
    return 1
  fi

  # Extract .run on the host to avoid decompression failures inside the container
  local extract_dir="${NVIDIA_WORKDIR}/extracted_${version}"
  local archive="/tmp/nvidia_lxc_${version}.tar.gz"

  msg_info "$(translate 'Extracting NVIDIA installer on host...')"
  rm -rf "$extract_dir"
  if ! sh "$run_file" --extract-only --target "$extract_dir" >>"$LOG_FILE" 2>&1; then
    msg_warn "$(translate 'Extraction failed. Check log:') ${LOG_FILE}"
    if [[ "$was_running" == "false" ]]; then pct stop "$ctid" >>"$LOG_FILE" 2>&1 || true; fi
    return 1
  fi
  msg_ok "$(translate 'Extracted.')"

  msg_info "$(translate 'Packing and copying installer to container') ${ctid}..."
  tar -czf "$archive" -C "$extract_dir" . >>"$LOG_FILE" 2>&1
  if ! pct push "$ctid" "$archive" /tmp/nvidia_lxc.tar.gz >>"$LOG_FILE" 2>&1; then
    msg_warn "$(translate 'pct push failed. Check log:') ${LOG_FILE}"
    rm -f "$archive"
    if [[ "$was_running" == "false" ]]; then pct stop "$ctid" >>"$LOG_FILE" 2>&1 || true; fi
    return 1
  fi
  rm -f "$archive"
  msg_ok "$(translate 'Installer copied to container.')"

  msg_info2 "$(translate 'Starting NVIDIA installer in container') ${ctid}. $(translate 'This may take several minutes...')"
  echo "" >>"$LOG_FILE"
  pct exec "$ctid" -- bash -c "
    mkdir -p /tmp/nvidia_lxc_install
    tar -xzf /tmp/nvidia_lxc.tar.gz -C /tmp/nvidia_lxc_install 2>&1
    /tmp/nvidia_lxc_install/nvidia-installer \
      --no-kernel-modules \
      --no-questions \
      --ui=none \
      --no-nouveau-check \
      --no-dkms
    EXIT=\$?
    rm -rf /tmp/nvidia_lxc_install /tmp/nvidia_lxc.tar.gz
    exit \$EXIT
  " 2>&1 | tee -a "$LOG_FILE"
  local rc=${PIPESTATUS[0]}

  rm -rf "$extract_dir"

  if [[ $rc -ne 0 ]]; then
    msg_warn "$(translate 'NVIDIA installer returned error') ${rc}. $(translate 'Check log:') ${LOG_FILE}"
    if [[ "$was_running" == "false" ]]; then pct stop "$ctid" >>"$LOG_FILE" 2>&1 || true; fi
    return 1
  fi

  msg_ok "$(translate 'Container') ${ctid}: ${old_version} → ${version}"
  msg_info2 "$(translate 'NVIDIA driver verification in container') ${ctid}:"
  pct exec "$ctid" -- nvidia-smi 2>/dev/null || true

  if [[ "$was_running" == "false" ]]; then
    msg_info "$(translate 'Stopping container') ${ctid}..."
    pct stop "$ctid" >>"$LOG_FILE" 2>&1 || true
    msg_ok "$(translate 'Container stopped.')"
  fi
}


# ============================================================
# Host NVIDIA update
# ============================================================
_stop_nvidia_services() {
  for svc in nvidia-persistenced.service nvidia-powerd.service; do
    systemctl is-active  --quiet "$svc" 2>/dev/null && systemctl stop    "$svc" >/dev/null 2>&1 || true
    systemctl is-enabled --quiet "$svc" 2>/dev/null && systemctl disable "$svc" >/dev/null 2>&1 || true
  done
}

_unload_nvidia_modules() {
  for mod in nvidia_uvm nvidia_drm nvidia_modeset nvidia; do
    modprobe -r "$mod" >/dev/null 2>&1 || true
  done
  # Second pass for stubborn modules
  for mod in nvidia_uvm nvidia_drm nvidia_modeset nvidia; do
    modprobe -r --force "$mod" >/dev/null 2>&1 || true
  done
}

_purge_nvidia_host() {
  msg_info "$(translate 'Uninstalling current NVIDIA driver from host...')"

  _stop_nvidia_services
  _unload_nvidia_modules

  command -v nvidia-uninstall >/dev/null 2>&1 \
    && nvidia-uninstall --silent >>"$LOG_FILE" 2>&1 || true

  # Remove DKMS entries
  local dkms_versions
  dkms_versions=$(dkms status 2>/dev/null | awk -F, '/nvidia/ {gsub(/ /,"",$2); print $2}' || true)
  while IFS= read -r ver; do
    [[ -z "$ver" ]] && continue
    dkms remove -m nvidia -v "$ver" --all >/dev/null 2>&1 || true
  done <<< "$dkms_versions"

  apt-get -y purge 'nvidia-*' 'libnvidia-*' 'cuda-*' >>"$LOG_FILE" 2>&1 || true
  apt-get -y autoremove --purge >>"$LOG_FILE" 2>&1 || true

  rm -f /etc/udev/rules.d/70-nvidia.rules
  rm -f /etc/modprobe.d/nvidia*.conf /usr/lib/modprobe.d/nvidia*.conf

  msg_ok "$(translate 'Current NVIDIA driver removed from host.')"
}

_download_installer() {
  local version="$1"
  local run_file="${NVIDIA_WORKDIR}/NVIDIA-Linux-x86_64-${version}.run"

  mkdir -p "$NVIDIA_WORKDIR"

  # Reuse cached file if valid
  local existing_size
  existing_size=$(stat -c%s "$run_file" 2>/dev/null || echo "0")
  if [[ -f "$run_file" ]] && [[ "$existing_size" -gt 40000000 ]]; then
    if file "$run_file" 2>/dev/null | grep -q "executable"; then
      msg_ok "$(translate 'Installer already cached.')"
      echo "$run_file"
      return 0
    fi
  fi
  rm -f "$run_file"

  msg_info "$(translate 'Downloading NVIDIA driver') ${version}..."

  local urls=(
    "${NVIDIA_BASE_URL}/${version}/NVIDIA-Linux-x86_64-${version}.run"
    "${NVIDIA_BASE_URL}/${version}/NVIDIA-Linux-x86_64-${version}-no-compat32.run"
  )

  local ok=false
  for url in "${urls[@]}"; do
    if curl -fL --connect-timeout 30 --max-time 600 "$url" -o "$run_file" >>"$LOG_FILE" 2>&1; then
      local sz
      sz=$(stat -c%s "$run_file" 2>/dev/null || echo "0")
      if [[ "$sz" -gt 40000000 ]] && file "$run_file" 2>/dev/null | grep -q "executable"; then
        ok=true
        break
      fi
    fi
    rm -f "$run_file"
  done

  if ! $ok; then
    msg_error "$(translate 'Download failed. Check /tmp/nvidia_update.log')"
    exit 1
  fi

  chmod +x "$run_file"
  msg_ok "$(translate 'Download complete.')"
  echo "$run_file"
}

_run_installer() {
  local installer="$1"
  local tmp_dir="${NVIDIA_WORKDIR}/tmp_extract"
  mkdir -p "$tmp_dir"

  msg_info "$(translate 'Installing NVIDIA driver on host. This may take several minutes...')"

  sh "$installer" \
    --tmpdir="$tmp_dir" \
    --no-questions \
    --ui=none \
    --disable-nouveau \
    --no-nouveau-check \
    --dkms \
    >>"$LOG_FILE" 2>&1
  local rc=$?

  rm -rf "$tmp_dir"

  if [[ $rc -ne 0 ]]; then
    msg_error "$(translate 'NVIDIA installer failed. Check /tmp/nvidia_update.log')"
    exit 1
  fi

  msg_ok "$(translate 'NVIDIA driver installed on host.')"
}

update_host_nvidia() {
  local version="$1"

  _purge_nvidia_host

  local installer
  installer=$(_download_installer "$version")

  _run_installer "$installer"

  msg_info "$(translate 'Updating initramfs...')"
  update-initramfs -u -k all >>"$LOG_FILE" 2>&1 || true
  msg_ok "$(translate 'initramfs updated.')"
}


# ============================================================
# Overview dialog (current state)
# ============================================================
show_current_state_dialog() {
  find_nvidia_containers

  local info
  info="\n$(translate 'Host NVIDIA driver:') ${HOST_NVIDIA_VERSION}\n\n"

  if [[ ${#NVIDIA_CONTAINERS[@]} -eq 0 ]]; then
    info+="$(translate 'No LXC containers with NVIDIA passthrough found.')\n"
  else
    info+="$(translate 'LXC containers with NVIDIA passthrough:')\n\n"
    for ctid in "${NVIDIA_CONTAINERS[@]}"; do
      local lxc_ver
      lxc_ver=$(get_lxc_nvidia_version "$ctid")
      local ct_name
      ct_name=$(pct config "$ctid" 2>/dev/null | grep "^hostname:" | awk '{print $2}')
      info+="  CT ${ctid}  ${ct_name:+(${ct_name})}  — libcuda1: ${lxc_ver}\n"
    done
  fi

  info+="\n$(translate 'After selecting a version, LXC containers will be updated first, then the host.')"
  info+="\n$(translate 'A reboot is required after the host update.')"

  dialog --backtitle "ProxMenux" \
    --title "$(translate 'NVIDIA Update — Current State')" \
    --yesno "$info" 20 80 \
    >/dev/tty 2>&1 || exit 0
}


# ============================================================
# Restart prompt
# ============================================================
restart_prompt() {
  echo
  msg_success "$(translate 'NVIDIA driver update completed.')"
  echo
  msg_info "$(translate 'Removing no longer required packages and purging old cached updates...')"
  apt-get -y autoremove >/dev/null 2>&1
  apt-get -y autoclean >/dev/null 2>&1
  msg_ok "$(translate 'Cleanup finished.')"
  echo -e "${TAB}${BL}Log: ${LOG_FILE}${CL}"
  echo

  if whiptail --title "$(translate 'Reboot Required')" \
    --yesno "$(translate 'The host driver update requires a reboot to take effect. Do you want to restart now?')" 10 70; then
    msg_success "$(translate 'Press Enter to continue...')"
    read -r
    msg_warn "$(translate 'Rebooting the system...')"
    reboot
  else
    msg_info2 "$(translate 'You can reboot later manually.')"
    msg_success "$(translate 'Press Enter to continue...')"
    read -r
  fi
}


# ============================================================
# Main
# ============================================================
main() {
  : >"$LOG_FILE"

  # ---- Phase 1: dialogs ----
  check_gpu_not_in_vm_passthrough
  detect_host_nvidia
  show_current_state_dialog
  select_target_version

  # Same version confirmation
  if [[ "$TARGET_VERSION" == "$HOST_NVIDIA_VERSION" ]]; then
    if ! dialog --backtitle "ProxMenux" \
      --title "$(translate 'Same Version')" \
      --yesno "\n$(translate 'Version') ${TARGET_VERSION} $(translate 'is already installed on the host.')\n\n$(translate 'Reinstall and force-update all LXC containers anyway?')" \
      10 70 >/dev/tty 2>&1; then
      exit 0
    fi
  fi

  # ---- Phase 2: processing ----
  show_proxmenux_logo
  msg_title "$(translate 'NVIDIA Driver Update')"

  # Download installer once — reused by both LXC containers and host
  local run_file
  run_file=$(_download_installer "$TARGET_VERSION")

  # Update LXC containers first (no reboot needed for userspace libs)
  if [[ ${#NVIDIA_CONTAINERS[@]} -gt 0 ]]; then
    msg_info2 "$(translate 'Updating LXC containers...')"
    for ctid in "${NVIDIA_CONTAINERS[@]}"; do
      update_lxc_nvidia "$ctid" "$TARGET_VERSION"
    done
  fi

  # Update host kernel module + drivers (reuses the already-downloaded installer)
  update_host_nvidia "$TARGET_VERSION"

  restart_prompt
}

main
