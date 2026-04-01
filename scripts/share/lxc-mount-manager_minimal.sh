#!/bin/bash
# ==========================================================
# ProxMenux - LXC Mount Manager
# ==========================================================
# Author      : MacRimi
# Copyright   : (c) 2024 MacRimi
# License     : MIT
# ==========================================================
# Description:
# Adds bind mounts from Proxmox host directories into LXC
# containers using pct set -mpX (Proxmox native).
#
# SAFE DESIGN: This script NEVER modifies permissions, ownership,
# or ACLs on the host or inside the container. All existing
# configurations are preserved as-is.
# ==========================================================

BASE_DIR="/usr/local/share/proxmenux"
source "$BASE_DIR/utils.sh"

load_language
initialize_cache

# ==========================================================
# DIRECTORY DETECTION
# ==========================================================

detect_mounted_shares() {
    local mounted_shares=()

    while IFS= read -r line; do
        local device mount_point fs_type
        read -r device mount_point fs_type _ <<< "$line"

        local type=""
        case "$fs_type" in
            nfs|nfs4) type="NFS" ;;
            cifs)     type="CIFS/SMB" ;;
            *)        continue ;;
        esac

        # Skip internal Proxmox mounts
        local skip=false
        for internal in /mnt/pve/local /mnt/pve/local-lvm /mnt/pve/local-zfs \
                        /mnt/pve/backup /mnt/pve/dump /mnt/pve/images \
                        /mnt/pve/template /mnt/pve/snippets /mnt/pve/vztmpl; do
            if [[ "$mount_point" == "$internal" || "$mount_point" =~ ^${internal}/ ]]; then
                skip=true
                break
            fi
        done
        [[ "$skip" == true ]] && continue

        local size used
        local df_info
        df_info=$(df -h "$mount_point" 2>/dev/null | tail -n1)
        if [[ -n "$df_info" ]]; then
            size=$(echo "$df_info" | awk '{print $2}')
            used=$(echo "$df_info" | awk '{print $3}')
        else
            size="N/A"
            used="N/A"
        fi

        local source="Manual"
        [[ "$mount_point" =~ ^/mnt/pve/ ]] && source="Proxmox-Storage"

        mounted_shares+=("$mount_point|$device|$type|$size|$used|$source")
    done < /proc/mounts

    printf '%s\n' "${mounted_shares[@]}"
}

detect_fstab_network_mounts() {
    local fstab_mounts=()

    while IFS= read -r line; do
        [[ "$line" =~ ^[[:space:]]*# ]] && continue
        [[ -z "${line// }" ]] && continue

        local source mount_point fs_type
        read -r source mount_point fs_type _ <<< "$line"

        local type=""
        case "$fs_type" in
            nfs|nfs4) type="NFS" ;;
            cifs)     type="CIFS/SMB" ;;
            *)        continue ;;
        esac

        [[ ! -d "$mount_point" ]] && continue

        # Skip if already mounted (already captured by detect_mounted_shares)
        local is_mounted=false
        while IFS= read -r proc_line; do
            local proc_mp proc_fs
            read -r _ proc_mp proc_fs _ <<< "$proc_line"
            if [[ "$proc_mp" == "$mount_point" && ("$proc_fs" == "nfs" || "$proc_fs" == "nfs4" || "$proc_fs" == "cifs") ]]; then
                is_mounted=true
                break
            fi
        done < /proc/mounts

        [[ "$is_mounted" == false ]] && fstab_mounts+=("$mount_point|$source|$type|0|0|fstab-inactive")
    done < /etc/fstab

    printf '%s\n' "${fstab_mounts[@]}"
}

