#!/bin/bash
# ProxMenux - Universal GPU/iGPU Passthrough to LXC
# ==================================================
# Author      : MacRimi
# License     : MIT
# Version     : 1.0
# Last Updated: 01/04/2026
# ==================================================

LOCAL_SCRIPTS="/usr/local/share/proxmenux/scripts"
BASE_DIR="/usr/local/share/proxmenux"
UTILS_FILE="$BASE_DIR/utils.sh"
LOG_FILE="/tmp/add_gpu_lxc.log"
NVIDIA_WORKDIR="/opt/nvidia"
INSTALL_ABORTED=false
NVIDIA_INSTALL_SUCCESS=false
NVIDIA_SMI_OUTPUT=""
screen_capture="/tmp/proxmenux_add_gpu_screen_capture_$$.txt"

if [[ -f "$UTILS_FILE" ]]; then
  source "$UTILS_FILE"
fi

load_language
initialize_cache


# ============================================================
# Helper: next available devN index in LXC config
# ============================================================
get_next_dev_index() {
  local config="$1"
  local idx=0
  while grep -q "^dev${idx}:" "$config" 2>/dev/null; do
    idx=$((idx + 1))
  done
  echo "$idx"
}


# ============================================================
# GPU detection on host
# ============================================================
detect_host_gpus() {
  HAS_INTEL=false
  HAS_AMD=false
  HAS_NVIDIA=false
  NVIDIA_READY=false
  NVIDIA_HOST_VERSION=""
  INTEL_NAME=""
  AMD_NAME=""
  NVIDIA_NAME=""

  local intel_line amd_line nvidia_line
  intel_line=$(lspci | grep -iE "VGA compatible|3D controller|Display controller" \
    | grep -i "Intel" | grep -iv "Ethernet\|Audio\|Network" | head -1)
  amd_line=$(lspci | grep -iE "VGA compatible|3D controller|Display controller" \
    | grep -iE "AMD|Advanced Micro|Radeon" | head -1)
  nvidia_line=$(lspci | grep -iE "VGA compatible|3D controller|Display controller" \
    | grep -i "NVIDIA" | head -1)

  if [[ -n "$intel_line" ]]; then
    HAS_INTEL=true
    INTEL_NAME=$(echo "$intel_line" | sed 's/^.*: //' | cut -c1-58)
  fi
  if [[ -n "$amd_line" ]]; then
    HAS_AMD=true
    AMD_NAME=$(echo "$amd_line" | sed 's/^.*: //' | cut -c1-58)
  fi
  if [[ -n "$nvidia_line" ]]; then
    HAS_NVIDIA=true
    NVIDIA_NAME=$(echo "$nvidia_line" | sed 's/^.*: //' | cut -c1-58)
    if lsmod | grep -q "^nvidia " && command -v nvidia-smi >/dev/null 2>&1; then
      NVIDIA_HOST_VERSION=$(nvidia-smi --query-gpu=driver_version \
        --format=csv,noheader 2>/dev/null | head -n1 | tr -d '[:space:]')
      [[ -n "$NVIDIA_HOST_VERSION" ]] && NVIDIA_READY=true
    fi
  fi
}


