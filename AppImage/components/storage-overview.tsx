"use client"

import { useEffect, useState } from "react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { HardDrive, Database, AlertTriangle, CheckCircle2, XCircle, Square, Thermometer, Archive, Info, Clock, Usb, Server, Activity, FileText, Play, Loader2 } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Progress } from "@/components/ui/progress"
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { fetchApi } from "../lib/api-config"

interface DiskInfo {
  name: string
  size?: number // Changed from string to number (KB) for formatMemory()
  size_formatted?: string // Added formatted size string for display
  temperature: number
  health: string
  power_on_hours?: number
  smart_status?: string
  model?: string
  serial?: string
  mountpoint?: string
  fstype?: string
  total?: number
  used?: number
  available?: number
  usage_percent?: number
  reallocated_sectors?: number
  pending_sectors?: number
  crc_errors?: number
  rotation_rate?: number
  power_cycles?: number
  percentage_used?: number // NVMe: Percentage Used (0-100)
  media_wearout_indicator?: number // SSD: Media Wearout Indicator
  wear_leveling_count?: number // SSD: Wear Leveling Count
  total_lbas_written?: number // SSD/NVMe: Total LBAs Written (GB)
  ssd_life_left?: number // SSD: SSD Life Left percentage
  io_errors?: {
    count: number
    severity: string
    sample: string
    reason: string
    error_type?: string  // 'io' | 'filesystem'
  }
  observations_count?: number
  connection_type?: 'usb' | 'sata' | 'nvme' | 'sas' | 'internal' | 'unknown'
  removable?: boolean
  is_system_disk?: boolean
  system_usage?: string[]
}

interface DiskObservation {
  id: number
  error_type: string
  error_signature: string
  first_occurrence: string
  last_occurrence: string
  occurrence_count: number
  raw_message: string
  severity: string
  dismissed: boolean
  device_name: string
  serial: string
  model: string
}

interface ZFSPool {
  name: string
  size: string
  allocated: string
  free: string
  health: string
}

interface StorageData {
  total: number
  used: number
  available: number
  disks: DiskInfo[]
  zfs_pools: ZFSPool[]
  disk_count: number
  healthy_disks: number
  warning_disks: number
  critical_disks: number
  error?: string
}

interface ProxmoxStorage {
  name: string
  type: string
  status: string
  total: number
  used: number
  available: number
  percent: number
  node: string // Added node property for detailed debug logging
}

interface ProxmoxStorageData {
  storage: ProxmoxStorage[]
  error?: string
}

const formatStorage = (sizeInGB: number): string => {
  if (sizeInGB < 1) {
    // Less than 1 GB, show in MB
    return `${(sizeInGB * 1024).toFixed(1)} MB`
  } else if (sizeInGB > 999) {
    return `${(sizeInGB / 1024).toFixed(2)} TB`
  } else {
    // Between 1 and 999 GB, show in GB
    return `${sizeInGB.toFixed(2)} GB`
  }
}

