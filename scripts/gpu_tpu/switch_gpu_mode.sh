#!/bin/bash
# ==========================================================
# ProxMenux - GPU Switch Mode (VM <-> LXC)
# ==========================================================
# Author      : MacRimi
# Copyright   : (c) 2024 MacRimi
# License     : GPL-3.0
# Version     : 1.0
# Last Updated: 05/04/2026
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

  # Restore previous modules-load policy if ProxMenux disabled it in VM mode.
  if [[ -f "$disabled_file" ]]; then
    mv "$disabled_file" "$active_file" >>"$LOG_FILE" 2>&1 || true
    changed=true
  fi

  # Best effort: load NVIDIA kernel modules now that we are back in native mode.
  # If not installed, these calls simply fail silently.
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

_add_vfio_modules() {
  local modules=("vfio" "vfio_iommu_type1" "vfio_pci")
  local kernel_major kernel_minor
  kernel_major=$(uname -r | cut -d. -f1)
  kernel_minor=$(uname -r | cut -d. -f2)
  if (( kernel_major < 6 || ( kernel_major == 6 && kernel_minor < 2 ) )); then
    modules+=("vfio_virqfd")
  fi
  local mod
  for mod in "${modules[@]}"; do
    _add_line_if_missing "$mod" /etc/modules
  done
}

_remove_vfio_modules_if_unused() {
  local vfio_count
  vfio_count=$(_read_vfio_ids | wc -l | tr -d '[:space:]')
  [[ "$vfio_count" != "0" ]] && return 1
  local modules_file="/etc/modules"
  [[ ! -f "$modules_file" ]] && return 1
  local had_any=false
  grep -qE '^vfio$|^vfio_iommu_type1$|^vfio_pci$|^vfio_virqfd$' "$modules_file" 2>/dev/null && had_any=true
  sed -i '/^vfio$/d' "$modules_file"
  sed -i '/^vfio_iommu_type1$/d' "$modules_file"
  sed -i '/^vfio_pci$/d' "$modules_file"
  sed -i '/^vfio_virqfd$/d' "$modules_file"
  if $had_any; then
    HOST_CONFIG_CHANGED=true
    return 0
  fi
  return 1
}

_configure_iommu_options() {
  _add_line_if_missing "options vfio_iommu_type1 allow_unsafe_interrupts=1" /etc/modprobe.d/iommu_unsafe_interrupts.conf
  _add_line_if_missing "options kvm ignore_msrs=1" /etc/modprobe.d/kvm.conf
}

_selected_types_unique() {
  local -a out=()
  local idx t
  for idx in "${SELECTED_GPU_IDX[@]}"; do
    t="${ALL_GPU_TYPES[$idx]}"
    _contains_in_array "$t" "${out[@]}" || out+=("$t")
  done
  printf '%s\n' "${out[@]}"
}

