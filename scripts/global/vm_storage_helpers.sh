#!/usr/bin/env bash

if [[ -n "${__PROXMENUX_VM_STORAGE_HELPERS__}" ]]; then
  return 0
fi
__PROXMENUX_VM_STORAGE_HELPERS__=1

function _array_contains() {
  local needle="$1"
  shift
  local item
  for item in "$@"; do
    [[ "$item" == "$needle" ]] && return 0
  done
  return 1
}

function _refresh_host_storage_cache() {
  MOUNTED_DISKS=$(lsblk -ln -o NAME,MOUNTPOINT | awk '$2!="" {print "/dev/" $1}')
  SWAP_DISKS=$(swapon --noheadings --raw --show=NAME 2>/dev/null)
  LVM_DEVICES=$(pvs --noheadings -o pv_name 2> >(grep -v 'File descriptor .* leaked') | xargs -r -n1 readlink -f | sort -u)
  CONFIG_DATA=$(grep -vE '^\s*#' /etc/pve/qemu-server/*.conf /etc/pve/lxc/*.conf 2>/dev/null)

  ZFS_DISKS=""
  local zfs_raw entry path base_disk
  zfs_raw=$(zpool list -v -H 2>/dev/null | awk '{print $1}' | grep -v '^NAME$' | grep -v '^-' | grep -v '^mirror')
  for entry in $zfs_raw; do
    path=""
    if [[ "$entry" == wwn-* || "$entry" == ata-* ]]; then
      [[ -e "/dev/disk/by-id/$entry" ]] && path=$(readlink -f "/dev/disk/by-id/$entry")
    elif [[ "$entry" == /dev/* ]]; then
      path="$entry"
    fi
    if [[ -n "$path" ]]; then
      base_disk=$(lsblk -no PKNAME "$path" 2>/dev/null)
      [[ -n "$base_disk" ]] && ZFS_DISKS+="/dev/$base_disk"$'\n'
    fi
  done
  ZFS_DISKS=$(echo "$ZFS_DISKS" | sort -u)
}

function _disk_is_host_system_used() {
  local disk="$1"
  local disk_real part fstype part_path
  DISK_USAGE_REASON=""

  while read -r part fstype; do
    [[ -z "$part" ]] && continue
    part_path="/dev/$part"

    if grep -qFx "$part_path" <<< "$MOUNTED_DISKS"; then
      DISK_USAGE_REASON="$(translate "Mounted filesystem detected") ($part_path)"
      return 0
    fi
    if grep -qFx "$part_path" <<< "$SWAP_DISKS"; then
      DISK_USAGE_REASON="$(translate "Swap partition detected") ($part_path)"
      return 0
    fi
    case "$fstype" in
      zfs_member)
        DISK_USAGE_REASON="$(translate "ZFS member detected") ($part_path)"
        return 0
        ;;
      linux_raid_member)
        DISK_USAGE_REASON="$(translate "RAID member detected") ($part_path)"
        return 0
        ;;
      LVM2_member)
        DISK_USAGE_REASON="$(translate "LVM physical volume detected") ($part_path)"
        return 0
        ;;
    esac
  done < <(lsblk -ln -o NAME,FSTYPE "$disk" 2>/dev/null)

  disk_real=$(readlink -f "$disk" 2>/dev/null)
  if [[ -n "$disk_real" && -n "$LVM_DEVICES" ]] && grep -qFx "$disk_real" <<< "$LVM_DEVICES"; then
    DISK_USAGE_REASON="$(translate "Disk is part of host LVM")"
    return 0
  fi
  if [[ -n "$ZFS_DISKS" && "$ZFS_DISKS" == *"$disk"* ]]; then
    DISK_USAGE_REASON="$(translate "Disk is part of a host ZFS pool")"
    return 0
  fi
  return 1
}

function _disk_used_in_guest_configs() {
  local disk="$1"
  local real_path
  real_path=$(readlink -f "$disk" 2>/dev/null)

  if [[ -n "$real_path" ]] && grep -Fq "$real_path" <<< "$CONFIG_DATA"; then
    return 0
  fi

  local symlink
  for symlink in /dev/disk/by-id/*; do
    [[ -e "$symlink" ]] || continue
    if [[ "$(readlink -f "$symlink")" == "$real_path" ]] && grep -Fq "$symlink" <<< "$CONFIG_DATA"; then
      return 0
    fi
  done
  return 1
}

function _controller_block_devices() {
  local pci_full="$1"
  local pci_root="/sys/bus/pci/devices/$pci_full"
  [[ -d "$pci_root" ]] || return 0

  local sys_block dev_name cur base
  # Walk /sys/block and resolve each block device back to its ancestor PCI device.
  # This avoids unbounded recursive scans while still handling NVMe/SATA paths.
  for sys_block in /sys/block/*; do
    [[ -e "$sys_block/device" ]] || continue
    dev_name=$(basename "$sys_block")
    [[ -b "/dev/$dev_name" ]] || continue

    cur=$(readlink -f "$sys_block/device" 2>/dev/null)
    [[ -n "$cur" ]] || continue

    while [[ "$cur" != "/" ]]; do
      base=$(basename "$cur")
      if [[ "$base" == "$pci_full" ]]; then
        echo "/dev/$dev_name"
        break
      fi
      cur=$(dirname "$cur")
    done
  done
}

function _vm_is_q35() {
  local vmid="$1"
  local machine_line
  machine_line=$(qm config "$vmid" 2>/dev/null | awk -F': ' '/^machine:/ {print $2}')
  [[ "$machine_line" == *q35* ]]
}

function _vm_storage_enable_iommu_cmdline() {
  local cpu_vendor iommu_param
  cpu_vendor=$(grep -m1 "vendor_id" /proc/cpuinfo 2>/dev/null | awk '{print $3}')

  if [[ "$cpu_vendor" == "GenuineIntel" ]]; then
    iommu_param="intel_iommu=on"
  elif [[ "$cpu_vendor" == "AuthenticAMD" ]]; then
    iommu_param="amd_iommu=on"
  else
    return 1
  fi

  local cmdline_file="/etc/kernel/cmdline"
  local grub_file="/etc/default/grub"

  if [[ -f "$cmdline_file" ]] && grep -qE 'root=ZFS=|root=ZFS/' "$cmdline_file" 2>/dev/null; then
    if ! grep -q "$iommu_param" "$cmdline_file"; then
      cp "$cmdline_file" "${cmdline_file}.bak.$(date +%Y%m%d_%H%M%S)"
      sed -i "s|\\s*$| ${iommu_param} iommu=pt|" "$cmdline_file"
      proxmox-boot-tool refresh >/dev/null 2>&1 || true
    fi
  elif [[ -f "$grub_file" ]]; then
    if ! grep -q "$iommu_param" "$grub_file"; then
      cp "$grub_file" "${grub_file}.bak.$(date +%Y%m%d_%H%M%S)"
      sed -i "/GRUB_CMDLINE_LINUX_DEFAULT=/ s|\"$| ${iommu_param} iommu=pt\"|" "$grub_file"
      update-grub >/dev/null 2>&1 || true
    fi
  else
    return 1
  fi

  return 0
}

function _vm_storage_ensure_iommu_or_offer() {
  if declare -F _pci_is_iommu_active >/dev/null 2>&1 && _pci_is_iommu_active; then
    return 0
  fi

  if grep -qE 'intel_iommu=on|amd_iommu=on' /proc/cmdline 2>/dev/null && \
     [[ -d /sys/kernel/iommu_groups ]] && \
     [[ -n "$(ls /sys/kernel/iommu_groups/ 2>/dev/null)" ]]; then
    return 0
  fi

  local prompt
  prompt="$(translate "IOMMU is not active on this system.")\n\n"
  prompt+="$(translate "Controller/NVMe passthrough to VMs requires IOMMU enabled in BIOS/UEFI and kernel.")\n\n"
  prompt+="$(translate "Do you want to enable IOMMU now?")\n\n"
  prompt+="$(translate "A host reboot is required after this change.")"

  whiptail --title "IOMMU Required" --yesno "$prompt" 14 78
  [[ $? -ne 0 ]] && return 1

  if ! _vm_storage_enable_iommu_cmdline; then
    whiptail --title "IOMMU" --msgbox \
"$(translate "Failed to configure IOMMU automatically.")\n\n$(translate "Please configure it manually and reboot.")" \
      10 72
    return 1
  fi

  local reboot_policy="${VM_STORAGE_IOMMU_REBOOT_POLICY:-ask_now}"
  if [[ "$reboot_policy" == "defer" ]]; then
    VM_STORAGE_IOMMU_PENDING_REBOOT=1
    export VM_STORAGE_IOMMU_PENDING_REBOOT
    whiptail --title "Reboot Required" --msgbox \
"$(translate "IOMMU configured successfully.")\n\n$(translate "Continue the VM wizard and reboot the host at the end.")\n\n$(translate "Controller/NVMe passthrough will be available after reboot.")" \
      12 78
    return 1
  fi

  if whiptail --title "Reboot Required" --yesno \
"$(translate "IOMMU configured successfully.")\n\n$(translate "Do you want to reboot now?")" 10 68; then
    reboot
  else
    whiptail --title "Reboot Required" --msgbox \
"$(translate "Please reboot manually and run the passthrough step again.")" 9 68
  fi

  return 1
}

function _vm_storage_confirm_controller_passthrough_risk() {
  local vmid="${1:-}"
  local vm_name="${2:-}"
  local title="${3:-Controller + NVMe}"
  local vm_label=""
  if [[ -n "$vmid" ]]; then
    vm_label="$vmid"
    [[ -n "$vm_name" ]] && vm_label="${vm_label} (${vm_name})"
  fi

  local msg
  msg="$(translate "Important compatibility notice")\n\n"
  msg+="$(translate "Not all motherboards support physical Controller/NVMe passthrough to VMs reliably, especially systems with old platforms or limited BIOS/UEFI firmware.")\n\n"
  msg+="$(translate "On some systems, the VM may fail to start or the host may freeze when the VM boots.")\n\n"

  local reinforce_limited_firmware="no"
  local bios_date bios_year current_year cpu_model
  bios_date=$(cat /sys/class/dmi/id/bios_date 2>/dev/null)
  bios_year=$(echo "$bios_date" | grep -oE '[0-9]{4}' | tail -n1)
  current_year=$(date +%Y 2>/dev/null)
  if [[ -n "$bios_year" && -n "$current_year" ]]; then
    if (( current_year - bios_year >= 7 )); then
      reinforce_limited_firmware="yes"
    fi
  fi
  cpu_model=$(grep -m1 'model name' /proc/cpuinfo 2>/dev/null | cut -d: -f2- | xargs)
  if echo "$cpu_model" | grep -qiE 'J4[0-9]{3}|J3[0-9]{3}|N4[0-9]{3}|N3[0-9]{3}|Apollo Lake'; then
    reinforce_limited_firmware="yes"
  fi

  if [[ "$reinforce_limited_firmware" == "yes" ]]; then
    msg+="$(translate "Detected risk factor: this host may use an older or limited firmware platform, which increases passthrough instability risk.")\n\n"
  fi

  if [[ -n "$vm_label" ]]; then
    msg+="$(translate "Target VM"): ${vm_label}\n\n"
  fi
  msg+="$(translate "If this happens after assignment"):\n"
  msg+="  - $(translate "Power cycle the host if it is frozen.")\n"
  msg+="  - $(translate "Remove the hostpci controller/NVMe entries from the VM config file.")\n"
  msg+="    /etc/pve/qemu-server/${vmid:-<VMID>}.conf\n"
  msg+="  - $(translate "Start the VM again without that passthrough device.")\n\n"
  msg+="$(translate "Do you want to continue with this assignment?")"

  whiptail --title "$title" --yesno "$msg" 21 96
}

function _shorten_text() {
  local text="$1"
  local max_len="${2:-42}"
  [[ -z "$text" ]] && { echo ""; return; }
  if (( ${#text} > max_len )); then
    echo "${text:0:$((max_len-3))}..."
  else
    echo "$text"
  fi
}

function _pci_slot_base() {
  local pci_full="$1"
  local slot
  slot="${pci_full#0000:}"
  slot="${slot%.*}"
  echo "$slot"
}

function _vm_status_is_running() {
  local vmid="$1"
  qm status "$vmid" 2>/dev/null | grep -q "status: running"
}

function _vm_onboot_is_enabled() {
  local vmid="$1"
  qm config "$vmid" 2>/dev/null | grep -qE '^onboot:\s*1'
}

function _vm_name_by_id() {
  local vmid="$1"
  local conf="/etc/pve/qemu-server/${vmid}.conf"
  local vm_name
  vm_name=$(awk '/^name:/ {print $2}' "$conf" 2>/dev/null)
  [[ -z "$vm_name" ]] && vm_name="VM-${vmid}"
  echo "$vm_name"
}

function _vm_has_pci_slot() {
  local vmid="$1"
  local slot_base="$2"
  local conf="/etc/pve/qemu-server/${vmid}.conf"
  [[ -f "$conf" ]] || return 1
  grep -qE "^hostpci[0-9]+:.*(0000:)?${slot_base}(\\.[0-7])?([,[:space:]]|$)" "$conf"
}

function _pci_assigned_vm_ids() {
  local pci_full="$1"
  local exclude_vmid="${2:-}"
  local slot_base conf vmid
  slot_base=$(_pci_slot_base "$pci_full")

  for conf in /etc/pve/qemu-server/*.conf; do
    [[ -f "$conf" ]] || continue
    vmid=$(basename "$conf" .conf)
    [[ -n "$exclude_vmid" && "$vmid" == "$exclude_vmid" ]] && continue
    if grep -qE "^hostpci[0-9]+:.*(0000:)?${slot_base}(\\.[0-7])?([,[:space:]]|$)" "$conf"; then
      echo "$vmid"
    fi
  done
}

function _remove_pci_slot_from_vm_config() {
  local vmid="$1"
  local slot_base="$2"
  local conf="/etc/pve/qemu-server/${vmid}.conf"
  [[ -f "$conf" ]] || return 1
  local tmpf
  tmpf=$(mktemp)
  awk -v slot="$slot_base" '
    $0 ~ "^hostpci[0-9]+:.*(0000:)?" slot "(\\.[0-7])?([,[:space:]]|$)" {next}
    {print}
  ' "$conf" > "$tmpf" && cat "$tmpf" > "$conf"
  rm -f "$tmpf"
}

function _pci_assigned_vm_summary() {
  local pci_full="$1"
  local slot_base conf vmid vm_name running onboot
  local -a refs=()
  local running_count=0 onboot_count=0

  slot_base="${pci_full#0000:}"
  slot_base="${slot_base%.*}"

  for conf in /etc/pve/qemu-server/*.conf; do
    [[ -f "$conf" ]] || continue

    if ! grep -qE "^hostpci[0-9]+:.*(0000:)?${slot_base}(\\.[0-7])?([,[:space:]]|$)" "$conf"; then
      continue
    fi

    vmid=$(basename "$conf" .conf)
    vm_name=$(awk '/^name:/ {print $2}' "$conf" 2>/dev/null)
    [[ -z "$vm_name" ]] && vm_name="VM-${vmid}"

    if qm status "$vmid" 2>/dev/null | grep -q "status: running"; then
      running="running"
      running_count=$((running_count + 1))
    else
      running="stopped"
    fi

    if grep -qE "^onboot:\s*1" "$conf" 2>/dev/null; then
      onboot="1"
      onboot_count=$((onboot_count + 1))
    else
      onboot="0"
    fi

    refs+=("${vmid}[${running},onboot=${onboot}]")
  done

  [[ ${#refs[@]} -eq 0 ]] && return 1

  local joined summary
  joined=$(IFS=', '; echo "${refs[*]}")
  summary="$(translate "Assigned to VM(s)"): ${joined}"
  if [[ "$running_count" -gt 0 ]]; then
    summary+=" ($(translate "running"): ${running_count})"
  fi
  if [[ "$onboot_count" -gt 0 ]]; then
    summary+=", onboot=1: ${onboot_count}"
  fi
  echo "$summary"
  return 0
}
