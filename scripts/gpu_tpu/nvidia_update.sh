#!/bin/bash
# ProxMenux - NVIDIA Driver Updater (Host + LXC)
# ================================================
# Author      : MacRimi
# License     : MIT
# Version     : 2.0
# Last Updated: 17/04/2026
# ================================================
#
# Aligned with nvidia_installer.sh (host install flow & kernel filter)
# and add_gpu_lxc.sh (LXC userspace install flow with distro + memory
# awareness and visible progress output).

SCRIPT_TITLE="NVIDIA Driver Update (Host + LXC)"

LOCAL_SCRIPTS="/usr/local/share/proxmenux/scripts"
BASE_DIR="/usr/local/share/proxmenux"
UTILS_FILE="$BASE_DIR/utils.sh"
COMPONENTS_STATUS_FILE="$BASE_DIR/components_status.json"
LOG_FILE="/tmp/nvidia_update.log"
screen_capture="/tmp/proxmenux_nvidia_update_screen_capture_$$.txt"

NVIDIA_BASE_URL="https://download.nvidia.com/XFree86/Linux-x86_64"
NVIDIA_WORKDIR="/opt/nvidia"

export BASE_DIR
export COMPONENTS_STATUS_FILE

if [[ -f "$UTILS_FILE" ]]; then
  source "$UTILS_FILE"
fi

if [[ ! -f "$COMPONENTS_STATUS_FILE" ]]; then
  echo "{}" > "$COMPONENTS_STATUS_FILE"
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
# Kernel compatibility (mirror of nvidia_installer.sh)
# ============================================================
get_kernel_compatibility_info() {
  local kernel_version
  kernel_version=$(uname -r)

  KERNEL_MAJOR=$(echo "$kernel_version" | cut -d. -f1)
  KERNEL_MINOR=$(echo "$kernel_version" | cut -d. -f2)

  # Minimum driver version matrix (keyed to kernel series) — based on
  # https://docs.nvidia.com/datacenter/tesla/drivers/index.html
  if [[ "$KERNEL_MAJOR" -ge 6 ]] && [[ "$KERNEL_MINOR" -ge 17 ]]; then
    MIN_DRIVER_VERSION="580.82.07"   # PVE 9.x
  elif [[ "$KERNEL_MAJOR" -ge 6 ]] && [[ "$KERNEL_MINOR" -ge 8 ]]; then
    MIN_DRIVER_VERSION="550"          # PVE 8.2+
  elif [[ "$KERNEL_MAJOR" -ge 6 ]]; then
    MIN_DRIVER_VERSION="535"          # PVE 8.x initial
  elif [[ "$KERNEL_MAJOR" -eq 5 ]] && [[ "$KERNEL_MINOR" -ge 15 ]]; then
    MIN_DRIVER_VERSION="470"          # PVE 7.x / 8.x legacy
  else
    MIN_DRIVER_VERSION="450"          # Old kernels
  fi
}