# ============================================================
# Container selection
# ============================================================
select_container() {
  local menu_items=()
  while IFS= read -r line; do
    [[ "$line" =~ ^VMID ]] && continue
    local ctid status name
    ctid=$(echo "$line" | awk '{print $1}')
    status=$(echo "$line" | awk '{print $2}')
    name=$(echo "$line" | awk '{print $3}')
    [[ -z "$ctid" ]] && continue
    menu_items+=("$ctid" "${name:-CT-${ctid}} (${status})")
  done < <(pct list 2>/dev/null)

  if [[ ${#menu_items[@]} -eq 0 ]]; then
    dialog --backtitle "ProxMenux" \
      --title "$(translate 'Add GPU to LXC')" \
      --msgbox "\n$(translate 'No LXC containers found on this system.')" 8 60
    exit 0
  fi

  CONTAINER_ID=$(dialog --backtitle "ProxMenux" \
    --title "$(translate 'Add GPU to LXC')" \
    --menu "\n$(translate 'Select the LXC container:')" 20 72 12 \
    "${menu_items[@]}" \
    2>&1 >/dev/tty) || exit 0
}


# ============================================================
# GPU checklist selection
# ============================================================
select_gpus() {
  local gpu_items=()
  $HAS_INTEL  && gpu_items+=("intel"  "${INTEL_NAME:-Intel iGPU}"  "off")
  $HAS_AMD    && gpu_items+=("amd"    "${AMD_NAME:-AMD GPU}"        "off")
  $HAS_NVIDIA && gpu_items+=("nvidia" "${NVIDIA_NAME:-NVIDIA GPU}"  "off")

  local count=$(( ${#gpu_items[@]} / 3 ))

  if [[ $count -eq 0 ]]; then
    dialog --backtitle "ProxMenux" \
      --title "$(translate 'Add GPU to LXC')" \
      --msgbox "\n$(translate 'No compatible GPUs detected on this host.')" 8 60
    exit 0
  fi

  # Only one GPU — auto-select without menu
  if [[ $count -eq 1 ]]; then
    SELECTED_GPUS=("${gpu_items[0]}")
    return
  fi

  # Multiple GPUs — checklist with loop on empty selection
  while true; do
    local raw_selection
    raw_selection=$(dialog --backtitle "ProxMenux" \
      --title "$(translate 'Add GPU to LXC')" \
      --checklist "\n$(translate 'Select the GPU(s) to add to LXC') ${CONTAINER_ID}:" \
      18 80 10 \
      "${gpu_items[@]}" \
      2>&1 >/dev/tty) || exit 0

    local selection
    selection=$(echo "$raw_selection" | tr -d '"')

    if [[ -z "$selection" ]]; then
      dialog --backtitle "ProxMenux" \
        --title "$(translate 'Add GPU to LXC')" \
        --msgbox "\n$(translate 'No GPU selected. Please select at least one GPU to continue.')" 8 68
      continue
    fi

    read -ra SELECTED_GPUS <<< "$selection"
    break
  done
}


# ============================================================
# NVIDIA host driver readiness check
# ============================================================
check_nvidia_ready() {
  if ! $NVIDIA_READY; then
    dialog --colors --backtitle "ProxMenux" \
      --title "$(translate 'NVIDIA Drivers Not Found')" \
      --msgbox "\n$(translate 'NVIDIA drivers are not installed or not loaded on this host.')\n\n$(translate 'Please install the NVIDIA drivers first using the option:')\n\n  \Zb$(translate 'Install NVIDIA Drivers on Host')\Zn\n\n$(translate 'available in this same GPU and TPU menu.')" \
      14 72
    exit 0
  fi
}


# ============================================================
# LXC config: DRI device passthrough (Intel / AMD shared)
# ============================================================
_configure_dri_devices() {
  local cfg="$1"
  local video_gid render_gid idx gid

  video_gid=$(getent group video  2>/dev/null | cut -d: -f3); [[ -z "$video_gid"  ]] && video_gid="44"
  render_gid=$(getent group render 2>/dev/null | cut -d: -f3); [[ -z "$render_gid" ]] && render_gid="104"

  # Remove any pre-existing lxc.mount.entry for /dev/dri — it conflicts with devN: entries
  sed -i '/lxc\.mount\.entry:.*dev\/dri.*bind/d' "$cfg" 2>/dev/null || true

  for dri_dev in /dev/dri/card0 /dev/dri/card1 /dev/dri/renderD128 /dev/dri/renderD129; do
    [[ ! -c "$dri_dev" ]] && continue
    if ! grep -qE "dev[0-9]+:.*${dri_dev}[^0-9/]" "$cfg" 2>/dev/null; then
      idx=$(get_next_dev_index "$cfg")
      case "$dri_dev" in
        /dev/dri/renderD*) gid="$render_gid" ;;
        *)                  gid="$video_gid"  ;;
      esac
      echo "dev${idx}: ${dri_dev},gid=${gid}" >> "$cfg"
    fi
  done
}

_configure_intel() {
  local cfg="$1"
  _configure_dri_devices "$cfg"
}

_configure_amd() {
  local cfg="$1"
  local render_gid idx

  _configure_dri_devices "$cfg"

  # /dev/kfd for ROCm / compute workloads
  if [[ -c "/dev/kfd" ]]; then
    render_gid=$(getent group render 2>/dev/null | cut -d: -f3)
    [[ -z "$render_gid" ]] && render_gid="104"
    if ! grep -q "dev.*/dev/kfd" "$cfg" 2>/dev/null; then
      idx=$(get_next_dev_index "$cfg")
      echo "dev${idx}: /dev/kfd,gid=${render_gid}" >> "$cfg"
    fi
  fi
}

_configure_nvidia() {
  local cfg="$1"
  local idx dev video_gid

  video_gid=$(getent group video 2>/dev/null | cut -d: -f3)
  [[ -z "$video_gid" ]] && video_gid="44"

  local -a nv_devs=()
  for dev in /dev/nvidia[0-9]* /dev/nvidiactl /dev/nvidia-uvm /dev/nvidia-uvm-tools /dev/nvidia-modeset; do
    [[ -c "$dev" ]] && nv_devs+=("$dev")
  done
  if [[ -d /dev/nvidia-caps ]]; then
    for dev in /dev/nvidia-caps/nvidia-cap[0-9]*; do
      [[ -c "$dev" ]] && nv_devs+=("$dev")
    done
  fi

  for dev in "${nv_devs[@]}"; do
    if ! grep -q "dev.*${dev}" "$cfg" 2>/dev/null; then
      idx=$(get_next_dev_index "$cfg")
      echo "dev${idx}: ${dev},gid=${video_gid}" >> "$cfg"
    fi
  done
}

configure_passthrough() {
  local ctid="$1"
  local cfg="/etc/pve/lxc/${ctid}.conf"
  CT_WAS_RUNNING=false

  if pct status "$ctid" 2>/dev/null | grep -q "running"; then
    CT_WAS_RUNNING=true
    msg_info "$(translate 'Stopping container') ${ctid}..."
    pct stop "$ctid" >>"$LOG_FILE" 2>&1
    msg_ok "$(translate 'Container stopped.')" | tee -a "$screen_capture"
  fi

  for gpu_type in "${SELECTED_GPUS[@]}"; do
    case "$gpu_type" in
      intel)
        msg_info "$(translate 'Configuring Intel iGPU passthrough...')"
        _configure_intel "$cfg"
        msg_ok "$(translate 'Intel iGPU passthrough configured.')" | tee -a "$screen_capture"
        ;;
      amd)
        msg_info "$(translate 'Configuring AMD GPU passthrough...')"
        _configure_amd "$cfg"
        msg_ok "$(translate 'AMD GPU passthrough configured.')" | tee -a "$screen_capture"
        ;;
      nvidia)
        msg_info "$(translate 'Configuring NVIDIA GPU passthrough...')"
        _configure_nvidia "$cfg"
        msg_ok "$(translate 'NVIDIA GPU passthrough configured.')" | tee -a "$screen_capture"
        ;;
    esac
  done
}


# ============================================================
# Driver / userspace library installation inside container
# ============================================================
# ============================================================
# Detect distro inside container (POSIX sh — works on Alpine too)
# ============================================================
_detect_container_distro() {
  local distro
  distro=$(pct exec "$1" -- grep "^ID=" /etc/os-release 2>/dev/null \
    | cut -d= -f2 | tr -d '[:space:]"')
  echo "${distro:-unknown}"
}

# ============================================================
# GID sync helper — POSIX sh, works on all distros
# ============================================================
_sync_gids_in_container() {
  local ctid="$1"
  local hvid hrid
  hvid=$(getent group video  2>/dev/null | cut -d: -f3); [[ -z "$hvid" ]] && hvid="44"
  hrid=$(getent group render 2>/dev/null | cut -d: -f3); [[ -z "$hrid" ]] && hrid="104"

  pct exec "$ctid" -- sh -c "
    sed -i 's/^video:x:[0-9]*:/video:x:${hvid}:/'  /etc/group 2>/dev/null || true
    sed -i 's/^render:x:[0-9]*:/render:x:${hrid}:/' /etc/group 2>/dev/null || true
    grep -q '^video:'  /etc/group 2>/dev/null || echo 'video:x:${hvid}:'  >> /etc/group
    grep -q '^render:' /etc/group 2>/dev/null || echo 'render:x:${hrid}:' >> /etc/group
  " >>"$LOG_FILE" 2>&1 || true
}

# ============================================================
_install_intel_drivers() {
  local ctid="$1"
  local distro="$2"

  _sync_gids_in_container "$ctid"

  case "$distro" in
    alpine)
      pct exec "$ctid" -- sh -c \
        "apk update && apk add --no-cache mesa-va-gallium libva-utils" \
        2>&1 | tee -a "$LOG_FILE"
      ;;
    arch|manjaro|endeavouros)
      pct exec "$ctid" -- bash -c \
        "pacman -Sy --noconfirm intel-media-driver libva-utils mesa" \
        2>&1 | tee -a "$LOG_FILE"
      ;;
    *)
      pct exec "$ctid" -- bash -s >>"$LOG_FILE" 2>&1 << EOF
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y va-driver-all vainfo libva2 intel-media-va-driver-non-free i965-va-driver 2>/dev/null || \
apt-get install -y va-driver-all vainfo libva2 2>/dev/null || true
EOF
      ;;
  esac
}

