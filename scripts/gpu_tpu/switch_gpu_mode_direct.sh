#!/bin/bash
# ==========================================================
# ProxMenux - GPU Switch Mode Direct (VM <-> LXC)
# ==========================================================
# Author      : MacRimi
# Copyright   : (c) 2024 MacRimi
# License     : GPL-3.0
# Version     : 1.0
# Last Updated: 09/04/2026
# ==========================================================
# This script is a hybrid version for ProxMenux Monitor.
# It accepts parameters to skip GPU selection and uses
# hybrid dialogs for web rendering.
# ==========================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCAL_SCRIPTS_LOCAL="$(cd "$SCRIPT_DIR/.." && pwd)"
LOCAL_SCRIPTS_DEFAULT="/usr/local/share/proxmenux/scripts"
LOCAL_SCRIPTS="$LOCAL_SCRIPTS_DEFAULT"
BASE_DIR="/usr/local/share/proxmenux"
UTILS_FILE="$LOCAL_SCRIPTS/utils.sh"
if [[ -f "$LOCAL_SCRIPTS_LOCAL/utils.sh" ]]; then
  LOCAL_SCRIPTS="$LOCAL_SCRIPTS_LOCAL"
  UTILS_FILE="$LOCAL_SCRIPTS/utils.sh"
elif [[ ! -f "$UTILS_FILE" ]]; then
  UTILS_FILE="$BASE_DIR/utils.sh"
fi

LOG_FILE="/tmp/proxmenux_gpu_switch_mode.log"
screen_capture="/tmp/proxmenux_gpu_switch_mode_screen_$$.txt"

if [[ -f "$UTILS_FILE" ]]; then
  source "$UTILS_FILE"
fi
if [[ -f "$LOCAL_SCRIPTS_LOCAL/global/pci_passthrough_helpers.sh" ]]; then
  source "$LOCAL_SCRIPTS_LOCAL/global/pci_passthrough_helpers.sh"
elif [[ -f "$LOCAL_SCRIPTS_DEFAULT/global/pci_passthrough_helpers.sh" ]]; then
  source "$LOCAL_SCRIPTS_DEFAULT/global/pci_passthrough_helpers.sh"
fi
if [[ -f "$LOCAL_SCRIPTS_LOCAL/global/gpu_hook_guard_helpers.sh" ]]; then
  source "$LOCAL_SCRIPTS_LOCAL/global/gpu_hook_guard_helpers.sh"
elif [[ -f "$LOCAL_SCRIPTS_DEFAULT/global/gpu_hook_guard_helpers.sh" ]]; then
  source "$LOCAL_SCRIPTS_DEFAULT/global/gpu_hook_guard_helpers.sh"
fi

load_language
initialize_cache

# ==========================================================
# Global Variables
# ==========================================================
declare -a ALL_GPU_PCIS=()
declare -a ALL_GPU_TYPES=()
declare -a ALL_GPU_NAMES=()
declare -a ALL_GPU_DRIVERS=()
declare -a ALL_GPU_VIDDID=()
declare -a SELECTED_GPU_IDX=()

declare -a SELECTED_IOMMU_IDS=()
declare -a SELECTED_PCI_SLOTS=()

declare -a LXC_AFFECTED_CTIDS=()
declare -a LXC_AFFECTED_NAMES=()
declare -a LXC_AFFECTED_RUNNING=()
declare -a LXC_AFFECTED_ONBOOT=()

declare -a VM_AFFECTED_IDS=()
declare -a VM_AFFECTED_NAMES=()
declare -a VM_AFFECTED_RUNNING=()
declare -a VM_AFFECTED_ONBOOT=()

TARGET_MODE=""                    # vm | lxc
CURRENT_MODE=""                   # vm | lxc | mixed
LXC_ACTION=""                     # keep_gpu_disable_onboot | remove_gpu_keep_onboot
VM_ACTION=""                      # keep_gpu_disable_onboot | remove_gpu_keep_onboot
GPU_COUNT=0
HOST_CONFIG_CHANGED=false

# Parameters from command line
PARAM_GPU_SLOT=""
PARAM_TARGET_MODE=""

# ==========================================================
# Helper Functions (same as original)
# ==========================================================
_set_title() {
  show_proxmenux_logo
  case "$TARGET_MODE" in
    vm)  msg_title "GPU Switch Mode (GPU -> VM)" ;;
    lxc) msg_title "GPU Switch Mode (GPU -> LXC)" ;;
    *)   msg_title "GPU Switch Mode (VM <-> LXC)" ;;
  esac
}

_add_line_if_missing() {
  local line="$1"
  local file="$2"
  touch "$file"
  if ! grep -qFx "$line" "$file" 2>/dev/null; then
    echo "$line" >>"$file"
    HOST_CONFIG_CHANGED=true
  fi
}

_get_pci_driver() {
  local pci_full="$1"
  local driver_link="/sys/bus/pci/devices/${pci_full}/driver"
  if [[ -L "$driver_link" ]]; then
    basename "$(readlink "$driver_link")"
  else
    echo "none"
  fi
}

_ct_is_running() {
  local ctid="$1"
  pct status "$ctid" 2>/dev/null | grep -q "status: running"
}

_ct_onboot_enabled() {
  local ctid="$1"
  pct config "$ctid" 2>/dev/null | grep -qE "^onboot:\s*1"
}

_vm_is_running() {
  local vmid="$1"
  qm status "$vmid" 2>/dev/null | grep -q "status: running"
}

