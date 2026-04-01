#!/bin/bash
# ==========================================================
# ProxMenux - Local Disk Manager for Proxmox Host
# ==========================================================
# Author      : MacRimi
# Copyright   : (c) 2024 MacRimi
# License     : MIT
# ==========================================================
# Description:
# Adds local SCSI/SATA/NVMe disks as Proxmox directory storage
# (pvesm add dir). The disk is formatted (ext4 or xfs), mounted
# permanently, and registered in Proxmox.
# ==========================================================

LOCAL_SCRIPTS="/usr/local/share/proxmenux/scripts"
BASE_DIR="/usr/local/share/proxmenux"
UTILS_FILE="$BASE_DIR/utils.sh"

if [[ -f "$UTILS_FILE" ]]; then
    source "$UTILS_FILE"
fi

load_language
initialize_cache

if ! command -v pveversion >/dev/null 2>&1; then
    dialog --backtitle "ProxMenux" --title "$(translate "Error")" \
        --msgbox "$(translate "This script must be run on a Proxmox host.")" 8 60
    exit 1
fi

# ==========================================================
# STORAGE CONFIG READER
# ==========================================================
get_storage_config() {
    local storage_id="$1"
    awk -v id="$storage_id" '
        /^[a-z]+: / { found = ($0 ~ ": "id"$"); next }
        found && /^[^ \t]/ { exit }
        found { print }
    ' /etc/pve/storage.cfg
}

# ==========================================================
# DISK DETECTION
# ==========================================================

get_available_disks() {
    # List block devices that are:
    # - Whole disks (not partitions, not loop, not dm)
    # - Not the system disk (where / is mounted)
    local system_disk
    system_disk=$(lsblk -ndo PKNAME "$(findmnt -n -o SOURCE /)" 2>/dev/null | head -1)

    while IFS= read -r line; do
        local name size type model ro
        name=$(echo "$line" | awk '{print $1}')
        size=$(echo "$line" | awk '{print $2}')
        type=$(echo "$line" | awk '{print $3}')
        model=$(echo "$line" | awk '{for(i=4;i<=NF;i++) printf "%s ", $i; print ""}' | sed 's/[[:space:]]*$//')
        ro=$(echo "$line" | awk '{print $NF}')

        # Only whole disks
        [[ "$type" != "disk" ]] && continue
        # Skip read-only
        [[ "$ro" == "1" ]] && continue
        # Skip system disk
        [[ "$name" == "$system_disk" ]] && continue

        # Check if fully mounted (any partition or the disk itself is mounted at /)
        local is_mounted=false
        if lsblk -no MOUNTPOINT "/dev/$name" 2>/dev/null | grep -qE "^/[[:space:]]*$|^/boot"; then
            is_mounted=true
        fi
        [[ "$is_mounted" == true ]] && continue

        local info="${size}"
        [[ -n "$model" && "$model" != " " ]] && info="${size} — ${model}"

        # Show mount status
        local mount_info
        mount_info=$(lsblk -no MOUNTPOINT "/dev/$name" 2>/dev/null | grep -v "^$" | tr '\n' ' ' | sed 's/[[:space:]]*$//')
        if [[ -n "$mount_info" ]]; then
            info="${info} [${mount_info}]"
        fi

        echo "/dev/$name|$info"
    done < <(lsblk -ndo NAME,SIZE,TYPE,MODEL,RO 2>/dev/null)
}

