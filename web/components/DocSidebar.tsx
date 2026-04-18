"use client"

import Link from "next/link"
import { usePathname } from "next/navigation"
import { useState, useEffect } from "react"
import { ChevronDown, ChevronRight, Menu, X } from "lucide-react"

interface SubMenuItem {
  title: string
  href: string
}

interface MenuItem {
  title: string
  href?: string
  submenu?: SubMenuItem[]
}

export const sidebarItems: MenuItem[] = [
  { title: "Introduction", href: "/docs/introduction" },
  { title: "Installation", href: "/docs/installation" },
  {
    title: "Post-Install Script",
    submenu: [
      { title: "Overview", href: "/docs/post-install" },
      { title: "Basic Settings", href: "/docs/post-install/basic-settings" },
      { title: "System", href: "/docs/post-install/system" },
      { title: "Virtualization", href: "/docs/post-install/virtualization" },
      { title: "Network", href: "/docs/post-install/network" },
      { title: "Storage", href: "/docs/post-install/storage" },
      { title: "Security", href: "/docs/post-install/security" },
      { title: "Customization", href: "/docs/post-install/customization" },
      { title: "Monitoring", href: "/docs/post-install/monitoring" },
      { title: "Performance", href: "/docs/post-install/performance" },
      { title: "Optional", href: "/docs/post-install/optional" },
    ],
  },
  {
    title: "Help and Info",
    submenu: [
      { title: "Overview", href: "/docs/help-info" },
      { title: "Useful System Commands", href: "/docs/help-info/system-commands" },
      { title: "VM and CT Management", href: "/docs/help-info/vm-ct-commands" },
      { title: "Storage and Disks", href: "/docs/help-info/storage-commands" },
      { title: "Network Commands", href: "/docs/help-info/network-commands" },
      { title: "Updates and Packages", href: "/docs/help-info/update-commands" },
      { title: "GPU Passthrough", href: "/docs/help-info/gpu-commands" },
      { title: "ZFS Management", href: "/docs/help-info/zfs-commands" },
      { title: "Backup and Restore", href: "/docs/help-info/backup-commands" },
      { title: "System CLI Tools", href: "/docs/help-info/tools-commands" },
    ],
  },
  {
    title: "GPUs and Coral",
    submenu: [
      { title: "HW iGPU acceleration to an LXC", href: "/docs/hardware/igpu-acceleration-lxc" },
      { title: "Coral TPU to an LXC", href: "/docs/hardware/coral-tpu-lxc" },
      { title: "Install Coral TPU on the Host", href: "/docs/hardware/install-coral-tpu-host" },
    ],
  },
  {
    title: "Create VM",
    submenu: [
      { title: "Overview", href: "/docs/create-vm" },
      { title: "System NAS", href: "/docs/create-vm/system-nas" },
      { title: "Synology VM", href: "/docs/create-vm/synology" },
      { title: "Others System NAS", href: "/docs/create-vm/system-nas/system-nas-others" },
      { title: "System Windows", href: "/docs/create-vm/system-windows" },
      { title: "UUP Dump ISO Creator", href: "/docs/utils/UUp-Dump-ISO-Creator" },
      { title: "System Linux", href: "/docs/create-vm/system-linux" },
    ],
  },
  {
    title: "Storage",
    submenu: [
      { title: "Disk Passthrough to a VM", href: "/docs/storage/disk-passthrough-vm" },
      { title: "Disk Passthrough to a CT", href: "/docs/storage/disk-passthrough-ct" },
      { title: "Import Disk Image to a VM", href: "/docs/storage/import-disk-image-vm" },
    ],
  },
  {
    title: "Network",
    submenu: [
      { title: "Verify Network", href: "/docs/network/verify-network" },
      { title: "Show IP Information", href: "/docs/network/show-ip-information" },
    ],
  },
  {
    title: "Settings ProxMenux",
    submenu: [
      { title: "Change Language", href: "/docs/settings/change-language" },
      { title: "Show Version Information", href: "/docs/settings/show-version-information" },
      { title: "Uninstall ProxMenux", href: "/docs/settings/uninstall-proxmenux" },
    ],
  },
  {
    title: "About",
    submenu: [
      { title: "Code of Conduct", href: "/docs/about/code-of-conduct" },
      { title: "FAQ", href: "/docs/about/faq" },
      { title: "Contributors", href: "/docs/about/contributors" }, 
    ],
  },
  { title: "External Repositories", href: "/docs/external-repositories" },
]

