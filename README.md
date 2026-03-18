<div align="center">

<img src="https://raw.githubusercontent.com/MacRimi/ProxMenux/main/images/proxmenux_logo.png" alt="ProxMenux Logo" width="180"/>

# ProxMenux

**An Interactive Menu for Proxmox VE Management**

[![License: GPL-3.0](https://img.shields.io/badge/License-GPL--3.0-blue.svg)](https://github.com/MacRimi/ProxMenux/blob/main/LICENSE)
[![Stars](https://img.shields.io/github/stars/MacRimi/ProxMenux?style=flat&color=yellow)](https://github.com/MacRimi/ProxMenux/stargazers)
[![Forks](https://img.shields.io/github/forks/MacRimi/ProxMenux?style=flat&color=orange)](https://github.com/MacRimi/ProxMenux/network/members)
[![Last Commit](https://img.shields.io/github/last-commit/MacRimi/ProxMenux?color=green)](https://github.com/MacRimi/ProxMenux/commits/main)

</div>

---

ProxMenux is a powerful menu-driven toolkit for Proxmox VE management, designed to simplify and streamline administration tasks through an intuitive interactive interface. Manage virtual machines, containers, networking, storage, hardware and more — all without memorizing complex commands.

---

## 📋 Table of Contents

- [Features](#-features)
- [ProxMenux Monitor](#-proxmenux-monitor)
- [Installation](#-installation)
- [Beta Program](#-beta-program)
- [Usage](#-usage)
- [Menu Structure](#-menu-structure)
- [Dependencies](#-dependencies)
- [Contributing](#-contributing)
- [License](#-license)

---

## ✨ Features

- **Interactive menus** — navigate everything with arrow keys and Enter, no commands to memorize
- **VM & LXC management** — create, start, stop, restart and delete virtual machines and containers
- **Network configuration** — manage bridges, physical interfaces and network troubleshooting
- **Storage management** — configure and monitor ZFS pools, add disks, manage volumes
- **Hardware management** — detect PCI devices, configure IOMMU and passthrough (GPU, Coral TPU)
- **Post-install optimization** — apply recommended settings after a fresh Proxmox installation
- **Helper Scripts integration** — access Proxmox VE Helper-Scripts directly from the menu
- **Multi-language support** — English and other languages via Google Translate (Translation version)
- **Automatic updates** — ProxMenux checks for new versions on startup and updates itself
- **ProxMenux Monitor** — real-time web dashboard for system monitoring (see below)

---

## 🖥️ ProxMenux Monitor

ProxMenux Monitor is an integrated web dashboard that provides real-time visibility into your Proxmox infrastructure — accessible from any browser on your network, without needing a terminal.

**What it offers:**

- Real-time monitoring of CPU, RAM, disk usage and network traffic
- Overview of running VMs and LXC containers with status indicators
- Login authentication to protect access
- Two-Factor Authentication (2FA) with TOTP support
- Reverse proxy support (Nginx / Traefik)
- Designed to work across desktop and mobile devices

**Access:**

Once installed, the dashboard is available at:

```
http://<your-proxmox-ip>:8008
```

The Monitor is installed automatically as part of the standard ProxMenux installation and runs as a systemd service (`proxmenux-monitor.service`) that starts automatically on boot.

**Useful commands:**

```bash
# Check service status
systemctl status proxmenux-monitor

# View logs
journalctl -u proxmenux-monitor -n 50

# Restart the service
systemctl restart proxmenux-monitor
```

---

## 📦 Installation

Run the following command in your Proxmox server terminal:

```bash
bash -c "$(wget -qLO - https://raw.githubusercontent.com/MacRimi/ProxMenux/main/install_proxmenux.sh)"
```

> ⚠️ **Security notice:** Always review scripts before executing them.
> 📄 You can [read the source code](https://github.com/MacRimi/ProxMenux/blob/main/install_proxmenux.sh) before running.
> 🛡️ All executable links follow our [Code of Conduct](https://github.com/MacRimi/ProxMenux/blob/main/CODE_OF_CONDUCT.md).

**Two installation options are available:**

| Version | Description |
|---|---|
| **Normal** | English only. Minimal dependencies. Recommended for Proxmox VE 9+. |
| **Translation** | Multi-language support via Google Translate. Requires Python 3 + virtual environment. |

After installation, launch ProxMenux with:

```bash
menu
```

---

## 🧪 Beta Program

Want to try the latest features before the official release and help shape the final version?

The **ProxMenux Beta Program** gives early access to new functionality — including the newest builds of ProxMenux Monitor — directly from the `develop` branch. Beta builds may contain bugs or incomplete features. Your feedback is what helps fix them before the stable release.

**Install the beta version:**

```bash
bash -c "$(wget -qLO - https://raw.githubusercontent.com/MacRimi/ProxMenux/develop/install_proxmenux_beta.sh)"
```

**What to expect:**

- You'll get new features and Monitor builds before anyone else
- Some things may not work perfectly — that's expected and normal
- When a stable release is published, ProxMenux will notify you on the next `menu` launch and offer to switch automatically

**How to report issues:**

Open a [GitHub Issue](https://github.com/MacRimi/ProxMenux/issues) and include:
- What you did and what you expected to happen
- Any error messages shown on screen
- Logs from the Monitor if relevant:

```bash
journalctl -u proxmenux-monitor -n 50
```

> 💙 Thank you for being part of the beta program. Your help makes ProxMenux better for everyone.

---

## 🚀 Usage

After installation, type `menu` in your Proxmox terminal to launch ProxMenux. Use the arrow keys to navigate, Enter to select, and Escape or the Back option to return to the previous menu.

ProxMenux checks for available updates on each launch. If a newer version is available, it will prompt you to update before continuing.

---

## 🗂️ Menu Structure

ProxMenux is organized into modular sections:

- **Post Install** — apply recommended optimizations after a fresh Proxmox installation
- **Virtual Machines** — create and manage VMs including OS image downloads
- **LXC Containers** — create and manage containers with helper script integration
- **Network** — configure bridges, interfaces and troubleshoot connectivity
- **Storage** — manage ZFS, disks, passthrough and mount points
- **Hardware** — PCI passthrough, IOMMU, GPU and Coral TPU setup
- **System** — updates, utilities, Log2RAM, monitoring tools
- **Helper Scripts** — direct access to Proxmox VE Helper-Scripts

---

## 🔧 Dependencies

The following dependencies are installed automatically during setup:

| Package | Purpose |
|---|---|
| `dialog` | Interactive terminal menus |
| `curl` | Downloads and connectivity checks |
| `jq` | JSON processing |
| `git` | Repository cloning and updates |
| `python3` + `python3-venv` | Translation support *(Translation version only)* |
| `googletrans` | Google Translate library *(Translation version only)* |

---

## 🤝 Contributing

Contributions, bug reports and feature suggestions are welcome!

- 🐛 [Report a bug](https://github.com/MacRimi/ProxMenux/issues/new)
- 💡 [Suggest a feature](https://github.com/MacRimi/ProxMenux/discussions)
- 🔀 [Submit a pull request](https://github.com/MacRimi/ProxMenux/pulls)

If you find ProxMenux useful, consider giving it a ⭐ on GitHub — it helps others discover the project!

---

## 📄 License

ProxMenux is released under the [GPL-3.0 License](https://github.com/MacRimi/ProxMenux/blob/main/LICENSE).

---

<div align="center">

Made with ❤️ by [MacRimi](https://github.com/MacRimi) and contributors

</div>