detect_local_directories() {
    local local_dirs=()
    local network_mps=()

    # Collect network mount points to exclude
    while IFS='|' read -r mp _ _ _ _ _; do
        [[ -n "$mp" ]] && network_mps+=("$mp")
    done < <({ detect_mounted_shares; detect_fstab_network_mounts; })

    if [[ -d "/mnt" ]]; then
        for dir in /mnt/*/; do
            [[ ! -d "$dir" ]] && continue
            local dir_path="${dir%/}"
            [[ "$(basename "$dir_path")" == "pve" ]] && continue

            local is_network=false
            for nmp in "${network_mps[@]}"; do
                [[ "$dir_path" == "$nmp" ]] && is_network=true && break
            done
            [[ "$is_network" == true ]] && continue

            local dir_size
            dir_size=$(du -sh "$dir_path" 2>/dev/null | awk '{print $1}')
            local_dirs+=("$dir_path|Local|Directory|$dir_size|-|Manual")
        done
    fi

    printf '%s\n' "${local_dirs[@]}"
}

# ==========================================================
# HOST DIRECTORY SELECTION
# ==========================================================

detect_problematic_storage() {
    local dir="$1"
    local check_source="$2"
    local check_type="$3"

    while IFS='|' read -r mp _ type _ _ source; do
        if [[ "$mp" == "$dir" && "$source" == "$check_source" && "$type" == "$check_type" ]]; then
            return 0
        fi
    done < <(detect_mounted_shares)
    return 1
}

select_host_directory_unified() {
    local mounted_shares fstab_mounts local_dirs
    mounted_shares=$(detect_mounted_shares)
    fstab_mounts=$(detect_fstab_network_mounts)
    local_dirs=$(detect_local_directories)

    # Deduplicate and build option list
    local all_entries=()
    declare -A seen_paths

    # Process network shares (mounted + fstab)
    while IFS='|' read -r mp device type size used source; do
        [[ -z "$mp" ]] && continue
        [[ -n "${seen_paths[$mp]}" ]] && continue
        seen_paths["$mp"]=1

        local prefix=""
        case "$source" in
            "Proxmox-Storage") prefix="PVE-" ;;
            "fstab-inactive")  prefix="fstab(off)-" ;;
            *)                 prefix="" ;;
        esac

        local info="${prefix}${type}"
        [[ "$size" != "N/A" && "$size" != "0" ]] && info="${info} [${used}/${size}]"
        all_entries+=("$mp" "$info")
    done < <(echo "$mounted_shares"; echo "$fstab_mounts")

    # Process local directories
    while IFS='|' read -r mp _ type size _ _; do
        [[ -z "$mp" ]] && continue
        [[ -n "${seen_paths[$mp]}" ]] && continue
        seen_paths["$mp"]=1
        local info="Local"
        [[ -n "$size" && "$size" != "0" ]] && info="Local [${size}]"
        all_entries+=("$mp" "$info")
    done < <(echo "$local_dirs")

    # Add Proxmox storage paths (/mnt/pve/*)
    if [[ -d "/mnt/pve" ]]; then
        for dir in /mnt/pve/*/; do
            [[ ! -d "$dir" ]] && continue
            local dir_path="${dir%/}"
            [[ -n "${seen_paths[$dir_path]}" ]] && continue
            seen_paths["$dir_path"]=1
            all_entries+=("$dir_path" "Proxmox-Storage")
        done
    fi

    all_entries+=("MANUAL" "$(translate "Enter path manually")")

    local result
    result=$(dialog --clear --colors --title "$(translate "Select Host Directory")" \
        --menu "\n$(translate "Select the directory to bind to container:")" 25 85 15 \
        "${all_entries[@]}" 3>&1 1>&2 2>&3)

    local dialog_exit=$?
    [[ $dialog_exit -ne 0 ]] && return 1
    [[ -z "$result" || "$result" =~ ^━ ]] && return 1

    if [[ "$result" == "MANUAL" ]]; then
        result=$(whiptail --title "$(translate "Manual Path Entry")" \
            --inputbox "$(translate "Enter the full path to the host directory:")" \
            10 70 "/mnt/" 3>&1 1>&2 2>&3)
        [[ $? -ne 0 ]] && return 1
    fi

    [[ -z "$result" ]] && return 1

    if [[ ! -d "$result" ]]; then
        whiptail --title "$(translate "Invalid Path")" \
            --msgbox "$(translate "The selected path is not a valid directory:") $result" 8 70
        return 1
    fi

    # Warn about CIFS Proxmox-GUI storage (read-only limitation)
    if detect_problematic_storage "$result" "Proxmox-Storage" "CIFS/SMB"; then
        dialog --clear --title "$(translate "CIFS Storage Notice")" --yesno "\
$(translate "This directory is a CIFS storage managed by Proxmox.")\n\n\
$(translate "CIFS storage configured through Proxmox GUI applies restrictive permissions.")\n\
$(translate "LXC containers can usually READ but may NOT be able to WRITE.")\n\n\
$(translate "For write access, use 'Add Samba Share as Proxmox Storage' option instead.")\n\n\
$(translate "Do you want to continue anyway?")" 14 80 3>&1 1>&2 2>&3
        [[ $? -ne 0 ]] && return 1
    fi

    echo "$result"
    return 0
}

# ==========================================================
# CONTAINER SELECTION
# ==========================================================