detect_host_gpus() {
  ALL_GPU_PCIS=()
  ALL_GPU_TYPES=()
  ALL_GPU_NAMES=()
  ALL_GPU_DRIVERS=()
  ALL_GPU_VIDDID=()

  while IFS= read -r line; do
    local pci_short pci_full name type driver viddid pci_info
    pci_short=$(echo "$line" | awk '{print $1}')
    pci_full="0000:${pci_short}"
    pci_info=$(lspci -nn -s "${pci_short}" 2>/dev/null | sed 's/^[^ ]* //')
    name="${pci_info#*: }"
    [[ "$name" == "$pci_info" ]] && name="$pci_info"
    name=$(echo "$name" | sed -E 's/ \(rev [^)]+\)$//' | cut -c1-72)
    [[ -z "$name" ]] && name="$(translate 'Unknown GPU')"
    if echo "$line" | grep -qi "Intel"; then
      type="intel"
    elif echo "$line" | grep -qiE "AMD|Advanced Micro|Radeon"; then
      type="amd"
    elif echo "$line" | grep -qi "NVIDIA"; then
      type="nvidia"
    else
      continue
    fi
    driver=$(_get_pci_driver "$pci_full")
    viddid=$(echo "$line" | grep -oE '\[[0-9a-f]{4}:[0-9a-f]{4}\]' | tr -d '[]')
    ALL_GPU_PCIS+=("$pci_full")
    ALL_GPU_TYPES+=("$type")
    ALL_GPU_NAMES+=("$name")
    ALL_GPU_DRIVERS+=("$driver")
    ALL_GPU_VIDDID+=("$viddid")
  done < <(lspci -nn | grep -iE "VGA compatible controller|3D controller|Display controller" | grep -iv "Ethernet\|Network\|Audio")

  GPU_COUNT=${#ALL_GPU_PCIS[@]}
  if [[ "$GPU_COUNT" -eq 0 ]]; then
    dialog --backtitle "ProxMenux" \
      --title "$(translate 'GPU Switch Mode')" \
      --msgbox "\n$(translate 'No compatible GPUs were detected on this host.')" 8 64
    exit 0
  fi
}

_selected_gpu_current_mode() {
  local mode=""
  local idx drv cur
  for idx in "${SELECTED_GPU_IDX[@]}"; do
    drv="${ALL_GPU_DRIVERS[$idx]}"
    if [[ "$drv" == "vfio-pci" ]]; then
      cur="vm"
    else
      cur="lxc"
    fi

    if [[ -z "$mode" ]]; then
      mode="$cur"
    elif [[ "$mode" != "$cur" ]]; then
      echo "mixed"
      return
    fi
  done
  [[ -z "$mode" ]] && mode="lxc"
  echo "$mode"
}

select_target_mode() {
  CURRENT_MODE=$(_selected_gpu_current_mode)

  if [[ "$CURRENT_MODE" == "mixed" ]]; then
    local msg idx mode_label
    msg="\n$(translate 'Mixed current mode detected in selected GPU(s).')\n\n"
    msg+="$(translate 'Please select GPU(s) that are currently in the same mode and try again.')\n\n"
    msg+="$(translate 'Selected GPU(s):')\n"
    for idx in "${SELECTED_GPU_IDX[@]}"; do
      if [[ "${ALL_GPU_DRIVERS[$idx]}" == "vfio-pci" ]]; then
        mode_label="GPU -> VM"
      else
        mode_label="GPU -> LXC"
      fi
      msg+="  •  ${ALL_GPU_NAMES[$idx]} (${ALL_GPU_PCIS[$idx]}) [${mode_label}]\n"
    done
    dialog --backtitle "ProxMenux" \
      --title "$(translate 'Mixed GPU Modes')" \
      --msgbox "$msg" 20 94
    return 2
  fi

  local menu_title menu_option_tag menu_option_desc current_mode_label current_mode_highlight
  if [[ "$CURRENT_MODE" == "vm" ]]; then
    TARGET_MODE="lxc"
    current_mode_label="GPU -> VM (VFIO passthrough mode)"
    menu_option_tag="lxc"
    menu_option_desc="$(translate 'Switch to GPU -> LXC (native driver mode)')"
  else
    TARGET_MODE="vm"
    current_mode_label="GPU -> LXC (native driver mode)"
    menu_option_tag="vm"
    menu_option_desc="$(translate 'Switch to GPU -> VM (VFIO passthrough mode)')"
  fi

  current_mode_highlight="\\Zb\\Z4${current_mode_label}\\Zn"
  menu_title="\n$(translate 'Select target mode for selected GPU(s):')\n\n$(translate 'Current mode'): ${current_mode_highlight}\n\n$(translate 'Available action'):"
  local selected
  selected=$(dialog --backtitle "ProxMenux" --colors \
    --title "$(translate 'GPU Switch Mode')" \
    --menu "$menu_title" 16 80 6 \
    "$menu_option_tag" "$menu_option_desc" \
    2>&1 >/dev/tty) || exit 0

  [[ "$selected" != "$menu_option_tag" ]] && exit 0
  return 0
}

# Return codes:
#   0 = compatible
#   1 = blocked and should exit
#   2 = blocked but user can reselect GPUs
validate_vm_mode_blocked_ids() {
  [[ "$TARGET_MODE" != "vm" ]] && return 0

  local -a blocked_lines=()
  local idx viddid name pci
  for idx in "${SELECTED_GPU_IDX[@]}"; do
    viddid="${ALL_GPU_VIDDID[$idx]}"
    name="${ALL_GPU_NAMES[$idx]}"
    pci="${ALL_GPU_PCIS[$idx]}"

    case "$viddid" in
      8086:5a84|8086:5a85)
        blocked_lines+=("  •  ${name} (${pci}) [ID: ${viddid}]")
        ;;
    esac
  done

  [[ ${#blocked_lines[@]} -eq 0 ]] && return 0

  local msg
  msg="\n\Zb\Z1$(translate 'Blocked GPU ID for VM Mode')\Zn\n\n"
  msg+="$(translate 'At least one selected GPU is blocked by policy for GPU -> VM mode due to passthrough instability risk.')\n\n"
  msg+="$(translate 'Blocked device(s):')\n"
  local line
  for line in "${blocked_lines[@]}"; do
    msg+="${line}\n"
  done
  msg+="\n$(translate 'Recommended: use GPU -> LXC mode for these devices.')\n"

  if [[ "$GPU_COUNT" -gt 1 ]]; then
    msg+="\n$(translate 'Please reselect GPU(s) and choose only compatible devices for VM mode.')"
    dialog --backtitle "ProxMenux" --colors \
      --title "$(translate 'GPU Switch Mode Blocked')" \
      --msgbox "$msg" 20 88
    return 2
  fi

  dialog --backtitle "ProxMenux" --colors \
    --title "$(translate 'GPU Switch Mode Blocked')" \
    --msgbox "$msg" 19 84
  return 1
}

select_gpus() {
  SELECTED_GPU_IDX=()
  if [[ "$GPU_COUNT" -eq 1 ]]; then
    SELECTED_GPU_IDX=(0)
    return 0
  fi

  local -a menu_items=()
  local i
  for i in "${!ALL_GPU_PCIS[@]}"; do
    menu_items+=("$i" "${ALL_GPU_NAMES[$i]} [${ALL_GPU_DRIVERS[$i]}] — ${ALL_GPU_PCIS[$i]}" "off")
  done

  local raw sel
  raw=$(dialog --backtitle "ProxMenux" \
    --title "$(translate 'Select GPU(s)')" \
    --checklist "\n$(translate 'Select one or more GPU(s) to switch mode:')" 20 96 12 \
    "${menu_items[@]}" \
    2>&1 >/dev/tty) || exit 0

  sel=$(echo "$raw" | tr -d '"')
  if [[ -z "$sel" ]]; then
    dialog --backtitle "ProxMenux" \
      --title "$(translate 'Select GPU(s)')" \
      --msgbox "\n$(translate 'No GPU selected.')" 7 52
    exit 0
  fi
  read -ra SELECTED_GPU_IDX <<< "$sel"
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

prompt_lxc_action_for_vm_mode() {
  [[ ${#LXC_AFFECTED_CTIDS[@]} -eq 0 ]] && return 0

  local running_count=0 onboot_count=0 i
  for i in "${!LXC_AFFECTED_CTIDS[@]}"; do
    [[ "${LXC_AFFECTED_RUNNING[$i]}" == "1" ]] && running_count=$((running_count + 1))
    [[ "${LXC_AFFECTED_ONBOOT[$i]}" == "1" ]] && onboot_count=$((onboot_count + 1))
  done

  local msg choice
  msg="\n$(translate 'The selected GPU(s) are used in these LXC container(s):')\n\n"
  for i in "${!LXC_AFFECTED_CTIDS[@]}"; do
    local st ob
    st="$(translate 'stopped')"; ob="onboot=0"
    [[ "${LXC_AFFECTED_RUNNING[$i]}" == "1" ]] && st="$(translate 'running')"
    [[ "${LXC_AFFECTED_ONBOOT[$i]}" == "1" ]] && ob="onboot=1"
    msg+="  •  CT ${LXC_AFFECTED_CTIDS[$i]} (${LXC_AFFECTED_NAMES[$i]}) [${st}, ${ob}]\n"
  done
  msg+="\n$(translate 'Switching to GPU -> VM mode requires exclusive VFIO binding.')\n"
  [[ "$running_count" -gt 0 ]] && msg+="\Z3$(translate 'Running containers detected'): ${running_count}\Zn\n"
  [[ "$onboot_count" -gt 0 ]] && msg+="\Z1\Zb$(translate 'Start on boot enabled'): ${onboot_count}\Zn\n"
  msg+="\n$(translate 'Choose conflict policy:')"

  choice=$(dialog --backtitle "ProxMenux" --colors \
    --title "$(translate 'LXC Conflict Policy')" \
    --default-item "2" \
    --menu "$msg" 24 80 8 \
    "1" "$(translate 'Keep GPU in LXC config (disable Start on boot)')" \
    "2" "$(translate 'Remove GPU from LXC config (keep Start on boot)')" \
    2>&1 >/dev/tty) || exit 0

  case "$choice" in
    1) LXC_ACTION="keep_gpu_disable_onboot" ;;
    2) LXC_ACTION="remove_gpu_keep_onboot" ;;
    *) exit 0 ;;
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

prompt_vm_action_for_lxc_mode() {
  [[ ${#VM_AFFECTED_IDS[@]} -eq 0 ]] && return 0

  local running_count=0 onboot_count=0 i
  for i in "${!VM_AFFECTED_IDS[@]}"; do
    [[ "${VM_AFFECTED_RUNNING[$i]}" == "1" ]] && running_count=$((running_count + 1))
    [[ "${VM_AFFECTED_ONBOOT[$i]}" == "1" ]] && onboot_count=$((onboot_count + 1))
  done

  local msg choice
  msg="\n$(translate 'The selected GPU(s) are configured in these VM(s):')\n\n"
  for i in "${!VM_AFFECTED_IDS[@]}"; do
    local st ob
    st="$(translate 'stopped')"; ob="onboot=0"
    [[ "${VM_AFFECTED_RUNNING[$i]}" == "1" ]] && st="$(translate 'running')"
    [[ "${VM_AFFECTED_ONBOOT[$i]}" == "1" ]] && ob="onboot=1"
    msg+="  •  VM ${VM_AFFECTED_IDS[$i]} (${VM_AFFECTED_NAMES[$i]}) [${st}, ${ob}]\n"
  done
  msg+="\n$(translate 'Switching to GPU -> LXC mode removes VFIO exclusivity.')\n"
  [[ "$running_count" -gt 0 ]] && msg+="\Z3$(translate 'Running VM detected'): ${running_count}\Zn\n"
  [[ "$onboot_count" -gt 0 ]] && msg+="\Z1\Zb$(translate 'Start on boot enabled'): ${onboot_count}\Zn\n"
  msg+="\n$(translate 'Choose conflict policy:')"

  choice=$(dialog --backtitle "ProxMenux" --colors \
    --title "$(translate 'VM Conflict Policy')" \
    --default-item "1" \
    --menu "$msg" 24 80 8 \
    "1" "$(translate 'Keep GPU in VM config (disable Start on boot)')" \
    "2" "$(translate 'Remove GPU from VM config (keep Start on boot)')" \
    2>&1 >/dev/tty) || exit 0

  case "$choice" in
    1) VM_ACTION="keep_gpu_disable_onboot" ;;
    2) VM_ACTION="remove_gpu_keep_onboot" ;;
    *) exit 0 ;;
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

confirm_plan() {
  local msg mode_line
  if [[ "$TARGET_MODE" == "vm" ]]; then
    mode_line="$(translate 'Target mode'): GPU -> VM (VFIO)"
  else
    mode_line="$(translate 'Target mode'): GPU -> LXC (native driver)"
  fi

  msg="\n${mode_line}\n\n$(translate 'Selected GPU(s)'):\n"
  local idx
  for idx in "${SELECTED_GPU_IDX[@]}"; do
    msg+="  •  ${ALL_GPU_NAMES[$idx]} (${ALL_GPU_PCIS[$idx]}) [${ALL_GPU_DRIVERS[$idx]}]\n"
  done
  msg+="\n$(translate 'Do you want to proceed?')"

  dialog --backtitle "ProxMenux" --colors \
    --title "$(translate 'Confirm GPU Switch Mode')" \
    --yesno "$msg" 18 88
  [[ $? -ne 0 ]] && exit 0
}

final_summary() {
  _set_title
  cat "$screen_capture"
  echo
  echo -e "${TAB}${BL}Log: ${LOG_FILE}${CL}"

  if [[ "$HOST_CONFIG_CHANGED" == "true" ]]; then
    echo -e "${TAB}${DGN}- $(translate 'Host GPU binding changed — reboot required.')${CL}"
    whiptail --title "$(translate 'Reboot Required')" \
      --yesno "$(translate 'A reboot is required to apply the new GPU mode. Do you want to restart now?')" 10 74
    if [[ $? -eq 0 ]]; then
      msg_warn "$(translate 'Rebooting the system...')"
      reboot
    else
      msg_info2 "$(translate 'Please reboot manually to complete the switch.')"
      msg_success "$(translate 'Press Enter to continue...')"
      read -r
    fi
  else
    echo -e "${TAB}${DGN}- $(translate 'No host VFIO/native binding changes were required.')${CL}"
    msg_success "$(translate 'Press Enter to continue...')"
    read -r
  fi
}

main() {
  : >"$LOG_FILE"
  : >"$screen_capture"

  detect_host_gpus
  while true; do
    select_gpus
    select_target_mode
    [[ $? -eq 2 ]] && continue
    validate_vm_mode_blocked_ids
    case $? in
      2) continue ;;
      1) exit 0 ;;
    esac
    break
  done
  confirm_plan

  clear
  _set_title
  echo

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
