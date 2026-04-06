#!/bin/bash
# ==========================================================
# ProxMenux - Add Controller or NVMe PCIe to VM
# ==========================================================
# Author      : MacRimi
# Copyright   : (c) 2024 MacRimi
# License     : GPL-3.0
# Version     : 1.0
# Last Updated: 06/04/2026
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

LOG_FILE="/tmp/proxmenux_add_controller_nvme_vm.log"
screen_capture="/tmp/proxmenux_add_controller_nvme_vm_screen_$$.txt"

if [[ -f "$UTILS_FILE" ]]; then
  source "$UTILS_FILE"
fi
if [[ -f "$LOCAL_SCRIPTS_LOCAL/global/vm_storage_helpers.sh" ]]; then
  source "$LOCAL_SCRIPTS_LOCAL/global/vm_storage_helpers.sh"
elif [[ -f "$LOCAL_SCRIPTS_DEFAULT/global/vm_storage_helpers.sh" ]]; then
  source "$LOCAL_SCRIPTS_DEFAULT/global/vm_storage_helpers.sh"
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

SELECTED_VMID=""
SELECTED_VM_NAME=""
declare -a SELECTED_CONTROLLER_PCIS=()

set_title() {
  show_proxmenux_logo
  msg_title "$(translate "Add Controller or NVMe PCIe to VM")"
}

enable_iommu_cmdline() {
  local cpu_vendor iommu_param
  cpu_vendor=$(grep -m1 "vendor_id" /proc/cpuinfo 2>/dev/null | awk '{print $3}')

  if [[ "$cpu_vendor" == "GenuineIntel" ]]; then
    iommu_param="intel_iommu=on"
    msg_info "$(translate "Intel CPU detected")"
  elif [[ "$cpu_vendor" == "AuthenticAMD" ]]; then
    iommu_param="amd_iommu=on"
    msg_info "$(translate "AMD CPU detected")"
  else
    msg_error "$(translate "Unknown CPU vendor. Cannot determine IOMMU parameter.")"
    return 1
  fi

  local cmdline_file="/etc/kernel/cmdline"
  local grub_file="/etc/default/grub"

  if [[ -f "$cmdline_file" ]] && grep -qE 'root=ZFS=|root=ZFS/' "$cmdline_file" 2>/dev/null; then
    if ! grep -q "$iommu_param" "$cmdline_file"; then
      cp "$cmdline_file" "${cmdline_file}.bak.$(date +%Y%m%d_%H%M%S)"
      sed -i "s|\\s*$| ${iommu_param} iommu=pt|" "$cmdline_file"
      proxmox-boot-tool refresh >/dev/null 2>&1 || true
      msg_ok "$(translate "IOMMU parameters added to /etc/kernel/cmdline")"
    else
      msg_ok "$(translate "IOMMU already configured in /etc/kernel/cmdline")"
    fi
  elif [[ -f "$grub_file" ]]; then
    if ! grep -q "$iommu_param" "$grub_file"; then
      cp "$grub_file" "${grub_file}.bak.$(date +%Y%m%d_%H%M%S)"
      sed -i "/GRUB_CMDLINE_LINUX_DEFAULT=/ s|\"$| ${iommu_param} iommu=pt\"|" "$grub_file"
      update-grub >/dev/null 2>&1 || true
      msg_ok "$(translate "IOMMU parameters added to GRUB")"
    else
      msg_ok "$(translate "IOMMU already configured in GRUB")"
    fi
  else
    msg_error "$(translate "Neither /etc/kernel/cmdline nor /etc/default/grub found.")"
    return 1
  fi
}