select_disk() {
    show_proxmenux_logo
    msg_title "$(translate "Add Local Disk as Proxmox Storage")"
    msg_info "$(translate "Scanning available disks...")"

    local disk_list
    disk_list=$(get_available_disks)

    if [[ -z "$disk_list" ]]; then
        dialog --backtitle "ProxMenux" --title "$(translate "No Disks Found")" \
            --msgbox "\n$(translate "No available disks found.")\n\n$(translate "All disks may already be in use or mounted.")" 10 60
        return 1
    fi

    local options=()
    while IFS='|' read -r device info; do
        [[ -n "$device" ]] && options+=("$device" "$info")
    done <<< "$disk_list"

    if [[ ${#options[@]} -eq 0 ]]; then
        dialog --backtitle "ProxMenux" --title "$(translate "No Disks Found")" \
            --msgbox "\n$(translate "No suitable disks found.")" 8 60
        return 1
    fi

    SELECTED_DISK=$(dialog --backtitle "ProxMenux" --title "$(translate "Select Disk")" \
        --menu "\n$(translate "Select the disk to add as Proxmox storage:")\n$(translate "WARNING: All data on selected disk will be ERASED if formatted.")" \
        20 80 10 "${options[@]}" 3>&1 1>&2 2>&3)
    [[ -z "$SELECTED_DISK" ]] && return 1

    return 0
}

inspect_disk() {
    local disk="$1"

    # Check existing partitions/filesystem
    local partition_info
    partition_info=$(lsblk -no NAME,SIZE,FSTYPE,MOUNTPOINT "$disk" 2>/dev/null | tail -n +2)

    local existing_fs
    existing_fs=$(blkid -s TYPE -o value "$disk" 2>/dev/null || true)

    DISK_HAS_DATA=false
    DISK_EXISTING_FS=""

    if [[ -n "$partition_info" || -n "$existing_fs" ]]; then
        DISK_HAS_DATA=true
        DISK_EXISTING_FS="$existing_fs"
    fi

    return 0
}

select_partition_action() {
    local disk="$1"
    inspect_disk "$disk"

    local disk_size
    disk_size=$(lsblk -ndo SIZE "$disk" 2>/dev/null)

    if [[ "$DISK_HAS_DATA" == "true" ]]; then
        local msg="$(translate "Disk:"): $disk ($disk_size)\n"
        [[ -n "$DISK_EXISTING_FS" ]] && msg+="$(translate "Existing filesystem:"): $DISK_EXISTING_FS\n"
        msg+="\n$(translate "Options:")\n"
        msg+="• $(translate "Format: ERASE all data and create new filesystem")\n"
        [[ -n "$DISK_EXISTING_FS" ]] && msg+="• $(translate "Use existing: mount without formatting")\n"
        msg+="\n$(translate "Continue?")"

        DISK_ACTION=$(whiptail --title "$(translate "Disk Setup")" \
            --menu "$msg" 20 80 3 \
            "format" "$(translate "Format disk (ERASE all data)")" \
            $(if [[ -n "$DISK_EXISTING_FS" ]]; then echo '"use_existing" "'"$(translate "Use existing filesystem")"'"'; fi) \
            "cancel" "$(translate "Cancel")" \
            3>&1 1>&2 2>&3)
    else
        DISK_ACTION=$(whiptail --title "$(translate "Disk Setup")" \
            --menu "$(translate "Disk:"): $disk ($disk_size)\n\n$(translate "Disk appears empty. It will be formatted.")" \
            14 70 2 \
            "format"  "$(translate "Format and add as Proxmox storage")" \
            "cancel"  "$(translate "Cancel")" \
            3>&1 1>&2 2>&3)
    fi

    [[ -z "$DISK_ACTION" || "$DISK_ACTION" == "cancel" ]] && return 1
    return 0
}

select_filesystem() {
    FILESYSTEM=$(whiptail --title "$(translate "Select Filesystem")" \
        --menu "$(translate "Choose filesystem for the disk:")" 14 60 3 \
        "ext4" "$(translate "ext4 — recommended, most compatible")" \
        "xfs"  "$(translate "xfs  — better for large files and VMs")" \
        3>&1 1>&2 2>&3)
    [[ -z "$FILESYSTEM" ]] && return 1
    return 0
}

# ==========================================================
# STORAGE CONFIGURATION
# ==========================================================

configure_disk_storage() {
    local disk_name
    disk_name=$(basename "$SELECTED_DISK")

    STORAGE_ID=$(whiptail --inputbox "$(translate "Enter storage ID for Proxmox:")" \
        10 60 "disk-${disk_name}" \
        --title "$(translate "Storage ID")" 3>&1 1>&2 2>&3)
    [[ $? -ne 0 ]] && return 1
    [[ -z "$STORAGE_ID" ]] && STORAGE_ID="disk-${disk_name}"

    if [[ ! "$STORAGE_ID" =~ ^[a-zA-Z0-9][a-zA-Z0-9_-]*$ ]]; then
        whiptail --msgbox "$(translate "Invalid storage ID. Use only letters, numbers, hyphens and underscores.")" 8 70
        return 1
    fi

    MOUNT_PATH="/mnt/${STORAGE_ID}"
    MOUNT_PATH=$(whiptail --inputbox "$(translate "Enter mount path on host:")" \
        10 60 "$MOUNT_PATH" \
        --title "$(translate "Mount Path")" 3>&1 1>&2 2>&3)
    [[ $? -ne 0 || -z "$MOUNT_PATH" ]] && return 1

    CONTENT_TYPE=$(whiptail --title "$(translate "Content Types")" \
        --menu "$(translate "Select content types for this storage:")" 16 70 5 \
        "1" "$(translate "VM Storage     (images, backup)")" \
        "2" "$(translate "Standard NAS   (backup, iso, vztmpl)")" \
        "3" "$(translate "All types      (images, backup, iso, vztmpl, snippets)")" \
        "4" "$(translate "Custom")" \
        3>&1 1>&2 2>&3)
    [[ $? -ne 0 ]] && return 1

    case "$CONTENT_TYPE" in
        1) MOUNT_CONTENT="images,backup" ;;
        2) MOUNT_CONTENT="backup,iso,vztmpl" ;;
        3) MOUNT_CONTENT="images,backup,iso,vztmpl,snippets" ;;
        4)
            MOUNT_CONTENT=$(whiptail --inputbox "$(translate "Enter content types (comma-separated):")" \
                10 70 "images,backup" --title "$(translate "Custom Content")" 3>&1 1>&2 2>&3)
            [[ $? -ne 0 || -z "$MOUNT_CONTENT" ]] && MOUNT_CONTENT="images,backup"
            ;;
        *) return 1 ;;
    esac

    return 0
}