export function StorageOverview() {
  const [storageData, setStorageData] = useState<StorageData | null>(null)
  const [proxmoxStorage, setProxmoxStorage] = useState<ProxmoxStorageData | null>(null)
  const [loading, setLoading] = useState(true)
  const [selectedDisk, setSelectedDisk] = useState<DiskInfo | null>(null)
  const [detailsOpen, setDetailsOpen] = useState(false)
  const [diskObservations, setDiskObservations] = useState<DiskObservation[]>([])
  const [loadingObservations, setLoadingObservations] = useState(false)
  const [activeModalTab, setActiveModalTab] = useState<"overview" | "smart">("overview")

  const fetchStorageData = async () => {
    try {
      const [data, proxmoxData] = await Promise.all([
        fetchApi<StorageData>("/api/storage"),
        fetchApi<ProxmoxStorageData>("/api/proxmox-storage"),
      ])

      setStorageData(data)
      setProxmoxStorage(proxmoxData)
    } catch (error) {
      console.error("Error fetching storage data:", error)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchStorageData()
    const interval = setInterval(fetchStorageData, 60000)
    return () => clearInterval(interval)
  }, [])

  const getHealthIcon = (health: string) => {
    switch (health.toLowerCase()) {
      case "healthy":
      case "passed":
      case "online":
        return <CheckCircle2 className="h-5 w-5 text-green-500" />
      case "warning":
        return <AlertTriangle className="h-5 w-5 text-yellow-500" />
      case "critical":
      case "failed":
      case "degraded":
        return <XCircle className="h-5 w-5 text-red-500" />
      default:
        return <AlertTriangle className="h-5 w-5 text-gray-500" />
    }
  }

  const getHealthBadge = (health: string) => {
    switch (health.toLowerCase()) {
      case "healthy":
      case "passed":
      case "online":
        return <Badge className="bg-green-500/10 text-green-500 border-green-500/20">Healthy</Badge>
      case "warning":
        return <Badge className="bg-yellow-500/10 text-yellow-500 border-yellow-500/20">Warning</Badge>
      case "critical":
      case "failed":
      case "degraded":
        return <Badge className="bg-red-500/10 text-red-500 border-red-500/20">Critical</Badge>
      default:
        return <Badge className="bg-gray-500/10 text-gray-500 border-gray-500/20">Unknown</Badge>
    }
  }

  const getTempColor = (temp: number, diskName?: string, rotationRate?: number) => {
    if (temp === 0) return "text-gray-500"

    // Determinar el tipo de disco
    let diskType = "HDD" // Por defecto
    if (diskName) {
      if (diskName.startsWith("nvme")) {
        diskType = "NVMe"
      } else if (!rotationRate || rotationRate === 0) {
        diskType = "SSD"
      }
    }

    // Aplicar rangos de temperatura según el tipo
    switch (diskType) {
      case "NVMe":
        // NVMe: ≤70°C verde, 71-80°C amarillo, >80°C rojo
        if (temp <= 70) return "text-green-500"
        if (temp <= 80) return "text-yellow-500"
        return "text-red-500"

      case "SSD":
        // SSD: ≤59°C verde, 60-70°C amarillo, >70°C rojo
        if (temp <= 59) return "text-green-500"
        if (temp <= 70) return "text-yellow-500"
        return "text-red-500"

      case "HDD":
      default:
        // HDD: ≤45°C verde, 46-55°C amarillo, >55°C rojo
        if (temp <= 45) return "text-green-500"
        if (temp <= 55) return "text-yellow-500"
        return "text-red-500"
    }
  }

  const formatHours = (hours: number) => {
    if (hours === 0) return "N/A"
    const years = Math.floor(hours / 8760)
    const days = Math.floor((hours % 8760) / 24)
    if (years > 0) {
      return `${years}y ${days}d`
    }
    return `${days}d`
  }

  const formatRotationRate = (rpm: number | undefined) => {
    if (!rpm || rpm === 0) return "SSD"
    return `${rpm.toLocaleString()} RPM`
  }

  const getDiskType = (diskName: string, rotationRate: number | undefined): string => {
    if (diskName.startsWith("nvme")) {
      return "NVMe"
    }
    // rotation_rate = -1 means HDD but RPM is unknown (detected via kernel rotational flag)
    // rotation_rate = 0 or undefined means SSD
    // rotation_rate > 0 means HDD with known RPM
    if (rotationRate === -1) {
      return "HDD"
    }
    if (!rotationRate || rotationRate === 0) {
      return "SSD"
    }
    return "HDD"
  }

  const getDiskTypeBadge = (diskName: string, rotationRate: number | undefined) => {
    const diskType = getDiskType(diskName, rotationRate)
    const badgeStyles: Record<string, { className: string; label: string }> = {
      NVMe: {
        className: "bg-purple-500/10 text-purple-500 border-purple-500/20",
        label: "NVMe",
      },
      SSD: {
        className: "bg-cyan-500/10 text-cyan-500 border-cyan-500/20",
        label: "SSD",
      },
      HDD: {
        className: "bg-blue-500/10 text-blue-500 border-blue-500/20",
        label: "HDD",
      },
    }
    return badgeStyles[diskType]
  }

  const handleDiskClick = async (disk: DiskInfo) => {
    setSelectedDisk(disk)
    setDetailsOpen(true)
    setDiskObservations([])

    // Always attempt to fetch observations -- the count enrichment may lag
    // behind the actual observation recording (especially for USB disks).
    setLoadingObservations(true)
    try {
      const params = new URLSearchParams()
      if (disk.name) params.set('device', disk.name)
      if (disk.serial && disk.serial !== 'Unknown') params.set('serial', disk.serial)
      const data = await fetchApi<{ observations: DiskObservation[] }>(`/api/storage/observations?${params.toString()}`)
      setDiskObservations(data.observations || [])
    } catch {
      setDiskObservations([])
    } finally {
      setLoadingObservations(false)
    }
  }

  const formatObsDate = (iso: string) => {
    if (!iso) return 'N/A'
    try {
      const d = new Date(iso)
      const day = d.getDate().toString().padStart(2, '0')
      const month = (d.getMonth() + 1).toString().padStart(2, '0')
      const year = d.getFullYear()
      const hours = d.getHours().toString().padStart(2, '0')
      const mins = d.getMinutes().toString().padStart(2, '0')
      return `${day}/${month}/${year} ${hours}:${mins}`
    } catch { return iso }
  }

  const obsTypeLabel = (t: string) =>
    ({ smart_error: 'SMART Error', io_error: 'I/O Error', filesystem_error: 'Filesystem Error', zfs_pool_error: 'ZFS Pool Error', connection_error: 'Connection Error' }[t] || t)

  const getStorageTypeBadge = (type: string) => {
    const typeColors: Record<string, string> = {
      pbs: "bg-purple-500/10 text-purple-500 border-purple-500/20",
      dir: "bg-blue-500/10 text-blue-500 border-blue-500/20",
      lvmthin: "bg-cyan-500/10 text-cyan-500 border-cyan-500/20",
      zfspool: "bg-green-500/10 text-green-500 border-green-500/20",
      nfs: "bg-orange-500/10 text-orange-500 border-orange-500/20",
      cifs: "bg-yellow-500/10 text-yellow-500 border-yellow-500/20",
    }
    return typeColors[type.toLowerCase()] || "bg-gray-500/10 text-gray-500 border-gray-500/20"
  }

  const getStatusIcon = (status: string) => {
    switch (status.toLowerCase()) {
      case "active":
      case "online":
        return <CheckCircle2 className="h-5 w-5 text-green-500" />
      case "inactive":
      case "offline":
        return <Square className="h-5 w-5 text-gray-500" />
      case "error":
      case "failed":
        return <AlertTriangle className="h-5 w-5 text-red-500" />
      default:
        return <CheckCircle2 className="h-5 w-5 text-gray-500" />
    }
  }

  const getWearIndicator = (disk: DiskInfo): { value: number; label: string } | null => {
    const diskType = getDiskType(disk.name, disk.rotation_rate)

    if (diskType === "NVMe" && disk.percentage_used !== undefined && disk.percentage_used !== null) {
      return { value: disk.percentage_used, label: "Percentage Used" }
    }

    if (diskType === "SSD") {
      // Prioridad: Media Wearout Indicator > Wear Leveling Count > SSD Life Left
      if (disk.media_wearout_indicator !== undefined && disk.media_wearout_indicator !== null) {
        return { value: disk.media_wearout_indicator, label: "Media Wearout" }
      }
      if (disk.wear_leveling_count !== undefined && disk.wear_leveling_count !== null) {
        return { value: disk.wear_leveling_count, label: "Wear Level" }
      }
      if (disk.ssd_life_left !== undefined && disk.ssd_life_left !== null) {
        return { value: 100 - disk.ssd_life_left, label: "Life Used" }
      }
    }

    return null
  }

  const getWearColor = (wearPercent: number): string => {
    if (wearPercent <= 50) return "text-green-500"
    if (wearPercent <= 80) return "text-yellow-500"
    return "text-red-500"
  }

  const getEstimatedLifeRemaining = (disk: DiskInfo): string | null => {
    const wearIndicator = getWearIndicator(disk)
    if (!wearIndicator || !disk.power_on_hours || disk.power_on_hours === 0) {
      return null
    }

    const wearPercent = wearIndicator.value
    const hoursUsed = disk.power_on_hours

    // Si el desgaste es 0, no podemos calcular
    if (wearPercent === 0) {
      return "N/A"
    }

    // Calcular horas totales estimadas: hoursUsed / (wearPercent / 100)
    const totalEstimatedHours = hoursUsed / (wearPercent / 100)
    const remainingHours = totalEstimatedHours - hoursUsed

    // Convertir a años
    const remainingYears = remainingHours / 8760 // 8760 horas en un año

    if (remainingYears < 1) {
      const remainingMonths = Math.round(remainingYears * 12)
      return `~${remainingMonths} months`
    }

    return `~${remainingYears.toFixed(1)} years`
  }

  const getDiskHealthBreakdown = () => {
    if (!storageData || !storageData.disks) {
      return { normal: 0, warning: 0, critical: 0 }
    }

    let normal = 0
    let warning = 0
    let critical = 0

    storageData.disks.forEach((disk) => {
      if (disk.temperature === 0) {
        // Si no hay temperatura, considerarlo normal
        normal++
        return
      }

      const diskType = getDiskType(disk.name, disk.rotation_rate)

      switch (diskType) {
        case "NVMe":
          if (disk.temperature <= 70) normal++
          else if (disk.temperature <= 80) warning++
          else critical++
          break
        case "SSD":
          if (disk.temperature <= 59) normal++
          else if (disk.temperature <= 70) warning++
          else critical++
          break
        case "HDD":
        default:
          if (disk.temperature <= 45) normal++
          else if (disk.temperature <= 55) warning++
          else critical++
          break
      }
    })

    return { normal, warning, critical }
  }

  const getDiskTypesBreakdown = () => {
    if (!storageData || !storageData.disks) {
      return { nvme: 0, ssd: 0, hdd: 0, usb: 0 }
    }

    let nvme = 0
    let ssd = 0
    let hdd = 0
    let usb = 0

    storageData.disks.forEach((disk) => {
      if (disk.connection_type === 'usb') {
        usb++
        return
      }
      const diskType = getDiskType(disk.name, disk.rotation_rate)
      if (diskType === "NVMe") nvme++
      else if (diskType === "SSD") ssd++
      else if (diskType === "HDD") hdd++
    })

    return { nvme, ssd, hdd, usb }
  }

  const getWearProgressColor = (wearPercent: number): string => {
    if (wearPercent < 70) return "[&>div]:bg-blue-500"
    if (wearPercent < 85) return "[&>div]:bg-yellow-500"
    return "[&>div]:bg-red-500"
  }

  const getUsageColor = (percent: number): string => {
    if (percent < 70) return "text-blue-500"
    if (percent < 85) return "text-yellow-500"
    if (percent < 95) return "text-orange-500"
    return "text-red-500"
  }

  const diskHealthBreakdown = getDiskHealthBreakdown()
  const diskTypesBreakdown = getDiskTypesBreakdown()

  const localStorageTypes = ["dir", "lvmthin", "lvm", "zfspool", "btrfs"]
  const remoteStorageTypes = ["pbs", "nfs", "cifs", "smb", "glusterfs", "iscsi", "iscsidirect", "rbd", "cephfs"]

  const totalLocalUsed =
    proxmoxStorage?.storage
      .filter(
        (storage) =>
          storage &&
          storage.name &&
          storage.status === "active" &&
          storage.total > 0 &&
          storage.used >= 0 &&
          storage.available >= 0 &&
          localStorageTypes.includes(storage.type.toLowerCase()),
      )
      .reduce((sum, storage) => sum + storage.used, 0) || 0

  const totalLocalCapacity =
    proxmoxStorage?.storage
      .filter(
        (storage) =>
          storage &&
          storage.name &&
          storage.status === "active" &&
          storage.total > 0 &&
          storage.used >= 0 &&
          storage.available >= 0 &&
          localStorageTypes.includes(storage.type.toLowerCase()),
      )
      .reduce((sum, storage) => sum + storage.total, 0) || 0

  const localUsagePercent = totalLocalCapacity > 0 ? ((totalLocalUsed / totalLocalCapacity) * 100).toFixed(2) : "0.00"

  const totalRemoteUsed =
    proxmoxStorage?.storage
      .filter(
        (storage) =>
          storage &&
          storage.name &&
          storage.status === "active" &&
          storage.total > 0 &&
          storage.used >= 0 &&
          storage.available >= 0 &&
          remoteStorageTypes.includes(storage.type.toLowerCase()),
      )
      .reduce((sum, storage) => sum + storage.used, 0) || 0

  const totalRemoteCapacity =
    proxmoxStorage?.storage
      .filter(
        (storage) =>
          storage &&
          storage.name &&
          storage.status === "active" &&
          storage.total > 0 &&
          storage.used >= 0 &&
          storage.available >= 0 &&
          remoteStorageTypes.includes(storage.type.toLowerCase()),
      )
      .reduce((sum, storage) => sum + storage.total, 0) || 0

  const remoteUsagePercent =
    totalRemoteCapacity > 0 ? ((totalRemoteUsed / totalRemoteCapacity) * 100).toFixed(2) : "0.00"

  const remoteStorageCount =
    proxmoxStorage?.storage.filter(
      (storage) =>
        storage &&
        storage.name &&
        storage.status === "active" &&
        remoteStorageTypes.includes(storage.type.toLowerCase()),
    ).length || 0

  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center min-h-[400px] gap-4">
        <div className="relative">
          <div className="h-12 w-12 rounded-full border-2 border-muted"></div>
          <div className="absolute inset-0 h-12 w-12 rounded-full border-2 border-transparent border-t-primary animate-spin"></div>
        </div>
        <div className="text-sm font-medium text-foreground">Loading storage data...</div>
        <p className="text-xs text-muted-foreground">Scanning disks, partitions and storage pools</p>
      </div>
    )
  }

  if (!storageData || storageData.error) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="text-red-500">Error loading storage data: {storageData?.error || "Unknown error"}</div>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      {/* Storage Summary */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 lg:gap-6">
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Total Storage</CardTitle>
            <HardDrive className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-xl lg:text-2xl font-bold">{storageData.total.toFixed(1)} TB</div>
            <p className="text-xs text-muted-foreground mt-1">{storageData.disk_count} physical disks</p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Local Used</CardTitle>
            <Database className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-xl lg:text-2xl font-bold">{formatStorage(totalLocalUsed)}</div>
            <p className="text-xs mt-1">
              <span className={getUsageColor(Number.parseFloat(localUsagePercent))}>{localUsagePercent}%</span>
              <span className="text-muted-foreground"> of </span>
              <span className="text-green-500">{formatStorage(totalLocalCapacity)}</span>
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Remote Used</CardTitle>
            <Archive className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-xl lg:text-2xl font-bold">
              {remoteStorageCount > 0 ? formatStorage(totalRemoteUsed) : "None"}
            </div>
            <p className="text-xs mt-1">
              {remoteStorageCount > 0 ? (
                <>
                  <span className={getUsageColor(Number.parseFloat(remoteUsagePercent))}>{remoteUsagePercent}%</span>
                  <span className="text-muted-foreground"> of </span>
                  <span className="text-green-500">{formatStorage(totalRemoteCapacity)}</span>
                </>
              ) : (
                <span className="text-muted-foreground">No remote storage</span>
              )}
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Physical Disks</CardTitle>
            <HardDrive className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-xl lg:text-2xl font-bold">{storageData.disk_count} disks</div>
            <div className="space-y-1 mt-1">
              <p className="text-xs">
                {diskTypesBreakdown.nvme > 0 && <span className="text-purple-500">{diskTypesBreakdown.nvme} NVMe</span>}
                {diskTypesBreakdown.ssd > 0 && (
                  <>
                    {diskTypesBreakdown.nvme > 0 && ", "}
                    <span className="text-cyan-500">{diskTypesBreakdown.ssd} SSD</span>
                  </>
                )}
                {diskTypesBreakdown.hdd > 0 && (
                  <>
                    {(diskTypesBreakdown.nvme > 0 || diskTypesBreakdown.ssd > 0) && ", "}
                    <span className="text-blue-500">{diskTypesBreakdown.hdd} HDD</span>
                  </>
                )}
                {diskTypesBreakdown.usb > 0 && (
                  <>
                    {(diskTypesBreakdown.nvme > 0 || diskTypesBreakdown.ssd > 0 || diskTypesBreakdown.hdd > 0) && ", "}
                    <span className="text-orange-400">{diskTypesBreakdown.usb} USB</span>
                  </>
                )}
              </p>
              <p className="text-xs">
                <span className="text-green-500">{diskHealthBreakdown.normal} normal</span>
                {diskHealthBreakdown.warning > 0 && (
                  <>
                    {", "}
                    <span className="text-yellow-500">{diskHealthBreakdown.warning} warning</span>
                  </>
                )}
                {diskHealthBreakdown.critical > 0 && (
                  <>
                    {", "}
                    <span className="text-red-500">{diskHealthBreakdown.critical} critical</span>
                  </>
                )}
              </p>
            </div>
          </CardContent>
        </Card>
      </div>

      {proxmoxStorage && proxmoxStorage.storage && proxmoxStorage.storage.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Database className="h-5 w-5" />
              Proxmox Storage
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-4">
              {proxmoxStorage.storage
                .filter((storage) => storage && storage.name && storage.used >= 0 && storage.available >= 0)
                .sort((a, b) => a.name.localeCompare(b.name))
                .map((storage) => {
                  // Check if storage is excluded from monitoring
                  const isExcluded = storage.excluded === true
                  const hasError = storage.status === "error" && !isExcluded
                  
                  return (
                  <div
                    key={storage.name}
                    className={`border rounded-lg p-4 ${
                      hasError 
                        ? "border-red-500/50 bg-red-500/5" 
                        : isExcluded 
                          ? "border-purple-500/30 bg-purple-500/5 opacity-75" 
                          : ""
                    }`}
                  >
                    <div className="flex items-center justify-between mb-3">
                      {/* Desktop: Icon + Name + Badge tipo alineados horizontalmente */}
                      <div className="hidden md:flex items-center gap-3">
                        <Database className="h-5 w-5 text-muted-foreground" />
                        <h3 className="font-semibold text-lg">{storage.name}</h3>
                        <Badge className={getStorageTypeBadge(storage.type)}>{storage.type}</Badge>
                        {isExcluded && (
                          <Badge className="bg-purple-500/10 text-purple-400 border-purple-500/20 text-[10px]">
                            excluded
                          </Badge>
                        )}
                      </div>

                      <div className="flex md:hidden items-center gap-2 flex-1">
                        <Database className="h-5 w-5 text-muted-foreground flex-shrink-0" />
                        <Badge className={getStorageTypeBadge(storage.type)}>{storage.type}</Badge>
                        <h3 className="font-semibold text-base flex-1 min-w-0 truncate">{storage.name}</h3>
                        {isExcluded ? (
                          <Badge className="bg-purple-500/10 text-purple-400 border-purple-500/20 text-[10px]">
                            excluded
                          </Badge>
                        ) : (
                          getStatusIcon(storage.status)
                        )}
                      </div>

                      {/* Desktop: Badge active + Porcentaje */}
                      <div className="hidden md:flex items-center gap-2">
                        <Badge
                          className={
                            isExcluded
                              ? "bg-purple-500/10 text-purple-400 border-purple-500/20"
                              : storage.status === "active"
                                ? "bg-green-500/10 text-green-500 border-green-500/20"
                                : storage.status === "error"
                                  ? "bg-red-500/10 text-red-500 border-red-500/20"
                                  : "bg-gray-500/10 text-gray-500 border-gray-500/20"
                          }
                        >
                          {isExcluded ? "not monitored" : storage.status}
                        </Badge>
                        <span className="text-sm font-medium">{storage.percent}%</span>
                      </div>
                    </div>

                    <div className="space-y-2">
                      <Progress
                        value={storage.percent}
                        className={`h-2 ${
                          storage.percent > 90
                            ? "[&>div]:bg-red-500"
                            : storage.percent > 75
                              ? "[&>div]:bg-yellow-500"
                              : "[&>div]:bg-blue-500"
                        }`}
                      />
                      <div className="grid grid-cols-3 gap-4 text-sm">
                        <div>
                          <p className="text-muted-foreground">Total</p>
                          <p className="font-medium">{formatStorage(storage.total)}</p>
                        </div>
                        <div>
                          <p className="text-muted-foreground">Used</p>
                          <p
                            className={`font-medium ${
                              storage.percent > 90
                                ? "text-red-400"
                                : storage.percent > 75
                                  ? "text-yellow-400"
                                  : "text-blue-400"
                            }`}
                          >
                            {formatStorage(storage.used)}
                          </p>
                        </div>
                        <div>
                          <p className="text-muted-foreground">Available</p>
                          <p className="font-medium text-green-400">{formatStorage(storage.available)}</p>
                        </div>
                      </div>
                    </div>
                  </div>
                  )
                })}
            </div>
          </CardContent>
        </Card>
      )}

      {/* ZFS Pools */}
      {storageData.zfs_pools && storageData.zfs_pools.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Database className="h-5 w-5" />
              ZFS Pools
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-4">
              {storageData.zfs_pools.map((pool) => (
                <div key={pool.name} className="border rounded-lg p-4">
                  <div className="flex items-center justify-between mb-2">
                    <div className="flex items-center gap-3">
                      <h3 className="font-semibold text-lg">{pool.name}</h3>
                      {getHealthBadge(pool.health)}
                    </div>
                    {getHealthIcon(pool.health)}
                  </div>
                  <div className="grid grid-cols-3 gap-4 text-sm">
                    <div>
                      <p className="text-sm text-muted-foreground">Size</p>
                      <p className="font-medium">{pool.size}</p>
                    </div>
                    <div>
                      <p className="text-sm text-muted-foreground">Allocated</p>
                      <p className="font-medium">{pool.allocated}</p>
                    </div>
                    <div>
                      <p className="text-sm text-muted-foreground">Free</p>
                      <p className="font-medium">{pool.free}</p>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      {/* Physical Disks (internal only) */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <HardDrive className="h-5 w-5" />
            Physical Disks & SMART Status
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="space-y-4">
            {storageData.disks.filter(d => d.connection_type !== 'usb').map((disk) => (
              <div key={disk.name}>
                <div
                  className="sm:hidden border border-white/10 rounded-lg p-4 cursor-pointer bg-white/5 transition-colors"
                  onClick={() => handleDiskClick(disk)}
                >
                  <div className="space-y-2 mb-3">
                    {/* Row 1: Device name and type badge */}
                    <div className="flex items-center gap-2 flex-wrap">
                      <HardDrive className="h-5 w-5 text-muted-foreground flex-shrink-0" />
                      <h3 className="font-semibold">/dev/{disk.name}</h3>
                      <Badge className={getDiskTypeBadge(disk.name, disk.rotation_rate).className}>
                        {getDiskTypeBadge(disk.name, disk.rotation_rate).label}
                      </Badge>
                      {disk.is_system_disk && (
                        <Badge className="bg-orange-500/10 text-orange-500 border-orange-500/20 gap-1">
                          <Server className="h-3 w-3" />
                          System
                        </Badge>
                      )}
                    </div>

                    {/* Row 2: Model, temperature, and health status */}
                    <div className="flex items-center justify-between gap-3 pl-7">
                      {disk.model && disk.model !== "Unknown" && (
                        <p className="text-sm text-muted-foreground truncate flex-1 min-w-0">{disk.model}</p>
                      )}
                      <div className="flex items-center gap-3 flex-shrink-0">
                        {disk.temperature > 0 && (
                          <div className="flex items-center gap-1">
                            <Thermometer
                              className={`h-4 w-4 ${getTempColor(disk.temperature, disk.name, disk.rotation_rate)}`}
                            />
                            <span
                              className={`text-sm font-medium ${getTempColor(disk.temperature, disk.name, disk.rotation_rate)}`}
                            >
                              {disk.temperature}°C
                            </span>
                          </div>
                        )}
                        {(disk.observations_count ?? 0) > 0 && (
                          <Badge className="bg-blue-500/10 text-blue-400 border-blue-500/20 gap-1">
                            <Info className="h-3 w-3" />
                            {disk.observations_count} obs.
                          </Badge>
                        )}
                        {getHealthBadge(disk.health)}
                      </div>
                    </div>
                  </div>

                    {disk.io_errors && disk.io_errors.count > 0 && (
                    <div className={`flex items-start gap-2 p-2 rounded text-xs ${
                      disk.io_errors.severity === 'CRITICAL'
                        ? 'bg-red-500/10 text-red-400 border border-red-500/20'
                        : 'bg-yellow-500/10 text-yellow-400 border border-yellow-500/20'
                    }`}>
                      <AlertTriangle className="h-3.5 w-3.5 flex-shrink-0 mt-0.5" />
                      <span>
                        {disk.io_errors.error_type === 'filesystem'
                          ? `Filesystem corruption detected`
                          : `${disk.io_errors.count} I/O error${disk.io_errors.count !== 1 ? 's' : ''} in 5 min`}
                      </span>
                    </div>
                    )}
                    
                    <div className="grid grid-cols-2 gap-4 text-sm">
                      {disk.size_formatted && (
                      <div>
                        <p className="text-sm text-muted-foreground">Size</p>
                        <p className="font-medium">{disk.size_formatted}</p>
                      </div>
                    )}
                    {disk.smart_status && disk.smart_status !== "unknown" && (
                      <div>
                        <p className="text-sm text-muted-foreground">SMART Status</p>
                        <p className="font-medium capitalize">{disk.smart_status}</p>
                      </div>
                    )}
                    {disk.power_on_hours !== undefined && disk.power_on_hours > 0 && (
                      <div>
                        <p className="text-sm text-muted-foreground">Power On Time</p>
                        <p className="font-medium">{formatHours(disk.power_on_hours)}</p>
                      </div>
                    )}
                    {disk.serial && disk.serial !== "Unknown" && (
                      <div>
                        <p className="text-sm text-muted-foreground">Serial</p>
                        <p className="font-medium text-xs">{disk.serial}</p>
                      </div>
                    )}
                  </div>
                </div>

                <div
                  className="hidden sm:block border border-white/10 rounded-lg p-4 cursor-pointer bg-card hover:bg-white/5 transition-colors"
                  onClick={() => handleDiskClick(disk)}
                >
                  <div className="space-y-2 mb-3">
                    {/* Row 1: Device name and type badge */}
                    <div className="flex items-center gap-2">
                      <HardDrive className="h-5 w-5 text-muted-foreground flex-shrink-0" />
                      <h3 className="font-semibold">/dev/{disk.name}</h3>
                      <Badge className={getDiskTypeBadge(disk.name, disk.rotation_rate).className}>
                        {getDiskTypeBadge(disk.name, disk.rotation_rate).label}
                      </Badge>
                      {disk.is_system_disk && (
                        <Badge className="bg-orange-500/10 text-orange-500 border-orange-500/20 gap-1">
                          <Server className="h-3 w-3" />
                          System
                        </Badge>
                      )}
                    </div>

                    {/* Row 2: Model, temperature, and health status */}
                    <div className="flex items-center justify-between gap-3 pl-7">
                      {disk.model && disk.model !== "Unknown" && (
                        <p className="text-sm text-muted-foreground truncate flex-1 min-w-0">{disk.model}</p>
                      )}
                      <div className="flex items-center gap-3 flex-shrink-0">
                        {disk.temperature > 0 && (
                          <div className="flex items-center gap-1">
                            <Thermometer
                              className={`h-4 w-4 ${getTempColor(disk.temperature, disk.name, disk.rotation_rate)}`}
                            />
                            <span
                              className={`text-sm font-medium ${getTempColor(disk.temperature, disk.name, disk.rotation_rate)}`}
                            >
                              {disk.temperature}°C
                            </span>
                          </div>
                        )}
                        {(disk.observations_count ?? 0) > 0 && (
                          <Badge className="bg-blue-500/10 text-blue-400 border-blue-500/20 gap-1">
                            <Info className="h-3 w-3" />
                            {disk.observations_count} obs.
                          </Badge>
                        )}
                        {getHealthBadge(disk.health)}
                      </div>
                    </div>
                  </div>

                  {disk.io_errors && disk.io_errors.count > 0 && (
                    <div className={`flex items-start gap-2 p-2 rounded text-xs ${
                      disk.io_errors.severity === 'CRITICAL'
                        ? 'bg-red-500/10 text-red-400 border border-red-500/20'
                        : 'bg-yellow-500/10 text-yellow-400 border border-yellow-500/20'
                    }`}>
                      <AlertTriangle className="h-3.5 w-3.5 flex-shrink-0 mt-0.5" />
                      <div>
                        {disk.io_errors.error_type === 'filesystem' ? (
                          <>
                            <span className="font-medium">Filesystem corruption detected</span>
                            {disk.io_errors.reason && (
                              <p className="mt-0.5 opacity-90 whitespace-pre-line">{disk.io_errors.reason}</p>
                            )}
                          </>
                        ) : (
                          <>
                            <span className="font-medium">{disk.io_errors.count} I/O error{disk.io_errors.count !== 1 ? 's' : ''} in 5 min</span>
                            {disk.io_errors.sample && (
                              <p className="mt-0.5 opacity-80 font-mono truncate max-w-md">{disk.io_errors.sample}</p>
                            )}
                          </>
                        )}
                      </div>
                    </div>
                  )}

                  <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
                    {disk.size_formatted && (
                      <div>
                        <p className="text-sm text-muted-foreground">Size</p>
                        <p className="font-medium">{disk.size_formatted}</p>
                      </div>
                    )}
                    {disk.smart_status && disk.smart_status !== "unknown" && (
                      <div>
                        <p className="text-sm text-muted-foreground">SMART Status</p>
                        <p className="font-medium capitalize">{disk.smart_status}</p>
                      </div>
                    )}
                    {disk.power_on_hours !== undefined && disk.power_on_hours > 0 && (
                      <div>
                        <p className="text-sm text-muted-foreground">Power On Time</p>
                        <p className="font-medium">{formatHours(disk.power_on_hours)}</p>
                      </div>
                    )}
                    {disk.serial && disk.serial !== "Unknown" && (
                      <div>
                        <p className="text-sm text-muted-foreground">Serial</p>
                        <p className="font-medium text-xs">{disk.serial.replace(/\\x[0-9a-fA-F]{2}/g, '')}</p>
                      </div>
                    )}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>

      {/* External Storage (USB) */}
      {storageData.disks.filter(d => d.connection_type === 'usb').length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Usb className="h-5 w-5" />
              External Storage (USB)
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-4">
              {storageData.disks.filter(d => d.connection_type === 'usb').map((disk) => (
                <div key={disk.name}>
                  {/* Mobile card */}
                  <div
                    className="sm:hidden border border-white/10 rounded-lg p-4 cursor-pointer bg-white/5 transition-colors"
                    onClick={() => handleDiskClick(disk)}
                  >
                    <div className="space-y-2 mb-3">
                      <div className="flex items-center gap-2">
                        <Usb className="h-5 w-5 text-orange-400 flex-shrink-0" />
                        <h3 className="font-semibold">/dev/{disk.name}</h3>
                        <Badge className="bg-orange-500/10 text-orange-400 border-orange-500/20 text-[10px] px-1.5">USB</Badge>
                      </div>
                      <div className="flex items-center justify-between gap-3 pl-7">
                        {disk.model && disk.model !== "Unknown" && (
                          <p className="text-sm text-muted-foreground truncate flex-1 min-w-0">{disk.model}</p>
                        )}
                        <div className="flex items-center gap-3 flex-shrink-0">
                          {disk.temperature > 0 && (
                            <div className="flex items-center gap-1">
                              <Thermometer className={`h-4 w-4 ${getTempColor(disk.temperature, disk.name, disk.rotation_rate)}`} />
                              <span className={`text-sm font-medium ${getTempColor(disk.temperature, disk.name, disk.rotation_rate)}`}>
                                {disk.temperature}°C
                              </span>
                            </div>
                          )}
                          {(disk.observations_count ?? 0) > 0 && (
                            <Badge className="bg-blue-500/10 text-blue-400 border-blue-500/20 gap-1 text-[10px] px-1.5 py-0">
                              <Info className="h-3 w-3" />
                              {disk.observations_count}
                            </Badge>
                          )}
                          {getHealthBadge(disk.health)}
                        </div>
                      </div>
                    </div>
                    
                    {/* USB Mobile: Size, SMART, Serial grid */}
                    <div className="grid grid-cols-2 gap-4 text-sm">
                      {disk.size_formatted && (
                        <div>
                          <p className="text-sm text-muted-foreground">Size</p>
                          <p className="font-medium">{disk.size_formatted}</p>
                        </div>
                      )}
                      {disk.smart_status && disk.smart_status !== "unknown" && (
                        <div>
                          <p className="text-sm text-muted-foreground">SMART Status</p>
                          <p className="font-medium capitalize">{disk.smart_status}</p>
                        </div>
                      )}
{disk.serial && disk.serial !== "Unknown" && (
                      <div>
                        <p className="text-sm text-muted-foreground">Serial</p>
                        <p className="font-medium text-xs">{disk.serial.replace(/\\x[0-9a-fA-F]{2}/g, '')}</p>
                      </div>
                    )}
                    </div>
                </div>

                {/* Desktop */}
                <div
                  className="hidden sm:block border border-white/10 rounded-lg p-4 cursor-pointer hover:bg-white/5 transition-colors"
                  onClick={() => handleDiskClick(disk)}
                  >
                    <div className="flex items-center justify-between mb-3">
                      <div className="flex items-center gap-2">
                        <Usb className="h-5 w-5 text-orange-400" />
                        <h3 className="font-semibold">/dev/{disk.name}</h3>
                        <Badge className="bg-orange-500/10 text-orange-400 border-orange-500/20 text-[10px] px-1.5">USB</Badge>
                      </div>
                      <div className="flex items-center gap-3">
                        {disk.temperature > 0 && (
                          <div className="flex items-center gap-1">
                            <Thermometer className={`h-4 w-4 ${getTempColor(disk.temperature, disk.name, disk.rotation_rate)}`} />
                            <span className={`text-sm font-medium ${getTempColor(disk.temperature, disk.name, disk.rotation_rate)}`}>
                              {disk.temperature}°C
                            </span>
                          </div>
                        )}
                        {getHealthBadge(disk.health)}
                        {(disk.observations_count ?? 0) > 0 && (
                          <Badge className="bg-blue-500/10 text-blue-400 border-blue-500/20 gap-1">
                            <Info className="h-3 w-3" />
                            {disk.observations_count} obs.
                          </Badge>
                        )}
                      </div>
                    </div>
                    {disk.model && disk.model !== "Unknown" && (
                      <p className="text-sm text-muted-foreground mb-3 ml-7">{disk.model}</p>
                    )}

                    {disk.io_errors && disk.io_errors.count > 0 && (
                      <div className={`flex items-start gap-2 p-2 rounded text-xs mb-3 ${
                        disk.io_errors.severity === 'CRITICAL'
                          ? 'bg-red-500/10 text-red-400 border border-red-500/20'
                          : 'bg-yellow-500/10 text-yellow-400 border border-yellow-500/20'
                      }`}>
                        <AlertTriangle className="h-3.5 w-3.5 flex-shrink-0 mt-0.5" />
                        <div>
                          {disk.io_errors.error_type === 'filesystem' ? (
                            <>
                              <span className="font-medium">Filesystem corruption detected</span>
                              {disk.io_errors.reason && (
                                <p className="mt-0.5 opacity-90 whitespace-pre-line">{disk.io_errors.reason}</p>
                              )}
                            </>
                          ) : (
                            <>
                              <span className="font-medium">{disk.io_errors.count} I/O error{disk.io_errors.count !== 1 ? 's' : ''} in 5 min</span>
                              {disk.io_errors.sample && (
                                <p className="mt-0.5 opacity-80 font-mono truncate max-w-md">{disk.io_errors.sample}</p>
                              )}
                            </>
                          )}
                        </div>
                      </div>
                    )}

                    <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
                      {disk.size_formatted && (
                        <div>
                          <p className="text-sm text-muted-foreground">Size</p>
                          <p className="font-medium">{disk.size_formatted}</p>
                        </div>
                      )}
                      {disk.smart_status && disk.smart_status !== "unknown" && (
                        <div>
                          <p className="text-sm text-muted-foreground">SMART Status</p>
                          <p className="font-medium capitalize">{disk.smart_status}</p>
                        </div>
                      )}
                      {disk.power_on_hours !== undefined && disk.power_on_hours > 0 && (
                        <div>
                          <p className="text-sm text-muted-foreground">Power On Time</p>
                          <p className="font-medium">{formatHours(disk.power_on_hours)}</p>
                        </div>
                      )}
                      {disk.serial && disk.serial !== "Unknown" && (
                        <div>
                          <p className="text-sm text-muted-foreground">Serial</p>
                          <p className="font-medium text-xs">{disk.serial.replace(/\\x[0-9a-fA-F]{2}/g, '')}</p>
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      {/* Disk Details Dialog */}
      <Dialog open={detailsOpen} onOpenChange={(open) => {
        setDetailsOpen(open)
        if (!open) setActiveModalTab("overview")
      }}>
        <DialogContent className="max-w-2xl max-h-[80vh] sm:max-h-[85vh] overflow-hidden flex flex-col p-0">
          <DialogHeader className="px-6 pt-6 pb-0">
            <DialogTitle className="flex items-center gap-2">
              {selectedDisk?.connection_type === 'usb' ? (
                <Usb className="h-5 w-5 text-orange-400" />
              ) : (
                <HardDrive className="h-5 w-5" />
              )}
              Disk Details: /dev/{selectedDisk?.name}
              {selectedDisk?.connection_type === 'usb' && (
                <Badge className="bg-orange-500/10 text-orange-400 border-orange-500/20 text-[10px] px-1.5">USB</Badge>
              )}
              {selectedDisk?.is_system_disk && (
                <Badge className="bg-orange-500/10 text-orange-500 border-orange-500/20 gap-1">
                  <Server className="h-3 w-3" />
                  System
                </Badge>
              )}
            </DialogTitle>
            <DialogDescription>
              {selectedDisk?.model !== "Unknown" ? selectedDisk?.model : "Physical disk"} - {selectedDisk?.size_formatted}
            </DialogDescription>
          </DialogHeader>
          
          {/* Tab Navigation */}
          <div className="flex border-b border-border px-6">
            <button
              onClick={() => setActiveModalTab("overview")}
              className={`flex items-center gap-2 px-4 py-2.5 text-sm font-medium transition-colors border-b-2 -mb-px ${
                activeModalTab === "overview"
                  ? "border-blue-500 text-blue-500"
                  : "border-transparent text-muted-foreground hover:text-foreground"
              }`}
            >
              <Info className="h-4 w-4" />
              Overview
            </button>
            <button
              onClick={() => setActiveModalTab("smart")}
              className={`flex items-center gap-2 px-4 py-2.5 text-sm font-medium transition-colors border-b-2 -mb-px ${
                activeModalTab === "smart"
                  ? "border-green-500 text-green-500"
                  : "border-transparent text-muted-foreground hover:text-foreground"
              }`}
            >
              <Activity className="h-4 w-4" />
              SMART Test
            </button>
          </div>
          
          {/* Tab Content */}
          <div className="flex-1 overflow-y-auto px-6 py-4 min-h-0">
          {selectedDisk && activeModalTab === "overview" && (
            <div className="space-y-4">
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <p className="text-sm text-muted-foreground">Model</p>
                  <p className="font-medium">{selectedDisk.model}</p>
                </div>
                <div>
                  <p className="text-sm text-muted-foreground">Serial Number</p>
                  <p className="font-medium">{selectedDisk.serial?.replace(/\\x[0-9a-fA-F]{2}/g, '') || 'Unknown'}</p>
                </div>
                <div>
                  <p className="text-sm text-muted-foreground">Capacity</p>
                  <p className="font-medium">{selectedDisk.size_formatted}</p>
                </div>
                <div>
                  <p className="text-sm text-muted-foreground">Health Status</p>
                  <div className="flex items-center gap-2 mt-1">
                    {getHealthBadge(selectedDisk.health)}
                    {(selectedDisk.observations_count ?? 0) > 0 && (
<Badge className="bg-blue-500/10 text-blue-400 border-blue-500/20 gap-1">
                      <Info className="h-3 w-3" />
                      {selectedDisk.observations_count} obs.
                    </Badge>
                    )}
                  </div>
                </div>
              </div>

              {/* Wear & Lifetime Section */}
              {getWearIndicator(selectedDisk) && (
                <div className="border-t pt-4">
                  <h4 className="font-semibold mb-3">Wear & Lifetime</h4>
                  <div className="space-y-3">
                    <div>
                      <div className="flex items-center justify-between mb-2">
                        <p className="text-sm text-muted-foreground">{getWearIndicator(selectedDisk)!.label}</p>
                        <p className={`font-medium ${getWearColor(getWearIndicator(selectedDisk)!.value)}`}>
                          {getWearIndicator(selectedDisk)!.value}%
                        </p>
                      </div>
                      <Progress
                        value={getWearIndicator(selectedDisk)!.value}
                        className={`h-2 ${getWearProgressColor(getWearIndicator(selectedDisk)!.value)}`}
                      />
                    </div>
                    {getEstimatedLifeRemaining(selectedDisk) && (
                      <div className="grid grid-cols-2 gap-4">
                        <div>
                          <p className="text-sm text-muted-foreground">Estimated Life Remaining</p>
                          <p className="font-medium">{getEstimatedLifeRemaining(selectedDisk)}</p>
                        </div>
                        {selectedDisk.total_lbas_written && selectedDisk.total_lbas_written > 0 && (
                          <div>
                            <p className="text-sm text-muted-foreground">Total Data Written</p>
                            <p className="font-medium">
                              {selectedDisk.total_lbas_written >= 1024
                                ? `${(selectedDisk.total_lbas_written / 1024).toFixed(2)} TB`
                                : `${selectedDisk.total_lbas_written.toFixed(2)} GB`}
                            </p>
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                </div>
              )}

              <div className="border-t pt-4">
                <h4 className="font-semibold mb-3">SMART Attributes</h4>
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <p className="text-sm text-muted-foreground">Temperature</p>
                    <p
                      className={`font-medium ${getTempColor(selectedDisk.temperature, selectedDisk.name, selectedDisk.rotation_rate)}`}
                    >
                      {selectedDisk.temperature > 0 ? `${selectedDisk.temperature}°C` : "N/A"}
                    </p>
                  </div>
                  <div>
                    <p className="text-sm text-muted-foreground">Power On Hours</p>
                    <p className="font-medium">
                      {selectedDisk.power_on_hours && selectedDisk.power_on_hours > 0
                        ? `${selectedDisk.power_on_hours.toLocaleString()}h (${formatHours(selectedDisk.power_on_hours)})`
                        : "N/A"}
                    </p>
                  </div>
                  <div>
                    <p className="text-sm text-muted-foreground">Rotation Rate</p>
                    <p className="font-medium">{formatRotationRate(selectedDisk.rotation_rate)}</p>
                  </div>
                  <div>
                    <p className="text-sm text-muted-foreground">Power Cycles</p>
                    <p className="font-medium">
                      {selectedDisk.power_cycles && selectedDisk.power_cycles > 0
                        ? selectedDisk.power_cycles.toLocaleString()
                        : "N/A"}
                    </p>
                  </div>
                  <div>
                    <p className="text-sm text-muted-foreground">SMART Status</p>
                    <p className="font-medium capitalize">{selectedDisk.smart_status}</p>
                  </div>
                  <div>
                    <p className="text-sm text-muted-foreground">Reallocated Sectors</p>
                    <p
                      className={`font-medium ${selectedDisk.reallocated_sectors && selectedDisk.reallocated_sectors > 0 ? "text-yellow-500" : ""}`}
                    >
                      {selectedDisk.reallocated_sectors ?? 0}
                    </p>
                  </div>
                  <div>
                    <p className="text-sm text-muted-foreground">Pending Sectors</p>
                    <p
                      className={`font-medium ${selectedDisk.pending_sectors && selectedDisk.pending_sectors > 0 ? "text-yellow-500" : ""}`}
                    >
                      {selectedDisk.pending_sectors ?? 0}
                    </p>
                  </div>
                  <div>
                    <p className="text-sm text-muted-foreground">CRC Errors</p>
                    <p
                      className={`font-medium ${selectedDisk.crc_errors && selectedDisk.crc_errors > 0 ? "text-yellow-500" : ""}`}
                    >
                      {selectedDisk.crc_errors ?? 0}
                    </p>
                  </div>
                </div>
              </div>

              {/* Observations Section */}
              {(diskObservations.length > 0 || loadingObservations) && (
                <div className="border-t pt-4">
                  <h4 className="font-semibold mb-2 flex items-center gap-2">
                    <Info className="h-4 w-4 text-blue-400" />
                    Observations
                    <Badge className="bg-blue-500/10 text-blue-400 border-blue-500/20 text-[10px] px-1.5 py-0">
                      {diskObservations.length}
                    </Badge>
                  </h4>
                  <p className="text-xs text-muted-foreground mb-3">
                    The following observations have been recorded for this disk:
                  </p>
                  {loadingObservations ? (
                    <div className="flex items-center gap-2 text-sm text-muted-foreground py-2">
                      <div className="h-4 w-4 rounded-full border-2 border-transparent border-t-blue-400 animate-spin" />
                      Loading observations...
                    </div>
                  ) : (
                    <div className="space-y-3">
                      {diskObservations.map((obs) => (
                        <div
                          key={obs.id}
                          className={`rounded-lg border p-3 text-sm ${
                            obs.severity === 'critical'
                              ? 'bg-red-500/5 border-red-500/20'
                              : 'bg-blue-500/5 border-blue-500/20'
                          }`}
                        >
                          {/* Header with type badge */}
                          <div className="flex items-center gap-2 flex-wrap mb-2">
                            <Badge className={`text-[10px] px-1.5 py-0 ${
                              obs.severity === 'critical'
                                ? 'bg-red-500/10 text-red-400 border-red-500/20'
                                : 'bg-blue-500/10 text-blue-400 border-blue-500/20'
                            }`}>
                              {obsTypeLabel(obs.error_type)}
                            </Badge>
                          </div>
                          
                          {/* Error message - responsive text wrap */}
                          <p className="text-xs whitespace-pre-wrap break-words opacity-90 font-mono leading-relaxed mb-3">
                            {obs.raw_message}
                          </p>
                          
                          {/* Dates - stacked on mobile, inline on desktop */}
                          <div className="flex flex-col sm:flex-row sm:items-center gap-1 sm:gap-3 text-[10px] text-muted-foreground border-t border-white/5 pt-2">
                            <span className="flex items-center gap-1">
                              <Clock className="h-3 w-3 flex-shrink-0" />
                              <span className="break-words">First: {formatObsDate(obs.first_occurrence)}</span>
                            </span>
                            <span className="flex items-center gap-1">
                              <Clock className="h-3 w-3 flex-shrink-0" />
                              <span className="break-words">Last: {formatObsDate(obs.last_occurrence)}</span>
                            </span>
                          </div>
                          
                          {/* Occurrences count */}
                          <div className="text-[10px] text-muted-foreground mt-1">
                            Occurrences: <span className="font-medium text-foreground">{obs.occurrence_count}</span>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
          
          {/* SMART Test Tab */}
          {selectedDisk && activeModalTab === "smart" && (
            <SmartTestTab disk={selectedDisk} />
          )}
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}

// Generate SMART Report HTML and open in new window (same pattern as Lynis/Latency reports)
function openSmartReport(disk: DiskInfo, testStatus: SmartTestStatus, smartAttributes: Array<{id: number; name: string; value: number; worst: number; threshold: number; raw_value: string; status: 'ok' | 'warning' | 'critical'}>) {
  const now = new Date().toLocaleString()
  const logoUrl = `${window.location.origin}/images/proxmenux-logo.png`
  const reportId = `SMART-${Date.now().toString(36).toUpperCase()}`
  
  // Determine disk type
  let diskType = "HDD"
  if (disk.name.startsWith("nvme")) {
    diskType = "NVMe"
  } else if (!disk.rotation_rate || disk.rotation_rate === 0) {
    diskType = "SSD"
  }
  
  // Health status styling
  const healthStatus = testStatus.smart_status || (testStatus.smart_data?.smart_status) || 'unknown'
  const isHealthy = healthStatus.toLowerCase() === 'passed'
  const healthColor = isHealthy ? '#16a34a' : healthStatus.toLowerCase() === 'failed' ? '#dc2626' : '#ca8a04'
  const healthLabel = isHealthy ? 'PASSED' : healthStatus.toUpperCase()
  
  // Format power on time
  const powerOnHours = disk.power_on_hours || testStatus.smart_data?.power_on_hours || 0
  const powerOnDays = Math.round(powerOnHours / 24)
  const powerOnYears = Math.floor(powerOnHours / 8760)
  const powerOnRemainingDays = Math.floor((powerOnHours % 8760) / 24)
  const powerOnFormatted = powerOnYears > 0 
    ? `${powerOnYears}y ${powerOnRemainingDays}d (${powerOnHours.toLocaleString()}h)`
    : `${powerOnDays}d (${powerOnHours.toLocaleString()}h)`
  
  // Build attributes table
  const attributeRows = smartAttributes.map((attr, i) => {
  const statusColor = attr.status === 'ok' ? '#16a34a' : attr.status === 'warning' ? '#ca8a04' : '#dc2626'
  const statusBg = attr.status === 'ok' ? '#16a34a15' : attr.status === 'warning' ? '#ca8a0415' : '#dc262615'
  return `
  <tr>
  <td style="font-weight:600;">${attr.id}</td>
  <td class="col-name">${attr.name.replace(/_/g, ' ')}</td>
  <td style="text-align:center;">${attr.value}</td>
  <td style="text-align:center;">${attr.worst}</td>
  <td class="hide-mobile" style="text-align:center;">${attr.threshold}</td>
  <td class="col-raw">${attr.raw_value}</td>
  <td><span class="f-tag" style="background:${statusBg};color:${statusColor}">${attr.status === 'ok' ? 'OK' : attr.status.toUpperCase()}</span></td>
  </tr>
  `
  }).join('')
  
  // Critical attributes to highlight
  const criticalAttrs = smartAttributes.filter(a => a.status !== 'ok')
  const hasCritical = criticalAttrs.length > 0
  
  // Build recommendations
  const recommendations: string[] = []
  if (isHealthy) {
    recommendations.push('<div class="rec-item rec-ok"><div class="rec-icon">&#10003;</div><div><strong>Disk is Healthy</strong><p>All SMART attributes are within normal ranges. Continue regular monitoring.</p></div></div>')
  } else {
    recommendations.push('<div class="rec-item rec-critical"><div class="rec-icon">&#10007;</div><div><strong>Critical: Disk Health Issue Detected</strong><p>SMART has reported a health issue. Backup all data immediately and plan for disk replacement.</p></div></div>')
  }
  
  if ((disk.reallocated_sectors ?? 0) > 0) {
    recommendations.push(`<div class="rec-item rec-warn"><div class="rec-icon">&#9888;</div><div><strong>Reallocated Sectors Detected (${disk.reallocated_sectors})</strong><p>The disk has bad sectors that have been remapped. Monitor closely and consider replacement if count increases.</p></div></div>`)
  }
  
  if ((disk.pending_sectors ?? 0) > 0) {
    recommendations.push(`<div class="rec-item rec-warn"><div class="rec-icon">&#9888;</div><div><strong>Pending Sectors (${disk.pending_sectors})</strong><p>There are sectors waiting to be reallocated. This may indicate impending failure.</p></div></div>`)
  }
  
  if (disk.temperature > 55 && diskType === 'HDD') {
    recommendations.push(`<div class="rec-item rec-warn"><div class="rec-icon">&#9888;</div><div><strong>High Temperature (${disk.temperature}°C)</strong><p>HDD is running hot. Improve case airflow or add cooling.</p></div></div>`)
  } else if (disk.temperature > 70 && diskType === 'SSD') {
    recommendations.push(`<div class="rec-item rec-warn"><div class="rec-icon">&#9888;</div><div><strong>High Temperature (${disk.temperature}°C)</strong><p>SSD is running hot. Check airflow around the drive.</p></div></div>`)
  } else if (disk.temperature > 80 && diskType === 'NVMe') {
    recommendations.push(`<div class="rec-item rec-warn"><div class="rec-icon">&#9888;</div><div><strong>High Temperature (${disk.temperature}°C)</strong><p>NVMe is overheating. Consider adding a heatsink or improving case airflow.</p></div></div>`)
  }
  
  if (recommendations.length === 1 && isHealthy) {
    recommendations.push('<div class="rec-item rec-info"><div class="rec-icon">&#9432;</div><div><strong>Regular Maintenance</strong><p>Schedule periodic extended SMART tests (monthly) to catch issues early.</p></div></div>')
    recommendations.push('<div class="rec-item rec-info"><div class="rec-icon">&#9432;</div><div><strong>Backup Strategy</strong><p>Ensure critical data is backed up regularly regardless of disk health status.</p></div></div>')
  }
  
  const html = `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SMART Health Report - /dev/${disk.name}</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; color: #1a1a2e; background: #fff; font-size: 13px; line-height: 1.5; }
  @page { margin: 10mm; size: A4; }
  @media print {
    .no-print { display: none !important; }
    .page-break { page-break-before: always; }
    * { -webkit-print-color-adjust: exact !important; print-color-adjust: exact !important; }
    body { font-size: 11px; padding-top: 0; }
    .section { page-break-inside: avoid; break-inside: avoid; }
  }
  @media screen {
    body { max-width: 1000px; margin: 0 auto; padding: 24px 32px; padding-top: 64px; overflow-x: hidden; }
  }
  @media screen and (max-width: 640px) {
    body { padding: 16px; padding-top: 64px; }
    .grid-4 { grid-template-columns: 1fr 1fr; }
    .rpt-header { flex-direction: column; gap: 12px; align-items: flex-start; }
    .rpt-header-right { text-align: left; }
  }
  
  /* Top bar */
  .top-bar {
    position: fixed; top: 0; left: 0; right: 0; background: #0f172a; color: #e2e8f0;
    padding: 12px 24px; display: flex; align-items: center; justify-content: space-between; z-index: 100;
  }
  .top-bar-left { display: flex; align-items: center; gap: 12px; }
  .top-bar-title { font-weight: 600; }
  .top-bar-subtitle { font-size: 11px; color: #94a3b8; }
  .top-bar button {
    background: #06b6d4; color: #fff; border: none; padding: 10px 20px; border-radius: 6px;
    font-size: 14px; font-weight: 600; cursor: pointer;
  }
  .top-bar button:hover { background: #0891b2; }
  @media print { .top-bar { display: none; } body { padding-top: 0; } }

  /* Header */
  .rpt-header {
    display: flex; align-items: center; justify-content: space-between;
    padding: 18px 0; border-bottom: 3px solid #0f172a; margin-bottom: 22px;
  }
  .rpt-header-left { display: flex; align-items: center; gap: 14px; }
  .rpt-header-left img { height: 44px; width: auto; }
  .rpt-header-left h1 { font-size: 22px; font-weight: 700; color: #0f172a; }
  .rpt-header-left p { font-size: 11px; color: #64748b; }
  .rpt-header-right { text-align: right; font-size: 11px; color: #64748b; line-height: 1.6; }
  .rpt-header-right .rid { font-family: monospace; font-size: 10px; color: #94a3b8; }

  /* Sections */
  .section { margin-bottom: 22px; }
  .section-title {
    font-size: 14px; font-weight: 700; color: #0f172a; text-transform: uppercase;
    letter-spacing: 0.05em; padding-bottom: 5px; border-bottom: 2px solid #e2e8f0; margin-bottom: 12px;
  }

  /* Executive summary */
  .exec-box {
    display: flex; align-items: flex-start; gap: 20px; padding: 20px;
    background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; margin-bottom: 16px;
    flex-wrap: wrap;
  }
  .health-ring {
    width: 96px; height: 96px; border-radius: 50%; display: flex; flex-direction: column;
    align-items: center; justify-content: center; border: 4px solid; flex-shrink: 0;
  }
  .health-icon { font-size: 32px; line-height: 1; }
  .health-lbl { font-size: 11px; font-weight: 700; letter-spacing: 0.05em; margin-top: 4px; }
  .exec-text { flex: 1; min-width: 200px; }
  .exec-text h3 { font-size: 16px; margin-bottom: 4px; }
  .exec-text p { font-size: 12px; color: #64748b; line-height: 1.5; }

  /* Grids */
  .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-bottom: 8px; }
  .grid-3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 8px; margin-bottom: 8px; }
  .grid-4 { display: grid; grid-template-columns: 1fr 1fr 1fr 1fr; gap: 8px; margin-bottom: 8px; }
  .card { padding: 10px 12px; background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 6px; }
  .card-label { font-size: 10px; font-weight: 600; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 2px; }
  .card-value { font-size: 13px; font-weight: 600; color: #0f172a; }
  .card-c { text-align: center; }
  .card-c .card-value { font-size: 20px; font-weight: 800; }

  /* Tags */
  .f-tag { font-size: 9px; padding: 2px 6px; border-radius: 4px; font-weight: 600; }

  /* Tables */
  .attr-tbl { width: 100%; border-collapse: collapse; font-size: 11px; }
  .attr-tbl th { text-align: left; padding: 6px 4px; font-size: 10px; color: #64748b; font-weight: 600; border-bottom: 2px solid #e2e8f0; background: #f1f5f9; }
  .attr-tbl td { padding: 5px 4px; border-bottom: 1px solid #f1f5f9; color: #1e293b; }
  .attr-tbl tr:hover { background: #f8fafc; }
  .attr-tbl .col-name { word-break: break-word; }
  .attr-tbl .col-raw { font-family: monospace; font-size: 10px; }
  .hide-mobile { display: table-cell; }
  @media screen and (max-width: 640px) {
    .hide-mobile { display: none !important; }
    .attr-tbl { font-size: 11px; }
    .attr-tbl th { font-size: 11px; padding: 5px 3px; }
    .attr-tbl td { padding: 5px 3px; }
    .attr-tbl .col-name { padding-right: 6px; }
    .attr-tbl .col-raw { font-size: 11px; word-break: break-all; }
  }

  /* Recommendations */
  .rec-item { display: flex; align-items: flex-start; gap: 12px; padding: 12px; border-radius: 6px; margin-bottom: 8px; }
  .rec-icon { font-size: 18px; flex-shrink: 0; width: 24px; text-align: center; }
  .rec-item strong { display: block; margin-bottom: 2px; }
  .rec-item p { font-size: 12px; color: #64748b; margin: 0; }
  .rec-ok { background: #dcfce7; border: 1px solid #86efac; }
  .rec-ok .rec-icon { color: #16a34a; }
  .rec-warn { background: #fef3c7; border: 1px solid #fcd34d; }
  .rec-warn .rec-icon { color: #ca8a04; }
  .rec-critical { background: #fee2e2; border: 1px solid #fca5a5; }
  .rec-critical .rec-icon { color: #dc2626; }
  .rec-info { background: #e0f2fe; border: 1px solid #7dd3fc; }
  .rec-info .rec-icon { color: #0284c7; }

  /* Footer */
  .rpt-footer {
    margin-top: 32px; padding-top: 12px; border-top: 1px solid #e2e8f0;
    display: flex; justify-content: space-between; font-size: 10px; color: #94a3b8;
  }
</style>
</head>
<body>
<!-- Top bar (screen only) -->
<div class="top-bar no-print">
  <div class="top-bar-left">
    <div class="top-bar-title">SMART Health Report</div>
    <div class="top-bar-subtitle">/dev/${disk.name}</div>
  </div>
  <button onclick="window.print()">Print Report</button>
</div>

<!-- Header -->
<div class="rpt-header">
  <div class="rpt-header-left">
    <img src="${logoUrl}" alt="ProxMenux" onerror="this.style.display='none'">
    <div>
      <h1>SMART Health Report</h1>
      <p>ProxMenux Monitor - Disk Health Analysis</p>
    </div>
  </div>
  <div class="rpt-header-right">
    <div>Date: ${now}</div>
    <div>Device: /dev/${disk.name}</div>
    <div class="rid">ID: ${reportId}</div>
  </div>
</div>

<!-- 1. Executive Summary -->
<div class="section">
  <div class="section-title">1. Executive Summary</div>
  <div class="exec-box">
    <div class="health-ring" style="border-color:${healthColor};color:${healthColor}">
      <div class="health-icon">${isHealthy ? '&#10003;' : '&#10007;'}</div>
      <div class="health-lbl">${healthLabel}</div>
    </div>
    <div class="exec-text">
      <h3>Disk Health Assessment</h3>
      <p>
        ${isHealthy 
          ? `This disk is operating within normal parameters. All SMART attributes are within acceptable thresholds. The disk has been powered on for approximately ${powerOnFormatted} and is currently operating at ${disk.temperature > 0 ? disk.temperature + '°C' : 'N/A'}. ${(disk.reallocated_sectors ?? 0) === 0 ? 'No bad sectors have been detected.' : `${disk.reallocated_sectors} reallocated sector(s) detected - monitor closely.`}`
          : `This disk has reported a SMART health failure. Immediate action is required. Backup all critical data and plan for disk replacement.`
        }
      </p>
    </div>
  </div>
</div>

<!-- 2. Disk Information -->
<div class="section">
  <div class="section-title">2. Disk Information</div>
  <div class="grid-4">
    <div class="card">
      <div class="card-label">Model</div>
      <div class="card-value" style="font-size:11px;">${disk.model || testStatus.smart_data?.model || 'Unknown'}</div>
    </div>
    <div class="card">
      <div class="card-label">Serial</div>
      <div class="card-value" style="font-size:11px;font-family:monospace;">${disk.serial || testStatus.smart_data?.serial || 'Unknown'}</div>
    </div>
    <div class="card">
      <div class="card-label">Capacity</div>
      <div class="card-value" style="font-size:11px;">${disk.size_formatted || 'Unknown'}</div>
    </div>
    <div class="card">
      <div class="card-label">Type</div>
      <div class="card-value" style="font-size:11px;">${diskType}</div>
    </div>
  </div>
  <div class="grid-4">
    <div class="card card-c">
      <div class="card-value" style="color:${disk.temperature > 55 ? '#dc2626' : disk.temperature > 45 ? '#ca8a04' : '#16a34a'}">${disk.temperature > 0 ? disk.temperature + '°C' : 'N/A'}</div>
      <div class="card-label">Temperature</div>
    </div>
    <div class="card card-c">
      <div class="card-value">${powerOnHours.toLocaleString()}h</div>
      <div class="card-label">Power On Time</div>
    </div>
    <div class="card card-c">
      <div class="card-value">${(disk.power_cycles ?? 0).toLocaleString()}</div>
      <div class="card-label">Power Cycles</div>
    </div>
    <div class="card card-c">
      <div class="card-value" style="color:${(disk.reallocated_sectors ?? 0) > 0 ? '#dc2626' : '#16a34a'}">${disk.reallocated_sectors ?? 0}</div>
      <div class="card-label">Reallocated Sectors</div>
    </div>
  </div>
</div>

<!-- 3. SMART Attributes -->
<div class="section">
  <div class="section-title">3. SMART Attributes (${smartAttributes.length} total${hasCritical ? `, ${criticalAttrs.length} warning(s)` : ''})</div>
  <table class="attr-tbl">
    <thead>
      <tr>
        <th style="width:28px;">ID</th>
        <th class="col-name">Attribute</th>
        <th style="text-align:center;width:40px;">Val</th>
        <th style="text-align:center;width:40px;">Worst</th>
        <th class="hide-mobile" style="text-align:center;width:40px;">Thr</th>
        <th class="col-raw" style="width:60px;">Raw</th>
        <th style="width:36px;"></th>
      </tr>
    </thead>
    <tbody>
      ${attributeRows || '<tr><td colspan="7" style="text-align:center;color:#94a3b8;padding:20px;">No SMART attributes available</td></tr>'}
    </tbody>
  </table>
</div>
  
  <!-- 4. Last Test Result -->
<div class="section">
  <div class="section-title">4. Last Self-Test Result</div>
  ${testStatus.last_test ? `
    <div class="grid-4">
      <div class="card">
        <div class="card-label">Test Type</div>
        <div class="card-value" style="text-transform:capitalize;">${testStatus.last_test.type}</div>
      </div>
      <div class="card">
        <div class="card-label">Result</div>
        <div class="card-value" style="color:${testStatus.last_test.status === 'passed' ? '#16a34a' : '#dc2626'};text-transform:capitalize;">${testStatus.last_test.status}</div>
      </div>
      <div class="card">
        <div class="card-label">Completed</div>
        <div class="card-value" style="font-size:11px;">${testStatus.last_test.timestamp || 'N/A'}</div>
      </div>
      <div class="card">
        <div class="card-label">Duration</div>
        <div class="card-value">${testStatus.last_test.duration || 'N/A'}</div>
      </div>
    </div>
  ` : `
    <div style="text-align:center;padding:20px;color:#94a3b8;background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;">
      No self-test history available. Run a SMART self-test to see results here.
    </div>
  `}
</div>

<!-- 5. Recommendations -->
<div class="section">
  <div class="section-title">5. Recommendations</div>
  ${recommendations.join('')}
</div>

<!-- Footer -->
<div class="rpt-footer">
  <div>Report generated by ProxMenux Monitor</div>
  <div>ProxMenux Monitor v1.0.2-beta</div>
</div>

</body>
</html>`

  const blob = new Blob([html], { type: "text/html" })
  const url = URL.createObjectURL(blob)
  window.open(url, "_blank")
}

// SMART Test Tab Component
interface SmartTestTabProps {
  disk: DiskInfo
}

interface SmartTestStatus {
  status: 'idle' | 'running' | 'completed' | 'failed'
  test_type?: string
  progress?: number
  result?: string
  last_test?: {
    type: string
    status: string
    timestamp: string
    duration?: string
    lifetime_hours?: number
  }
  smart_data?: {
    device: string
    model: string
    serial: string
    firmware: string
    smart_status: string
    temperature: number
    power_on_hours: number
    attributes: Array<{
      id: number
      name: string
      value: number
      worst: number
      threshold: number
      raw_value: string
      status: 'ok' | 'warning' | 'critical'
    }>
  }
}

function SmartTestTab({ disk }: SmartTestTabProps) {
  const [testStatus, setTestStatus] = useState<SmartTestStatus>({ status: 'idle' })
  const [loading, setLoading] = useState(true)
  const [runningTest, setRunningTest] = useState<'short' | 'long' | null>(null)
  
  // Extract SMART attributes from testStatus for the report
  const smartAttributes = testStatus.smart_data?.attributes || []
  
  // Fetch current SMART status on mount
  useEffect(() => {
    fetchSmartStatus()
  }, [disk.name])
  
  const fetchSmartStatus = async () => {
    try {
      setLoading(true)
      const data = await fetchApi<SmartTestStatus>(`/api/storage/smart/${disk.name}`)
      setTestStatus(data)
    } catch {
      setTestStatus({ status: 'idle' })
    } finally {
      setLoading(false)
    }
  }
  
  const [testError, setTestError] = useState<string | null>(null)
  
  const runSmartTest = async (testType: 'short' | 'long') => {
    try {
      setRunningTest(testType)
      setTestError(null)
      const response = await fetch(`/api/storage/smart/${disk.name}/test`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ test_type: testType })
      })
      
      if (!response.ok) {
        const errorData = await response.json().catch(() => ({ error: 'Unknown error' }))
        setTestError(errorData.error || 'Failed to start test')
        setRunningTest(null)
        return
      }
      
      // Poll for status updates
      const pollInterval = setInterval(async () => {
        try {
          const data = await fetchApi<SmartTestStatus>(`/api/storage/smart/${disk.name}`)
          setTestStatus(data)
          if (data.status !== 'running') {
            clearInterval(pollInterval)
            setRunningTest(null)
          }
        } catch {
          clearInterval(pollInterval)
          setRunningTest(null)
        }
      }, 5000)
    } catch (err) {
      setTestError('Failed to connect to server')
      setRunningTest(null)
    }
  }
  
  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center py-12 gap-3">
        <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
        <p className="text-sm text-muted-foreground">Loading SMART data...</p>
      </div>
    )
  }
  
  return (
    <div className="space-y-6">
      {/* Quick Actions */}
      <div className="space-y-3">
        <h4 className="font-semibold flex items-center gap-2">
          <Play className="h-4 w-4" />
          Run SMART Test
        </h4>
        <div className="flex flex-wrap gap-3">
          <Button
            variant="outline"
            size="sm"
            onClick={() => runSmartTest('short')}
            disabled={runningTest !== null}
            className="gap-2 bg-blue-500/10 border-blue-500/30 text-blue-500 hover:bg-blue-500/20 hover:text-blue-400"
          >
            {runningTest === 'short' ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Activity className="h-4 w-4" />
            )}
            Short Test (~2 min)
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => runSmartTest('long')}
            disabled={runningTest !== null}
            className="gap-2 bg-blue-500/10 border-blue-500/30 text-blue-500 hover:bg-blue-500/20 hover:text-blue-400"
          >
            {runningTest === 'long' ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Activity className="h-4 w-4" />
            )}
            Extended Test (background)
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={fetchSmartStatus}
            disabled={runningTest !== null}
            className="gap-2 bg-blue-500/10 border-blue-500/30 text-blue-500 hover:bg-blue-500/20 hover:text-blue-400"
          >
            <Activity className="h-4 w-4" />
            Refresh Status
          </Button>
        </div>
        <p className="text-xs text-muted-foreground">
          Short test takes ~2 minutes. Extended test runs in the background and can take several hours for large disks.
          You will receive a notification when the test completes.
        </p>
        
        {/* Error Message */}
        {testError && (
          <div className="flex items-start gap-2 p-3 rounded-lg bg-red-500/10 border border-red-500/20 text-red-400">
            <AlertTriangle className="h-4 w-4 mt-0.5 flex-shrink-0" />
            <div>
              <p className="text-sm font-medium">Failed to start test</p>
              <p className="text-xs opacity-80">{testError}</p>
            </div>
          </div>
        )}
        
        {/* Tools not installed warning */}
        {testStatus.tools_installed && (!testStatus.tools_installed.smartctl || !testStatus.tools_installed.nvme) && (
          <div className="flex items-start gap-2 p-3 rounded-lg bg-amber-500/10 border border-amber-500/20 text-amber-400">
            <AlertTriangle className="h-4 w-4 mt-0.5 flex-shrink-0" />
            <div>
              <p className="text-sm font-medium">SMART tools not fully installed</p>
              <p className="text-xs opacity-80">
                {!testStatus.tools_installed.smartctl && 'smartmontools (for SATA/SAS) '}
                {!testStatus.tools_installed.smartctl && !testStatus.tools_installed.nvme && 'and '}
                {!testStatus.tools_installed.nvme && 'nvme-cli (for NVMe) '}
                not found. Click a test button to auto-install.
              </p>
            </div>
          </div>
        )}
      </div>
      
      {/* Test Progress */}
      {testStatus.status === 'running' && (
        <div className="border rounded-lg p-4 bg-blue-500/5 border-blue-500/20">
          <div className="flex items-center gap-3 mb-3">
            <Loader2 className="h-5 w-5 animate-spin text-blue-500" />
            <div>
              <p className="font-medium text-blue-500">
                {testStatus.test_type === 'short' ? 'Short' : 'Extended'} test in progress
              </p>
              <p className="text-xs text-muted-foreground">
                Please wait while the test completes...
              </p>
            </div>
          </div>
          {testStatus.progress !== undefined && (
            <Progress value={testStatus.progress} className="h-2 [&>div]:bg-blue-500" />
          )}
        </div>
      )}
      
      {/* Last Test Result */}
      {testStatus.last_test && (
        <div className="space-y-3">
          <h4 className="font-semibold flex items-center gap-2">
            <FileText className="h-4 w-4" />
            Last Test Result
          </h4>
          <div className={`border rounded-lg p-4 ${
            testStatus.last_test.status === 'passed' 
              ? 'bg-green-500/5 border-green-500/20' 
              : 'bg-red-500/5 border-red-500/20'
          }`}>
            <div className="flex items-center justify-between mb-2">
              <div className="flex items-center gap-2">
                {testStatus.last_test.status === 'passed' ? (
                  <CheckCircle2 className="h-5 w-5 text-green-500" />
                ) : (
                  <XCircle className="h-5 w-5 text-red-500" />
                )}
                <span className="font-medium">
                  {testStatus.last_test.type === 'short' ? 'Short' : 'Extended'} Test - {' '}
                  {testStatus.last_test.status === 'passed' ? 'Passed' : 'Failed'}
                </span>
              </div>
              <Badge className={testStatus.last_test.status === 'passed' 
                ? 'bg-green-500/10 text-green-500 border-green-500/20'
                : 'bg-red-500/10 text-red-500 border-red-500/20'
              }>
                {testStatus.last_test.status}
              </Badge>
            </div>
            <div className="grid grid-cols-2 gap-4 text-sm">
              <div>
                <p className="text-muted-foreground">Result</p>
                <p className="font-medium">{testStatus.last_test.timestamp}</p>
              </div>
              {testStatus.last_test.lifetime_hours && (
                <div>
                  <p className="text-muted-foreground">At Power-On Hours</p>
                  <p className="font-medium">{testStatus.last_test.lifetime_hours.toLocaleString()}h</p>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
      
      {/* SMART Attributes Summary */}
      {testStatus.smart_data?.attributes && testStatus.smart_data.attributes.length > 0 && (
        <div className="space-y-3">
          <h4 className="font-semibold flex items-center gap-2">
            <Activity className="h-4 w-4" />
            SMART Attributes
          </h4>
          <div className="border rounded-lg overflow-hidden">
            <div className="grid grid-cols-12 gap-2 p-3 bg-muted/30 text-xs font-medium text-muted-foreground">
              <div className="col-span-1">ID</div>
              <div className="col-span-5">Attribute</div>
              <div className="col-span-2 text-center">Value</div>
              <div className="col-span-2 text-center">Worst</div>
              <div className="col-span-2 text-center">Status</div>
            </div>
            <div className="divide-y divide-border max-h-[200px] overflow-y-auto">
              {testStatus.smart_data.attributes.slice(0, 15).map((attr) => (
                <div key={attr.id} className="grid grid-cols-12 gap-2 p-3 text-sm items-center">
                  <div className="col-span-1 text-muted-foreground">{attr.id}</div>
                  <div className="col-span-5 truncate" title={attr.name}>{attr.name}</div>
                  <div className="col-span-2 text-center font-mono">{attr.value}</div>
                  <div className="col-span-2 text-center font-mono text-muted-foreground">{attr.worst}</div>
                  <div className="col-span-2 text-center">
                    {attr.status === 'ok' ? (
                      <CheckCircle2 className="h-4 w-4 text-green-500 mx-auto" />
                    ) : attr.status === 'warning' ? (
                      <AlertTriangle className="h-4 w-4 text-yellow-500 mx-auto" />
                    ) : (
                      <XCircle className="h-4 w-4 text-red-500 mx-auto" />
                    )}
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
      
      {/* View Full Report Button */}
      <div className="pt-4 border-t">
        <Button 
          variant="outline" 
          className="w-full gap-2 bg-blue-500/10 border-blue-500/30 text-blue-500 hover:bg-blue-500/20 hover:text-blue-400"
          onClick={() => openSmartReport(disk, testStatus, smartAttributes)}
        >
          <FileText className="h-4 w-4" />
          View Full SMART Report
        </Button>
        <p className="text-xs text-muted-foreground text-center mt-2">
          Generate a comprehensive professional report with detailed analysis and recommendations.
        </p>
      </div>
      

    </div>
  )
}