_vm_onboot_enabled() {
  local vmid="$1"
  qm config "$vmid" 2>/dev/null | grep -qE "^onboot:\s*1"
}

_get_iommu_group_ids() {
  local pci_full="$1"
  local group_link="/sys/bus/pci/devices/${pci_full}/iommu_group"
  [[ ! -L "$group_link" ]] && return

  local group_dir
  group_dir="/sys/kernel/iommu_groups/$(basename "$(readlink "$group_link")")/devices"
  for dev_path in "${group_dir}/"*; do
    [[ -e "$dev_path" ]] || continue
    local dev dev_class vid did
    dev=$(basename "$dev_path")
    dev_class=$(cat "/sys/bus/pci/devices/${dev}/class" 2>/dev/null)
    [[ "$dev_class" == "0x0604" || "$dev_class" == "0x0600" ]] && continue
    vid=$(cat "/sys/bus/pci/devices/${dev}/vendor" 2>/dev/null | sed 's/0x//')
    did=$(cat "/sys/bus/pci/devices/${dev}/device" 2>/dev/null | sed 's/0x//')
    [[ -n "$vid" && -n "$did" ]] && echo "${vid}:${did}"
  done
}

_read_vfio_ids() {
  local vfio_conf="/etc/modprobe.d/vfio.conf"
  local ids_line ids_part
  ids_line=$(grep "^options vfio-pci ids=" "$vfio_conf" 2>/dev/null | head -1)
  [[ -z "$ids_line" ]] && return
  ids_part=$(echo "$ids_line" | grep -oE 'ids=[^[:space:]]+' | sed 's/ids=//')
  [[ -z "$ids_part" ]] && return
  tr ',' '\n' <<< "$ids_part" | sed '/^$/d'
}

_write_vfio_ids() {
  local -a ids=("$@")
  local vfio_conf="/etc/modprobe.d/vfio.conf"
  touch "$vfio_conf"

  local current_line new_line ids_str
  current_line=$(grep "^options vfio-pci ids=" "$vfio_conf" 2>/dev/null | head -1)
  sed -i '/^options vfio-pci ids=/d' "$vfio_conf"

  if [[ ${#ids[@]} -gt 0 ]]; then
    ids_str=$(IFS=','; echo "${ids[*]}")
    new_line="options vfio-pci ids=${ids_str} disable_vga=1"
    echo "$new_line" >>"$vfio_conf"
    [[ "$current_line" != "$new_line" ]] && HOST_CONFIG_CHANGED=true
  else
    [[ -n "$current_line" ]] && HOST_CONFIG_CHANGED=true
  fi
}

_contains_in_array() {
  local needle="$1"
  shift
  local x
  for x in "$@"; do
    [[ "$x" == "$needle" ]] && return 0
  done
  return 1
}

_remove_gpu_blacklist() {
  local gpu_type="$1"
  local blacklist_file="/etc/modprobe.d/blacklist.conf"
  [[ ! -f "$blacklist_file" ]] && return
  local changed=false
  case "$gpu_type" in
    nvidia)
      grep -qE '^blacklist (nouveau|nvidia|nvidiafb|nvidia_drm|nvidia_modeset|nvidia_uvm|lbm-nouveau)$|^options nouveau modeset=0$' "$blacklist_file" 2>/dev/null && changed=true
      sed -i '/^blacklist nouveau$/d' "$blacklist_file"
      sed -i '/^blacklist nvidia$/d' "$blacklist_file"
      sed -i '/^blacklist nvidiafb$/d' "$blacklist_file"
      sed -i '/^blacklist nvidia_drm$/d' "$blacklist_file"
      sed -i '/^blacklist nvidia_modeset$/d' "$blacklist_file"
      sed -i '/^blacklist nvidia_uvm$/d' "$blacklist_file"
      sed -i '/^blacklist lbm-nouveau$/d' "$blacklist_file"
      sed -i '/^options nouveau modeset=0$/d' "$blacklist_file"
      ;;
    amd)
      grep -qE '^blacklist (radeon|amdgpu)$' "$blacklist_file" 2>/dev/null && changed=true
      sed -i '/^blacklist radeon$/d' "$blacklist_file"
      sed -i '/^blacklist amdgpu$/d' "$blacklist_file"
      ;;
    intel)
      grep -qE '^blacklist i915$' "$blacklist_file" 2>/dev/null && changed=true
      sed -i '/^blacklist i915$/d' "$blacklist_file"
      ;;
  esac
  $changed && HOST_CONFIG_CHANGED=true
  $changed
}

_add_gpu_blacklist() {
  local gpu_type="$1"
  local blacklist_file="/etc/modprobe.d/blacklist.conf"
  touch "$blacklist_file"
  case "$gpu_type" in
    nvidia)
      _add_line_if_missing "blacklist nouveau" "$blacklist_file"
      _add_line_if_missing "blacklist nvidia" "$blacklist_file"
      _add_line_if_missing "blacklist nvidiafb" "$blacklist_file"
      _add_line_if_missing "blacklist nvidia_drm" "$blacklist_file"
      _add_line_if_missing "blacklist nvidia_modeset" "$blacklist_file"
      _add_line_if_missing "blacklist nvidia_uvm" "$blacklist_file"
      _add_line_if_missing "blacklist lbm-nouveau" "$blacklist_file"
      _add_line_if_missing "options nouveau modeset=0" "$blacklist_file"
      ;;
    amd)
      _add_line_if_missing "blacklist radeon" "$blacklist_file"
      _add_line_if_missing "blacklist amdgpu" "$blacklist_file"
      ;;
    intel)
      _add_line_if_missing "blacklist i915" "$blacklist_file"
      ;;
  esac
}