is_version_compatible() {
  local version="$1"
  local ver_major ver_minor ver_patch

  ver_major=$(echo "$version" | cut -d. -f1)
  ver_minor=$(echo "$version" | cut -d. -f2)
  ver_patch=$(echo "$version" | cut -d. -f3)

  if [[ "$MIN_DRIVER_VERSION" == "580.82.07" ]]; then
    if [[ ${ver_major} -gt 580 ]]; then
      return 0
    elif [[ ${ver_major} -eq 580 ]]; then
      if [[ $((10#${ver_minor})) -gt 82 ]]; then
        return 0
      elif [[ $((10#${ver_minor})) -eq 82 ]]; then
        if [[ $((10#${ver_patch:-0})) -ge 7 ]]; then
          return 0
        fi
      fi
    fi
    return 1
  fi

  if [[ ${ver_major} -ge ${MIN_DRIVER_VERSION} ]]; then
    return 0
  else
    return 1
  fi
}

version_le() {
  local v1="$1"
  local v2="$2"

  IFS='.' read -r a1 b1 c1 <<<"$v1"
  IFS='.' read -r a2 b2 c2 <<<"$v2"

  a1=${a1:-0}; b1=${b1:-0}; c1=${c1:-0}
  a2=${a2:-0}; b2=${b2:-0}; c2=${c2:-0}

  a1=$((10#$a1)); b1=$((10#$b1)); c1=$((10#$c1))
  a2=$((10#$a2)); b2=$((10#$b2)); c2=$((10#$c2))

  if (( a1 < a2 )); then
    return 0
  elif (( a1 > a2 )); then
    return 1
  fi

  if (( b1 < b2 )); then
    return 0
  elif (( b1 > b2 )); then
    return 1
  fi

  if (( c1 <= c2 )); then
    return 0
  else
    return 1
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
# Version selection menu (filtered by kernel compatibility)
# ============================================================
select_target_version() {
  local latest versions_list
  latest=$(get_latest_version 2>/dev/null)
  versions_list=$(list_available_versions 2>/dev/null)

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

  # Kernel compatibility filter
  local current_list="$versions_list"
  if [[ -n "$MIN_DRIVER_VERSION" ]]; then
    local filtered_list=""
    while IFS= read -r ver; do
      [[ -z "$ver" ]] && continue
      if is_version_compatible "$ver"; then
        filtered_list+="$ver"$'\n'
      fi
    done <<< "$current_list"
    current_list="$filtered_list"
  fi

  # Cap at latest (avoid showing beta branches newer than published latest)
  if [[ -n "$latest" ]]; then
    local filtered_max_list=""
    while IFS= read -r ver; do
      [[ -z "$ver" ]] && continue
      if version_le "$ver" "$latest"; then
        filtered_max_list+="$ver"$'\n'
      fi
    done <<< "$current_list"
    current_list="$filtered_max_list"
  fi

  local menu_text
  menu_text="\n$(translate 'Current host version:') ${HOST_NVIDIA_VERSION}\n"
  menu_text+="$(translate 'Kernel:') $(uname -r)\n\n"
  menu_text+="$(translate 'Select the target version to install on host and all affected LXCs:')\n"
  menu_text+="$(translate 'Versions shown are compatible with your running kernel.')"

  local choices=()
  choices+=("latest" "$(translate 'Latest available') (${latest:-?})")
  choices+=("" "")

  if [[ -n "$current_list" ]]; then
    while IFS= read -r ver; do
      ver=$(echo "$ver" | tr -d '[:space:]')
      [[ -z "$ver" ]] && continue
      choices+=("$ver" "$ver")
    done <<< "$current_list"
  else
    choices+=("" "$(translate 'No compatible versions found for your kernel')")
  fi

  TARGET_VERSION=$(dialog --backtitle "ProxMenux" \
    --title "$(translate 'NVIDIA Driver Version')" \
    --menu "$menu_text" 28 80 16 \
    "${choices[@]}" \
    2>&1 >/dev/tty) || exit 0

  [[ -z "$TARGET_VERSION" ]] && exit 0

  if [[ "$TARGET_VERSION" == "latest" ]]; then
    TARGET_VERSION="$latest"
  fi
  TARGET_VERSION=$(echo "$TARGET_VERSION" | tr -d '[:space:]')
}


# ============================================================
# Overview dialog (current state)
# ============================================================
show_current_state_dialog() {
  find_nvidia_containers

  local info
  info="\n$(translate 'Host NVIDIA driver:') ${HOST_NVIDIA_VERSION}\n"
  info+="$(translate 'Kernel:') $(uname -r)\n\n"

  if [[ ${#NVIDIA_CONTAINERS[@]} -eq 0 ]]; then
    info+="$(translate 'No LXC containers with NVIDIA passthrough found.')\n"
  else
    info+="$(translate 'LXC containers with NVIDIA passthrough:')\n\n"
    for ctid in "${NVIDIA_CONTAINERS[@]}"; do
      local lxc_ver ct_name
      lxc_ver=$(get_lxc_nvidia_version "$ctid")
      ct_name=$(pct config "$ctid" 2>/dev/null | grep "^hostname:" | awk '{print $2}')
      info+="  CT ${ctid}  ${ct_name:+(${ct_name})}  — $(translate 'driver:') ${lxc_ver}\n"
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
# System preparation (mirror of nvidia_installer.sh)
# ============================================================
ensure_repos_and_headers() {
  msg_info "$(translate 'Checking kernel headers and build tools...')"

  local kver
  kver=$(uname -r)

  apt-get update -qq >>"$LOG_FILE" 2>&1

  if ! dpkg -s "pve-headers-$kver" >/dev/null 2>&1 && \
     ! dpkg -s "proxmox-headers-$kver" >/dev/null 2>&1; then
    apt-get install -y "pve-headers-$kver" "proxmox-headers-$kver" build-essential dkms >>"$LOG_FILE" 2>&1 || true
  else
    apt-get install -y build-essential dkms >>"$LOG_FILE" 2>&1 || true
  fi

  msg_ok "$(translate 'Kernel headers and build tools verified.')" | tee -a "$screen_capture"
}


# ============================================================
# Host NVIDIA cleanup (before update)
# ============================================================
_stop_nvidia_services() {
  local services=(
    "nvidia-persistenced.service"
    "nvidia-persistenced"
    "nvidia-powerd.service"
  )

  local services_detected=0
  for service in "${services[@]}"; do
    if systemctl is-active --quiet "$service" 2>/dev/null || \
       systemctl is-enabled --quiet "$service" 2>/dev/null; then
      services_detected=1
      break
    fi
  done

  if [ "$services_detected" -eq 1 ]; then
    msg_info "$(translate 'Stopping and disabling NVIDIA services...')"
    for service in "${services[@]}"; do
      systemctl is-active --quiet "$service" 2>/dev/null && systemctl stop "$service" >/dev/null 2>&1 || true
      systemctl is-enabled --quiet "$service" 2>/dev/null && systemctl disable "$service" >/dev/null 2>&1 || true
    done
    sleep 2
    msg_ok "$(translate 'NVIDIA services stopped and disabled.')" | tee -a "$screen_capture"
  fi
}

_unload_nvidia_modules() {
  msg_info "$(translate 'Unloading NVIDIA kernel modules...')"

  for mod in nvidia_uvm nvidia_drm nvidia_modeset nvidia; do
    modprobe -r "$mod" >/dev/null 2>&1 || true
  done

  if lsmod | grep -qi '\bnvidia'; then
    for mod in nvidia_uvm nvidia_drm nvidia_modeset nvidia; do
      modprobe -r --force "$mod" >/dev/null 2>&1 || true
    done
  fi

  if lsmod | grep -qi '\bnvidia'; then
    msg_warn "$(translate 'Some NVIDIA modules could not be unloaded. Update may fail. Ensure no processes are using the GPU.')"
  else
    msg_ok "$(translate 'NVIDIA kernel modules unloaded successfully.')" | tee -a "$screen_capture"
  fi
}

cleanup_nvidia_dkms() {
  local versions
  versions=$(dkms status 2>/dev/null | awk -F, '/nvidia/ {gsub(/ /,"",$2); print $2}' || true)
  [[ -z "$versions" ]] && return 0

  msg_info "$(translate 'Removing NVIDIA DKMS entries...')"
  while IFS= read -r ver; do
    [[ -z "$ver" ]] && continue
    dkms remove -m nvidia -v "$ver" --all >/dev/null 2>&1 || true
  done <<< "$versions"
  msg_ok "$(translate 'NVIDIA DKMS entries removed.')" | tee -a "$screen_capture"
}

_purge_nvidia_host() {
  msg_info2 "$(translate 'Preparing host for driver update...')"

  _stop_nvidia_services
  _unload_nvidia_modules

  if command -v nvidia-uninstall >/dev/null 2>&1; then
    msg_info "$(translate 'Running nvidia-uninstall...')"
    nvidia-uninstall --silent >>"$LOG_FILE" 2>&1 || true
    msg_ok "$(translate 'nvidia-uninstall completed.')" | tee -a "$screen_capture"
  fi

  cleanup_nvidia_dkms

  msg_info "$(translate 'Purging NVIDIA packages...')"
  apt-get -y purge 'nvidia-*' 'libnvidia-*' 'cuda-*' 'libcudnn*' >>"$LOG_FILE" 2>&1 || true
  apt-get -y autoremove --purge >>"$LOG_FILE" 2>&1 || true
  msg_ok "$(translate 'NVIDIA packages purged.')" | tee -a "$screen_capture"

  # Remove stale udev / modprobe files so the new installer can write fresh ones
  rm -f /etc/udev/rules.d/70-nvidia.rules
  rm -f /etc/modprobe.d/nvidia*.conf /usr/lib/modprobe.d/nvidia*.conf
}


# ============================================================
# Download installer (with integrity check — mirror of installer)
# ============================================================
ensure_workdir() {
  mkdir -p "$NVIDIA_WORKDIR"
}

verify_version_exists() {
  local version="$1"
  local url="${NVIDIA_BASE_URL}/${version}/"
  if curl -fsSL --head "$url" >/dev/null 2>&1; then
    return 0
  else
    return 1
  fi
}

download_nvidia_installer() {
  ensure_workdir
  local version="$1"
  version=$(echo "$version" | tr -d '[:space:]' | tr -d '\n' | tr -d '\r')

  if [[ ! "$version" =~ ^[0-9]+\.[0-9]+(\.[0-9]+)?$ ]]; then
    msg_error "Invalid version format: $version" >&2
    echo "ERROR: Invalid version format: '$version'" >> "$LOG_FILE"
    return 1
  fi

  local run_file="$NVIDIA_WORKDIR/NVIDIA-Linux-x86_64-${version}.run"

  if [[ -f "$run_file" ]]; then
    local existing_size file_type
    existing_size=$(stat -c%s "$run_file" 2>/dev/null || echo "0")
    file_type=$(file "$run_file" 2>/dev/null || echo "unknown")

    if [[ $existing_size -gt 40000000 ]] && echo "$file_type" | grep -q "executable"; then
      if sh "$run_file" --check 2>&1 | tee -a "$LOG_FILE" | grep -q "OK"; then
        msg_ok "$(translate 'Installer already downloaded and verified.')" | tee -a "$screen_capture" >&2
        printf '%s\n' "$run_file"
        return 0
      else
        msg_warn "$(translate 'Existing file failed verification, re-downloading...')" >&2
        rm -f "$run_file"
      fi
    else
      msg_warn "$(translate 'Removing invalid existing file...')" >&2
      rm -f "$run_file"
    fi
  fi

  if ! verify_version_exists "$version"; then
    msg_error "Version $version does not exist on NVIDIA servers" >&2
    return 1
  fi

  local urls=(
    "${NVIDIA_BASE_URL}/${version}/NVIDIA-Linux-x86_64-${version}.run"
    "${NVIDIA_BASE_URL}/${version}/NVIDIA-Linux-x86_64-${version}-no-compat32.run"
  )

  # Header line on the real terminal so it stays visible regardless of caller redirects.
  printf '\n  %s NVIDIA-Linux-x86_64-%s.run\n' \
    "$(translate 'Downloading')" "$version" >/dev/tty

  local success=false
  for url in "${urls[@]}"; do
    rm -f "$run_file"
    echo "Attempting download from: $url" >> "$LOG_FILE"

    # wget --show-progress writes its progress bar to stderr. We route it to
    # /dev/tty explicitly so the user always sees it (same UX as ISO downloads
    # in vm_creator.sh). The file contents still go to $run_file.
    if wget --no-verbose --show-progress \
            --connect-timeout=30 --timeout=600 --tries=1 \
            -O "$run_file" "$url" 2>/dev/tty; then
      [[ ! -f "$run_file" ]] && { echo "ERROR: File not created" >> "$LOG_FILE"; continue; }
      local file_size file_type
      file_size=$(stat -c%s "$run_file" 2>/dev/null || echo "0")
      file_type=$(file "$run_file" 2>/dev/null)
      echo "Downloaded file size: $file_size bytes, type: $file_type" >> "$LOG_FILE"
      if [[ $file_size -gt 40000000 ]] && echo "$file_type" | grep -q "executable"; then
        success=true
        break
      fi
      rm -f "$run_file"
    else
      echo "ERROR: wget failed for $url (exit: $?)" >> "$LOG_FILE"
      rm -f "$run_file"
    fi
  done

  if ! $success; then
    msg_error "$(translate 'Download failed. Check') ${LOG_FILE}" >&2
    return 1
  fi

  chmod +x "$run_file"
  msg_ok "$(translate 'Download complete.')" | tee -a "$screen_capture" >&2
  printf '%s\n' "$run_file"
}


# ============================================================
# Host installer run (visible output — mirror of installer)
# ============================================================
run_host_installer() {
  local installer="$1"
  local tmp_extract_dir="$NVIDIA_WORKDIR/tmp_extract"
  mkdir -p "$tmp_extract_dir"

  msg_info2 "$(translate 'Starting NVIDIA installer on host. This may take several minutes...')"
  echo "" >>"$LOG_FILE"
  echo "=== Running NVIDIA installer: $installer ===" >>"$LOG_FILE"

  sh "$installer" \
    --tmpdir="$tmp_extract_dir" \
    --no-questions \
    --ui=none \
    --disable-nouveau \
    --no-nouveau-check \
    --dkms \
    2>&1 | tee -a "$LOG_FILE"
  local rc=${PIPESTATUS[0]}
  echo "" >>"$LOG_FILE"

  rm -rf "$tmp_extract_dir"

  if [[ $rc -ne 0 ]]; then
    msg_error "$(translate 'NVIDIA installer reported an error. Check') ${LOG_FILE}"
    update_component_status "nvidia_driver" "failed" "" "gpu" '{"patched":false}'
    return 1
  fi

  msg_ok "$(translate 'NVIDIA driver installed on host.')" | tee -a "$screen_capture"
  return 0
}


# ============================================================
# LXC NVIDIA update — aligned with add_gpu_lxc.sh::_install_nvidia_drivers
# ============================================================
CT_ORIG_MEM=""
NVIDIA_INSTALL_MIN_MB=2048
CT_WAS_STARTED_FOR_UPDATE=false

_detect_container_distro() {
  local distro
  distro=$(pct exec "$1" -- grep "^ID=" /etc/os-release 2>/dev/null \
    | cut -d= -f2 | tr -d '[:space:]"')
  echo "${distro:-unknown}"
}

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
      msg_warn "$(translate 'Insufficient memory. Skipping LXC') ${ctid}."
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
    msg_warn "$(translate 'Container') ${ctid} $(translate 'did not become ready. Skipping.')"
    return 1
  fi
  msg_ok "$(translate 'Container') ${ctid} $(translate 'started.')" | tee -a "$screen_capture"
  return 0
}

update_lxc_nvidia() {
  local ctid="$1"
  local version="$2"
  CT_WAS_STARTED_FOR_UPDATE=false

  local old_version
  old_version=$(get_lxc_nvidia_version "$ctid")

  msg_info2 "$(translate 'Container') ${ctid}: $(translate 'updating NVIDIA userspace libs') (${old_version} → ${version})"

  # Start the container if stopped (required for pct exec based install)
  if ! pct status "$ctid" 2>/dev/null | grep -q "running"; then
    CT_WAS_STARTED_FOR_UPDATE=true
    if ! start_container_and_wait "$ctid"; then
      return 1
    fi
  fi

  # Detect distro (alpine / arch / debian-like)
  msg_info "$(translate 'Detecting container OS...')"
  local distro
  distro=$(_detect_container_distro "$ctid")
  msg_ok "$(translate 'Container OS:') ${distro}" | tee -a "$screen_capture"

  local install_rc=0

  case "$distro" in
    alpine)
      # Alpine: musl — use apk nvidia-utils (repo-managed, no .run)
      msg_info2 "$(translate 'Upgrading NVIDIA utils (Alpine)...')"
      pct exec "$ctid" -- sh -c \
        "apk update && apk add --no-cache --upgrade nvidia-utils" \
        2>&1 | tee -a "$LOG_FILE"
      install_rc=${PIPESTATUS[0]}
      ;;

    arch|manjaro|endeavouros)
      msg_info2 "$(translate 'Upgrading NVIDIA utils (Arch)...')"
      pct exec "$ctid" -- bash -c \
        "pacman -Syu --noconfirm nvidia-utils" \
        2>&1 | tee -a "$LOG_FILE"
      install_rc=${PIPESTATUS[0]}
      ;;

    *)
      # Debian / Ubuntu / generic glibc: use the host-cached .run binary
      local run_file="${NVIDIA_WORKDIR}/NVIDIA-Linux-x86_64-${version}.run"

      if [[ ! -f "$run_file" ]]; then
        msg_warn "$(translate 'Installer not found:') ${run_file}. $(translate 'Skipping LXC') ${ctid}."
        install_rc=1
      else
        # Memory check — nvidia-installer needs ~2GB during install
        if ! _ensure_container_memory "$ctid"; then
          install_rc=1
        else
          # Disk space check — NVIDIA libs need ~1.5 GB free in the container
          local free_mb
          free_mb=$(pct exec "$ctid" -- df -m / 2>/dev/null | awk 'NR==2{print $4}' || echo 0)
          if [[ "$free_mb" -lt 1500 ]]; then
            _restore_container_memory "$ctid"
            dialog --backtitle "ProxMenux" \
              --title "$(translate 'Insufficient Disk Space')" \
              --msgbox "\n$(translate 'Container') ${ctid} $(translate 'has only') ${free_mb}MB $(translate 'of free disk space.')\n\n$(translate 'NVIDIA libs require approximately 1.5GB of free space.')\n\n$(translate 'Please expand the container disk and run this option again.')" \
              12 72
            msg_warn "$(translate 'Insufficient disk space. Skipping LXC') ${ctid}."
            install_rc=1
          else
            # Extract .run on the host (avoids decompression OOM inside container)
            local extract_dir="${NVIDIA_WORKDIR}/extracted_${version}"
            local archive="/tmp/nvidia_lxc_${version}.tar.gz"

            msg_info2 "$(translate 'Extracting NVIDIA installer on host...')"
            rm -rf "$extract_dir"
            sh "$run_file" --extract-only --target "$extract_dir" 2>&1 | tee -a "$LOG_FILE"
            if [[ ${PIPESTATUS[0]} -ne 0 ]]; then
              msg_warn "$(translate 'Extraction failed. Check log:') ${LOG_FILE}"
              _restore_container_memory "$ctid"
              install_rc=1
            else
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
                rm -rf "$extract_dir"
                _restore_container_memory "$ctid"
                install_rc=1
              else
                rm -f "$archive"
                msg_ok "$(translate 'Installer copied to container.')" | tee -a "$screen_capture"

                msg_info2 "$(translate 'Running NVIDIA installer in container. This may take several minutes...')"
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
                install_rc=${PIPESTATUS[0]}

                rm -rf "$extract_dir"
                _restore_container_memory "$ctid"
              fi
            fi
          fi
        fi
      fi
      ;;
  esac

  if [[ $install_rc -ne 0 ]]; then
    msg_warn "$(translate 'NVIDIA update failed for LXC') ${ctid} ($(translate 'rc='))${install_rc}. $(translate 'Check log:') ${LOG_FILE}"
    if [[ "$CT_WAS_STARTED_FOR_UPDATE" == "true" ]]; then
      msg_info "$(translate 'Stopping container') ${ctid}..."
      pct stop "$ctid" >>"$LOG_FILE" 2>&1 || true
      msg_ok "$(translate 'Container stopped.')" | tee -a "$screen_capture"
    fi
    return 1
  fi

  # Verify nvidia-smi inside the container
  if pct exec "$ctid" -- sh -c "which nvidia-smi" >/dev/null 2>&1; then
    local new_ver
    new_ver=$(pct exec "$ctid" -- nvidia-smi \
      --query-gpu=driver_version --format=csv,noheader 2>/dev/null \
      | head -1 | tr -d '[:space:]' || true)
    msg_ok "$(translate 'Container') ${ctid}: ${old_version} → ${new_ver:-$version}" | tee -a "$screen_capture"
  else
    msg_warn "$(translate 'nvidia-smi not found in container') ${ctid} $(translate 'after update.')"
  fi

  if [[ "$CT_WAS_STARTED_FOR_UPDATE" == "true" ]]; then
    msg_info "$(translate 'Stopping container') ${ctid}..."
    pct stop "$ctid" >>"$LOG_FILE" 2>&1 || true
    msg_ok "$(translate 'Container stopped.')" | tee -a "$screen_capture"
  fi
  return 0
}


# ============================================================
# Restart prompt
# ============================================================
restart_prompt() {
  echo
  msg_info "$(translate 'Removing no longer required packages and purging old cached updates...')"
  apt-get -y autoremove >/dev/null 2>&1
  apt-get -y autoclean >/dev/null 2>&1
  msg_ok "$(translate 'Cleanup finished.')" | tee -a "$screen_capture"

  if whiptail --title "$(translate 'Reboot Required')" \
    --yesno "$(translate 'The host driver update requires a reboot to take effect. Do you want to restart now?')" 10 70; then
    msg_success "$(translate 'Press Enter to continue...')"
    read -r
    msg_warn "$(translate 'Rebooting the system...')"
    rm -f "$screen_capture"
    reboot
  else
    msg_info2 "$(translate 'You can reboot later manually.')"
    msg_success "$(translate 'Press Enter to continue...')"
    read -r
    rm -f "$screen_capture"
  fi
}


# ============================================================
# Main
# ============================================================
main() {
  : >"$LOG_FILE"
  : >"$screen_capture"

  # ---- Phase 1: dialogs ----
  check_gpu_not_in_vm_passthrough
  detect_host_nvidia
  get_kernel_compatibility_info
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
  msg_title "$(translate "$SCRIPT_TITLE")"

  ensure_repos_and_headers

  # Download installer once — shared between LXC and host updates.
  # No 2>>"$LOG_FILE" redirect: we want msg_warn/msg_error from the function to
  # reach the user's terminal, and wget's progress bar goes to /dev/tty directly.
  local installer
  installer=$(download_nvidia_installer "$TARGET_VERSION")
  local download_result=$?

  if [[ $download_result -ne 0 || -z "$installer" || ! -f "$installer" ]]; then
    msg_error "$(translate 'Failed to obtain NVIDIA installer. Check') ${LOG_FILE}"
    rm -f "$screen_capture"
    exit 1
  fi

  # Update LXCs first (userspace libs only — doesn't need a reboot)
  if [[ ${#NVIDIA_CONTAINERS[@]} -gt 0 ]]; then
    msg_info2 "$(translate 'Updating LXC containers...')"
    for ctid in "${NVIDIA_CONTAINERS[@]}"; do
      update_lxc_nvidia "$ctid" "$TARGET_VERSION" || true
    done
  fi

  # Purge and reinstall host driver
  _purge_nvidia_host

  if ! run_host_installer "$installer"; then
    rm -f "$screen_capture"
    exit 1
  fi

  msg_info "$(translate 'Updating initramfs for all kernels...')"
  update-initramfs -u -k all >>"$LOG_FILE" 2>&1 || true
  msg_ok "$(translate 'initramfs updated.')" | tee -a "$screen_capture"

  # ---- Phase 3: summary ----
  sleep 2
  show_proxmenux_logo
  msg_title "$(translate "$SCRIPT_TITLE")"
  cat "$screen_capture"
  echo -e "${TAB}${GN}📄 $(translate "Log file")${CL}: ${BL}$LOG_FILE${CL}"

  msg_info2 "$(translate 'Checking NVIDIA driver status with nvidia-smi')"
  if command -v nvidia-smi >/dev/null 2>&1; then
    nvidia-smi || true
    local NEW_HOST_VERSION
    NEW_HOST_VERSION=$(nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>/dev/null | head -n1)
    if [[ -n "$NEW_HOST_VERSION" ]]; then
      msg_ok "$(translate 'NVIDIA driver') $NEW_HOST_VERSION $(translate 'installed successfully on host.')"
      update_component_status "nvidia_driver" "installed" "$NEW_HOST_VERSION" "gpu" '{"patched":false}'
    fi
  else
    msg_warn "$(translate 'nvidia-smi not found in PATH. Verify the update manually after reboot.')"
  fi

  msg_success "$(translate 'NVIDIA driver update completed.')"
  restart_prompt
}

main