# ==========================================================
# DISK SETUP AND MOUNT
# ==========================================================

format_and_mount_disk() {
    local disk="$1"
    local mount_path="$2"
    local filesystem="$3"

    # Final confirmation before any destructive operation
    local disk_size
    disk_size=$(lsblk -ndo SIZE "$disk" 2>/dev/null)
    if ! whiptail --yesno \
        "$(translate "FINAL CONFIRMATION — DATA WILL BE ERASED")\n\n$(translate "Disk:"): $disk ($disk_size)\n$(translate "Filesystem:"): $filesystem\n$(translate "Mount path:"): $mount_path\n\n$(translate "ALL DATA ON") $disk $(translate "WILL BE PERMANENTLY ERASED.")\n\n$(translate "Are you absolutely sure?")" \
        14 80 --title "$(translate "CONFIRM FORMAT")"; then
        return 1
    fi

    msg_info "$(translate "Wiping existing partition table...")"
    wipefs -a "$disk" >/dev/null 2>&1 || true
    sgdisk --zap-all "$disk" >/dev/null 2>&1 || true

    msg_info "$(translate "Creating partition...")"
    if ! parted -s "$disk" mklabel gpt mkpart primary 0% 100% >/dev/null 2>&1; then
        msg_error "$(translate "Failed to create partition table")"
        return 1
    fi

    # Wait for kernel to recognize new partition
    sleep 2
    partprobe "$disk" 2>/dev/null || true
    sleep 1

    # Determine partition device
    local partition
    if [[ "$disk" =~ [0-9]$ ]]; then
        partition="${disk}p1"
    else
        partition="${disk}1"
    fi

    msg_info "$(translate "Formatting as") $filesystem..."
    case "$filesystem" in
        ext4)
            if ! mkfs.ext4 -F -L "$STORAGE_ID" "$partition" >/dev/null 2>&1; then
                msg_error "$(translate "Failed to format disk as ext4")"
                return 1
            fi
            ;;
        xfs)
            if ! mkfs.xfs -f -L "$STORAGE_ID" "$partition" >/dev/null 2>&1; then
                msg_error "$(translate "Failed to format disk as xfs")"
                return 1
            fi
            ;;
    esac

    msg_ok "$(translate "Disk formatted as") $filesystem"

    DISK_PARTITION="$partition"
    return 0
}

mount_disk_permanently() {
    local partition="$1"
    local mount_path="$2"
    local filesystem="$3"

    msg_info "$(translate "Creating mount point...")"
    if ! mkdir -p "$mount_path"; then
        msg_error "$(translate "Failed to create mount point:") $mount_path"
        return 1
    fi

    msg_info "$(translate "Mounting disk...")"
    if ! mount -t "$filesystem" "$partition" "$mount_path" 2>/dev/null; then
        msg_error "$(translate "Failed to mount disk")"
        return 1
    fi
    msg_ok "$(translate "Disk mounted at") $mount_path"

    msg_info "$(translate "Adding to /etc/fstab for permanent mounting...")"
    local disk_uuid
    disk_uuid=$(blkid -s UUID -o value "$partition" 2>/dev/null)

    if [[ -n "$disk_uuid" ]]; then
        # Remove any existing fstab entry for this UUID or mount point
        sed -i "\|UUID=$disk_uuid|d" /etc/fstab
        sed -i "\|[[:space:]]$mount_path[[:space:]]|d" /etc/fstab
        echo "UUID=$disk_uuid  $mount_path  $filesystem  defaults,nofail  0  2" >> /etc/fstab
        msg_ok "$(translate "Added to /etc/fstab using UUID")"
    else
        sed -i "\|[[:space:]]$mount_path[[:space:]]|d" /etc/fstab
        echo "$partition  $mount_path  $filesystem  defaults,nofail  0  2" >> /etc/fstab
        msg_ok "$(translate "Added to /etc/fstab using device path")"
    fi

    systemctl daemon-reload 2>/dev/null || true
    return 0
}