_sanitize_nvidia_host_stack_for_vfio() {
  local changed=false
  local state_dir="/var/lib/proxmenux"
  local state_file="${state_dir}/nvidia-host-services.state"
  local svc
  local -a services=(
    "nvidia-persistenced.service"
    "nvidia-powerd.service"
    "nvidia-fabricmanager.service"
  )

  mkdir -p "$state_dir" >/dev/null 2>&1 || true
  : > "$state_file"

  for svc in "${services[@]}"; do
    local was_enabled=0 was_active=0
    if systemctl is-enabled --quiet "$svc" 2>/dev/null; then
      was_enabled=1
    fi
    if systemctl is-active --quiet "$svc" 2>/dev/null; then
      was_active=1
    fi
    if (( was_enabled == 1 || was_active == 1 )); then
      echo "${svc} enabled=${was_enabled} active=${was_active}" >>"$state_file"
    fi

    if systemctl is-active --quiet "$svc" 2>/dev/null; then
      systemctl stop "$svc" >>"$LOG_FILE" 2>&1 || true
      changed=true
    fi
    if systemctl is-enabled --quiet "$svc" 2>/dev/null; then
      systemctl disable "$svc" >>"$LOG_FILE" 2>&1 || true
      changed=true
    fi
  done

  [[ -s "$state_file" ]] || rm -f "$state_file"

  if [[ -f /etc/modules-load.d/nvidia-vfio.conf ]]; then
    mv /etc/modules-load.d/nvidia-vfio.conf /etc/modules-load.d/nvidia-vfio.conf.proxmenux-disabled-vfio >>"$LOG_FILE" 2>&1 || true
    changed=true
  fi

  if grep -qE '^(nvidia|nvidia_uvm|nvidia_drm|nvidia_modeset)$' /etc/modules 2>/dev/null; then
    sed -i '/^nvidia$/d;/^nvidia_uvm$/d;/^nvidia_drm$/d;/^nvidia_modeset$/d' /etc/modules
    changed=true
  fi

  if $changed; then
    HOST_CONFIG_CHANGED=true
    msg_ok "$(translate 'NVIDIA host services/autoload disabled for VFIO mode')" | tee -a "$screen_capture"
  else
    msg_ok "$(translate 'NVIDIA host services/autoload already aligned for VFIO mode')" | tee -a "$screen_capture"
  fi
}

_restore_nvidia_host_stack_for_lxc() {
  local changed=false
  local state_file="/var/lib/proxmenux/nvidia-host-services.state"
  local disabled_file="/etc/modules-load.d/nvidia-vfio.conf.proxmenux-disabled-vfio"
  local active_file="/etc/modules-load.d/nvidia-vfio.conf"

  if [[ -f "$disabled_file" ]]; then
    mv "$disabled_file" "$active_file" >>"$LOG_FILE" 2>&1 || true
    changed=true
  fi

  modprobe nvidia >/dev/null 2>&1 || true
  modprobe nvidia_uvm >/dev/null 2>&1 || true
  modprobe nvidia_modeset >/dev/null 2>&1 || true
  modprobe nvidia_drm >/dev/null 2>&1 || true

  if [[ -f "$state_file" ]]; then
    while IFS= read -r line; do
      [[ -z "$line" ]] && continue
      local svc enabled active
      svc=$(echo "$line" | awk '{print $1}')
      enabled=$(echo "$line" | awk -F'enabled=' '{print $2}' | awk '{print $1}')
      active=$(echo "$line" | awk -F'active=' '{print $2}' | awk '{print $1}')
      [[ "$enabled" == "1" ]] && systemctl enable "$svc" >>"$LOG_FILE" 2>&1 || true
      [[ "$active" == "1" ]] && systemctl start "$svc" >>"$LOG_FILE" 2>&1 || true
    done <"$state_file"
    rm -f "$state_file"
    changed=true
  fi

  if $changed; then
    HOST_CONFIG_CHANGED=true
    msg_ok "$(translate 'NVIDIA host services/autoload restored for native mode')" | tee -a "$screen_capture"
  else
    msg_ok "$(translate 'NVIDIA host services/autoload already aligned for native mode')" | tee -a "$screen_capture"
  fi
}

_add_amd_softdep() {
  local vfio_conf="/etc/modprobe.d/vfio.conf"
  _add_line_if_missing "softdep radeon pre: vfio-pci" "$vfio_conf"
  _add_line_if_missing "softdep amdgpu pre: vfio-pci" "$vfio_conf"
  _add_line_if_missing "softdep snd_hda_intel pre: vfio-pci" "$vfio_conf"
}

_remove_amd_softdep() {
  local vfio_conf="/etc/modprobe.d/vfio.conf"
  [[ ! -f "$vfio_conf" ]] && return
  local changed=false
  grep -qE '^softdep (radeon|amdgpu|snd_hda_intel) pre: vfio-pci$' "$vfio_conf" 2>/dev/null && changed=true
  sed -i '/^softdep radeon pre: vfio-pci$/d' "$vfio_conf"
  sed -i '/^softdep amdgpu pre: vfio-pci$/d' "$vfio_conf"
  sed -i '/^softdep snd_hda_intel pre: vfio-pci$/d' "$vfio_conf"
  $changed && HOST_CONFIG_CHANGED=true
  $changed
}

