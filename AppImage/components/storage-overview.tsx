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
  const [showReport, setShowReport] = useState(false)
  const [reportTab, setReportTab] = useState<'overview' | 'attributes' | 'history' | 'recommendations'>('overview')
  
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
  
  const runSmartTest = async (testType: 'short' | 'long') => {
    try {
      setRunningTest(testType)
      await fetchApi(`/api/storage/smart/${disk.name}/test`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ test_type: testType })
      })
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
    } catch {
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
            className="gap-2"
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
            className="gap-2"
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
            className="gap-2"
          >
            <Activity className="h-4 w-4" />
            Refresh Status
          </Button>
        </div>
        <p className="text-xs text-muted-foreground">
          Short test takes ~2 minutes. Extended test runs in the background and can take several hours for large disks.
          You will receive a notification when the test completes.
        </p>
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
                <p className="text-muted-foreground">Completed</p>
                <p className="font-medium">{testStatus.last_test.timestamp}</p>
              </div>
              {testStatus.last_test.duration && (
                <div>
                  <p className="text-muted-foreground">Duration</p>
                  <p className="font-medium">{testStatus.last_test.duration}</p>
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
          className="w-full gap-2"
          onClick={() => setShowReport(true)}
        >
          <FileText className="h-4 w-4" />
          View Full SMART Report
        </Button>
        <p className="text-xs text-muted-foreground text-center mt-2">
          Generate a comprehensive professional report with detailed analysis and recommendations.
        </p>
      </div>
      
      {/* Full SMART Report Dialog */}
      <Dialog open={showReport} onOpenChange={setShowReport}>
        <DialogContent className="max-w-4xl max-h-[90vh] overflow-hidden flex flex-col p-0">
          <DialogHeader className="px-6 pt-6 pb-0">
            <DialogTitle className="flex items-center gap-2">
              <FileText className="h-5 w-5" />
              SMART Health Report: /dev/{disk.name}
            </DialogTitle>
            <DialogDescription>
              Comprehensive analysis of disk health, SMART attributes, and recommendations
            </DialogDescription>
          </DialogHeader>
          
          {/* Report Tabs */}
          <div className="flex border-b border-border px-6 overflow-x-auto">
            <button
              onClick={() => setReportTab('overview')}
              className={`flex items-center gap-2 px-4 py-2.5 text-sm font-medium transition-colors border-b-2 -mb-px whitespace-nowrap ${
                reportTab === 'overview'
                  ? "border-blue-500 text-blue-500"
                  : "border-transparent text-muted-foreground hover:text-foreground"
              }`}
            >
              <Info className="h-4 w-4" />
              Overview
            </button>
            <button
              onClick={() => setReportTab('attributes')}
              className={`flex items-center gap-2 px-4 py-2.5 text-sm font-medium transition-colors border-b-2 -mb-px whitespace-nowrap ${
                reportTab === 'attributes'
                  ? "border-purple-500 text-purple-500"
                  : "border-transparent text-muted-foreground hover:text-foreground"
              }`}
            >
              <Activity className="h-4 w-4" />
              Attributes
            </button>
            <button
              onClick={() => setReportTab('history')}
              className={`flex items-center gap-2 px-4 py-2.5 text-sm font-medium transition-colors border-b-2 -mb-px whitespace-nowrap ${
                reportTab === 'history'
                  ? "border-amber-500 text-amber-500"
                  : "border-transparent text-muted-foreground hover:text-foreground"
              }`}
            >
              <Clock className="h-4 w-4" />
              History
            </button>
            <button
              onClick={() => setReportTab('recommendations')}
              className={`flex items-center gap-2 px-4 py-2.5 text-sm font-medium transition-colors border-b-2 -mb-px whitespace-nowrap ${
                reportTab === 'recommendations'
                  ? "border-green-500 text-green-500"
                  : "border-transparent text-muted-foreground hover:text-foreground"
              }`}
            >
              <CheckCircle2 className="h-4 w-4" />
              Recommendations
            </button>
          </div>
          
          {/* Report Content */}
          <div className="flex-1 overflow-y-auto px-6 py-4 min-h-0">
            {/* Overview Tab */}
            {reportTab === 'overview' && (
              <div className="space-y-6">
                {/* Health Score Card */}
                <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                  <div className={`border rounded-lg p-4 ${
                    testStatus.smart_status === 'passed' 
                      ? 'bg-green-500/5 border-green-500/20' 
                      : testStatus.smart_status === 'failed'
                        ? 'bg-red-500/5 border-red-500/20'
                        : 'bg-yellow-500/5 border-yellow-500/20'
                  }`}>
                    <p className="text-sm text-muted-foreground mb-1">Overall Health</p>
                    <div className="flex items-center gap-2">
                      {testStatus.smart_status === 'passed' ? (
                        <CheckCircle2 className="h-6 w-6 text-green-500" />
                      ) : testStatus.smart_status === 'failed' ? (
                        <XCircle className="h-6 w-6 text-red-500" />
                      ) : (
                        <AlertTriangle className="h-6 w-6 text-yellow-500" />
                      )}
                      <span className="text-xl font-bold capitalize">
                        {testStatus.smart_status || 'Unknown'}
                      </span>
                    </div>
                  </div>
                  
                  <div className="border rounded-lg p-4 bg-muted/20">
                    <p className="text-sm text-muted-foreground mb-1">Temperature</p>
                    <div className="flex items-center gap-2">
                      <Thermometer className="h-6 w-6 text-blue-500" />
                      <span className="text-xl font-bold">
                        {disk.temperature > 0 ? `${disk.temperature}°C` : 'N/A'}
                      </span>
                    </div>
                  </div>
                  
                  <div className="border rounded-lg p-4 bg-muted/20">
                    <p className="text-sm text-muted-foreground mb-1">Power On Time</p>
                    <div className="flex items-center gap-2">
                      <Clock className="h-6 w-6 text-purple-500" />
                      <span className="text-xl font-bold">
                        {disk.power_on_hours ? `${disk.power_on_hours.toLocaleString()}h` : 'N/A'}
                      </span>
                    </div>
                  </div>
                </div>
                
                {/* Executive Summary */}
                <div className="border rounded-lg p-4">
                  <h4 className="font-semibold mb-3 flex items-center gap-2">
                    <FileText className="h-4 w-4" />
                    Executive Summary
                  </h4>
                  <div className="prose prose-sm prose-invert max-w-none">
                    <p className="text-muted-foreground leading-relaxed">
                      {testStatus.smart_status === 'passed' ? (
                        <>
                          This disk is operating within normal parameters. All SMART attributes are within acceptable thresholds, 
                          indicating good health. The disk has been powered on for approximately{' '}
                          <span className="text-foreground font-medium">
                            {disk.power_on_hours ? `${Math.round(disk.power_on_hours / 24)} days` : 'an unknown period'}
                          </span>{' '}
                          and is currently operating at{' '}
                          <span className="text-foreground font-medium">{disk.temperature || 'N/A'}°C</span>.
                          {disk.reallocated_sectors === 0 && disk.pending_sectors === 0 
                            ? ' No bad sectors have been detected.'
                            : disk.reallocated_sectors && disk.reallocated_sectors > 0 
                              ? ` ${disk.reallocated_sectors} sectors have been reallocated, which may indicate early signs of wear.`
                              : ''}
                        </>
                      ) : testStatus.smart_status === 'failed' ? (
                        <>
                          <span className="text-red-400 font-medium">Warning: This disk has failed SMART health assessment.</span>{' '}
                          One or more critical SMART attributes have exceeded their failure threshold. 
                          It is strongly recommended to backup all data immediately and consider replacing this disk.
                          {disk.reallocated_sectors && disk.reallocated_sectors > 0 
                            ? ` The disk has ${disk.reallocated_sectors} reallocated sectors, indicating physical media degradation.`
                            : ''}
                        </>
                      ) : (
                        <>
                          The disk health status could not be fully determined. Some SMART attributes may be showing warning signs.
                          It is recommended to run a full SMART self-test and monitor the disk closely.
                        </>
                      )}
                    </p>
                  </div>
                </div>
                
                {/* Key Metrics */}
                <div className="border rounded-lg p-4">
                  <h4 className="font-semibold mb-3">Key Metrics</h4>
                  <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
                    <div>
                      <p className="text-muted-foreground">Model</p>
                      <p className="font-medium">{disk.model || 'Unknown'}</p>
                    </div>
                    <div>
                      <p className="text-muted-foreground">Serial</p>
                      <p className="font-medium font-mono text-xs">{disk.serial?.replace(/\\x[0-9a-fA-F]{2}/g, '') || 'Unknown'}</p>
                    </div>
                    <div>
                      <p className="text-muted-foreground">Capacity</p>
                      <p className="font-medium">{disk.size_formatted || 'Unknown'}</p>
                    </div>
                    <div>
                      <p className="text-muted-foreground">Power Cycles</p>
                      <p className="font-medium">{disk.power_cycles?.toLocaleString() || 'N/A'}</p>
                    </div>
                    <div>
                      <p className="text-muted-foreground">Reallocated Sectors</p>
                      <p className={`font-medium ${disk.reallocated_sectors && disk.reallocated_sectors > 0 ? 'text-yellow-500' : ''}`}>
                        {disk.reallocated_sectors ?? 0}
                      </p>
                    </div>
                    <div>
                      <p className="text-muted-foreground">Pending Sectors</p>
                      <p className={`font-medium ${disk.pending_sectors && disk.pending_sectors > 0 ? 'text-yellow-500' : ''}`}>
                        {disk.pending_sectors ?? 0}
                      </p>
                    </div>
                    <div>
                      <p className="text-muted-foreground">CRC Errors</p>
                      <p className={`font-medium ${disk.crc_errors && disk.crc_errors > 0 ? 'text-yellow-500' : ''}`}>
                        {disk.crc_errors ?? 0}
                      </p>
                    </div>
                    <div>
                      <p className="text-muted-foreground">Disk Type</p>
                      <p className="font-medium">
                        {disk.name.startsWith('nvme') ? 'NVMe' : !disk.rotation_rate || disk.rotation_rate === 0 ? 'SSD' : 'HDD'}
                      </p>
                    </div>
                  </div>
                </div>
              </div>
            )}
            
            {/* Attributes Tab */}
            {reportTab === 'attributes' && (
              <div className="space-y-4">
                <div className="border rounded-lg p-4 bg-muted/20">
                  <h4 className="font-semibold mb-2">Understanding SMART Attributes</h4>
                  <p className="text-sm text-muted-foreground">
                    SMART (Self-Monitoring, Analysis and Reporting Technology) attributes are sensors built into hard drives and SSDs. 
                    Each attribute has a current value, a worst recorded value, and a threshold. When the current value drops below the threshold, 
                    the attribute is considered failed. Values typically decrease from 100 (or 200/253 on some drives) as the attribute degrades.
                  </p>
                </div>
                
                {testStatus.smart_data?.attributes && testStatus.smart_data.attributes.length > 0 ? (
                  <div className="border rounded-lg overflow-hidden">
                    <div className="grid grid-cols-12 gap-2 p-3 bg-muted/30 text-xs font-medium text-muted-foreground">
                      <div className="col-span-1">ID</div>
                      <div className="col-span-4">Attribute Name</div>
                      <div className="col-span-2 text-center">Value</div>
                      <div className="col-span-2 text-center">Worst</div>
                      <div className="col-span-2 text-center">Threshold</div>
                      <div className="col-span-1 text-center">Status</div>
                    </div>
                    <div className="divide-y divide-border max-h-[400px] overflow-y-auto">
                      {testStatus.smart_data.attributes.map((attr) => (
                        <div key={attr.id} className={`grid grid-cols-12 gap-2 p-3 text-sm items-center ${
                          attr.status === 'critical' ? 'bg-red-500/5' : attr.status === 'warning' ? 'bg-yellow-500/5' : ''
                        }`}>
                          <div className="col-span-1 text-muted-foreground font-mono">{attr.id}</div>
                          <div className="col-span-4">
                            <p className="truncate font-medium" title={attr.name}>{attr.name.replace(/_/g, ' ')}</p>
                            <p className="text-xs text-muted-foreground">Raw: {attr.raw_value}</p>
                          </div>
                          <div className="col-span-2 text-center font-mono font-medium">{attr.value}</div>
                          <div className="col-span-2 text-center font-mono text-muted-foreground">{attr.worst}</div>
                          <div className="col-span-2 text-center font-mono text-muted-foreground">{attr.threshold}</div>
                          <div className="col-span-1 text-center">
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
                ) : (
                  <div className="text-center py-8 text-muted-foreground">
                    <Activity className="h-12 w-12 mx-auto mb-3 opacity-50" />
                    <p>No SMART attribute data available.</p>
                    <p className="text-sm mt-1">Run a SMART test to collect attribute data.</p>
                  </div>
                )}
              </div>
            )}
            
            {/* History Tab */}
            {reportTab === 'history' && (
              <div className="space-y-4">
                {testStatus.last_test ? (
                  <div className={`border rounded-lg p-4 ${
                    testStatus.last_test.status === 'passed' 
                      ? 'bg-green-500/5 border-green-500/20' 
                      : 'bg-red-500/5 border-red-500/20'
                  }`}>
                    <div className="flex items-center justify-between mb-3">
                      <h4 className="font-semibold flex items-center gap-2">
                        {testStatus.last_test.status === 'passed' ? (
                          <CheckCircle2 className="h-5 w-5 text-green-500" />
                        ) : (
                          <XCircle className="h-5 w-5 text-red-500" />
                        )}
                        Last Test Result
                      </h4>
                      <Badge className={testStatus.last_test.status === 'passed' 
                        ? 'bg-green-500/10 text-green-500 border-green-500/20'
                        : 'bg-red-500/10 text-red-500 border-red-500/20'
                      }>
                        {testStatus.last_test.status}
                      </Badge>
                    </div>
                    <div className="grid grid-cols-2 gap-4 text-sm">
                      <div>
                        <p className="text-muted-foreground">Test Type</p>
                        <p className="font-medium capitalize">{testStatus.last_test.type}</p>
                      </div>
                      <div>
                        <p className="text-muted-foreground">Completed</p>
                        <p className="font-medium">{testStatus.last_test.timestamp}</p>
                      </div>
                    </div>
                  </div>
                ) : (
                  <div className="text-center py-8 text-muted-foreground">
                    <Clock className="h-12 w-12 mx-auto mb-3 opacity-50" />
                    <p>No test history available.</p>
                    <p className="text-sm mt-1">Run a SMART self-test to see results here.</p>
                  </div>
                )}
                
                <div className="border rounded-lg p-4 bg-muted/20">
                  <h4 className="font-semibold mb-2">About Self-Tests</h4>
                  <div className="space-y-2 text-sm text-muted-foreground">
                    <p>
                      <strong className="text-foreground">Short Test (~2 minutes):</strong> Performs a quick check of the disk&apos;s 
                      basic functionality including read/seek tests on a small portion of the disk surface.
                    </p>
                    <p>
                      <strong className="text-foreground">Extended Test (hours):</strong> Performs a comprehensive surface scan 
                      of the entire disk. Duration depends on disk size - typically 1-2 hours per TB.
                    </p>
                  </div>
                </div>
              </div>
            )}
            
            {/* Recommendations Tab */}
            {reportTab === 'recommendations' && (
              <div className="space-y-4">
                {/* Status-based recommendations */}
                {testStatus.smart_status === 'passed' && (
                  <div className="border rounded-lg p-4 bg-green-500/5 border-green-500/20">
                    <div className="flex items-start gap-3">
                      <CheckCircle2 className="h-5 w-5 text-green-500 mt-0.5" />
                      <div>
                        <h4 className="font-semibold text-green-500">Disk is Healthy</h4>
                        <p className="text-sm text-muted-foreground mt-1">
                          All SMART attributes are within normal ranges. Continue with regular monitoring.
                        </p>
                      </div>
                    </div>
                  </div>
                )}
                
                {testStatus.smart_status === 'failed' && (
                  <div className="border rounded-lg p-4 bg-red-500/5 border-red-500/20">
                    <div className="flex items-start gap-3">
                      <XCircle className="h-5 w-5 text-red-500 mt-0.5" />
                      <div>
                        <h4 className="font-semibold text-red-500">Critical: Disk Replacement Recommended</h4>
                        <p className="text-sm text-muted-foreground mt-1">
                          This disk has failed SMART health assessment. Backup all data immediately and plan for disk replacement.
                        </p>
                      </div>
                    </div>
                  </div>
                )}
                
                {/* Conditional recommendations */}
                <div className="space-y-3">
                  <h4 className="font-semibold">Recommendations</h4>
                  
                  {(disk.reallocated_sectors ?? 0) > 0 && (
                    <div className="border rounded-lg p-3 bg-yellow-500/5 border-yellow-500/20">
                      <div className="flex items-start gap-3">
                        <AlertTriangle className="h-5 w-5 text-yellow-500 mt-0.5" />
                        <div>
                          <p className="font-medium">Reallocated Sectors Detected</p>
                          <p className="text-sm text-muted-foreground mt-1">
                            {disk.reallocated_sectors} sectors have been reallocated. This indicates the disk has found and 
                            remapped bad sectors. Monitor this value - if it increases rapidly, consider replacing the disk.
                          </p>
                        </div>
                      </div>
                    </div>
                  )}
                  
                  {(disk.pending_sectors ?? 0) > 0 && (
                    <div className="border rounded-lg p-3 bg-yellow-500/5 border-yellow-500/20">
                      <div className="flex items-start gap-3">
                        <AlertTriangle className="h-5 w-5 text-yellow-500 mt-0.5" />
                        <div>
                          <p className="font-medium">Pending Sectors Detected</p>
                          <p className="text-sm text-muted-foreground mt-1">
                            {disk.pending_sectors} sectors are pending reallocation. These sectors may be unreadable. 
                            Run an extended self-test to force reallocation attempts.
                          </p>
                        </div>
                      </div>
                    </div>
                  )}
                  
                  {disk.temperature > 55 && (
                    <div className="border rounded-lg p-3 bg-yellow-500/5 border-yellow-500/20">
                      <div className="flex items-start gap-3">
                        <Thermometer className="h-5 w-5 text-yellow-500 mt-0.5" />
                        <div>
                          <p className="font-medium">Elevated Temperature</p>
                          <p className="text-sm text-muted-foreground mt-1">
                            Current temperature ({disk.temperature}°C) is above optimal. Improve airflow or reduce disk activity. 
                            Sustained high temperatures can reduce disk lifespan.
                          </p>
                        </div>
                      </div>
                    </div>
                  )}
                  
                  {(disk.power_on_hours ?? 0) > 35000 && (
                    <div className="border rounded-lg p-3 bg-blue-500/5 border-blue-500/20">
                      <div className="flex items-start gap-3">
                        <Info className="h-5 w-5 text-blue-500 mt-0.5" />
                        <div>
                          <p className="font-medium">High Power-On Hours</p>
                          <p className="text-sm text-muted-foreground mt-1">
                            This disk has been running for {Math.round((disk.power_on_hours ?? 0) / 8760)} years. 
                            While still operational, consider planning for replacement as disks typically have a 3-5 year lifespan.
                          </p>
                        </div>
                      </div>
                    </div>
                  )}
                  
                  {/* General best practices */}
                  <div className="border rounded-lg p-4 bg-muted/20 mt-6">
                    <h4 className="font-semibold mb-3">Best Practices</h4>
                    <ul className="space-y-2 text-sm text-muted-foreground">
                      <li className="flex items-start gap-2">
                        <CheckCircle2 className="h-4 w-4 text-green-500 mt-0.5 flex-shrink-0" />
                        <span>Run a short SMART test monthly to catch early issues</span>
                      </li>
                      <li className="flex items-start gap-2">
                        <CheckCircle2 className="h-4 w-4 text-green-500 mt-0.5 flex-shrink-0" />
                        <span>Run an extended test quarterly for comprehensive verification</span>
                      </li>
                      <li className="flex items-start gap-2">
                        <CheckCircle2 className="h-4 w-4 text-green-500 mt-0.5 flex-shrink-0" />
                        <span>Maintain regular backups - SMART can detect some failures but not all</span>
                      </li>
                      <li className="flex items-start gap-2">
                        <CheckCircle2 className="h-4 w-4 text-green-500 mt-0.5 flex-shrink-0" />
                        <span>Keep disk temperatures below 50°C for optimal lifespan</span>
                      </li>
                      <li className="flex items-start gap-2">
                        <CheckCircle2 className="h-4 w-4 text-green-500 mt-0.5 flex-shrink-0" />
                        <span>Replace disks proactively after 4-5 years of heavy use</span>
                      </li>
                    </ul>
                  </div>
                </div>
              </div>
            )}
          </div>
          
          {/* Report Footer */}
          <div className="border-t px-6 py-4 flex justify-between items-center bg-muted/10">
            <p className="text-xs text-muted-foreground">
              Report generated by ProxMenux Monitor
            </p>
            <Button variant="outline" size="sm" onClick={() => window.print()} className="gap-2">
              <FileText className="h-4 w-4" />
              Print Report
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}