_install_amd_drivers() {
  local ctid="$1"
  local distro="$2"

  _sync_gids_in_container "$ctid"

  case "$distro" in
    alpine)
      pct exec "$ctid" -- sh -c \
        "apk update && apk add --no-cache mesa-va-gallium mesa-dri-gallium libva-utils" \
        2>&1 | tee -a "$LOG_FILE"
      ;;
    arch|manjaro|endeavouros)
      pct exec "$ctid" -- bash -c \
        "pacman -Sy --noconfirm mesa libva-mesa-driver libva-utils" \
        2>&1 | tee -a "$LOG_FILE"
      ;;
    *)
      pct exec "$ctid" -- bash -s >>"$LOG_FILE" 2>&1 << EOF
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y mesa-va-drivers libdrm-amdgpu1 vainfo libva2 2>/dev/null || true
EOF
      ;;
  esac
}

# ============================================================
# Memory management helpers (for NVIDIA .run installer)
# ============================================================
CT_ORIG_MEM=""
NVIDIA_INSTALL_MIN_MB=2048

_ensure_container_memory() {
  local ctid="$1"
  local cur_mem
  cur_mem=$(pct config "$ctid" 2>/dev/null | awk '/^memory:/{print $2}')
  [[ -z "$cur_mem" ]] && cur_mem=512

  if [[ "$cur_mem" -lt "$NVIDIA_INSTALL_MIN_MB" ]]; then
    if whiptail --title "$(translate 'Low Container Memory')" --yesno \
      "$(translate 'Container') ${ctid} $(translate 'has') ${cur_mem}MB RAM.\n\n$(translate 'The NVIDIA installer needs at least') ${NVIDIA_INSTALL_MIN_MB}MB $(translate 'to run without being killed by the OOM killer.')\n\n$(translate 'Increase container RAM temporarily to') ${NVIDIA_INSTALL_MIN_MB}MB?" \
      13 72; then
      CT_ORIG_MEM="$cur_mem"
      pct set "$ctid" -memory "$NVIDIA_INSTALL_MIN_MB" >>"$LOG_FILE" 2>&1 || true
    else
      INSTALL_ABORTED=true
      msg_warn "$(translate 'Insufficient memory. NVIDIA install aborted.')"
      return 1
    fi
  fi
  return 0
}

