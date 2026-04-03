#!/bin/bash
# ==========================================================
# ProxMenux - GPU Passthrough to Virtual Machine (VM)
# ==========================================================
# Author      : MacRimi
# Copyright   : (c) 2024 MacRimi
# License     : GPL-3.0
# Version     : 1.0
# Last Updated: 03/04/2026
# ==========================================================
# Description:
# Automates full GPU passthrough (VFIO) from Proxmox host to a VM.
# Supports Intel iGPU, AMD and NVIDIA GPUs.
#
# Features:
#  - IOMMU detection and activation offer
#  - Multi-GPU selection menu
#  - IOMMU group analysis (all group devices passed together)
#  - Single-GPU warning (host loses physical video output)
#  - Switch mode: detects GPU used in LXC or another VM
#  - AMD ROM dump via sysfs
#  - Idempotent host config (modules, vfio.conf, blacklist)
#  - VM config: hostpci entries, NVIDIA KVM hiding
# ==========================================================

LOCAL_SCRIPTS="/usr/local/share/proxmenux/scripts"
BASE_DIR="/usr/local/share/proxmenux"
UTILS_FILE="$BASE_DIR/utils.sh"
LOG_FILE="/tmp/add_gpu_vm.log"
screen_capture="/tmp/proxmenux_add_gpu_vm_screen_$$.txt"

if [[ -f "$UTILS_FILE" ]]; then
    source "$UTILS_FILE"
fi

load_language
initialize_cache

# ==========================================================
# Global state
# ==========================================================
declare -a ALL_GPU_PCIS=()       # "0000:01:00.0"
declare -a ALL_GPU_TYPES=()      # intel / amd / nvidia
declare -a ALL_GPU_NAMES=()      # human-readable name
declare -a ALL_GPU_DRIVERS=()    # current kernel driver

SELECTED_GPU=""                  # intel / amd / nvidia
SELECTED_GPU_PCI=""              # 0000:01:00.0
SELECTED_GPU_NAME=""

declare -a IOMMU_DEVICES=()      # all PCI addrs in IOMMU group (endpoint devices)
declare -a IOMMU_VFIO_IDS=()    # vendor:device for vfio-pci ids=
IOMMU_GROUP=""

SELECTED_VMID=""
VM_NAME=""

GPU_COUNT=0
SINGLE_GPU_SYSTEM=false

SWITCH_FROM_LXC=false
SWITCH_LXC_LIST=""
SWITCH_FROM_VM=false
SWITCH_VM_SRC=""
TARGET_VM_ALREADY_HAS_GPU=false

AMD_ROM_FILE=""

HOST_CONFIG_CHANGED=false   # set to true whenever host VFIO config is actually written


# ==========================================================
# Helpers
# ==========================================================
_get_pci_driver() {
    local pci_full="$1"
    local driver_link="/sys/bus/pci/devices/${pci_full}/driver"
    if [[ -L "$driver_link" ]]; then
        basename "$(readlink "$driver_link")"
    else
        echo "none"
    fi
}

_add_line_if_missing() {
    local line="$1"
    local file="$2"
    touch "$file"
    if ! grep -qF "$line" "$file"; then
        echo "$line" >> "$file"
        HOST_CONFIG_CHANGED=true
    fi
}

_get_vm_run_title() {
    if [[ "$SWITCH_FROM_LXC" == "true" && "$SWITCH_FROM_VM" == "true" ]]; then
        echo "GPU Switch Mode (LXC/VM → VM)"
    elif [[ "$SWITCH_FROM_LXC" == "true" ]]; then
        echo "GPU Switch Mode (LXC → VM)"
    elif [[ "$SWITCH_FROM_VM" == "true" ]]; then
        echo "GPU Switch Mode (VM → VM)"
    else
        echo "$(translate 'GPU Passthrough to VM')"
    fi
}

_is_pci_slot_assigned_to_vm() {
    local pci_full="$1"
    local vmid="$2"
    local slot_base
    slot_base="${pci_full#0000:}"
    slot_base="${slot_base%.*}"   # 01:00

    qm config "$vmid" 2>/dev/null \
        | grep -qE "^hostpci[0-9]+:.*(0000:)?${slot_base}(\\.[0-7])?([,[:space:]]|$)"
}

# Match a specific PCI function when possible.
# For function .0, also accept slot-only entries (e.g. 01:00) as equivalent.
_is_pci_function_assigned_to_vm() {
    local pci_full="$1"
    local vmid="$2"
    local bdf slot func pattern
    bdf="${pci_full#0000:}"      # 01:00.0
    slot="${bdf%.*}"             # 01:00
    func="${bdf##*.}"            # 0

    if [[ "$func" == "0" ]]; then
        pattern="^hostpci[0-9]+:.*(0000:)?(${bdf}|${slot})([,:[:space:]]|$)"
    else
        pattern="^hostpci[0-9]+:.*(0000:)?${bdf}([,[:space:]]|$)"
    fi

    qm config "$vmid" 2>/dev/null | grep -qE "$pattern"
}

ensure_selected_gpu_not_already_in_target_vm() {
    while _is_pci_slot_assigned_to_vm "$SELECTED_GPU_PCI" "$SELECTED_VMID"; do
        local current_driver
        current_driver=$(_get_pci_driver "$SELECTED_GPU_PCI")

        # GPU is already assigned to this VM, but host is not in VFIO mode.
        # Continue so the script can re-activate VM passthrough on the host.
        if [[ "$current_driver" != "vfio-pci" ]]; then
            TARGET_VM_ALREADY_HAS_GPU=true
            local popup_title
            popup_title=$(_get_vm_run_title)
            dialog --backtitle "ProxMenux" \
                --title "${popup_title}" \
                --msgbox "\n$(translate 'The selected GPU is already assigned to this VM, but the host is not currently using vfio-pci for this device.')\n\n$(translate 'Current driver'): ${current_driver}\n\n$(translate 'The script will continue to restore VM passthrough mode on the host and reuse existing hostpci entries.')" \
                13 78
            return 0
        fi

        # Single GPU system: nothing else to choose
        if [[ $GPU_COUNT -le 1 ]]; then
            dialog --backtitle "ProxMenux" \
                --title "$(translate 'GPU Already Added')" \
                --msgbox "\n$(translate 'The selected GPU is already assigned to this VM.')\n\n$(translate 'No changes are required.')" \
                9 66
            exit 0
        fi

        # Build menu with GPUs that are NOT already assigned to this VM
        local menu_items=()
        local i available
        available=0
        for i in "${!ALL_GPU_PCIS[@]}"; do
            local pci label
            pci="${ALL_GPU_PCIS[$i]}"
            _is_pci_slot_assigned_to_vm "$pci" "$SELECTED_VMID" && continue
            label="${ALL_GPU_NAMES[$i]}"
            [[ "${ALL_GPU_DRIVERS[$i]}" == "vfio-pci" ]] && label+=" [VFIO]"
            label+=" — ${pci}"
            menu_items+=("$i" "$label")
            available=$((available + 1))
        done

        if [[ $available -eq 0 ]]; then
            dialog --backtitle "ProxMenux" \
                --title "$(translate 'All GPUs Already Assigned')" \
                --msgbox "\n$(translate 'All detected GPUs are already assigned to this VM.')\n\n$(translate 'No additional GPU can be added.')" \
                10 70
            exit 0
        fi

        local choice
        choice=$(dialog --backtitle "ProxMenux" \
            --title "$(translate 'GPU Already Assigned to This VM')" \
            --menu "\n$(translate 'The selected GPU is already present in this VM. Select another GPU to continue:')" \
            18 82 10 \
            "${menu_items[@]}" \
            2>&1 >/dev/tty) || exit 0

        SELECTED_GPU="${ALL_GPU_TYPES[$choice]}"
        SELECTED_GPU_PCI="${ALL_GPU_PCIS[$choice]}"
        SELECTED_GPU_NAME="${ALL_GPU_NAMES[$choice]}"
    done
}


