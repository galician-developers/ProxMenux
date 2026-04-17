#!/usr/bin/env bash

if [[ -n "${__PROXMENUX_PCI_PASSTHROUGH_HELPERS__}" ]]; then
  return 0
fi
__PROXMENUX_PCI_PASSTHROUGH_HELPERS__=1

function _pci_is_iommu_active() {
  grep -qE 'intel_iommu=on|amd_iommu=on' /proc/cmdline 2>/dev/null || return 1
  [[ -d /sys/kernel/iommu_groups ]] || return 1
  find /sys/kernel/iommu_groups -mindepth 1 -maxdepth 1 -type d -print -quit 2>/dev/null | grep -q .
}

function _pci_next_hostpci_index() {
  local vmid="$1"
  local idx=0
  local hostpci_existing

  hostpci_existing=$(qm config "$vmid" 2>/dev/null) || return 1
  while grep -q "^hostpci${idx}:" <<< "$hostpci_existing"; do
    idx=$((idx + 1))
  done
  echo "$idx"
}

function _pci_slot_assigned_to_vm() {
  local pci_full="$1"
  local vmid="$2"
  local slot_base
  slot_base="${pci_full#0000:}"
  slot_base="${slot_base%.*}"

  qm config "$vmid" 2>/dev/null \
    | grep -qE "^hostpci[0-9]+:.*(0000:)?${slot_base}(\\.[0-7])?([,[:space:]]|$)"
}

function _pci_function_assigned_to_vm() {
  local pci_full="$1"
  local vmid="$2"
  local bdf slot func pattern
  bdf="${pci_full#0000:}"
  slot="${bdf%.*}"
  func="${bdf##*.}"

  if [[ "$func" == "0" ]]; then
    pattern="^hostpci[0-9]+:.*(0000:)?(${bdf}|${slot})([,:[:space:]]|$)"
  else
    pattern="^hostpci[0-9]+:.*(0000:)?${bdf}([,[:space:]]|$)"
  fi

  qm config "$vmid" 2>/dev/null | grep -qE "$pattern"
}