select_lxc_container() {
    local ct_list
    ct_list=$(pct list 2>/dev/null | awk 'NR>1 {print $1, $2, $3}')
    if [[ -z "$ct_list" ]]; then
        whiptail --title "Error" --msgbox "$(translate "No LXC containers available")" 8 50
        return 1
    fi

    local options=()
    while read -r id name status; do
        [[ -n "$id" && "$id" =~ ^[0-9]+$ ]] && options+=("$id" "${name:-unnamed} ($status)")
    done <<< "$ct_list"

    if [[ ${#options[@]} -eq 0 ]]; then
        dialog --title "Error" --msgbox "$(translate "No valid containers found")" 8 50
        return 1
    fi

    local ctid
    ctid=$(dialog --title "$(translate "Select LXC Container")" \
        --menu "$(translate "Select container:")" 25 85 15 \
        "${options[@]}" 3>&1 1>&2 2>&3)

    [[ $? -ne 0 || -z "$ctid" ]] && return 1
    echo "$ctid"
    return 0
}

select_container_mount_point() {
    local ctid="$1"
    local host_dir="$2"
    local base_name
    base_name=$(basename "$host_dir")

    while true; do
        local choice
        choice=$(dialog --clear --title "$(translate "Configure Mount Point inside LXC")" \
            --menu "\n$(translate "Where to mount inside container?")" 16 70 3 \
            "1" "$(translate "Create new directory in /mnt")" \
            "2" "$(translate "Enter path manually")" \
            "3" "$(translate "Cancel")" 3>&1 1>&2 2>&3)
        [[ $? -ne 0 ]] && return 1

        local mount_point
        case "$choice" in
            1)
                mount_point=$(whiptail --inputbox "$(translate "Enter folder name for /mnt:")" \
                    10 60 "$base_name" 3>&1 1>&2 2>&3)
                [[ $? -ne 0 || -z "$mount_point" ]] && continue
                mount_point="/mnt/$mount_point"
                ;;
            2)
                mount_point=$(whiptail --inputbox "$(translate "Enter full path:")" \
                    10 70 "/mnt/$base_name" 3>&1 1>&2 2>&3)
                [[ $? -ne 0 || -z "$mount_point" ]] && continue
                ;;
            3) return 1 ;;
        esac

        # Validate path format
        if [[ ! "$mount_point" =~ ^/ ]]; then
            whiptail --msgbox "$(translate "Path must be absolute (start with /)")" 8 60
            continue
        fi

        # Check if path is already used as a mount point in this CT
        if pct config "$ctid" 2>/dev/null | grep -q "mp=.*$mount_point"; then
            whiptail --msgbox "$(translate "This path is already used as a mount point in this container.")" 8 70
            continue
        fi

        # Create directory inside CT (only if CT is running)
        local ct_status
        ct_status=$(pct status "$ctid" 2>/dev/null | awk '{print $2}')
        if [[ "$ct_status" == "running" ]]; then
            pct exec "$ctid" -- mkdir -p "$mount_point" 2>/dev/null
        fi

        echo "$mount_point"
        return 0
    done
}

# ==========================================================
# MOUNT MANAGEMENT
# ==========================================================

get_next_mp_index() {
    local ctid="$1"
    local conf="/etc/pve/lxc/${ctid}.conf"

    if [[ ! "$ctid" =~ ^[0-9]+$ ]] || [[ ! -f "$conf" ]]; then
        echo "0"
        return 0
    fi

    local next=0
    local used
    used=$(awk -F: '/^mp[0-9]+:/ {print $1}' "$conf" | sed 's/mp//' | sort -n)
    for idx in $used; do
        [[ "$idx" -ge "$next" ]] && next=$((idx + 1))
    done
    echo "$next"
}

add_bind_mount() {
    local ctid="$1"
    local host_path="$2"
    local ct_path="$3"

    if [[ ! "$ctid" =~ ^[0-9]+$ || -z "$host_path" || -z "$ct_path" ]]; then
        msg_error "$(translate "Invalid parameters for bind mount")"
        return 1
    fi

    # Check if this host path is already mounted in this CT
    if pct config "$ctid" 2>/dev/null | grep -q "^mp[0-9]*:.*${host_path},"; then
        msg_warn "$(translate "Mount already exists for this path in container") $ctid"
        return 1
    fi

    local mpidx
    mpidx=$(get_next_mp_index "$ctid")

    local result
    result=$(pct set "$ctid" -mp${mpidx} "$host_path,mp=$ct_path,shared=1,backup=0" 2>&1)

    if [[ $? -eq 0 ]]; then
        msg_ok "$(translate "Bind mount added:") $host_path → $ct_path (mp${mpidx})"
        return 0
    else
        msg_error "$(translate "Failed to add bind mount:") $result"
        return 1
    fi
}