mount_existing_disk() {
    local disk="$1"
    local mount_path="$2"

    local existing_fs
    existing_fs=$(blkid -s TYPE -o value "$disk" 2>/dev/null || true)

    if [[ -z "$existing_fs" ]]; then
        msg_error "$(translate "Cannot detect filesystem on") $disk"
        return 1
    fi

    msg_info "$(translate "Creating mount point...")"
    mkdir -p "$mount_path"

    msg_info "$(translate "Mounting existing") $existing_fs $(translate "filesystem...")"
    if ! mount "$disk" "$mount_path" 2>/dev/null; then
        msg_error "$(translate "Failed to mount disk")"
        return 1
    fi
    msg_ok "$(translate "Disk mounted at") $mount_path"

    # Add to fstab
    local disk_uuid
    disk_uuid=$(blkid -s UUID -o value "$disk" 2>/dev/null)
    if [[ -n "$disk_uuid" ]]; then
        sed -i "\|UUID=$disk_uuid|d" /etc/fstab
        sed -i "\|[[:space:]]$mount_path[[:space:]]|d" /etc/fstab
        echo "UUID=$disk_uuid  $mount_path  $existing_fs  defaults,nofail  0  2" >> /etc/fstab
        msg_ok "$(translate "Added to /etc/fstab")"
    fi

    DISK_PARTITION="$disk"
    systemctl daemon-reload 2>/dev/null || true
    return 0
}

add_proxmox_dir_storage() {
    local storage_id="$1"
    local path="$2"
    local content="$3"

    if ! command -v pvesm >/dev/null 2>&1; then
        msg_error "$(translate "pvesm command not found. This should not happen on Proxmox.")"
        return 1
    fi

    if pvesm status "$storage_id" >/dev/null 2>&1; then
        msg_warn "$(translate "Storage ID already exists:") $storage_id"
        if ! whiptail --yesno "$(translate "Storage ID already exists. Do you want to remove and recreate it?")" \
            8 60 --title "$(translate "Storage Exists")"; then
            return 0
        fi
        pvesm remove "$storage_id" 2>/dev/null || true
    fi

    msg_info "$(translate "Registering disk as Proxmox storage...")"
    local pvesm_output
    if pvesm_output=$(pvesm add dir "$storage_id" \
        --path "$path" \
        --content "$content" 2>&1); then

        msg_ok "$(translate "Directory storage added successfully to Proxmox!")"
        echo -e ""
        echo -e "${TAB}${BOLD}$(translate "Storage Added:")${CL}"
        echo -e "${TAB}${BGN}$(translate "Storage ID:")${CL} ${BL}$storage_id${CL}"
        echo -e "${TAB}${BGN}$(translate "Path:")${CL} ${BL}$path${CL}"
        echo -e "${TAB}${BGN}$(translate "Content Types:")${CL} ${BL}$content${CL}"
        echo -e ""
        msg_ok "$(translate "Storage is now available in Proxmox web interface under Datacenter > Storage")"
        return 0
    else
        msg_error "$(translate "Failed to add storage to Proxmox.")"
        echo -e "${TAB}$(translate "Error details:"): $pvesm_output"
        echo -e ""
        msg_info2 "$(translate "You can add it manually through:")"
        echo -e "${TAB}• $(translate "Proxmox web interface: Datacenter > Storage > Add > Directory")"
        echo -e "${TAB}• pvesm add dir $storage_id --path $path --content $content"
        return 1
    fi
}

# ==========================================================
# MAIN OPERATIONS
# ==========================================================