_restore_container_memory() {
  local ctid="$1"
  if [[ -n "$CT_ORIG_MEM" ]]; then
    msg_info "$(translate 'Restoring container memory to') ${CT_ORIG_MEM}MB..."
    pct set "$ctid" -memory "$CT_ORIG_MEM" >>"$LOG_FILE" 2>&1 || true
    msg_ok "$(translate 'Memory restored.')"
    CT_ORIG_MEM=""
  fi
}

# ============================================================
_install_nvidia_drivers() {
  local ctid="$1"
  local version="$NVIDIA_HOST_VERSION"
  local distro="$2"

  case "$distro" in
    alpine)
      # Alpine uses apk — musl-compatible nvidia-utils from Alpine repos
      msg_info2 "$(translate 'Installing NVIDIA utils (Alpine)...')"
      pct exec "$ctid" -- sh -c \
        "apk update && apk add --no-cache nvidia-utils" \
        2>&1 | tee -a "$LOG_FILE"
      ;;

    arch|manjaro|endeavouros)
      # Arch uses pacman — nvidia-utils provides nvidia-smi
      msg_info2 "$(translate 'Installing NVIDIA utils (Arch)...')"
      pct exec "$ctid" -- bash -c \
        "pacman -Sy --noconfirm nvidia-utils" \
        2>&1 | tee -a "$LOG_FILE"
      ;;

    *)
      # Debian / Ubuntu / generic glibc: use the .run binary
      local run_file="${NVIDIA_WORKDIR}/NVIDIA-Linux-x86_64-${version}.run"

      if [[ ! -f "$run_file" ]]; then
        msg_warn "$(translate 'NVIDIA installer not found at') ${run_file}."
        msg_warn "$(translate 'Run \"Install NVIDIA Drivers on Host\" first so the installer is cached.')"
        return 1
      fi

      # Memory check — nvidia-installer needs ~2GB during install
      _ensure_container_memory "$ctid" || return 1

      # Disk space check — NVIDIA libs need ~1.5 GB free in the container
      local free_mb
      free_mb=$(pct exec "$ctid" -- df -m / 2>/dev/null | awk 'NR==2{print $4}' || echo 0)
      if [[ "$free_mb" -lt 1500 ]]; then
        _restore_container_memory "$ctid"
        dialog --backtitle "ProxMenux" \
          --title "$(translate 'Insufficient Disk Space')" \
          --msgbox "\n$(translate 'Container') ${ctid} $(translate 'has only') ${free_mb}MB $(translate 'of free disk space.')\n\n$(translate 'NVIDIA libs require approximately 1.5GB of free space.')\n\n$(translate 'Please expand the container disk and run this option again.')" \
          12 72
        INSTALL_ABORTED=true
        return 1
      fi

      # Extract .run on the host — avoids decompression OOM inside container
      # Use msg_info2 (no spinner) so tee output is not mixed with spinner animation
      local extract_dir="${NVIDIA_WORKDIR}/extracted_${version}"
      local archive="/tmp/nvidia_lxc_${version}.tar.gz"

      msg_info2 "$(translate 'Extracting NVIDIA installer on host...')"
      rm -rf "$extract_dir"
      sh "$run_file" --extract-only --target "$extract_dir" 2>&1 | tee -a "$LOG_FILE"
      if [[ ${PIPESTATUS[0]} -ne 0 ]]; then
        msg_warn "$(translate 'Extraction failed. Check log:') ${LOG_FILE}"
        _restore_container_memory "$ctid"
        return 1
      fi
      msg_ok "$(translate 'NVIDIA installer extracted.')" | tee -a "$screen_capture"

      msg_info2 "$(translate 'Packing installer archive...')"
      tar --checkpoint=5000 --checkpoint-action=dot \
          -czf "$archive" -C "$extract_dir" . 2>&1 | tee -a "$LOG_FILE"
      echo ""
      local archive_size
      archive_size=$(du -sh "$archive" 2>/dev/null | cut -f1)
      msg_ok "$(translate 'Archive ready') (${archive_size})." | tee -a "$screen_capture"

      msg_info "$(translate 'Copying installer to container') ${ctid}..."
      if ! pct push "$ctid" "$archive" /tmp/nvidia_lxc.tar.gz >>"$LOG_FILE" 2>&1; then
        msg_warn "$(translate 'pct push failed. Check log:') ${LOG_FILE}"
        rm -f "$archive"
        _restore_container_memory "$ctid"
        return 1
      fi
      rm -f "$archive"
      msg_ok "$(translate 'Installer copied to container.')" | tee -a "$screen_capture"

      msg_info2 "$(translate 'Installing NVIDIA drivers in container. This may take several minutes...')"
      echo "" >>"$LOG_FILE"
      pct exec "$ctid" -- bash -c "
        mkdir -p /tmp/nvidia_lxc_install
        tar -xzf /tmp/nvidia_lxc.tar.gz -C /tmp/nvidia_lxc_install 2>&1
        /tmp/nvidia_lxc_install/nvidia-installer \
          --no-kernel-modules \
          --no-questions \
          --ui=none \
          --no-nouveau-check \
          --no-dkms \
          --no-install-compat32-libs
        EXIT=\$?
        rm -rf /tmp/nvidia_lxc_install /tmp/nvidia_lxc.tar.gz
        exit \$EXIT
      " 2>&1 | tee -a "$LOG_FILE"
      local rc=${PIPESTATUS[0]}

      rm -rf "$extract_dir"
      _restore_container_memory "$ctid"

      if [[ $rc -ne 0 ]]; then
        msg_warn "$(translate 'NVIDIA installer returned error') ${rc}. $(translate 'Check log:') ${LOG_FILE}"
        return 1
      fi
      ;;
  esac

  if pct exec "$ctid" -- sh -c "which nvidia-smi" >/dev/null 2>&1; then
    return 0
  else
    msg_warn "$(translate 'nvidia-smi not found after install. Check log:') ${LOG_FILE}"
    return 1
  fi
}