_selected_types_unique() {
  local idx t
  local -a seen=()
  for idx in "${SELECTED_GPU_IDX[@]}"; do
    t="${ALL_GPU_TYPES[$idx]}"
    _contains_in_array "$t" "${seen[@]}" || { seen+=("$t"); echo "$t"; }
  done
}

# ==========================================================
# GPU Detection
# ==========================================================
detect_host_gpus() {
  ALL_GPU_PCIS=()
  ALL_GPU_TYPES=()
  ALL_GPU_NAMES=()
  ALL_GPU_DRIVERS=()
  ALL_GPU_VIDDID=()

  local line pci name vendor vid did drv
  while IFS= read -r line; do
    pci=$(echo "$line" | awk '{print $1}')
    [[ ! "$pci" =~ ^[0-9a-f]{4}: ]] && pci="0000:$pci"
    name=$(echo "$line" | sed 's/^[^ ]* //')

    vendor=""
    if echo "$name" | grep -qi "nvidia"; then
      vendor="nvidia"
    elif echo "$name" | grep -qiE "amd|radeon"; then
      vendor="amd"
    elif echo "$name" | grep -qi "intel"; then
      vendor="intel"
    else
      vendor="other"
    fi

    vid=$(cat "/sys/bus/pci/devices/${pci}/vendor" 2>/dev/null | sed 's/0x//')
    did=$(cat "/sys/bus/pci/devices/${pci}/device" 2>/dev/null | sed 's/0x//')
    drv=$(_get_pci_driver "$pci")

    ALL_GPU_PCIS+=("$pci")
    ALL_GPU_TYPES+=("$vendor")
    ALL_GPU_NAMES+=("$name")
    ALL_GPU_DRIVERS+=("$drv")
    ALL_GPU_VIDDID+=("${vid}:${did}")
  done < <(lspci -D | grep -iE "VGA|3D|Display" | grep -v "Audio")

  GPU_COUNT=${#ALL_GPU_PCIS[@]}
}

# ==========================================================
# Find GPU by PCI Slot (new function for direct mode)
# ==========================================================
find_gpu_by_slot() {
  local target_slot="$1"
  SELECTED_GPU_IDX=()
  
  # Normalize slot format (ensure 0000: prefix)
  [[ ! "$target_slot" =~ ^[0-9a-f]{4}: ]] && target_slot="0000:$target_slot"
  
  local i
  for i in "${!ALL_GPU_PCIS[@]}"; do
    if [[ "${ALL_GPU_PCIS[$i]}" == "$target_slot"* ]]; then
      SELECTED_GPU_IDX+=("$i")
      return 0
    fi
  done
  
  msg_error "$(translate 'GPU not found with slot'): $target_slot"
  return 1
}

collect_selected_iommu_ids() {
  SELECTED_IOMMU_IDS=()
  SELECTED_PCI_SLOTS=()

  local idx pci viddid slot
  for idx in "${SELECTED_GPU_IDX[@]}"; do
    pci="${ALL_GPU_PCIS[$idx]}"
    viddid="${ALL_GPU_VIDDID[$idx]}"
    slot="${pci#0000:}"
    slot="${slot%.*}"
    SELECTED_PCI_SLOTS+=("$slot")

    local -a group_ids=()
    mapfile -t group_ids < <(_get_iommu_group_ids "$pci")
    if [[ ${#group_ids[@]} -gt 0 ]]; then
      local gid
      for gid in "${group_ids[@]}"; do
        _contains_in_array "$gid" "${SELECTED_IOMMU_IDS[@]}" || SELECTED_IOMMU_IDS+=("$gid")
      done
    elif [[ -n "$viddid" ]]; then
      _contains_in_array "$viddid" "${SELECTED_IOMMU_IDS[@]}" || SELECTED_IOMMU_IDS+=("$viddid")
    fi
  done
}

# ==========================================================
# LXC Detection and Handling (hybrid dialogs)
# ==========================================================
_lxc_conf_uses_type() {
  local conf="$1"
  local gpu_type="$2"
  case "$gpu_type" in
    nvidia) grep -qE "dev[0-9]+:.*(/dev/nvidia|/dev/nvidia-caps)" "$conf" 2>/dev/null ;;
    amd)    grep -qE "dev[0-9]+:.*(/dev/dri|/dev/kfd)|lxc\.mount\.entry:.*dev/dri" "$conf" 2>/dev/null ;;
    intel)  grep -qE "dev[0-9]+:.*(/dev/dri)|lxc\.mount\.entry:.*dev/dri" "$conf" 2>/dev/null ;;
    *)      return 1 ;;
  esac
}