add_disk_to_proxmox() {
    # Check required tools
    for tool in parted mkfs.ext4 blkid lsblk; do
        if ! command -v "$tool" >/dev/null 2>&1; then
            msg_info "$(translate "Installing required tools...")"
            apt-get update &>/dev/null
            apt-get install -y parted e2fsprogs util-linux xfsprogs gdisk &>/dev/null
            break
        fi
    done

    # Step 1: Select disk
    select_disk || return

    # Step 2: Inspect and choose action
    select_partition_action "$SELECTED_DISK" || return

    # Step 3: Filesystem selection (only if formatting)
    if [[ "$DISK_ACTION" == "format" ]]; then
        select_filesystem || return
    fi

    # Step 4: Configure storage options
    configure_disk_storage || return

    show_proxmenux_logo
    msg_title "$(translate "Add Local Disk as Proxmox Storage")"
    msg_ok "$(translate "Disk:") $SELECTED_DISK"
    msg_ok "$(translate "Action:") $DISK_ACTION"
    [[ "$DISK_ACTION" == "format" ]] && msg_ok "$(translate "Filesystem:") $FILESYSTEM"
    msg_ok "$(translate "Mount path:") $MOUNT_PATH"
    msg_ok "$(translate "Storage ID:") $STORAGE_ID"
    msg_ok "$(translate "Content:") $MOUNT_CONTENT"
    echo ""

    # Step 5: Format/mount
    case "$DISK_ACTION" in
        format)
            format_and_mount_disk "$SELECTED_DISK" "$MOUNT_PATH" "$FILESYSTEM" || {
                echo ""
                msg_success "$(translate "Press Enter to continue...")"
                read -r
                return 1
            }
            mount_disk_permanently "$DISK_PARTITION" "$MOUNT_PATH" "$FILESYSTEM" || {
                echo ""
                msg_success "$(translate "Press Enter to continue...")"
                read -r
                return 1
            }
            ;;
        use_existing)
            mount_existing_disk "$SELECTED_DISK" "$MOUNT_PATH" || {
                echo ""
                msg_success "$(translate "Press Enter to continue...")"
                read -r
                return 1
            }
            ;;
    esac

    # Step 6: Register in Proxmox
    add_proxmox_dir_storage "$STORAGE_ID" "$MOUNT_PATH" "$MOUNT_CONTENT"

    echo ""
    msg_success "$(translate "Press Enter to continue...")"
    read -r
}

view_disk_storages() {
    show_proxmenux_logo
    msg_title "$(translate "Local Disk Storages in Proxmox")"

    echo "=================================================="
    echo ""

    if ! command -v pvesm >/dev/null 2>&1; then
        msg_error "$(translate "pvesm not found.")"
        echo ""
        msg_success "$(translate "Press Enter to continue...")"
        read -r
        return
    fi

    # Show all directory storages
    DIR_STORAGES=$(pvesm status 2>/dev/null | awk '$2 == "dir" {print $1, $3}')
    if [[ -z "$DIR_STORAGES" ]]; then
        msg_warn "$(translate "No directory storage configured in Proxmox.")"
        echo ""
        msg_info2 "$(translate "Use option 1 to add a local disk as Proxmox storage.")"
    else
        echo -e "${BOLD}$(translate "Directory Storages:")${CL}"
        echo ""
        while IFS=" " read -r storage_id storage_status; do
            [[ -z "$storage_id" ]] && continue
            local storage_info path content
            storage_info=$(get_storage_config "$storage_id")
            path=$(echo "$storage_info" | awk '$1 == "path" {print $2}')
            content=$(echo "$storage_info" | awk '$1 == "content" {print $2}')

            local disk_device=""
            if [[ -n "$path" ]]; then
                disk_device=$(findmnt -n -o SOURCE "$path" 2>/dev/null || true)
            fi

            local disk_size=""
            if [[ -n "$disk_device" ]]; then
                disk_size=$(lsblk -ndo SIZE "$disk_device" 2>/dev/null || true)
            fi

            echo -e "${TAB}${BOLD}$storage_id${CL}"
            echo -e "${TAB}  ${BGN}$(translate "Path:")${CL} ${BL}$path${CL}"
            [[ -n "$disk_device" ]] && echo -e "${TAB}  ${BGN}$(translate "Device:")${CL} ${BL}$disk_device${CL}"
            [[ -n "$disk_size" ]] && echo -e "${TAB}  ${BGN}$(translate "Size:")${CL} ${BL}$disk_size${CL}"
            echo -e "${TAB}  ${BGN}$(translate "Content:")${CL} ${BL}$content${CL}"
            if [[ "$storage_status" == "active" ]]; then
                echo -e "${TAB}  ${BGN}$(translate "Status:")${CL} ${GN}$(translate "Active")${CL}"
            else
                echo -e "${TAB}  ${BGN}$(translate "Status:")${CL} ${RD}$storage_status${CL}"
            fi
            echo ""
        done <<< "$DIR_STORAGES"
    fi

    echo ""
    msg_success "$(translate "Press Enter to continue...")"
    read -r
}