# ==========================================================
# Phase 1 — Step 1: Detect host GPUs
# ==========================================================
detect_host_gpus() {
    while IFS= read -r line; do
        local pci_short pci_full name type driver
        pci_short=$(echo "$line" | awk '{print $1}')
        pci_full="0000:${pci_short}"

        # Human-readable name: between first ":" and first "["
        name=$(echo "$line" | sed 's/^[^:]*[^:]: //' | sed 's/ \[.*//' | cut -c1-62)

        if echo "$line" | grep -qi "Intel"; then
            type="intel"
        elif echo "$line" | grep -qiE "AMD|Advanced Micro|Radeon"; then
            type="amd"
        elif echo "$line" | grep -qi "NVIDIA"; then
            type="nvidia"
        else
            type="other"
        fi

        driver=$(_get_pci_driver "$pci_full")

        ALL_GPU_PCIS+=("$pci_full")
        ALL_GPU_TYPES+=("$type")
        ALL_GPU_NAMES+=("$name")
        ALL_GPU_DRIVERS+=("$driver")

    done < <(lspci -nn | grep -iE "VGA compatible controller|3D controller|Display controller" \
                        | grep -iv "Ethernet\|Network\|Audio")

    GPU_COUNT=${#ALL_GPU_PCIS[@]}

    if [[ $GPU_COUNT -eq 0 ]]; then
        dialog --backtitle "ProxMenux" \
            --title "$(translate 'No GPU Detected')" \
            --msgbox "\n$(translate 'No compatible GPU was detected on this host.')" 8 60
        exit 0
    fi

    [[ $GPU_COUNT -eq 1 ]] && SINGLE_GPU_SYSTEM=true
}


# ==========================================================
# Phase 1 — Step 2: Check IOMMU, offer to enable it
# ==========================================================
check_iommu_enabled() {
    if grep -qE 'intel_iommu=on|amd_iommu=on' /proc/cmdline 2>/dev/null && \
       [[ -d /sys/kernel/iommu_groups ]] && \
       [[ -n "$(ls /sys/kernel/iommu_groups/ 2>/dev/null)" ]]; then
        return 0
    fi

    local msg
    msg="\n$(translate 'IOMMU is not active on this system.')\n\n"
    msg+="$(translate 'GPU passthrough to VMs requires IOMMU to be enabled in the kernel.')\n\n"
    msg+="$(translate 'Do you want to enable IOMMU now?')\n\n"
    msg+="$(translate 'Note: A system reboot will be required after enabling IOMMU.')\n"
    msg+="$(translate 'You must run this option again after rebooting.')"

    dialog --backtitle "ProxMenux" \
        --title "$(translate 'IOMMU Required')" \
        --yesno "$msg" 15 72

    local response=$?
    clear

    if [[ $response -eq 0 ]]; then
        show_proxmenux_logo
        msg_title "$(translate 'Enabling IOMMU')"
        _enable_iommu_cmdline
        echo
        msg_success "$(translate 'IOMMU configured. Please reboot and run GPU passthrough to VM again.')"
        echo
        msg_success "$(translate 'Press Enter to continue...')"
        read -r
    fi
    exit 0
}

_enable_iommu_cmdline() {
    local cpu_vendor
    cpu_vendor=$(grep -m1 "vendor_id" /proc/cpuinfo 2>/dev/null | awk '{print $3}')

    local iommu_param
    if [[ "$cpu_vendor" == "GenuineIntel" ]]; then
        iommu_param="intel_iommu=on"
        msg_info "$(translate 'Intel CPU detected')"
    elif [[ "$cpu_vendor" == "AuthenticAMD" ]]; then
        iommu_param="amd_iommu=on"
        msg_info "$(translate 'AMD CPU detected')"
    else
        msg_error "$(translate 'Unknown CPU vendor. Cannot determine IOMMU parameter.')"
        return 1
    fi

    local cmdline_file="/etc/kernel/cmdline"
    local grub_file="/etc/default/grub"

    if [[ -f "$cmdline_file" ]] && grep -qE 'root=ZFS=|root=ZFS/' "$cmdline_file" 2>/dev/null; then
        # systemd-boot / ZFS
        if ! grep -q "$iommu_param" "$cmdline_file"; then
            cp "$cmdline_file" "${cmdline_file}.bak.$(date +%Y%m%d_%H%M%S)"
            sed -i "s|\\s*$| ${iommu_param} iommu=pt|" "$cmdline_file"
            proxmox-boot-tool refresh >/dev/null 2>&1 || true
            msg_ok "$(translate 'IOMMU parameters added to /etc/kernel/cmdline')"
        else
            msg_ok "$(translate 'IOMMU already configured in /etc/kernel/cmdline')"
        fi
    elif [[ -f "$grub_file" ]]; then
        # GRUB
        if ! grep -q "$iommu_param" "$grub_file"; then
            cp "$grub_file" "${grub_file}.bak.$(date +%Y%m%d_%H%M%S)"
            sed -i "/GRUB_CMDLINE_LINUX_DEFAULT=/ s|\"$| ${iommu_param} iommu=pt\"|" "$grub_file"
            update-grub >/dev/null 2>&1 || true
            msg_ok "$(translate 'IOMMU parameters added to GRUB')"
        else
            msg_ok "$(translate 'IOMMU already configured in GRUB')"
        fi
    else
        msg_error "$(translate 'Neither /etc/kernel/cmdline nor /etc/default/grub found.')"
        return 1
    fi
}


# ==========================================================
# Phase 1 — Step 3: GPU selection
# ==========================================================
select_gpu() {
    # Single GPU: auto-select, no menu needed
    if [[ $GPU_COUNT -eq 1 ]]; then
        SELECTED_GPU="${ALL_GPU_TYPES[0]}"
        SELECTED_GPU_PCI="${ALL_GPU_PCIS[0]}"
        SELECTED_GPU_NAME="${ALL_GPU_NAMES[0]}"
        return 0
    fi

    # Multiple GPUs: show menu
    local menu_items=()
    local i
    for i in "${!ALL_GPU_PCIS[@]}"; do
        local label="${ALL_GPU_NAMES[$i]}"
        [[ "${ALL_GPU_DRIVERS[$i]}" == "vfio-pci" ]] && label+=" [VFIO]"
        label+=" — ${ALL_GPU_PCIS[$i]}"
        menu_items+=("$i" "$label")
    done

    local choice
    choice=$(dialog --backtitle "ProxMenux" \
        --title "$(translate 'Select GPU for VM Passthrough')" \
        --menu "\n$(translate 'Select the GPU to pass through to the VM:')" \
        18 82 10 \
        "${menu_items[@]}" \
        2>&1 >/dev/tty) || exit 0

    SELECTED_GPU="${ALL_GPU_TYPES[$choice]}"
    SELECTED_GPU_PCI="${ALL_GPU_PCIS[$choice]}"
    SELECTED_GPU_NAME="${ALL_GPU_NAMES[$choice]}"
}


# ==========================================================
# Phase 1 — Step 4: Single-GPU warning
# ==========================================================
warn_single_gpu() {
    [[ "$SINGLE_GPU_SYSTEM" != "true" ]] && return 0

    local msg
    msg="\n\Zb\Z1⚠  $(translate 'WARNING: This is a single GPU system')\Zn\n\n"
    msg+="$(translate 'When this GPU is passed through to a VM, the Proxmox host will lose all video output on the physical monitor.')\n\n"
    msg+="$(translate 'After the reboot, you will only be able to access the Proxmox host via:')\n"
    msg+="  •  SSH\n"
    msg+="  •  Proxmox Web UI (https)\n"
    msg+="  •  Serial console\n\n"
    msg+="$(translate 'The VM guest will have exclusive access to the GPU.')\n\n"
    msg+="$(translate 'Make sure you have SSH or Web UI access before rebooting.')\n\n"
    msg+="$(translate 'Do you want to continue?')"

    dialog --backtitle "ProxMenux" --colors \
        --title "$(translate 'Single GPU Warning')" \
        --yesno "$msg" 22 76

    [[ $? -ne 0 ]] && exit 0
}


# ==========================================================
# Phase 1 — Step 4b: Hardware passthrough compatibility check
# ==========================================================

# Returns: apu | dedicated | unknown
_detect_amd_gpu_subtype() {
    local name_lower
    name_lower=$(echo "$SELECTED_GPU_NAME" | tr '[:upper:]' '[:lower:]')

    # Known AMD APU / integrated GPU codenames (mobile and desktop)
    local apu_codenames=(
        "lucienne" "renoir" "cezanne" "van gogh" "barcelo"
        "rembrandt" "phoenix" "hawk point" "strix" "mendocino"
        "pollock" "raphael" "dragon range" "raven" "picasso"
    )
    for codename in "${apu_codenames[@]}"; do
        echo "$name_lower" | grep -qi "$codename" && echo "apu" && return
    done

    # Markers of discrete / dedicated cards
    if echo "$name_lower" | grep -qiE "radeon rx|radeon pro|radeon vii|radeon r[0-9]|firepro|instinct|navi|polaris|vega|fiji|ellesmere|baffin"; then
        echo "dedicated"
        return
    fi

    echo "unknown"
}

# Returns: flr | bus | pm | none | unknown
_check_pci_reset_method() {
    local pci_full="$1"
    local reset_file="/sys/bus/pci/devices/${pci_full}/reset_method"

    if [[ ! -f "$reset_file" ]]; then
        [[ -f "/sys/bus/pci/devices/${pci_full}/reset" ]] && echo "unknown" || echo "none"
        return
    fi

    local method
    method=$(cat "$reset_file" 2>/dev/null)
    echo "$method" | grep -q "flr" && echo "flr" && return
    echo "$method" | grep -q "pm"  && echo "pm"  && return
    echo "$method" | grep -q "bus" && echo "bus" && return
    echo "unknown"
}

# Returns: igpu | dedicated | unknown
_detect_intel_gpu_subtype() {
    local name_lower pci_full
    name_lower=$(echo "$SELECTED_GPU_NAME" | tr '[:upper:]' '[:lower:]')
    pci_full="$SELECTED_GPU_PCI"

    # Typical integrated Intel GPU PCI function
    [[ "$pci_full" == "0000:00:02.0" ]] && echo "igpu" && return

    # Common integrated markers
    if echo "$name_lower" | grep -qiE "uhd|hd graphics|iris|xe graphics|integrated"; then
        echo "igpu"
        return
    fi

    # Common dedicated markers (Intel Arc family)
    if echo "$name_lower" | grep -qiE "arc|a3[0-9]{2}|a5[0-9]{2}|a7[0-9]{2}|a7[5-9]0|b5[0-9]{2}|b7[0-9]{2}"; then
        echo "dedicated"
        return
    fi

    echo "unknown"
}

check_intel_vm_compatibility() {
    local pci_full="$SELECTED_GPU_PCI"
    local gpu_subtype reset_method power_state

    gpu_subtype=$(_detect_intel_gpu_subtype)
    reset_method=$(_check_pci_reset_method "$pci_full")
    power_state=$(cat "/sys/bus/pci/devices/${pci_full}/power_state" 2>/dev/null | tr -d '[:space:]')

    # ── BLOCKER: Intel GPU in D3cold ──────────────────────────────────────
    if [[ "$power_state" == "D3cold" ]]; then
        local msg
        msg="\n\Zb\Z1$(translate 'GPU Not Available for Reliable VM Passthrough')\Zn\n\n"
        msg+="$(translate 'The selected Intel GPU is currently in power state D3cold'):\n"
        msg+="  ${SELECTED_GPU_NAME}\n"
        msg+="  ${SELECTED_GPU_PCI}\n\n"
        msg+="$(translate 'Detected power state'): \Zb${power_state}\Zn\n\n"
        msg+="$(translate 'This state has a high probability of VM startup/reset failures.')\n\n"
        msg+="\Zb$(translate 'Configuration has been stopped to prevent an unusable VM state.')\Zn"

        dialog --backtitle "ProxMenux" --colors \
            --title "$(translate 'High-Risk GPU Power State')" \
            --msgbox "$msg" 20 80
        exit 0
    fi

    # ── BLOCKER: no usable reset method ──────────────────────────────────
    if [[ "$reset_method" == "none" ]]; then
        local msg
        msg="\n\Zb\Z1$(translate 'Incompatible Reset Capability for Intel GPU')\Zn\n\n"
        msg+="$(translate 'The selected Intel GPU does not expose a PCI reset interface'):\n"
        msg+="  ${SELECTED_GPU_NAME}\n"
        msg+="  ${SELECTED_GPU_PCI}\n\n"
        msg+="  $(translate 'Detected reset method'): \Zb${reset_method}\Zn\n\n"
        msg+="$(translate 'Without a usable reset path, passthrough reliability is poor and VM')\n"
        msg+="$(translate 'startup/restart errors are likely.')\n\n"
        msg+="\Zb$(translate 'Configuration has been stopped due to high reset risk.')\Zn"

        dialog --backtitle "ProxMenux" --colors \
            --title "$(translate 'Reset Capability Blocked')" \
            --msgbox "$msg" 20 80
        exit 0
    fi

    # ── BLOCKER: Intel dGPU without FLR ──────────────────────────────────
    if [[ "$gpu_subtype" == "dedicated" && "$reset_method" != "flr" ]]; then
        local msg
        msg="\n\Zb\Z1$(translate 'Incompatible Reset Capability for Intel dGPU')\Zn\n\n"
        msg+="$(translate 'An Intel dedicated GPU has been detected without FLR support'):\n"
        msg+="  ${SELECTED_GPU_NAME}\n"
        msg+="  ${SELECTED_GPU_PCI}\n\n"
        msg+="  $(translate 'Detected reset method'): \Zb${reset_method}\Zn\n\n"
        msg+="$(translate 'For dedicated GPUs, FLR is required by this policy to reduce VM')\n"
        msg+="$(translate 'start/restart failures and reset instability.')\n\n"
        msg+="\Zb$(translate 'Configuration has been stopped due to high reset risk.')\Zn"

        dialog --backtitle "ProxMenux" --colors \
            --title "$(translate 'Reset Capability Blocked')" \
            --msgbox "$msg" 20 80
        exit 0
    fi

    # ── WARNING: Intel subtype unknown and reset is not FLR ──────────────
    if [[ "$gpu_subtype" == "unknown" && "$reset_method" != "flr" ]]; then
        local msg
        msg="\n\Z4\Zb$(translate 'Warning: Limited PCI Reset Support')\Zn\n\n"
        msg+="$(translate 'The selected Intel GPU has non-FLR reset support and unknown subtype'):\n"
        msg+="  $(translate 'Detected subtype'): \Zb${gpu_subtype}\Zn\n"
        msg+="  $(translate 'Detected reset method'): \Zb${reset_method}\Zn\n\n"
        msg+="$(translate 'Passthrough may work, but startup/restart reliability is not guaranteed.')\n\n"
        msg+="$(translate 'Do you want to continue anyway?')"

        dialog --backtitle "ProxMenux" --colors \
            --title "$(translate 'Reset Capability Warning')" \
            --yesno "$msg" 18 78
        [[ $? -ne 0 ]] && exit 0
    fi
}

check_gpu_vm_compatibility() {
    [[ "$SELECTED_GPU" != "amd" && "$SELECTED_GPU" != "intel" ]] && return 0

    if [[ "$SELECTED_GPU" == "intel" ]]; then
        check_intel_vm_compatibility
        return 0
    fi

    local pci_full="$SELECTED_GPU_PCI"
    local gpu_subtype reset_method power_state

    gpu_subtype=$(_detect_amd_gpu_subtype)
    reset_method=$(_check_pci_reset_method "$pci_full")
    power_state=$(cat "/sys/bus/pci/devices/${pci_full}/power_state" 2>/dev/null | tr -d '[:space:]')

    # ── BLOCKER: AMD device currently in D3cold ──────────────────────────
    # D3cold on AMD passthrough candidates is a high-risk state for VM use.
    # In practice this often leads to failed power-up/reset when QEMU starts.
    if [[ "$power_state" == "D3cold" ]]; then
        local msg
        msg="\n\Zb\Z1$(translate 'GPU Not Available for Reliable VM Passthrough')\Zn\n\n"
        msg+="$(translate 'The selected AMD GPU is currently in power state D3cold'):\n"
        msg+="  ${SELECTED_GPU_NAME}\n"
        msg+="  ${SELECTED_GPU_PCI}\n\n"
        msg+="$(translate 'Detected power state'): \Zb${power_state}\Zn\n\n"
        msg+="$(translate 'This state indicates a high risk of passthrough failure due to'):\n"
        msg+="  •  $(translate 'Inaccessible device during VM startup')\n"
        msg+="  •  $(translate 'Failed transitions from D3cold to D0')\n"
        msg+="  •  $(translate 'Potential QEMU startup/assertion failures')\n\n"
        msg+="\Zb$(translate 'Configuration has been stopped to prevent an unusable VM state.')\Zn"

        dialog --backtitle "ProxMenux" --colors \
            --title "$(translate 'High-Risk GPU Power State')" \
            --msgbox "$msg" 22 80
        exit 0
    fi

    # ── BLOCKER: AMD APU without FLR reset ───────────────────────────────
    # Validated in testing: Lucienne/Renoir/Cezanne + bus-only reset →
    #   "write error: Inappropriate ioctl for device" on PCI reset
    #   "Unable to change power state from D3cold to D0"
    #   QEMU pci_irq_handler assertion failure → VM does not start
    if [[ "$gpu_subtype" == "apu" && "$reset_method" != "flr" ]]; then
        local msg
        msg="\n\Zb\Z1$(translate 'GPU Not Compatible with VM Passthrough')\Zn\n\n"
        msg+="$(translate 'An AMD integrated GPU (APU) has been detected'):\n"
        msg+="  ${SELECTED_GPU_NAME}\n"
        msg+="  ${SELECTED_GPU_PCI}\n\n"
        msg+="$(translate 'Although VFIO can bind to this device, full passthrough to a VM is')\n"
        msg+="$(translate 'not reliable on this hardware due to the following limitations'):\n\n"
        msg+="  •  $(translate 'PCI reset method'): \Zb${reset_method}\Zn"
        msg+=" — $(translate 'Function Level Reset (FLR) not available')\n"
        msg+="  •  $(translate 'SoC-integrated GPU: tight coupling with other SoC components')\n"
        msg+="  •  $(translate 'Power state D3cold/D0 transitions may be inaccessible')\n"
        [[ "$power_state" == "D3cold" ]] && \
        msg+="  •  \Z3$(translate 'Current power state: D3cold (device currently inaccessible)')\Zn\n"
        msg+="\n$(translate 'Attempting passthrough with this GPU typically results in'):\n"
        msg+="  —  write error: Inappropriate ioctl for device\n"
        msg+="  —  Unable to change power state from D3cold to D0\n"
        msg+="  —  QEMU IRQ assertion failure → VM does not start\n\n"
        msg+="\Zb$(translate 'Configuration has been stopped to prevent leaving the VM in an unusable state.')\Zn"

        dialog --backtitle "ProxMenux" --colors \
            --title "$(translate 'Incompatible GPU for VM Passthrough')" \
            --msgbox "$msg" 26 80
        exit 0
    fi

    # ── BLOCKER: AMD dedicated GPU without FLR reset ─────────────────────
    # User policy: for dGPU + no FLR, do not continue automatically.
    if [[ "$gpu_subtype" == "dedicated" && "$reset_method" != "flr" ]]; then
        local msg
        msg="\n\Zb\Z1$(translate 'Incompatible Reset Capability for AMD dGPU')\Zn\n\n"
        msg+="$(translate 'An AMD dedicated GPU has been detected without FLR support'):\n"
        msg+="  ${SELECTED_GPU_NAME}\n"
        msg+="  ${SELECTED_GPU_PCI}\n\n"
        msg+="  $(translate 'Detected reset method'): \Zb${reset_method}\Zn\n\n"
        msg+="$(translate 'Without Function Level Reset (FLR), passthrough is not considered reliable')\n"
        msg+="$(translate 'for this policy and may fail after first use or on subsequent VM starts.')\n\n"
        msg+="\Zb$(translate 'Configuration has been stopped due to high reset risk.')\Zn"

        dialog --backtitle "ProxMenux" --colors \
            --title "$(translate 'Reset Capability Blocked')" \
            --msgbox "$msg" 20 80
        exit 0
    fi

    # ── WARNING: Unknown AMD subtype without FLR ─────────────────────────
    # Keep optional path for unknown classifications only.
    if [[ "$gpu_subtype" == "unknown" && "$reset_method" != "flr" ]]; then
        local msg
        msg="\n\Z4\Zb$(translate 'Warning: Limited PCI Reset Support')\Zn\n\n"
        msg+="$(translate 'The selected AMD GPU does not report FLR reset support'):\n"
        msg+="  $(translate 'Detected subtype'): \Zb${gpu_subtype}\Zn\n"
        msg+="  $(translate 'Detected reset method'): \Zb${reset_method}\Zn\n\n"
        msg+="$(translate 'Passthrough may fail depending on hardware/firmware implementation.')\n\n"
        msg+="$(translate 'Do you want to continue anyway?')"

        dialog --backtitle "ProxMenux" --colors \
            --title "$(translate 'Reset Capability Warning')" \
            --yesno "$msg" 18 78
        [[ $? -ne 0 ]] && exit 0
    fi
}


# ==========================================================
# Phase 1 — Step 5: IOMMU group analysis
# ==========================================================
analyze_iommu_group() {
    local pci_full="$SELECTED_GPU_PCI"
    local group_link="/sys/bus/pci/devices/${pci_full}/iommu_group"

    if [[ ! -L "$group_link" ]]; then
        dialog --backtitle "ProxMenux" \
            --title "$(translate 'IOMMU Group Error')" \
            --msgbox "\n$(translate 'Could not determine the IOMMU group for the selected GPU.')\n\n$(translate 'Make sure IOMMU is properly enabled and the system has been rebooted after activation.')" \
            10 72
        exit 1
    fi

    IOMMU_GROUP=$(basename "$(readlink "$group_link")")
    IOMMU_DEVICES=()
    IOMMU_VFIO_IDS=()

    local group_dir="/sys/kernel/iommu_groups/${IOMMU_GROUP}/devices"
    local display_lines=""
    local extra_devices=0

    for dev_path in "${group_dir}/"*; do
        [[ -e "$dev_path" ]] || continue
        local dev
        dev=$(basename "$dev_path")

        # Skip PCI bridges and host bridges (class 0x0604 / 0x0600)
        local dev_class
        dev_class=$(cat "/sys/bus/pci/devices/${dev}/class" 2>/dev/null)
        if [[ "$dev_class" == "0x0604" || "$dev_class" == "0x0600" ]]; then
            continue
        fi

        IOMMU_DEVICES+=("$dev")

        # Collect vendor:device ID
        local vid did
        vid=$(cat "/sys/bus/pci/devices/${dev}/vendor" 2>/dev/null | sed 's/0x//')
        did=$(cat "/sys/bus/pci/devices/${dev}/device" 2>/dev/null | sed 's/0x//')
        [[ -n "$vid" && -n "$did" ]] && IOMMU_VFIO_IDS+=("${vid}:${did}")

        # Build display line
        local dev_name dev_driver
        dev_name=$(lspci -nn -s "${dev#0000:}" 2>/dev/null | sed 's/^[^ ]* //' | cut -c1-52)
        dev_driver=$(_get_pci_driver "$dev")
        display_lines+="  • ${dev}  ${dev_name}  [${dev_driver}]\n"

        [[ "$dev" != "$pci_full" ]] && extra_devices=$((extra_devices + 1))
    done

    local msg
    msg="$(translate 'IOMMU Group'): ${IOMMU_GROUP}\n\n"
    msg+="$(translate 'The following devices will all be passed to the VM') "
    msg+="($(translate 'IOMMU isolation rule')):\n\n"
    msg+="${display_lines}"

    if [[ $extra_devices -gt 0 ]]; then
        msg+="\n\Z3$(translate 'All devices in the same IOMMU group must be passed together.')\Zn"
    fi

    dialog --backtitle "ProxMenux" --colors \
        --title "$(translate 'IOMMU Group') ${IOMMU_GROUP}" \
        --msgbox "\n${msg}" 22 82
}


# ==========================================================
# Phase 1 — Step 6: VM selection
# ==========================================================
select_vm() {
    local menu_items=()

    while IFS= read -r line; do
        [[ "$line" =~ ^[[:space:]]*VMID ]] && continue
        local vmid name status
        vmid=$(echo "$line" | awk '{print $1}')
        name=$(echo "$line" | awk '{print $2}')
        status=$(echo "$line" | awk '{print $3}')
        [[ -z "$vmid" || ! "$vmid" =~ ^[0-9]+$ ]] && continue
        menu_items+=("$vmid" "${name:-VM-${vmid}} (${status})")
    done < <(qm list 2>/dev/null)

    if [[ ${#menu_items[@]} -eq 0 ]]; then
        dialog --backtitle "ProxMenux" \
            --title "$(translate 'No VMs Found')" \
            --msgbox "\n$(translate 'No Virtual Machines found on this system.')\n\n$(translate 'Create a VM first (machine type q35 + UEFI BIOS), then run this option again.')" \
            10 68
        exit 0
    fi

    SELECTED_VMID=$(dialog --backtitle "ProxMenux" \
        --title "$(translate 'Select Virtual Machine')" \
        --menu "\n$(translate 'Select the VM to add the GPU to:')" \
        20 72 12 \
        "${menu_items[@]}" \
        2>&1 >/dev/tty) || exit 0

    VM_NAME=$(qm config "$SELECTED_VMID" 2>/dev/null | grep "^name:" | awk '{print $2}')
}


# ==========================================================
# Phase 1 — Step 7: Machine type check (must be q35)
# ==========================================================
check_vm_machine_type() {
    local machine_line
    machine_line=$(qm config "$SELECTED_VMID" 2>/dev/null | grep "^machine:" | awk '{print $2}')

    if echo "$machine_line" | grep -q "q35"; then
        return 0
    fi

    local msg
    msg="\n$(translate 'The selected VM') \"${VM_NAME}\" (${SELECTED_VMID}) "
    msg+="$(translate 'is not configured as machine type q35.')\n\n"
    msg+="$(translate 'PCIe GPU passthrough requires:')\n"
    msg+="  •  $(translate 'Machine type: q35')\n"
    msg+="  •  $(translate 'BIOS: OVMF (UEFI)')\n\n"
    msg+="$(translate 'Changing the machine type on an existing installed VM is not safe: it changes the chipset and PCI slot layout, which typically prevents the guest OS from booting.')\n\n"
    msg+="$(translate 'To use GPU passthrough, please create a new VM configured with:')\n"
    msg+="  •  $(translate 'Machine: q35')\n"
    msg+="  •  $(translate 'BIOS: OVMF (UEFI)')\n"
    msg+="  •  $(translate 'Storage controller: VirtIO SCSI')"

    dialog --backtitle "ProxMenux" \
        --title "$(translate 'Incompatible Machine Type')" \
        --msgbox "$msg" 20 78
    exit 0
}


# ==========================================================
# Phase 1 — Step 8: Switch mode detection
# ==========================================================
check_switch_mode() {
    local pci_slot="${SELECTED_GPU_PCI#0000:}"  # 01:00.0
    pci_slot="${pci_slot%.*}"                   # 01:00

    # ── LXC conflict check ────────────────────────────────
    local lxc_affected=()
    for conf in /etc/pve/lxc/*.conf; do
        [[ -f "$conf" ]] || continue
        if grep -qE "dev[0-9]+:.*(/dev/dri|/dev/nvidia|/dev/kfd)" "$conf"; then
            local ctid ct_name
            ctid=$(basename "$conf" .conf)
            ct_name=$(pct config "$ctid" 2>/dev/null | grep "^hostname:" | awk '{print $2}')
            lxc_affected+=("CT ${ctid} (${ct_name:-CT-${ctid}})")
        fi
    done

    if [[ ${#lxc_affected[@]} -gt 0 ]]; then
        SWITCH_FROM_LXC=true
        SWITCH_LXC_LIST=$(IFS=', '; echo "${lxc_affected[*]}")

        local msg
        msg="\n$(translate 'The selected GPU is currently shared with the following LXC containers via device passthrough:')\n\n"
        for ct in "${lxc_affected[@]}"; do
            msg+="  •  ${ct}\n"
        done
        msg+="\n$(translate 'VM passthrough requires exclusive VFIO binding of the GPU.')\n"
        msg+="$(translate 'GPU device access will be removed from those LXC containers.')\n\n"
        msg+="$(translate 'Do you want to continue?')"

        dialog --backtitle "ProxMenux" \
            --title "$(translate 'GPU Used in LXC Containers')" \
            --yesno "$msg" 18 76
        [[ $? -ne 0 ]] && exit 0
    fi

    # ── VM conflict check (different VM than selected) ────
    local vm_src_id="" vm_src_name=""
    for conf in /etc/pve/qemu-server/*.conf; do
        [[ -f "$conf" ]] || continue
        local vmid
        vmid=$(basename "$conf" .conf)
        [[ "$vmid" == "$SELECTED_VMID" ]] && continue  # same target VM, no conflict
        if grep -qE "hostpci[0-9]+:.*${pci_slot}" "$conf"; then
            vm_src_id="$vmid"
            vm_src_name=$(grep "^name:" "$conf" 2>/dev/null | awk '{print $2}')
            break
        fi
    done

    if [[ -n "$vm_src_id" ]]; then
        SWITCH_FROM_VM=true
        SWITCH_VM_SRC="$vm_src_id"

        local msg
        msg="\n$(translate 'The selected GPU is already configured for passthrough to:')\n\n"
        msg+="  VM ${vm_src_id} (${vm_src_name:-VM-${vm_src_id}})\n\n"
        msg+="$(translate 'The existing hostpci entry will be removed from that VM and configured on'): "
        msg+="VM ${SELECTED_VMID} (${VM_NAME:-VM-${SELECTED_VMID}})\n\n"
        msg+="$(translate 'Do you want to continue?')"

        dialog --backtitle "ProxMenux" \
            --title "$(translate 'GPU Already Assigned to Another VM')" \
            --yesno "$msg" 14 76
        [[ $? -ne 0 ]] && exit 0
    fi
}


# ==========================================================
# Phase 1 — Step 9: Confirmation summary
# ==========================================================
confirm_summary() {
    local msg
    msg="\n$(translate 'The following changes will be applied'):\n"
    msg+="\n  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    msg+="  $(translate 'GPU')          :  ${SELECTED_GPU_NAME}\n"
    msg+="  $(translate 'PCI Address')  :  ${SELECTED_GPU_PCI}\n"
    msg+="  $(translate 'IOMMU Group')  :  ${IOMMU_GROUP} (${#IOMMU_DEVICES[@]} $(translate 'devices'))\n"
    msg+="  $(translate 'Target VM')    :  ${VM_NAME:-VM-${SELECTED_VMID}} (${SELECTED_VMID})\n"
    msg+="  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    msg+="  \Zb$(translate 'Host'):\Zn\n"
    msg+="  •  $(translate 'VFIO modules in /etc/modules')\n"
    msg+="  •  $(translate 'vfio-pci IDs in /etc/modprobe.d/vfio.conf')\n"
    [[ "$SELECTED_GPU" == "amd" ]] && \
        msg+="  •  $(translate 'AMD softdep configured')\n"
    [[ "$SELECTED_GPU" == "amd" ]] && \
        msg+="  •  $(translate 'GPU ROM dump to /usr/share/kvm/')\n"
    msg+="  •  $(translate 'GPU driver blacklisted')\n"
    msg+="  •  $(translate 'initramfs updated')\n"
    msg+="  •  \Zb$(translate 'System reboot required')\Zn\n\n"
    msg+="  \Zb$(translate 'VM') ${SELECTED_VMID}:\Zn\n"
    [[ "$TARGET_VM_ALREADY_HAS_GPU" == "true" ]] && \
        msg+="  •  $(translate 'Existing hostpci entries detected — they will be reused')\n"
    msg+="  •  $(translate 'Virtual display normalized to vga: std (compatibility)')\n"
    msg+="  •  $(translate 'hostpci entries for all IOMMU group devices')\n"
    [[ "$SELECTED_GPU" == "nvidia" ]] && \
        msg+="  •  $(translate 'NVIDIA KVM hiding (cpu hidden=1)')\n"
    [[ "$SWITCH_FROM_LXC" == "true" ]] && \
        msg+="\n  \Z3•  $(translate 'GPU will be removed from LXC containers'): ${SWITCH_LXC_LIST}\Zn\n"
    [[ "$SWITCH_FROM_VM" == "true" ]] && \
        msg+="\n  \Z3•  $(translate 'GPU will be removed from VM') ${SWITCH_VM_SRC}\Zn\n"
    msg+="\n$(translate 'Do you want to proceed?')"

    local run_title
    run_title=$(_get_vm_run_title)

    dialog --backtitle "ProxMenux" --colors \
        --title "${run_title}" \
        --yesno "$msg" 28 78

    [[ $? -ne 0 ]] && exit 0
}


# ==========================================================
# Phase 2 — Processing
# ==========================================================

# ── VFIO modules in /etc/modules ─────────────────────────
add_vfio_modules() {
    msg_info "$(translate 'Configuring VFIO modules...')"
    local modules=("vfio" "vfio_iommu_type1" "vfio_pci")
    local kernel_major kernel_minor
    kernel_major=$(uname -r | cut -d. -f1)
    kernel_minor=$(uname -r | cut -d. -f2)
    if (( kernel_major < 6 || ( kernel_major == 6 && kernel_minor < 2 ) )); then
        modules+=("vfio_virqfd")
    fi
    for mod in "${modules[@]}"; do
        _add_line_if_missing "$mod" /etc/modules
    done
    msg_ok "$(translate 'VFIO modules configured in /etc/modules')" | tee -a "$screen_capture"
}


# ── vfio-pci IDs — merge with existing ones ─────────────
configure_vfio_pci_ids() {
    msg_info "$(translate 'Configuring vfio-pci device IDs...')"
    local vfio_conf="/etc/modprobe.d/vfio.conf"
    touch "$vfio_conf"

    # Collect existing IDs (if any)
    local existing_ids=()
    local existing_line
    existing_line=$(grep "^options vfio-pci ids=" "$vfio_conf" 2>/dev/null | head -1)
    if [[ -n "$existing_line" ]]; then
        local ids_part
        ids_part=$(echo "$existing_line" | grep -oE 'ids=[^[:space:]]+' | sed 's/ids=//')
        IFS=',' read -ra existing_ids <<< "$ids_part"
    fi

    # Merge: add new IDs not already present
    local all_ids=("${existing_ids[@]}")
    for new_id in "${IOMMU_VFIO_IDS[@]}"; do
        local found=false
        for existing in "${existing_ids[@]}"; do
            [[ "$existing" == "$new_id" ]] && found=true && break
        done
        $found || all_ids+=("$new_id")
    done

    local ids_str
    ids_str=$(IFS=','; echo "${all_ids[*]}")

    local existing_full_line
    existing_full_line=$(grep "^options vfio-pci ids=" "$vfio_conf" 2>/dev/null | head -1)
    local new_full_line="options vfio-pci ids=${ids_str} disable_vga=1"
    if [[ "$existing_full_line" != "$new_full_line" ]]; then
        sed -i '/^options vfio-pci ids=/d' "$vfio_conf"
        echo "$new_full_line" >> "$vfio_conf"
        HOST_CONFIG_CHANGED=true
    fi
    msg_ok "$(translate 'vfio-pci IDs configured') (${ids_str})" | tee -a "$screen_capture"
}


# ── IOMMU interrupt remapping ─────────────────────────────
configure_iommu_options() {
    _add_line_if_missing "options vfio_iommu_type1 allow_unsafe_interrupts=1" \
        /etc/modprobe.d/iommu_unsafe_interrupts.conf
    _add_line_if_missing "options kvm ignore_msrs=1" \
        /etc/modprobe.d/kvm.conf
    msg_ok "$(translate 'IOMMU interrupt remapping configured')" | tee -a "$screen_capture"
}


# ── AMD softdep ──────────────────────────────────────────
add_softdep_amd() {
    msg_info "$(translate 'Configuring AMD softdep...')"
    local vfio_conf="/etc/modprobe.d/vfio.conf"
    _add_line_if_missing "softdep radeon pre: vfio-pci"       "$vfio_conf"
    _add_line_if_missing "softdep amdgpu pre: vfio-pci"       "$vfio_conf"
    _add_line_if_missing "softdep snd_hda_intel pre: vfio-pci" "$vfio_conf"
    msg_ok "$(translate 'AMD softdep configured in /etc/modprobe.d/vfio.conf')" | tee -a "$screen_capture"
}


# ── Blacklist GPU drivers (idempotent) ───────────────────
blacklist_gpu_drivers() {
    msg_info "$(translate 'Blacklisting GPU host drivers...')"
    local blacklist_file="/etc/modprobe.d/blacklist.conf"
    touch "$blacklist_file"

    case "$SELECTED_GPU" in
        nvidia)
            _add_line_if_missing "blacklist nouveau"          "$blacklist_file"
            _add_line_if_missing "blacklist nvidia"           "$blacklist_file"
            _add_line_if_missing "blacklist nvidiafb"         "$blacklist_file"
            _add_line_if_missing "blacklist lbm-nouveau"      "$blacklist_file"
            _add_line_if_missing "options nouveau modeset=0"  "$blacklist_file"
            ;;
        amd)
            _add_line_if_missing "blacklist radeon"   "$blacklist_file"
            _add_line_if_missing "blacklist amdgpu"   "$blacklist_file"
            ;;
        intel)
            _add_line_if_missing "blacklist i915"     "$blacklist_file"
            ;;
    esac
    msg_ok "$(translate 'GPU host driver blacklisted in /etc/modprobe.d/blacklist.conf')" | tee -a "$screen_capture"
}


# ── AMD ROM dump: sysfs first, VFCT ACPI table as fallback ───────────────
_dump_rom_via_vfct() {
    local rom_dest="$1"
    local vfct_file="/sys/firmware/acpi/tables/VFCT"
    [[ -f "$vfct_file" ]] || return 1

    # VFCT table layout:
    #   Offset  0-35  : standard ACPI header (36 bytes)
    #   Offset 36-47  : VFCT-specific fields (12 bytes)
    #   Offset 48     : first GPU_BIOS_IMAGE object
    #     +0  VendorID        (2 bytes)
    #     +2  DeviceID        (2 bytes)
    #     +4  SubsystemVendorID (2 bytes)
    #     +6  SubsystemDeviceID (2 bytes)
    #     +8  PCIBus/Device/Function/Reserved (4 bytes)
    #     +12 ImageLength     (4 bytes, little-endian)
    #     +16 VBIOS image data
    local img_length
    img_length=$(od -An -tu4 -j60 -N4 "$vfct_file" 2>/dev/null | tr -d '[:space:]')
    if [[ -z "$img_length" || "$img_length" -le 0 ]]; then
        return 1
    fi

    dd if="$vfct_file" bs=1 skip=64 count="$img_length" of="$rom_dest" 2>/dev/null
    [[ -s "$rom_dest" ]]
}

dump_amd_rom() {
    local pci_full="$SELECTED_GPU_PCI"
    local rom_path="/sys/bus/pci/devices/${pci_full}/rom"
    local kvm_dir="/usr/share/kvm"

    mkdir -p "$kvm_dir"
    local vid did
    vid=$(cat "/sys/bus/pci/devices/${pci_full}/vendor" 2>/dev/null | sed 's/0x//')
    did=$(cat "/sys/bus/pci/devices/${pci_full}/device" 2>/dev/null | sed 's/0x//')
    local rom_filename="vbios_${vid}_${did}.bin"
    local rom_dest="${kvm_dir}/${rom_filename}"

    # ── Method 1: sysfs /rom ──────────────────────────────
    if [[ -f "$rom_path" ]]; then
        msg_info "$(translate 'Dumping AMD GPU ROM BIOS via sysfs...')"
        echo 1 > "$rom_path" 2>/dev/null
        if cat "$rom_path" > "$rom_dest" 2>>"$LOG_FILE" && [[ -s "$rom_dest" ]]; then
            echo 0 > "$rom_path" 2>/dev/null
            AMD_ROM_FILE="$rom_filename"
            msg_ok "$(translate 'GPU ROM dumped to') ${rom_dest}" | tee -a "$screen_capture"
            return 0
        fi
        echo 0 > "$rom_path" 2>/dev/null
        rm -f "$rom_dest"
        msg_warn "$(translate 'sysfs ROM dump failed — trying ACPI VFCT table...')"
    else
        msg_info "$(translate 'No sysfs ROM entry — trying ACPI VFCT table...')"
    fi

    # ── Method 2: ACPI VFCT table ────────────────────────
    if _dump_rom_via_vfct "$rom_dest"; then
        AMD_ROM_FILE="$rom_filename"
        msg_ok "$(translate 'GPU ROM extracted from ACPI VFCT table to') ${rom_dest}" | tee -a "$screen_capture"
        return 0
    fi

    rm -f "$rom_dest"
    msg_warn "$(translate 'ROM dump not available — configuring without romfile.')"
    msg_warn "$(translate 'Passthrough may still work without a ROM file.')"
}


# ── Remove GPU from LXC configs (switch mode) ────────────
cleanup_lxc_configs() {
    [[ "$SWITCH_FROM_LXC" != "true" ]] && return 0

    msg_info "$(translate 'Removing GPU device access from LXC containers...')"
    for conf in /etc/pve/lxc/*.conf; do
        [[ -f "$conf" ]] || continue
        if grep -qE "dev[0-9]+:.*(/dev/dri|/dev/nvidia|/dev/kfd)" "$conf"; then
            sed -i '/dev[0-9]\+:.*\/dev\/dri/d'     "$conf"
            sed -i '/dev[0-9]\+:.*\/dev\/nvidia/d'  "$conf"
            sed -i '/dev[0-9]\+:.*\/dev\/kfd/d'     "$conf"
            sed -i '/lxc\.mount\.entry:.*dev\/dri/d' "$conf"
            sed -i '/lxc\.cgroup2\.devices\.allow:.*226/d' "$conf"
            local ctid
            ctid=$(basename "$conf" .conf)
            msg_ok "$(translate 'GPU removed from LXC') ${ctid}" | tee -a "$screen_capture"
        fi
    done
}


# ── Remove GPU from another VM config (switch mode) ──────
cleanup_vm_config() {
    [[ "$SWITCH_FROM_VM" != "true" ]] && return 0
    [[ -z "$SWITCH_VM_SRC" ]] && return 0

    local pci_slot="${SELECTED_GPU_PCI#0000:}"
    pci_slot="${pci_slot%.*}"   # 01:00

    local src_conf="/etc/pve/qemu-server/${SWITCH_VM_SRC}.conf"
    if [[ -f "$src_conf" ]]; then
        msg_info "$(translate 'Removing GPU from VM') ${SWITCH_VM_SRC}..."
        sed -i "/^hostpci[0-9]\+:.*${pci_slot}/d" "$src_conf"
        msg_ok "$(translate 'GPU removed from VM') ${SWITCH_VM_SRC}" | tee -a "$screen_capture"
    fi
}


# ── VM display normalization for passthrough stability ────
ensure_vm_display_std() {
    msg_info "$(translate 'Checking VM virtual display model...')"

    local current_vga current_base
    current_vga=$(qm config "$SELECTED_VMID" 2>/dev/null | awk '/^vga:/ {print $2}')
    current_base="${current_vga%%,*}"

    if [[ -z "$current_base" ]]; then
        if qm set "$SELECTED_VMID" --vga std >>"$LOG_FILE" 2>&1; then
            msg_ok "$(translate 'Virtual display set to'): vga: std" | tee -a "$screen_capture"
        else
            msg_warn "$(translate 'Could not set VM virtual display to vga: std')" | tee -a "$screen_capture"
        fi
        return 0
    fi

    if [[ "$current_base" == "std" ]]; then
        msg_ok "$(translate 'Virtual display already set to'): vga: std" | tee -a "$screen_capture"
        return 0
    fi

    if qm set "$SELECTED_VMID" --vga std >>"$LOG_FILE" 2>&1; then
        msg_ok "$(translate 'Virtual display changed from') ${current_base} $(translate 'to') std" | tee -a "$screen_capture"
    else
        msg_warn "$(translate 'Could not change VM virtual display to vga: std')" | tee -a "$screen_capture"
    fi
}


# ── Configure VM: add hostpci entries ─────────────────────
configure_vm() {
    msg_info "$(translate 'Configuring VM') ${SELECTED_VMID}..."

    # Find next free hostpciN index
    local idx=0
    while qm config "$SELECTED_VMID" 2>/dev/null | grep -q "^hostpci${idx}:"; do
        idx=$((idx + 1))
    done

    # Primary GPU: pcie=1, x-vga=1 only for NVIDIA/AMD (not Intel iGPU), romfile if AMD
    local gpu_opts="pcie=1"
    [[ "$SELECTED_GPU" == "nvidia" || "$SELECTED_GPU" == "amd" ]] && gpu_opts+=",x-vga=1"
    [[ -n "$AMD_ROM_FILE" ]] && gpu_opts+=",romfile=${AMD_ROM_FILE}"

    if _is_pci_function_assigned_to_vm "$SELECTED_GPU_PCI" "$SELECTED_VMID"; then
        msg_ok "$(translate 'GPU already present in target VM — existing hostpci entry reused')" | tee -a "$screen_capture"
    else
        qm set "$SELECTED_VMID" --hostpci${idx} "${SELECTED_GPU_PCI},${gpu_opts}" >>"$LOG_FILE" 2>&1
        msg_ok "$(translate 'GPU added'): hostpci${idx}: ${SELECTED_GPU_PCI},${gpu_opts}" | tee -a "$screen_capture"
        idx=$((idx + 1))
    fi

    # Remaining IOMMU group devices (audio, USB controllers, etc.)
    for dev in "${IOMMU_DEVICES[@]}"; do
        [[ "$dev" == "$SELECTED_GPU_PCI" ]] && continue
        if _is_pci_function_assigned_to_vm "$dev" "$SELECTED_VMID"; then
            msg_ok "$(translate 'Device already present in target VM — existing hostpci entry reused'): ${dev}" | tee -a "$screen_capture"
            continue
        fi
        qm set "$SELECTED_VMID" --hostpci${idx} "${dev},pcie=1" >>"$LOG_FILE" 2>&1
        msg_ok "$(translate 'Device added'): hostpci${idx}: ${dev},pcie=1" | tee -a "$screen_capture"
        idx=$((idx + 1))
    done

    # NVIDIA: hide KVM hypervisor from guest
    [[ "$SELECTED_GPU" == "nvidia" ]] && _configure_nvidia_kvm_hide
}

_configure_nvidia_kvm_hide() {
    msg_info "$(translate 'Configuring NVIDIA KVM hiding...')"

    # CPU: host,hidden=1
    local current_cpu
    current_cpu=$(qm config "$SELECTED_VMID" 2>/dev/null | grep "^cpu:" | awk '{print $2}')
    if ! echo "$current_cpu" | grep -q "hidden=1"; then
        qm set "$SELECTED_VMID" --cpu "host,hidden=1,flags=+pcid" >>"$LOG_FILE" 2>&1
        msg_ok "$(translate 'CPU set to host,hidden=1,flags=+pcid')" | tee -a "$screen_capture"
    else
        msg_ok "$(translate 'NVIDIA CPU hiding already configured')" | tee -a "$screen_capture"
    fi

    # args: kvm=off + vendor_id spoof
    local current_args
    current_args=$(qm config "$SELECTED_VMID" 2>/dev/null | grep "^args:" | sed 's/^args: //')
    if ! echo "$current_args" | grep -q "kvm=off"; then
        local kvm_args="-cpu 'host,+kvm_pv_unhalt,+kvm_pv_eoi,hv_vendor_id=NV43FIX,kvm=off'"
        local new_args
        if [[ -n "$current_args" ]]; then
            new_args="${current_args} ${kvm_args}"
        else
            new_args="$kvm_args"
        fi
        qm set "$SELECTED_VMID" --args "$new_args" >>"$LOG_FILE" 2>&1
        msg_ok "$(translate 'NVIDIA KVM args configured (kvm=off, vendor_id spoof)')" | tee -a "$screen_capture"
    else
        msg_ok "$(translate 'NVIDIA KVM hiding already configured')" | tee -a "$screen_capture"
    fi
}


# ── Update initramfs ─────────────────────────────────────
update_initramfs_host() {
    msg_info "$(translate 'Updating initramfs (this may take a minute)...')"
    update-initramfs -u -k all >>"$LOG_FILE" 2>&1
    msg_ok "$(translate 'initramfs updated')" | tee -a "$screen_capture"
}


# ==========================================================
# Main
# ==========================================================
main() {
    : >"$LOG_FILE"
    : >"$screen_capture"

    # ── Phase 1: all dialogs (no terminal output) ─────────
    detect_host_gpus
    check_iommu_enabled
    select_gpu
    warn_single_gpu
    select_vm
    ensure_selected_gpu_not_already_in_target_vm
    check_gpu_vm_compatibility
    analyze_iommu_group
    check_vm_machine_type
    check_switch_mode
    confirm_summary

    # ── Phase 2: processing ───────────────────────────────
    clear
    show_proxmenux_logo
    local run_title
    run_title=$(_get_vm_run_title)
    msg_title "${run_title}"

    add_vfio_modules
    configure_vfio_pci_ids
    configure_iommu_options
    [[ "$SELECTED_GPU" == "amd" ]] && add_softdep_amd
    blacklist_gpu_drivers
    [[ "$SELECTED_GPU" == "amd" ]] && dump_amd_rom
    cleanup_lxc_configs
    cleanup_vm_config
    ensure_vm_display_std
    configure_vm
    [[ "$HOST_CONFIG_CHANGED" == "true" ]] && update_initramfs_host

    # ── Phase 3: summary ─────────────────────────────────
    show_proxmenux_logo
    msg_title "${run_title}"
    cat "$screen_capture"

    echo
    echo -e "${TAB}${BL}📄 Log: ${LOG_FILE}${CL}"
    echo

    if [[ "$HOST_CONFIG_CHANGED" == "true" ]]; then
        msg_info2 "$(translate 'After rebooting, verify VFIO binding with:')"
        echo "    lspci -nnk | grep -A2 vfio-pci"
        echo
        msg_info2 "$(translate 'Next steps after reboot:')"
        echo "  1. $(translate 'Start the VM')"
    else
        msg_info2 "$(translate 'Host VFIO config was already up to date — no reboot needed.')"
        msg_info2 "$(translate 'Next steps:')"
        echo "  1. $(translate 'Start the VM')"
    fi

    case "$SELECTED_GPU" in
        nvidia)
            echo "  2. $(translate 'Install NVIDIA drivers from nvidia.com inside the guest')"
            echo "  3. $(translate 'If Code 43 error: KVM hiding is already configured')"
            ;;
        amd)
            echo "  2. $(translate 'Install AMD GPU drivers inside the guest')"
            echo "  3. $(translate 'If passthrough fails on Windows: install RadeonResetBugFix')"
            [[ -n "$AMD_ROM_FILE" ]] && \
            echo "     $(translate 'ROM file used'): /usr/share/kvm/${AMD_ROM_FILE}"
            ;;
        intel)
            echo "  2. $(translate 'Install Intel Graphics Driver inside the guest')"
            echo "  3. $(translate 'Enable Remote Desktop (RDP) before disabling the virtual display')"
            ;;
    esac

    echo
    msg_success "$(translate 'GPU passthrough configured for VM') ${SELECTED_VMID} (${VM_NAME})."
    echo

    rm -f "$screen_capture"

    if [[ "$HOST_CONFIG_CHANGED" == "true" ]]; then
        whiptail --title "$(translate 'Reboot Required')" \
            --yesno "$(translate 'A reboot is required for VFIO binding to take effect. Do you want to restart now?')" 10 68
        if [[ $? -eq 0 ]]; then
            msg_warn "$(translate 'Rebooting the system...')"
            reboot
        else
            msg_info2 "$(translate 'You can reboot later manually.')"
            msg_success "$(translate 'Press Enter to continue...')"
            read -r
        fi
    else
        msg_success "$(translate 'Press Enter to continue...')"
        read -r
    fi
}

main