start_container_and_wait() {
  local ctid="$1"
  msg_info "$(translate 'Starting container') ${ctid}..."
  pct start "$ctid" >>"$LOG_FILE" 2>&1 || true

  local ready=false
  for _ in {1..15}; do
    sleep 2
    if pct exec "$ctid" -- true >/dev/null 2>&1; then
      ready=true
      break
    fi
  done

  if ! $ready; then
    msg_warn "$(translate 'Container did not become ready in time. Skipping driver installation.')"
    return 1
  fi
  msg_ok "$(translate 'Container started.')" | tee -a "$screen_capture"
  return 0
}

install_drivers() {
  local ctid="$1"

  # Detect distro once — passed to each install function
  msg_info "$(translate 'Detecting container OS...')"
  local ct_distro
  ct_distro=$(_detect_container_distro "$ctid")
  msg_ok "$(translate 'Container OS:') ${ct_distro}" | tee -a "$screen_capture"

  for gpu_type in "${SELECTED_GPUS[@]}"; do
    case "$gpu_type" in
      intel)
        msg_info "$(translate 'Installing Intel VA-API drivers in container...')"
        _install_intel_drivers "$ctid" "$ct_distro"
        msg_ok "$(translate 'Intel VA-API drivers installed.')" | tee -a "$screen_capture"
        ;;
      amd)
        msg_info "$(translate 'Installing AMD mesa drivers in container...')"
        _install_amd_drivers "$ctid" "$ct_distro"
        msg_ok "$(translate 'AMD mesa drivers installed.')" | tee -a "$screen_capture"
        ;;
      nvidia)
        # No outer msg_info here — _install_nvidia_drivers manages its own messages
        # to avoid a dangling spinner before the whiptail memory dialog
        if _install_nvidia_drivers "$ctid" "$ct_distro"; then
          msg_ok "$(translate 'NVIDIA userspace libraries installed.')" | tee -a "$screen_capture"
          NVIDIA_INSTALL_SUCCESS=true
        elif [[ "$INSTALL_ABORTED" == "false" ]]; then
          msg_warn "$(translate 'NVIDIA install incomplete. Check log:') ${LOG_FILE}"
        fi
        ;;
    esac
  done
}