remove_disk_storage() {
    if ! command -v pvesm >/dev/null 2>&1; then
        dialog --backtitle "ProxMenux" --title "$(translate "Error")" \
            --msgbox "\n$(translate "pvesm not found.")" 8 60
        return
    fi

    DIR_STORAGES=$(pvesm status 2>/dev/null | awk '$2 == "dir" {print $1}')
    if [[ -z "$DIR_STORAGES" ]]; then
        dialog --backtitle "ProxMenux" --title "$(translate "No Disk Storage")" \
            --msgbox "\n$(translate "No directory storage found in Proxmox.")" 8 60
        return
    fi

    OPTIONS=()
    while IFS= read -r storage_id; do
        [[ -z "$storage_id" ]] && continue
        local path
        path=$(get_storage_config "$storage_id" | awk '$1 == "path" {print $2}')
        OPTIONS+=("$storage_id" "${path:-unknown}")
    done <<< "$DIR_STORAGES"

    SELECTED=$(dialog --backtitle "ProxMenux" --title "$(translate "Remove Disk Storage")" \
        --menu "$(translate "Select storage to remove:")" 20 80 10 \
        "${OPTIONS[@]}" 3>&1 1>&2 2>&3)
    [[ -z "$SELECTED" ]] && return

    local path content
    path=$(get_storage_config "$SELECTED" | awk '$1 == "path" {print $2}')
    content=$(get_storage_config "$SELECTED" | awk '$1 == "content" {print $2}')

    if whiptail --yesno "$(translate "Remove Proxmox storage:")\n\n$SELECTED\n\n$(translate "Path:"): $path\n$(translate "Content:"): $content\n\n$(translate "This removes the storage registration from Proxmox.")\n$(translate "The disk and its data will NOT be erased.")\n$(translate "The disk will remain mounted at:"): $path" \
        18 80 --title "$(translate "Confirm Remove")"; then

        show_proxmenux_logo
        msg_title "$(translate "Remove Disk Storage")"

        if pvesm remove "$SELECTED" 2>/dev/null; then
            msg_ok "$(translate "Storage") $SELECTED $(translate "removed from Proxmox.")"
            echo ""
            msg_info2 "$(translate "The disk remains mounted at:"): $path"
            msg_info2 "$(translate "The fstab entry is still present. Remove manually if needed.")"
        else
            msg_error "$(translate "Failed to remove storage.")"
        fi

        echo ""
        msg_success "$(translate "Press Enter to continue...")"
        read -r
    fi
}

list_available_disks() {
    show_proxmenux_logo
    msg_title "$(translate "Available Disks on Host")"

    echo "=================================================="
    echo ""

    echo -e "${BOLD}$(translate "All block devices:")${CL}"
    echo ""
    lsblk -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINT,MODEL 2>/dev/null
    echo ""

    echo -e "${BOLD}$(translate "Proxmox directory storages:")${CL}"
    if command -v pvesm >/dev/null 2>&1; then
        pvesm status 2>/dev/null | awk '$2 == "dir" {print "  " $1, $2, $3}' || echo "  $(translate "None")"
    fi

    echo ""
    msg_success "$(translate "Press Enter to continue...")"
    read -r
}

# ==========================================================
# MAIN MENU
# ==========================================================

while true; do
    CHOICE=$(dialog --backtitle "ProxMenux" \
        --title "$(translate "Local Disk Manager - Proxmox Host")" \
        --menu "$(translate "Choose an option:")" 18 70 6 \
        "1" "$(translate "Add Local Disk as Proxmox Storage")" \
        "2" "$(translate "View Disk Storages")" \
        "3" "$(translate "Remove Disk Storage")" \
        "4" "$(translate "List Available Disks")" \
        "5" "$(translate "Exit")" \
        3>&1 1>&2 2>&3)

    RETVAL=$?
    if [[ $RETVAL -ne 0 ]]; then
        exit 0
    fi

    case $CHOICE in
        1) add_disk_to_proxmox ;;
        2) view_disk_storages ;;
        3) remove_disk_storage ;;
        4) list_available_disks ;;
        5) exit 0 ;;
        *) exit 0 ;;
    esac
done