check_iommu_or_offer_enable() {
  if declare -F _pci_is_iommu_active >/dev/null 2>&1 && _pci_is_iommu_active; then
    return 0
  fi

  if grep -qE 'intel_iommu=on|amd_iommu=on' /proc/cmdline 2>/dev/null && \
     [[ -d /sys/kernel/iommu_groups ]] && \
     [[ -n "$(ls /sys/kernel/iommu_groups/ 2>/dev/null)" ]]; then
    return 0
  fi

  local msg
  msg="\n$(translate "IOMMU is not active on this system.")\n\n"
  msg+="$(translate "Controller/NVMe passthrough to VMs requires IOMMU to be enabled in the kernel.")\n\n"
  msg+="$(translate "Do you want to enable IOMMU now?")\n\n"
  msg+="$(translate "Note: A system reboot will be required after enabling IOMMU.")\n"
  msg+="$(translate "You must run this option again after rebooting.")"

  dialog --backtitle "ProxMenux" \
    --title "$(translate "IOMMU Required")" \
    --yesno "$msg" 15 74
  local response=$?
  clear

  [[ $response -ne 0 ]] && return 1

  set_title
  msg_title "$(translate "Enabling IOMMU")"
  echo
  if ! enable_iommu_cmdline; then
    echo
    msg_error "$(translate "Failed to configure IOMMU automatically.")"
    msg_success "$(translate "Press Enter to continue...")"
    read -r
    return 1
  fi

  echo
  msg_success "$(translate "IOMMU configured. Reboot required before using Controller/NVMe passthrough.")"
  echo
  if whiptail --title "$(translate "Reboot Required")" \
    --yesno "$(translate "Do you want to reboot now?")" 10 64; then
    msg_warn "$(translate "Rebooting the system...")"
    reboot
  else
    msg_info2 "$(translate "Please reboot manually and run this option again.")"
    msg_success "$(translate "Press Enter to continue...")"
    read -r
  fi
  return 1
}