# ==========================================================
# VIEW / REMOVE
# ==========================================================

view_mount_points() {
    show_proxmenux_logo
    msg_title "$(translate "Current LXC Mount Points")"

    local ct_list
    ct_list=$(pct list 2>/dev/null | awk 'NR>1 {print $1, $2, $3}')
    if [[ -z "$ct_list" ]]; then
        msg_warn "$(translate "No LXC containers found")"
        echo ""
        msg_success "$(translate "Press Enter to continue...")"
        read -r
        return 1
    fi

    local found_mounts=false

    while read -r id name status; do
        [[ -z "$id" || ! "$id" =~ ^[0-9]+$ ]] && continue
        local conf="/etc/pve/lxc/${id}.conf"
        [[ ! -f "$conf" ]] && continue

        local mounts
        mounts=$(grep "^mp[0-9]*:" "$conf" 2>/dev/null)
        [[ -z "$mounts" ]] && continue

        found_mounts=true
        echo -e "${TAB}${BOLD}$(translate "Container") $id: $name ($status)${CL}"

        while IFS= read -r mount_line; do
            [[ -z "$mount_line" ]] && continue
            local mp_id mount_info host_path container_path options
            mp_id=$(echo "$mount_line" | cut -d: -f1)
            mount_info=$(echo "$mount_line" | cut -d: -f2-)
            host_path=$(echo "$mount_info" | cut -d, -f1)
            container_path=$(echo "$mount_info" | grep -o 'mp=[^,]*' | cut -d= -f2)
            options=$(echo "$mount_info" | sed 's/^[^,]*,mp=[^,]*,*//')

            echo -e "${TAB}  ${BGN}$mp_id:${CL} ${BL}$host_path${CL} → ${BL}$container_path${CL}"
            [[ -n "$options" ]] && echo -e "${TAB}    ${DGN}$options${CL}"
        done <<< "$mounts"
        echo ""
    done <<< "$ct_list"

    if [[ "$found_mounts" == false ]]; then
        msg_ok "$(translate "No mount points found in any container")"
    fi

    echo ""
    msg_success "$(translate "Press Enter to continue...")"
    read -r
}