export default function DocSidebar() {
  const pathname = usePathname()
  const [openSections, setOpenSections] = useState<{ [key: string]: boolean }>({})
  const [isMobileMenuOpen, setIsMobileMenuOpen] = useState(false)

  const toggleSection = (title: string) => {
    setOpenSections((prev) => ({ ...prev, [title]: !prev[title] }))
  }

  const toggleMobileMenu = () => {
    setIsMobileMenuOpen(!isMobileMenuOpen)
  }

  useEffect(() => {
    const handleResize = () => {
      if (window.innerWidth >= 768) {
        setIsMobileMenuOpen(false)
      }
    }

    window.addEventListener("resize", handleResize)
    return () => window.removeEventListener("resize", handleResize)
  }, [])

  const renderMenuItem = (item: MenuItem) => {
    if (item.submenu) {
      const isOpen = openSections[item.title] || false
      return (
        <li key={item.title} className="mb-2">
          <button
            onClick={() => toggleSection(item.title)}
            className="flex items-center justify-between w-full text-left p-2 rounded hover:bg-gray-200"
          >
            <span>{item.title}</span>
            {isOpen ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
          </button>
          {isOpen && (
            <ul className="ml-4 mt-2 space-y-2">
              {item.submenu.map((subItem) => (
                <li key={subItem.href}>
                  <Link
                    href={subItem.href}
                    className={`block p-2 rounded ${
                      pathname === subItem.href
                        ? "bg-blue-500 text-white"
                        : "text-gray-700 hover:bg-gray-200 hover:text-gray-900"
                    }`}
                    onClick={() => setIsMobileMenuOpen(false)}
                  >
                    {subItem.title}
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </li>
      )
    } else {
      return (
        <li key={item.href}>
          <Link
            href={item.href!}
            className={`block p-2 rounded ${
              pathname === item.href ? "bg-blue-500 text-white" : "text-gray-700 hover:bg-gray-200 hover:text-gray-900"
            }`}
            onClick={() => setIsMobileMenuOpen(false)}
          >
            {item.title}
          </Link>
        </li>
      )
    }
  }

  return (
    <>
      <div className="lg:hidden fixed top-16 left-0 right-0 z-50 bg-gray-100 border-b border-gray-200">
        <button
          className="w-full p-4 text-left flex items-center justify-between"
          onClick={toggleMobileMenu}
          aria-label="Toggle menu"
        >
          <span className="font-semibold">Documentation</span>
          {isMobileMenuOpen ? <X className="h-6 w-6" /> : <Menu className="h-6 w-6" />}
        </button>
      </div>
      <nav
        className={`fixed lg:static top-[104px] left-0 w-full lg:w-72 h-[calc(100vh-104px)] lg:h-[calc(100vh-64px)] bg-gray-100 p-4 lg:p-6 pt-16 lg:pt-6 transform ${
          isMobileMenuOpen ? "translate-y-0" : "-translate-y-full"
        } lg:translate-y-0 transition-transform duration-300 ease-in-out overflow-y-auto z-30`}
      >
        <h2 className="text-lg font-semibold mb-4 text-gray-900 lg:mt-0 sr-only lg:not-sr-only">Documentation</h2>
        <ul className="space-y-2">{sidebarItems.map(renderMenuItem)}</ul>
      </nav>
    </>
  )
}