select_target_vm() {
  local -a vm_menu=()
  local line vmid vmname vmstatus vm_machine status_label

  while IFS= read -r line; do
    vmid=$(awk '{print $1}' <<< "$line")
    vmname=$(awk '{print $2}' <<< "$line")
    vmstatus=$(awk '{print $3}' <<< "$line")
    [[ -z "$vmid" || "$vmid" == "VMID" ]] && continue

    vm_machine=$(qm config "$vmid" 2>/dev/null | awk -F': ' '/^machine:/ {print $2}')
    [[ -z "$vm_machine" ]] && vm_machine="unknown"
    status_label="${vmstatus}, ${vm_machine}"
    vm_menu+=("$vmid" "${vmname} [${status_label}]")
  done < <(qm list 2>/dev/null)
  if [[ ${#vm_menu[@]} -eq 0 ]]; then
    dialog --backtitle "ProxMenux" \
      --title "$(translate "Add Controller or NVMe PCIe to VM")" \
      --msgbox "\n$(translate "No VMs available on this host.")" 8 64
    return 1
  fi

  SELECTED_VMID=$(dialog --backtitle "ProxMenux" \
    --title "$(translate "Select VM")" \
    --menu "\n$(translate "Select the target VM for PCI passthrough:")" 20 82 12 \
    "${vm_menu[@]}" \
    2>&1 >/dev/tty) || return 1

  SELECTED_VM_NAME=$(qm config "$SELECTED_VMID" 2>/dev/null | awk '/^name:/ {print $2}')
  [[ -z "$SELECTED_VM_NAME" ]] && SELECTED_VM_NAME="VM-${SELECTED_VMID}"
  return 0
}

validate_vm_requirements() {
  local status
  status=$(qm status "$SELECTED_VMID" 2>/dev/null | awk '{print $2}')
  if [[ "$status" == "running" ]]; then
    dialog --backtitle "ProxMenux" \
      --title "$(translate "VM Must Be Stopped")" \
      --msgbox "\n$(translate "The selected VM is running.")\n\n$(translate "Stop it first and run this option again.")" 10 72
    return 1
  fi

  if ! _vm_is_q35 "$SELECTED_VMID"; then
    dialog --backtitle "ProxMenux" --colors \
      --title "$(translate "Incompatible Machine Type")" \
      --msgbox "\n\Zb\Z1$(translate "Controller/NVMe passthrough requires machine type q35.")\Zn\n\n$(translate "Selected VM"): ${SELECTED_VM_NAME} (${SELECTED_VMID})\n\n$(translate "Edit the VM machine type to q35 and try again.")" 12 80
    return 1
  fi

  check_iommu_or_offer_enable || return 1

  return 0
}

select_controller_nvme() {
  _refresh_host_storage_cache

  local -a menu_items=()
  local blocked_report=""
  local pci_path pci_full class_hex name controller_desc disk state slot_base
  local -a controller_disks=()
  local safe_count=0 blocked_count=0 hidden_target_count=0

  while IFS= read -r pci_path; do
    pci_full=$(basename "$pci_path")
    class_hex=$(cat "$pci_path/class" 2>/dev/null | sed 's/^0x//')
    [[ -z "$class_hex" ]] && continue
    [[ "${class_hex:0:2}" != "01" ]] && continue
    slot_base=$(_pci_slot_base "$pci_full")

    # Already attached to target VM: hide from selection.
    if _vm_has_pci_slot "$SELECTED_VMID" "$slot_base"; then
      hidden_target_count=$((hidden_target_count + 1))
      continue
    fi

    name=$(lspci -nn -s "${pci_full#0000:}" 2>/dev/null | sed 's/^[^ ]* //')
    [[ -z "$name" ]] && name="$(translate "Unknown storage controller")"

    controller_disks=()
    while IFS= read -r disk; do
      [[ -z "$disk" ]] && continue
      _array_contains "$disk" "${controller_disks[@]}" || controller_disks+=("$disk")
    done < <(_controller_block_devices "$pci_full")

    local -a blocked_reasons=()
    for disk in "${controller_disks[@]}"; do
      if _disk_is_host_system_used "$disk"; then
        blocked_reasons+=("${disk} (${DISK_USAGE_REASON})")
      elif _disk_used_in_guest_configs "$disk"; then
        blocked_reasons+=("${disk} ($(translate "In use by VM/LXC config"))")
      fi
    done

    if [[ ${#blocked_reasons[@]} -gt 0 ]]; then
      blocked_count=$((blocked_count + 1))
      blocked_report+="------------------------------------------------------------\n"
      blocked_report+="PCI: ${pci_full}\n"
      blocked_report+="Name: ${name}\n"
      blocked_report+="$(translate "Blocked because protected or in-use disks are attached"):\n"
      local reason
      for reason in "${blocked_reasons[@]}"; do
        blocked_report+="  - ${reason}\n"
      done
      blocked_report+="\n"
      continue
    fi

    local short_name
    short_name=$(_shorten_text "$name" 42)

    local assigned_suffix=""
    if [[ -n "$(_pci_assigned_vm_ids "$pci_full" "$SELECTED_VMID" 2>/dev/null | head -1)" ]]; then
      assigned_suffix=" | $(translate "Assigned to VM")"
    fi

    if [[ ${#controller_disks[@]} -gt 0 ]]; then
      controller_desc="$(printf "%-42s [%s: %d]" "$short_name" "$(translate "attached disks")" "${#controller_disks[@]}")"
    else
      controller_desc="$(printf "%-42s [%s]" "$short_name" "$(translate "No attached disks")")"
    fi
    controller_desc+="${assigned_suffix}"

    state="off"
    menu_items+=("$pci_full" "$controller_desc" "$state")
    safe_count=$((safe_count + 1))
  done < <(ls -d /sys/bus/pci/devices/* 2>/dev/null | sort)

  if [[ "$safe_count" -eq 0 ]]; then
    local msg
    if [[ "$hidden_target_count" -gt 0 && "$blocked_count" -eq 0 ]]; then
      msg="$(translate "All detected controllers/NVMe are already present in the selected VM.")\n\n$(translate "No additional device needs to be added.")"
    else
      msg="$(translate "No safe controllers/NVMe devices are available for passthrough.")\n\n"
    fi
    if [[ "$blocked_count" -gt 0 ]]; then
      msg+="$(translate "Detected controllers blocked for safety:")\n\n${blocked_report}"
    fi
    dialog --backtitle "ProxMenux" \
      --title "$(translate "Controller + NVMe")" \
      --msgbox "$msg" 22 100
    return 1
  fi

  if [[ "$blocked_count" -gt 0 ]]; then
    dialog --backtitle "ProxMenux" \
      --title "$(translate "Controller + NVMe")" \
      --msgbox "$(translate "Some controllers were hidden because they have host system disks attached.")\n\n${blocked_report}" 22 100
  fi

  local raw selected
  raw=$(dialog --backtitle "ProxMenux" \
    --title "$(translate "Controller + NVMe")" \
    --checklist "\n$(translate "Select controllers/NVMe to passthrough (safe devices only):")\n\n$(translate "Only safe devices are shown in this list.")" 20 96 12 \
    "${menu_items[@]}" \
    2>&1 >/dev/tty) || return 1

  selected=$(echo "$raw" | tr -d '"')
  SELECTED_CONTROLLER_PCIS=()
  local pci
  for pci in $selected; do
    _array_contains "$pci" "${SELECTED_CONTROLLER_PCIS[@]}" || SELECTED_CONTROLLER_PCIS+=("$pci")
  done

  if [[ ${#SELECTED_CONTROLLER_PCIS[@]} -eq 0 ]]; then
    dialog --backtitle "ProxMenux" \
      --title "$(translate "Controller + NVMe")" \
      --msgbox "\n$(translate "No controller/NVMe selected.")" 8 62
    return 1
  fi

  if declare -F _vm_storage_confirm_controller_passthrough_risk >/dev/null 2>&1; then
    if ! _vm_storage_confirm_controller_passthrough_risk "$SELECTED_VMID" "$SELECTED_VM_NAME" "$(translate "Controller + NVMe")"; then
      return 1
    fi
  fi

  return 0
}

confirm_summary() {
  local msg
  msg="\n$(translate "The following devices will be added to VM") ${SELECTED_VMID} (${SELECTED_VM_NAME}):\n\n"
  local pci info
  for pci in "${SELECTED_CONTROLLER_PCIS[@]}"; do
    info=$(lspci -nn -s "${pci#0000:}" 2>/dev/null | sed 's/^[^ ]* //')
    msg+="  - ${pci}${info:+ (${info})}\n"
  done
  msg+="\n$(translate "Do you want to continue?")"

  dialog --backtitle "ProxMenux" --colors \
    --title "$(translate "Confirm Controller + NVMe Assignment")" \
    --yesno "$msg" 18 90
  [[ $? -ne 0 ]] && return 1
  return 0
}

prompt_controller_conflict_policy() {
  local pci="$1"
  shift
  local -a source_vms=("$@")
  local msg vmid vm_name st ob
  msg="$(translate "Selected device is already assigned to other VM(s):")\n\n"
  for vmid in "${source_vms[@]}"; do
    vm_name=$(_vm_name_by_id "$vmid")
    st="stopped"; _vm_status_is_running "$vmid" && st="running"
    ob="0"; _vm_onboot_is_enabled "$vmid" && ob="1"
    msg+="  - VM ${vmid} (${vm_name}) [${st}, onboot=${ob}]\n"
  done
  msg+="\n$(translate "Choose action for this controller/NVMe:")"

  local choice
  choice=$(whiptail --title "$(translate "Controller/NVMe Conflict Policy")" --menu "$msg" 22 96 10 \
    "1" "$(translate "Keep in source VM(s) + disable onboot + add to target VM")" \
    "2" "$(translate "Move to target VM (remove from source VM config)")" \
    "3" "$(translate "Skip this device")" \
    3>&1 1>&2 2>&3) || { echo "skip"; return; }

  case "$choice" in
    1) echo "keep_disable_onboot" ;;
    2) echo "move_remove_source" ;;
    *) echo "skip" ;;
  esac
}

apply_assignment() {
  : >"$LOG_FILE"
  set_title
  echo

  msg_info "$(translate "Applying Controller/NVMe passthrough to VM") ${SELECTED_VMID}..."
  msg_ok "$(translate "Target VM validated") (${SELECTED_VM_NAME} / ${SELECTED_VMID})"
  msg_ok "$(translate "Selected devices"): ${#SELECTED_CONTROLLER_PCIS[@]}"

  local hostpci_idx=0
  msg_info "$(translate "Calculating next available hostpci slot...")"
  if declare -F _pci_next_hostpci_index >/dev/null 2>&1; then
    hostpci_idx=$(_pci_next_hostpci_index "$SELECTED_VMID" 2>/dev/null || echo 0)
  else
    local hostpci_existing
    hostpci_existing=$(qm config "$SELECTED_VMID" 2>/dev/null)
    while grep -q "^hostpci${hostpci_idx}:" <<< "$hostpci_existing"; do
      hostpci_idx=$((hostpci_idx + 1))
    done
  fi
  msg_ok "$(translate "Next available hostpci slot"): hostpci${hostpci_idx}"

  local pci bdf assigned_count=0
  local need_hook_sync=false
  for pci in "${SELECTED_CONTROLLER_PCIS[@]}"; do
    bdf="${pci#0000:}"
    if declare -F _pci_function_assigned_to_vm >/dev/null 2>&1; then
      if _pci_function_assigned_to_vm "$pci" "$SELECTED_VMID"; then
        msg_warn "$(translate "Controller/NVMe already present in VM config") ($pci)"
        continue
      fi
    elif qm config "$SELECTED_VMID" 2>/dev/null | grep -qE "^hostpci[0-9]+:.*(0000:)?${bdf}([,[:space:]]|$)"; then
      msg_warn "$(translate "Controller/NVMe already present in VM config") ($pci)"
      continue
    fi

    local -a source_vms=()
    mapfile -t source_vms < <(_pci_assigned_vm_ids "$pci" "$SELECTED_VMID" 2>/dev/null)
    if [[ ${#source_vms[@]} -gt 0 ]]; then
      local has_running=false vmid action slot_base
      for vmid in "${source_vms[@]}"; do
        if _vm_status_is_running "$vmid"; then
          has_running=true
          msg_warn "$(translate "Controller/NVMe is in use by running VM") ${vmid} ($(translate "stop source VM first"))"
        fi
      done

      if $has_running; then
        continue
      fi

      action=$(prompt_controller_conflict_policy "$pci" "${source_vms[@]}")
      case "$action" in
        keep_disable_onboot)
          for vmid in "${source_vms[@]}"; do
            if _vm_onboot_is_enabled "$vmid"; then
              if qm set "$vmid" -onboot 0 >>"$LOG_FILE" 2>&1; then
                msg_warn "$(translate "Start on boot disabled for VM") ${vmid}"
              fi
            fi
          done
          need_hook_sync=true
          ;;
        move_remove_source)
          slot_base=$(_pci_slot_base "$pci")
          for vmid in "${source_vms[@]}"; do
            if _remove_pci_slot_from_vm_config "$vmid" "$slot_base"; then
              msg_ok "$(translate "Controller/NVMe removed from source VM") ${vmid} (${pci})"
            fi
          done
          ;;
        *)
          msg_info2 "$(translate "Skipped device"): ${pci}"
          continue
          ;;
      esac
    fi

    if qm set "$SELECTED_VMID" --hostpci${hostpci_idx} "${pci},pcie=1" >>"$LOG_FILE" 2>&1; then
      msg_ok "$(translate "Controller/NVMe assigned") (hostpci${hostpci_idx} -> ${pci})"
      assigned_count=$((assigned_count + 1))
      hostpci_idx=$((hostpci_idx + 1))
    else
      msg_error "$(translate "Failed to assign Controller/NVMe") (${pci})"
    fi
  done

  if $need_hook_sync && declare -F sync_proxmenux_gpu_guard_hooks >/dev/null 2>&1; then
    ensure_proxmenux_gpu_guard_hookscript
    sync_proxmenux_gpu_guard_hooks
    msg_ok "$(translate "VM hook guard synced for shared controller/NVMe protection")"
  fi

  echo
  echo -e "${TAB}${BL}Log: ${LOG_FILE}${CL}"

  if [[ "$assigned_count" -gt 0 ]]; then
    msg_success "$(translate "Completed. Controller/NVMe passthrough configured for VM") ${SELECTED_VMID}."
  else
    msg_warn "$(translate "No new Controller/NVMe entries were added.")"
  fi
  msg_success "$(translate "Press Enter to continue...")"
  read -r
}

main() {
  select_target_vm || exit 0
  validate_vm_requirements || exit 0
  select_controller_nvme || exit 0
  confirm_summary || exit 0
  clear
  apply_assignment
}

main "$@"