detect_affected_lxc_for_selected() {
  LXC_AFFECTED_CTIDS=()
  LXC_AFFECTED_NAMES=()
  LXC_AFFECTED_RUNNING=()
  LXC_AFFECTED_ONBOOT=()

  local -a types=()
  mapfile -t types < <(_selected_types_unique)

  local conf
  for conf in /etc/pve/lxc/*.conf; do
    [[ -f "$conf" ]] || continue
    local matched=false
    local t
    for t in "${types[@]}"; do
      _lxc_conf_uses_type "$conf" "$t" && matched=true && break
    done
    $matched || continue

    local ctid ct_name run onb
    ctid=$(basename "$conf" .conf)
    ct_name=$(pct config "$ctid" 2>/dev/null | awk '/^hostname:/ {print $2}')
    [[ -z "$ct_name" ]] && ct_name="CT-${ctid}"
    run=0; onb=0
    _ct_is_running "$ctid" && run=1
    _ct_onboot_enabled "$ctid" && onb=1

    LXC_AFFECTED_CTIDS+=("$ctid")
    LXC_AFFECTED_NAMES+=("$ct_name")
    LXC_AFFECTED_RUNNING+=("$run")
    LXC_AFFECTED_ONBOOT+=("$onb")
  done
}

# HYBRID: LXC conflict policy prompt
prompt_lxc_action_for_vm_mode() {
  [[ ${#LXC_AFFECTED_CTIDS[@]} -eq 0 ]] && return 0

  local running_count=0 onboot_count=0 i
  for i in "${!LXC_AFFECTED_CTIDS[@]}"; do
    [[ "${LXC_AFFECTED_RUNNING[$i]}" == "1" ]] && running_count=$((running_count + 1))
    [[ "${LXC_AFFECTED_ONBOOT[$i]}" == "1" ]] && onboot_count=$((onboot_count + 1))
  done

  local msg
  msg="$(translate 'The selected GPU(s) are used in these LXC container(s)'):\n\n"
  for i in "${!LXC_AFFECTED_CTIDS[@]}"; do
    local st ob
    st="$(translate 'stopped')"; ob="onboot=0"
    [[ "${LXC_AFFECTED_RUNNING[$i]}" == "1" ]] && st="$(translate 'running')"
    [[ "${LXC_AFFECTED_ONBOOT[$i]}" == "1" ]] && ob="onboot=1"
    msg+="  - CT ${LXC_AFFECTED_CTIDS[$i]} (${LXC_AFFECTED_NAMES[$i]}) [${st}, ${ob}]\n"
  done
  msg+="\n$(translate 'Switching to GPU -> VM mode requires exclusive VFIO binding.')\n"
  [[ "$running_count" -gt 0 ]] && msg+="$(translate 'Running containers detected'): ${running_count}\n"
  [[ "$onboot_count" -gt 0 ]] && msg+="$(translate 'Start on boot enabled'): ${onboot_count}\n"
  msg+="\n$(translate 'Choose conflict policy'):"

  local choice
  choice=$(hybrid_menu "$(translate 'LXC Conflict Policy')" "$msg" 24 80 8 \
    "1" "$(translate 'Keep GPU in LXC config (disable Start on boot)')" \
    "2" "$(translate 'Remove GPU from LXC config (keep Start on boot)')")

  case "$choice" in
    1) LXC_ACTION="keep_gpu_disable_onboot" ;;
    2) LXC_ACTION="remove_gpu_keep_onboot" ;;
    *) 
      msg_warn "$(translate 'Operation cancelled by user')"
      exit 0 
      ;;
  esac
}

_remove_type_from_lxc_conf() {
  local conf="$1"
  local gpu_type="$2"
  case "$gpu_type" in
    nvidia)
      sed -i '/dev[0-9]\+:.*\/dev\/nvidia/d' "$conf"
      ;;
    amd)
      sed -i '/dev[0-9]\+:.*\/dev\/dri/d' "$conf"
      sed -i '/dev[0-9]\+:.*\/dev\/kfd/d' "$conf"
      sed -i '/lxc\.mount\.entry:.*dev\/dri/d' "$conf"
      sed -i '/lxc\.cgroup2\.devices\.allow:.*226/d' "$conf"
      ;;
    intel)
      sed -i '/dev[0-9]\+:.*\/dev\/dri/d' "$conf"
      sed -i '/lxc\.mount\.entry:.*dev\/dri/d' "$conf"
      sed -i '/lxc\.cgroup2\.devices\.allow:.*226/d' "$conf"
      ;;
  esac
}

apply_lxc_action_for_vm_mode() {
  [[ ${#LXC_AFFECTED_CTIDS[@]} -eq 0 ]] && return 0
  local -a types=()
  mapfile -t types < <(_selected_types_unique)

  local i
  for i in "${!LXC_AFFECTED_CTIDS[@]}"; do
    local ctid conf
    ctid="${LXC_AFFECTED_CTIDS[$i]}"
    conf="/etc/pve/lxc/${ctid}.conf"

    if [[ "${LXC_AFFECTED_RUNNING[$i]}" == "1" ]]; then
      msg_info "$(translate 'Stopping LXC') ${ctid}..."
      pct stop "$ctid" >>"$LOG_FILE" 2>&1 || true
      msg_ok "$(translate 'LXC stopped') ${ctid}" | tee -a "$screen_capture"
    fi

    if [[ "$LXC_ACTION" == "keep_gpu_disable_onboot" && "${LXC_AFFECTED_ONBOOT[$i]}" == "1" ]]; then
      if pct set "$ctid" -onboot 0 >>"$LOG_FILE" 2>&1; then
        msg_warn "$(translate 'Start on boot disabled for LXC') ${ctid}" | tee -a "$screen_capture"
      fi
    fi

    if [[ "$LXC_ACTION" == "remove_gpu_keep_onboot" && -f "$conf" ]]; then
      local t
      for t in "${types[@]}"; do
        _remove_type_from_lxc_conf "$conf" "$t"
      done
      msg_ok "$(translate 'GPU access removed from LXC') ${ctid}" | tee -a "$screen_capture"
    fi
  done
}

# ==========================================================
# VM Detection and Handling (hybrid dialogs)
# ==========================================================
detect_affected_vms_for_selected() {
  VM_AFFECTED_IDS=()
  VM_AFFECTED_NAMES=()
  VM_AFFECTED_RUNNING=()
  VM_AFFECTED_ONBOOT=()

  local conf
  for conf in /etc/pve/qemu-server/*.conf; do
    [[ -f "$conf" ]] || continue
    local matched=false slot
    for slot in "${SELECTED_PCI_SLOTS[@]}"; do
      if grep -qE "hostpci[0-9]+:.*(0000:)?${slot}(\\.[0-7])?([,[:space:]]|$)" "$conf"; then
        matched=true
        break
      fi
    done
    $matched || continue

    local vmid vm_name run onb
    vmid=$(basename "$conf" .conf)
    vm_name=$(grep "^name:" "$conf" 2>/dev/null | awk '{print $2}')
    [[ -z "$vm_name" ]] && vm_name="VM-${vmid}"
    run=0; onb=0
    _vm_is_running "$vmid" && run=1
    _vm_onboot_enabled "$vmid" && onb=1

    VM_AFFECTED_IDS+=("$vmid")
    VM_AFFECTED_NAMES+=("$vm_name")
    VM_AFFECTED_RUNNING+=("$run")
    VM_AFFECTED_ONBOOT+=("$onb")
  done
}

# HYBRID: VM conflict policy prompt
prompt_vm_action_for_lxc_mode() {
  [[ ${#VM_AFFECTED_IDS[@]} -eq 0 ]] && return 0

  local running_count=0 onboot_count=0 i
  for i in "${!VM_AFFECTED_IDS[@]}"; do
    [[ "${VM_AFFECTED_RUNNING[$i]}" == "1" ]] && running_count=$((running_count + 1))
    [[ "${VM_AFFECTED_ONBOOT[$i]}" == "1" ]] && onboot_count=$((onboot_count + 1))
  done

  local msg
  msg="$(translate 'The selected GPU(s) are configured in these VM(s)'):\n\n"
  for i in "${!VM_AFFECTED_IDS[@]}"; do
    local st ob
    st="$(translate 'stopped')"; ob="onboot=0"
    [[ "${VM_AFFECTED_RUNNING[$i]}" == "1" ]] && st="$(translate 'running')"
    [[ "${VM_AFFECTED_ONBOOT[$i]}" == "1" ]] && ob="onboot=1"
    msg+="  - VM ${VM_AFFECTED_IDS[$i]} (${VM_AFFECTED_NAMES[$i]}) [${st}, ${ob}]\n"
  done
  msg+="\n$(translate 'Switching to GPU -> LXC mode removes VFIO exclusivity.')\n"
  [[ "$running_count" -gt 0 ]] && msg+="$(translate 'Running VM detected'): ${running_count}\n"
  [[ "$onboot_count" -gt 0 ]] && msg+="$(translate 'Start on boot enabled'): ${onboot_count}\n"
  msg+="\n$(translate 'Choose conflict policy'):"

  local choice
  choice=$(hybrid_menu "$(translate 'VM Conflict Policy')" "$msg" 24 80 8 \
    "1" "$(translate 'Keep GPU in VM config (disable Start on boot)')" \
    "2" "$(translate 'Remove GPU from VM config (keep Start on boot)')")

  case "$choice" in
    1) VM_ACTION="keep_gpu_disable_onboot" ;;
    2) VM_ACTION="remove_gpu_keep_onboot" ;;
    *) 
      msg_warn "$(translate 'Operation cancelled by user')"
      exit 0 
      ;;
  esac
}

apply_vm_action_for_lxc_mode() {
  [[ ${#VM_AFFECTED_IDS[@]} -eq 0 ]] && return 0

  local i
  for i in "${!VM_AFFECTED_IDS[@]}"; do
    local vmid conf
    vmid="${VM_AFFECTED_IDS[$i]}"
    conf="/etc/pve/qemu-server/${vmid}.conf"

    if [[ "${VM_AFFECTED_RUNNING[$i]}" == "1" ]]; then
      msg_info "$(translate 'Stopping VM') ${vmid}..."
      qm stop "$vmid" >>"$LOG_FILE" 2>&1 || true
      msg_ok "$(translate 'VM stopped') ${vmid}" | tee -a "$screen_capture"
    fi

    if [[ "$VM_ACTION" == "keep_gpu_disable_onboot" && "${VM_AFFECTED_ONBOOT[$i]}" == "1" ]]; then
      if qm set "$vmid" -onboot 0 >>"$LOG_FILE" 2>&1; then
        msg_warn "$(translate 'Start on boot disabled for VM') ${vmid}" | tee -a "$screen_capture"
      fi
    fi

    if [[ "$VM_ACTION" == "remove_gpu_keep_onboot" && -f "$conf" ]]; then
      local slot
      for slot in "${SELECTED_PCI_SLOTS[@]}"; do
        sed -i "/^hostpci[0-9]\+:.*${slot}/d" "$conf"
      done
      msg_ok "$(translate 'GPU removed from VM config') ${vmid}" | tee -a "$screen_capture"
    fi
  done
}

# ==========================================================
# Switch Mode Functions
# ==========================================================
switch_to_vm_mode() {
  detect_affected_lxc_for_selected
  prompt_lxc_action_for_vm_mode

  _set_title
  collect_selected_iommu_ids
  apply_lxc_action_for_vm_mode

  msg_info "$(translate 'Configuring host for GPU -> VM mode...')"
  _add_vfio_modules
  msg_ok "$(translate 'VFIO modules configured in /etc/modules')" | tee -a "$screen_capture"
  _configure_iommu_options
  msg_ok "$(translate 'IOMMU interrupt remapping configured')" | tee -a "$screen_capture"

  local -a current_ids=()
  mapfile -t current_ids < <(_read_vfio_ids)
  local id
  for id in "${SELECTED_IOMMU_IDS[@]}"; do
    _contains_in_array "$id" "${current_ids[@]}" || current_ids+=("$id")
  done
  _write_vfio_ids "${current_ids[@]}"
  if [[ ${#SELECTED_IOMMU_IDS[@]} -gt 0 ]]; then
    local ids_label
    ids_label=$(IFS=','; echo "${SELECTED_IOMMU_IDS[*]}")
    msg_ok "$(translate 'vfio-pci IDs configured') (${ids_label})" | tee -a "$screen_capture"
  fi

  local -a selected_types=()
  mapfile -t selected_types < <(_selected_types_unique)
  local t
  for t in "${selected_types[@]}"; do
    _add_gpu_blacklist "$t"
  done
  msg_ok "$(translate 'GPU host driver blacklisted in /etc/modprobe.d/blacklist.conf')" | tee -a "$screen_capture"
  _contains_in_array "nvidia" "${selected_types[@]}" && _sanitize_nvidia_host_stack_for_vfio
  _contains_in_array "amd" "${selected_types[@]}" && _add_amd_softdep

  if [[ "$HOST_CONFIG_CHANGED" == "true" ]]; then
    msg_info "$(translate 'Updating initramfs (this may take a minute)...')"
    update-initramfs -u -k all >>"$LOG_FILE" 2>&1
    msg_ok "$(translate 'initramfs updated')" | tee -a "$screen_capture"
  fi

  if declare -F sync_proxmenux_gpu_guard_hooks >/dev/null 2>&1; then
    sync_proxmenux_gpu_guard_hooks
  fi
}

_type_has_remaining_vfio_ids() {
  local gpu_type="$1"
  local -a remaining_ids=("$@")
  remaining_ids=("${remaining_ids[@]:1}")
  local idx viddid
  for idx in "${!ALL_GPU_TYPES[@]}"; do
    [[ "${ALL_GPU_TYPES[$idx]}" != "$gpu_type" ]] && continue
    viddid="${ALL_GPU_VIDDID[$idx]}"
    _contains_in_array "$viddid" "${remaining_ids[@]}" && return 0
  done
  return 1
}

switch_to_lxc_mode() {
  collect_selected_iommu_ids
  detect_affected_vms_for_selected
  prompt_vm_action_for_lxc_mode

  _set_title
  apply_vm_action_for_lxc_mode

  msg_info "$(translate 'Removing VFIO ownership for selected GPU(s)...')"

  local -a current_ids=() remaining_ids=() removed_ids=()
  mapfile -t current_ids < <(_read_vfio_ids)
  local id remove
  for id in "${current_ids[@]}"; do
    remove=false
    _contains_in_array "$id" "${SELECTED_IOMMU_IDS[@]}" && remove=true
    if $remove; then
      removed_ids+=("$id")
    else
      remaining_ids+=("$id")
    fi
  done
  _write_vfio_ids "${remaining_ids[@]}"
  if [[ ${#removed_ids[@]} -gt 0 ]]; then
    local ids_label
    ids_label=$(IFS=','; echo "${removed_ids[*]}")
    msg_ok "$(translate 'VFIO device IDs removed from /etc/modprobe.d/vfio.conf') (${ids_label})" | tee -a "$screen_capture"
  fi

  local -a selected_types=()
  mapfile -t selected_types < <(_selected_types_unique)
  local t
  for t in "${selected_types[@]}"; do
    if ! _type_has_remaining_vfio_ids "$t" "${remaining_ids[@]}"; then
      if _remove_gpu_blacklist "$t"; then
        msg_ok "$(translate 'Driver blacklist removed for') ${t}" | tee -a "$screen_capture"
      fi
      if [[ "$t" == "nvidia" ]]; then
        _restore_nvidia_host_stack_for_lxc
      fi
    fi
  done

  if ! _type_has_remaining_vfio_ids "amd" "${remaining_ids[@]}"; then
    _remove_amd_softdep || true
  fi

  if _remove_vfio_modules_if_unused; then
    msg_ok "$(translate 'VFIO modules removed from /etc/modules')" | tee -a "$screen_capture"
  fi

  if [[ "$HOST_CONFIG_CHANGED" == "true" ]]; then
    msg_info "$(translate 'Updating initramfs (this may take a minute)...')"
    update-initramfs -u -k all >>"$LOG_FILE" 2>&1
    msg_ok "$(translate 'initramfs updated')" | tee -a "$screen_capture"
  fi

  if declare -F sync_proxmenux_gpu_guard_hooks >/dev/null 2>&1; then
    sync_proxmenux_gpu_guard_hooks
  fi
}

# HYBRID: Confirmation prompt
confirm_plan() {
  local msg mode_line
  if [[ "$TARGET_MODE" == "vm" ]]; then
    mode_line="$(translate 'Target mode'): GPU -> VM (VFIO)"
  else
    mode_line="$(translate 'Target mode'): GPU -> LXC (native driver)"
  fi

  msg="${mode_line}\n\n$(translate 'Selected GPU(s)'):\n"
  local idx
  for idx in "${SELECTED_GPU_IDX[@]}"; do
    msg+="  - ${ALL_GPU_NAMES[$idx]} (${ALL_GPU_PCIS[$idx]}) [${ALL_GPU_DRIVERS[$idx]}]\n"
  done
  msg+="\n$(translate 'Do you want to proceed?')"

  if ! hybrid_yesno "$(translate 'Confirm GPU Switch Mode')" "$msg" 18 88; then
    msg_warn "$(translate 'Operation cancelled by user')"
    exit 0
  fi
}

# HYBRID: Final summary with reboot prompt
final_summary() {
  _set_title
  cat "$screen_capture"
  echo
  echo -e "${TAB}${BL}Log: ${LOG_FILE}${CL}"

  if [[ "$HOST_CONFIG_CHANGED" == "true" ]]; then
    echo -e "${TAB}${DGN}- $(translate 'Host GPU binding changed — reboot required.')${CL}"
    
    if hybrid_yesno "$(translate 'Reboot Required')" "$(translate 'A reboot is required to apply the new GPU mode. Do you want to restart now?')" 10 74; then
      msg_warn "$(translate 'Rebooting the system...')"
      reboot
    else
      msg_info2 "$(translate 'Please reboot manually to complete the switch.')"
      hybrid_msgbox "$(translate 'Reboot Required')" "$(translate 'Please reboot the system manually to complete the GPU switch.')" 8 60
    fi
  else
    echo -e "${TAB}${DGN}- $(translate 'No host VFIO/native binding changes were required.')${CL}"
    hybrid_msgbox "$(translate 'Complete')" "$(translate 'GPU switch mode completed. No reboot required.')" 8 60
  fi
}

# ==========================================================
# Parse Arguments (supports both CLI args and env vars)
# ==========================================================
parse_arguments() {
  # First, check combined parameter (format: "SLOT|MODE")
  # This is the primary method used by ProxMenux Monitor
  if [[ -n "$GPU_SWITCH_PARAMS" ]]; then
    PARAM_GPU_SLOT="${GPU_SWITCH_PARAMS%%|*}"
    PARAM_TARGET_MODE="${GPU_SWITCH_PARAMS##*|}"
  fi
  
  # Also check individual environment variables as fallback
  [[ -n "$GPU_SLOT" ]] && PARAM_GPU_SLOT="$GPU_SLOT"
  [[ -n "$TARGET_MODE" ]] && PARAM_TARGET_MODE="$TARGET_MODE"
  
  # Then, parse command line arguments (override env vars if provided)
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --gpu-slot=*)
        PARAM_GPU_SLOT="${1#*=}"
        ;;
      --target-mode=*)
        PARAM_TARGET_MODE="${1#*=}"
        ;;
      *)
        # Ignore unknown arguments
        ;;
    esac
    shift
  done
}

# ==========================================================
# Main Entry Point
# ==========================================================
main() {
  : >"$LOG_FILE"
  : >"$screen_capture"

  # Debug: Show received environment variables
  echo "[DEBUG] Environment variables received:"
  echo "[DEBUG] GPU_SWITCH_PARAMS='$GPU_SWITCH_PARAMS'"
  echo "[DEBUG] EXECUTION_MODE='$EXECUTION_MODE'"
  echo ""

  parse_arguments "$@"

  # Debug: Show parsed parameters
  echo "[DEBUG] After parsing:"
  echo "[DEBUG] PARAM_GPU_SLOT='$PARAM_GPU_SLOT'"
  echo "[DEBUG] PARAM_TARGET_MODE='$PARAM_TARGET_MODE'"
  echo ""

  # Validate required parameters
  if [[ -z "$PARAM_GPU_SLOT" ]]; then
    msg_error "$(translate 'Missing required parameter'): --gpu-slot"
    echo "Usage: $0 --gpu-slot=0000:01:00.0 --target-mode=vm|lxc"
    exit 1
  fi

  if [[ -z "$PARAM_TARGET_MODE" ]] || [[ ! "$PARAM_TARGET_MODE" =~ ^(vm|lxc)$ ]]; then
    msg_error "$(translate 'Missing or invalid parameter'): --target-mode (must be 'vm' or 'lxc')"
    echo "Usage: $0 --gpu-slot=0000:01:00.0 --target-mode=vm|lxc"
    exit 1
  fi

  TARGET_MODE="$PARAM_TARGET_MODE"

  # Detect all GPUs
  detect_host_gpus
  
  if [[ "$GPU_COUNT" -eq 0 ]]; then
    msg_error "$(translate 'No GPUs detected on this host.')"
    exit 1
  fi

  # Find the specific GPU by slot
  if ! find_gpu_by_slot "$PARAM_GPU_SLOT"; then
    exit 1
  fi

  # Show info about selected GPU
  local gpu_idx="${SELECTED_GPU_IDX[0]}"
  msg_info "$(translate 'GPU selected'): ${ALL_GPU_NAMES[$gpu_idx]} (${ALL_GPU_PCIS[$gpu_idx]})"
  msg_info "$(translate 'Current driver'): ${ALL_GPU_DRIVERS[$gpu_idx]}"
  msg_info "$(translate 'Target mode'): $TARGET_MODE"
  echo

  # Confirm the operation
  confirm_plan

  clear
  _set_title
  echo

  # Execute the switch
  if [[ "$TARGET_MODE" == "vm" ]]; then
    switch_to_vm_mode
    msg_success "$(translate 'GPU switch complete: VM mode prepared.')"
  else
    switch_to_lxc_mode
    msg_success "$(translate 'GPU switch complete: LXC mode prepared.')"
  fi

  final_summary
  rm -f "$screen_capture"
}

main "$@"