# ============================================================
# Main
# ============================================================
main() {
  : >"$LOG_FILE"
  : >"$screen_capture"

  # ---- Phase 1: all dialogs (no terminal output yet) ----
  detect_host_gpus
  select_container
  select_gpus

  # NVIDIA check runs only if NVIDIA was selected
  for gpu_type in "${SELECTED_GPUS[@]}"; do
    [[ "$gpu_type" == "nvidia" ]] && check_nvidia_ready
  done

  # ---- Phase 2: processing ----
  show_proxmenux_logo
  msg_title "$(translate 'Add GPU to LXC')"

  configure_passthrough "$CONTAINER_ID"

  if start_container_and_wait "$CONTAINER_ID"; then
    install_drivers "$CONTAINER_ID"

    # Capture nvidia-smi output while container is still running
    if $NVIDIA_INSTALL_SUCCESS; then
      NVIDIA_SMI_OUTPUT=$(pct exec "$CONTAINER_ID" -- nvidia-smi 2>/dev/null || true)
    fi

    if [[ "$CT_WAS_RUNNING" == "false" ]]; then
      pct stop "$CONTAINER_ID" >>"$LOG_FILE" 2>&1 || true
    fi
  fi

  if [[ "$INSTALL_ABORTED" == "true" ]]; then
    rm -f "$screen_capture"
    exit 0
  fi

  show_proxmenux_logo
  msg_title "$(translate 'Add GPU to LXC')"
  cat "$screen_capture"
  echo -e "${TAB}${GN}📄 $(translate 'Log')${CL}: ${BL}${LOG_FILE}${CL}"
  if [[ -n "$NVIDIA_SMI_OUTPUT" ]]; then
    msg_info2 "$(translate 'NVIDIA driver verification in container:')"
    echo "$NVIDIA_SMI_OUTPUT"
  fi
  msg_success "$(translate 'GPU passthrough configured for LXC') ${CONTAINER_ID}."
  msg_success "$(translate 'Completed. Press Enter to return to menu...')"
  read -r
  rm -f "$screen_capture"
}

main