remove_mount_point() {
    show_proxmenux_logo
    msg_title "$(translate "Remove LXC Mount Point")"

    local container_id
    container_id=$(select_lxc_container)
    [[ $? -ne 0 || -z "$container_id" ]] && return 1

    local conf="/etc/pve/lxc/${container_id}.conf"
    if [[ ! -f "$conf" ]]; then
        msg_error "$(translate "Container configuration not found")"
        echo ""
        msg_success "$(translate "Press Enter to continue...")"
        read -r
        return 1
    fi

    local mounts
    mounts=$(grep "^mp[0-9]*:" "$conf" 2>/dev/null)
    if [[ -z "$mounts" ]]; then
        show_proxmenux_logo
        msg_title "$(translate "Remove LXC Mount Point")"
        msg_warn "$(translate "No mount points found in container") $container_id"
        echo ""
        msg_success "$(translate "Press Enter to continue...")"
        read -r
        return 1
    fi

    local options=()
    while IFS= read -r mount_line; do
        [[ -z "$mount_line" ]] && continue
        local mp_id mount_info host_path container_path
        mp_id=$(echo "$mount_line" | cut -d: -f1)
        mount_info=$(echo "$mount_line" | cut -d: -f2-)
        host_path=$(echo "$mount_info" | cut -d, -f1)
        container_path=$(echo "$mount_info" | grep -o 'mp=[^,]*' | cut -d= -f2)
        options+=("$mp_id" "$host_path → $container_path")
    done <<< "$mounts"

    if [[ ${#options[@]} -eq 0 ]]; then
        show_proxmenux_logo
        msg_title "$(translate "Remove LXC Mount Point")"
        msg_warn "$(translate "No valid mount points found")"
        echo ""
        msg_success "$(translate "Press Enter to continue...")"
        read -r
        return 1
    fi

    local selected_mp
    selected_mp=$(dialog --clear --title "$(translate "Select Mount Point to Remove")" \
        --menu "\n$(translate "Select mount point to remove from container") $container_id:" 20 80 10 \
        "${options[@]}" 3>&1 1>&2 2>&3)
    [[ $? -ne 0 || -z "$selected_mp" ]] && return 1

    local selected_mount_line mount_info host_path container_path
    selected_mount_line=$(grep "^${selected_mp}:" "$conf")
    mount_info=$(echo "$selected_mount_line" | cut -d: -f2-)
    host_path=$(echo "$mount_info" | cut -d, -f1)
    container_path=$(echo "$mount_info" | grep -o 'mp=[^,]*' | cut -d= -f2)

    local confirm_msg
    confirm_msg="$(translate "Remove Mount Point Confirmation:")

$(translate "Container ID"): $container_id
$(translate "Mount Point ID"): $selected_mp
$(translate "Host Path"): $host_path
$(translate "Container Path"): $container_path

$(translate "NOTE: The host directory and its contents will remain unchanged.")

$(translate "Proceed with removal")?"

    if ! dialog --clear --title "$(translate "Confirm Mount Point Removal")" --yesno "$confirm_msg" 18 80; then
        return 1
    fi

    show_proxmenux_logo
    msg_title "$(translate "Remove LXC Mount Point")"
    msg_info "$(translate "Removing mount point") $selected_mp $(translate "from container") $container_id..."

    if pct set "$container_id" --delete "$selected_mp" 2>/dev/null; then
        msg_ok "$(translate "Mount point removed successfully")"

        local ct_status
        ct_status=$(pct status "$container_id" | awk '{print $2}')
        if [[ "$ct_status" == "running" ]]; then
            echo ""
            if whiptail --yesno "$(translate "Container is running. Restart to apply changes?")" 8 60; then
                msg_info "$(translate "Restarting container...")"
                if pct reboot "$container_id"; then
                    sleep 3
                    msg_ok "$(translate "Container restarted successfully")"
                else
                    msg_warn "$(translate "Failed to restart container — restart manually")"
                fi
            fi
        fi

        echo ""
        echo -e "${TAB}${BOLD}$(translate "Mount Point Removal Summary:")${CL}"
        echo -e "${TAB}${BGN}$(translate "Container:")${CL} ${BL}$container_id${CL}"
        echo -e "${TAB}${BGN}$(translate "Removed Mount:")${CL} ${BL}$selected_mp${CL}"
        echo -e "${TAB}${BGN}$(translate "Host Path:")${CL} ${BL}$host_path (preserved)${CL}"
        echo -e "${TAB}${BGN}$(translate "Container Path:")${CL} ${BL}$container_path (unmounted)${CL}"
    else
        msg_error "$(translate "Failed to remove mount point")"
    fi

    echo ""
    msg_success "$(translate "Press Enter to continue...")"
    read -r
}

# ==========================================================
# MAIN FUNCTION — ADD MOUNT
# ==========================================================

mount_host_directory_minimal() {
    # Step 1: Select container
    local container_id
    container_id=$(select_lxc_container)
    [[ $? -ne 0 || -z "$container_id" ]] && return 1

    # Step 2: Select host directory
    local host_dir
    host_dir=$(select_host_directory_unified)
    [[ $? -ne 0 || -z "$host_dir" ]] && return 1

    # Step 3: Select container mount point
    local ct_mount_point
    ct_mount_point=$(select_container_mount_point "$container_id" "$host_dir")
    [[ $? -ne 0 || -z "$ct_mount_point" ]] && return 1

    # Step 4: Get container type info (for display only)
    local uid_shift container_type_display
    uid_shift=$(awk -F: '/^lxc.idmap.*u 0/ {print $5}' "/etc/pve/lxc/${container_id}.conf" 2>/dev/null | head -1)
    local is_unprivileged
    is_unprivileged=$(grep "^unprivileged:" "/etc/pve/lxc/${container_id}.conf" 2>/dev/null | awk '{print $2}')
    if [[ "$is_unprivileged" == "1" ]]; then
        container_type_display="$(translate "Unprivileged")"
        uid_shift="${uid_shift:-100000}"
    else
        container_type_display="$(translate "Privileged")"
        uid_shift="0"
    fi

    # Step 5: Confirmation
    local confirm_msg
    confirm_msg="$(translate "Mount Configuration Summary:")

$(translate "Container ID"): $container_id ($container_type_display)
$(translate "Host Directory"): $host_dir
$(translate "Container Mount Point"): $ct_mount_point

$(translate "IMPORTANT NOTES:")
- $(translate "Host directory permissions and ownership are NOT modified")
- $(translate "Container filesystem is NOT modified")
- $(translate "If access fails after mounting, adjust permissions manually:")

$(if [[ "$is_unprivileged" == "1" ]]; then
    echo "  # Allow container UID ${uid_shift}+ to access host dir:"
    echo "  setfacl -m u:${uid_shift}:rwx \"$host_dir\""
    echo "  setfacl -d:m u:${uid_shift}:rwx \"$host_dir\""
else
    echo "  chmod 755 \"$host_dir\""
fi)

$(translate "Proceed")?"

    if ! dialog --clear --title "$(translate "Confirm Mount")" --yesno "$confirm_msg" 22 80; then
        return 1
    fi

    show_proxmenux_logo
    msg_title "$(translate "Mount Host Directory to LXC")"
    msg_ok "$(translate "Container:") $container_id ($container_type_display)"
    msg_ok "$(translate "Host directory:") $host_dir"
    msg_ok "$(translate "Container mount point:") $ct_mount_point"

    # Step 6: Add bind mount (the ONLY operation that changes anything)
    if ! add_bind_mount "$container_id" "$host_dir" "$ct_mount_point"; then
        echo ""
        msg_success "$(translate "Press Enter to continue...")"
        read -r
        return 1
    fi

    # Step 7: Summary with permission hints
    echo ""
    echo -e "${TAB}${BOLD}$(translate "Mount Added Successfully:")${CL}"
    echo -e "${TAB}${BGN}$(translate "Container:")${CL} ${BL}$container_id${CL}"
    echo -e "${TAB}${BGN}$(translate "Host Directory:")${CL} ${BL}$host_dir${CL}"
    echo -e "${TAB}${BGN}$(translate "Mount Point:")${CL} ${BL}$ct_mount_point${CL}"
    echo ""

    if [[ "$is_unprivileged" == "1" ]]; then
        local mapped_uid="$uid_shift"
        echo -e "${TAB}${YW}$(translate "UNPRIVILEGED container — UID mapping active:")${CL}"
        echo -e "${TAB}  $(translate "Container UID 0") → $(translate "Host UID") $mapped_uid"
        echo -e "${TAB}  $(translate "If access fails, run on the host:")"
        echo -e "${TAB}  ${DGN}setfacl -m u:${mapped_uid}:rwx \"$host_dir\"${CL}"
        echo -e "${TAB}  ${DGN}setfacl -d:m u:${mapped_uid}:rwx \"$host_dir\"${CL}"
    else
        echo -e "${TAB}${DGN}$(translate "PRIVILEGED container — direct UID mapping")${CL}"
        echo -e "${TAB}  $(translate "Ensure") $host_dir $(translate "is accessible by root (chmod 755 or wider)")"
    fi

    # Step 8: Offer restart
    echo ""
    if whiptail --yesno "$(translate "Restart container to activate mount?")" 8 60; then
        msg_info "$(translate "Restarting container...")"
        if pct reboot "$container_id"; then
            sleep 5
            msg_ok "$(translate "Container restarted successfully")"

            # Quick access test (read-only, no files written)
            local ct_status
            ct_status=$(pct status "$container_id" 2>/dev/null | awk '{print $2}')
            if [[ "$ct_status" == "running" ]]; then
                echo ""
                if pct exec "$container_id" -- test -d "$ct_mount_point" 2>/dev/null; then
                    msg_ok "$(translate "Mount point is accessible inside container")"
                else
                    msg_warn "$(translate "Mount point not yet accessible — may need manual permission adjustment")"
                fi
            fi
        else
            msg_warn "$(translate "Failed to restart — restart manually to activate mount")"
        fi
    fi

    echo ""
    msg_success "$(translate "Press Enter to continue...")"
    read -r
}

# ==========================================================
# MAIN MENU
# ==========================================================

main_menu() {
    while true; do
        local choice
        choice=$(dialog --title "$(translate "LXC Mount Manager")" \
            --menu "\n$(translate "Choose an option:")" 18 80 5 \
            "1" "$(translate "Add: Mount Host Directory into LXC")" \
            "2" "$(translate "View Mount Points")" \
            "3" "$(translate "Remove Mount Point")" \
            "4" "$(translate "Exit")" 3>&1 1>&2 2>&3)

        case $choice in
            1) mount_host_directory_minimal ;;
            2) view_mount_points ;;
            3) remove_mount_point ;;
            4|"") exit 0 ;;
        esac
    done
}

main_menu
