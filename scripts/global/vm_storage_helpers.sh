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
