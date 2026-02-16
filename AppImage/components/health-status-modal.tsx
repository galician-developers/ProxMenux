"use client"

import type React from "react"

import { useState, useEffect, useCallback } from "react"
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Loader2,
  CheckCircle2,
  AlertTriangle,
  XCircle,
  Info,
  Activity,
  Cpu,
  MemoryStick,
  HardDrive,
  Disc,
  Network,
  Box,
  Settings,
  FileText,
  RefreshCw,
  Shield,
  X,
  Clock,
  BellOff,
  ChevronRight,
} from "lucide-react"

interface CategoryCheck {
  status: string
  reason?: string
  details?: any
  checks?: Record<string, { status: string; detail: string; [key: string]: any }>
  dismissable?: boolean
  [key: string]: any
}

interface DismissedError {
  error_key: string
  category: string
  severity: string
  reason: string
  dismissed: boolean
  suppression_remaining_hours: number
  resolved_at: string
}

interface HealthDetails {
  overall: string
  summary: string
  details: {
    cpu: CategoryCheck
    memory: CategoryCheck
    storage: CategoryCheck
    disks: CategoryCheck
    network: CategoryCheck
    vms: CategoryCheck
    services: CategoryCheck
    logs: CategoryCheck
    updates: CategoryCheck
    security: CategoryCheck
  }
  timestamp: string
}

interface FullHealthData {
  health: HealthDetails
  active_errors: any[]
  dismissed: DismissedError[]
  timestamp: string
}

interface HealthStatusModalProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  getApiUrl: (path: string) => string
}

const CATEGORIES = [
  { key: "cpu", label: "CPU Usage & Temperature", Icon: Cpu },
  { key: "memory", label: "Memory & Swap", Icon: MemoryStick },
  { key: "storage", label: "Storage Mounts & Space", Icon: HardDrive },
  { key: "disks", label: "Disk I/O & Errors", Icon: Disc },
  { key: "network", label: "Network Interfaces", Icon: Network },
  { key: "vms", label: "VMs & Containers", Icon: Box },
  { key: "services", label: "PVE Services", Icon: Settings },
  { key: "logs", label: "System Logs", Icon: FileText },
  { key: "updates", label: "System Updates", Icon: RefreshCw },
  { key: "security", label: "Security & Certificates", Icon: Shield },
]

export function HealthStatusModal({ open, onOpenChange, getApiUrl }: HealthStatusModalProps) {
  const [loading, setLoading] = useState(true)
  const [healthData, setHealthData] = useState<HealthDetails | null>(null)
  const [dismissedItems, setDismissedItems] = useState<DismissedError[]>([])
  const [error, setError] = useState<string | null>(null)
  const [dismissingKey, setDismissingKey] = useState<string | null>(null)
  const [expandedCategories, setExpandedCategories] = useState<Set<string>>(new Set())

  const fetchHealthDetails = useCallback(async () => {
    setLoading(true)
    setError(null)

    try {
      // Use the new combined endpoint for fewer round-trips
      const response = await fetch(getApiUrl("/api/health/full"))
      if (!response.ok) {
        // Fallback to legacy endpoint
        const legacyResponse = await fetch(getApiUrl("/api/health/details"))
        if (!legacyResponse.ok) throw new Error("Failed to fetch health details")
        const data = await legacyResponse.json()
        setHealthData(data)
        setDismissedItems([])
      } else {
        const fullData: FullHealthData = await response.json()
        setHealthData(fullData.health)
        setDismissedItems(fullData.dismissed || [])
      }

      const event = new CustomEvent("healthStatusUpdated", {
        detail: { status: healthData?.overall || "OK" },
      })
      window.dispatchEvent(event)
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error")
    } finally {
      setLoading(false)
    }
  }, [getApiUrl, healthData?.overall])

  useEffect(() => {
    if (open) {
      fetchHealthDetails()
    }
  }, [open])

  // Auto-expand non-OK categories when data loads
  useEffect(() => {
    if (healthData?.details) {
      const nonOkCategories = new Set<string>()
      CATEGORIES.forEach(({ key }) => {
        const cat = healthData.details[key as keyof typeof healthData.details]
        if (cat && cat.status?.toUpperCase() !== "OK") {
          nonOkCategories.add(key)
        }
      })
      setExpandedCategories(nonOkCategories)
    }
  }, [healthData])

  const toggleCategory = (key: string) => {
    setExpandedCategories(prev => {
      const next = new Set(prev)
      if (next.has(key)) {
        next.delete(key)
      } else {
        next.add(key)
      }
      return next
    })
  }

  const getStatusIcon = (status: string, size: "sm" | "md" = "md") => {
    const statusUpper = status?.toUpperCase()
    const cls = size === "sm" ? "h-4 w-4" : "h-5 w-5"
    switch (statusUpper) {
      case "OK":
        return <CheckCircle2 className={`${cls} text-green-500`} />
      case "INFO":
        return <Info className={`${cls} text-blue-500`} />
      case "WARNING":
        return <AlertTriangle className={`${cls} text-yellow-500`} />
      case "CRITICAL":
        return <XCircle className={`${cls} text-red-500`} />
      default:
        return <Activity className={`${cls} text-muted-foreground`} />
    }
  }

  const getStatusBadge = (status: string) => {
    const statusUpper = status?.toUpperCase()
    switch (statusUpper) {
      case "OK":
        return <Badge className="bg-green-500 text-white hover:bg-green-500">OK</Badge>
      case "INFO":
        return <Badge className="bg-blue-500 text-white hover:bg-blue-500">Info</Badge>
      case "WARNING":
        return <Badge className="bg-yellow-500 text-white hover:bg-yellow-500">Warning</Badge>
      case "CRITICAL":
        return <Badge className="bg-red-500 text-white hover:bg-red-500">Critical</Badge>
      default:
        return <Badge>Unknown</Badge>
    }
  }

  const getHealthStats = () => {
    if (!healthData?.details) {
      return { total: 0, healthy: 0, info: 0, warnings: 0, critical: 0 }
    }

    let healthy = 0
    let info = 0
    let warnings = 0
    let critical = 0

    CATEGORIES.forEach(({ key }) => {
      const categoryData = healthData.details[key as keyof typeof healthData.details]
      if (categoryData) {
        const status = categoryData.status?.toUpperCase()
        if (status === "OK") healthy++
        else if (status === "INFO") info++
        else if (status === "WARNING") warnings++
        else if (status === "CRITICAL") critical++
      }
    })

    return { total: CATEGORIES.length, healthy, info, warnings, critical }
  }

  const stats = getHealthStats()

  const handleCategoryClick = (categoryKey: string, status: string) => {
    if (status === "OK" || status === "INFO") return

    onOpenChange(false)

    const categoryToTab: Record<string, string> = {
      storage: "storage",
      disks: "storage",
      network: "network",
      vms: "vms",
      logs: "logs",
      hardware: "hardware",
      services: "hardware",
    }

    const targetTab = categoryToTab[categoryKey]
    if (targetTab) {
      const event = new CustomEvent("changeTab", { detail: { tab: targetTab } })
      window.dispatchEvent(event)
    }
  }

  const handleAcknowledge = async (errorKey: string, e: React.MouseEvent) => {
    e.stopPropagation()
    setDismissingKey(errorKey)

    try {
      const response = await fetch(getApiUrl("/api/health/acknowledge"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ error_key: errorKey }),
      })

      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(errorData.error || "Failed to dismiss error")
      }

      await fetchHealthDetails()
    } catch (err) {
      console.error("Error dismissing:", err)
    } finally {
      setDismissingKey(null)
    }
  }

  const getTimeSinceCheck = () => {
    if (!healthData?.timestamp) return null
    const checkTime = new Date(healthData.timestamp)
    const now = new Date()
    const diffMs = now.getTime() - checkTime.getTime()
    const diffMin = Math.floor(diffMs / 60000)
    if (diffMin < 1) return "just now"
    if (diffMin === 1) return "1 minute ago"
    if (diffMin < 60) return `${diffMin} minutes ago`
    const diffHours = Math.floor(diffMin / 60)
    return `${diffHours}h ${diffMin % 60}m ago`
  }

  const getCategoryRowStyle = (status: string) => {
    const s = status?.toUpperCase()
    if (s === "CRITICAL") return "bg-red-500/5 border-red-500/20 hover:bg-red-500/10 cursor-pointer"
    if (s === "WARNING") return "bg-yellow-500/5 border-yellow-500/20 hover:bg-yellow-500/10 cursor-pointer"
    if (s === "INFO") return "bg-blue-500/5 border-blue-500/20 hover:bg-blue-500/10"
    return "bg-card border-border hover:bg-muted/30"
  }

  const getOutlineBadgeStyle = (status: string) => {
    const s = status?.toUpperCase()
    if (s === "OK") return "border-green-500 text-green-500 bg-transparent"
    if (s === "INFO") return "border-blue-500 text-blue-500 bg-blue-500/5"
    if (s === "WARNING") return "border-yellow-500 text-yellow-500 bg-yellow-500/5"
    if (s === "CRITICAL") return "border-red-500 text-red-500 bg-red-500/5"
    return ""
  }

  const formatCheckLabel = (key: string): string => {
    const labels: Record<string, string> = {
      // CPU
      cpu_usage: "CPU Usage",
      cpu_temperature: "Temperature",
      // Memory
      ram_usage: "RAM Usage",
      swap_usage: "Swap Usage",
      // Disk I/O
      root_filesystem: "Root Filesystem",
      smart_health: "SMART Health",
      io_errors: "I/O Errors",
      zfs_pools: "ZFS Pools",
      lvm_volumes: "LVM Volumes",
      lvm_check: "LVM Status",
      // Network
      connectivity: "Connectivity",
      // VMs & CTs
      qmp_communication: "QMP Communication",
      container_startup: "Container Startup",
      vm_startup: "VM Startup",
      oom_killer: "OOM Killer",
      // Services
      cluster_mode: "Cluster Mode",
      // Logs (prefixed with log_)
      log_error_cascade: "Error Cascade",
      log_error_spike: "Error Spike",
      log_persistent_errors: "Persistent Errors",
      log_critical_errors: "Critical Errors",
      // Updates
      security_updates: "Security Updates",
      system_age: "System Age",
      pending_updates: "Pending Updates",
      kernel_pve: "Kernel / PVE",
      // Security
      uptime: "Uptime",
      certificates: "Certificates",
      login_attempts: "Login Attempts",
      fail2ban: "Fail2Ban",
      // Storage (Proxmox)
      proxmox_storages: "Proxmox Storages",
    }
    if (labels[key]) return labels[key]
    // Convert snake_case or camelCase to Title Case
    return key
      .replace(/_/g, " ")
      .replace(/([a-z])([A-Z])/g, "$1 $2")
      .replace(/\b\w/g, (c) => c.toUpperCase())
  }

  const renderChecks = (
    checks: Record<string, { status: string; detail: string; dismissable?: boolean; [key: string]: any }>,
    categoryKey: string
  ) => {
    if (!checks || Object.keys(checks).length === 0) return null

    return (
      <div className="mt-2 space-y-0.5">
        {Object.entries(checks).map(([checkKey, checkData]) => {
          const isDismissable = checkData.dismissable === true
          const checkStatus = checkData.status?.toUpperCase() || "OK"

          return (
            <div
              key={checkKey}
              className="flex items-center justify-between gap-2 text-xs py-1.5 px-3 rounded-md hover:bg-muted/40 transition-colors"
            >
              <div className="flex items-center gap-2 min-w-0 flex-1 overflow-hidden">
                {getStatusIcon(checkData.status, "sm")}
                <span className="font-medium shrink-0">{formatCheckLabel(checkKey)}</span>
                <span className="text-muted-foreground truncate block">{checkData.detail}</span>
                {checkData.dismissed && (
                  <Badge variant="outline" className="text-[9px] px-1.5 py-0 h-4 shrink-0 text-blue-400 border-blue-400/30">
                    Dismissed
                  </Badge>
                )}
              </div>
              <div className="flex items-center gap-1.5 shrink-0">
                {(checkStatus === "WARNING" || checkStatus === "CRITICAL") && isDismissable && !checkData.dismissed && (
                  <Button
                    size="sm"
                    variant="outline"
                    className="h-5 px-1.5 shrink-0 hover:bg-red-500/10 hover:border-red-500/50 bg-transparent text-[10px]"
                    disabled={dismissingKey === checkKey}
                    onClick={(e) => {
                      e.stopPropagation()
                      handleAcknowledge(checkKey, e)
                    }}
                  >
                    {dismissingKey === checkKey ? (
                      <Loader2 className="h-3 w-3 animate-spin" />
                    ) : (
                      <>
                        <X className="h-3 w-3 mr-0.5" />
                        Dismiss
                      </>
                    )}
                  </Button>
                )}
              </div>
            </div>
          )
        })}
      </div>
    )
  }



  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-3xl w-[95vw] max-h-[85vh] overflow-y-auto overflow-x-hidden">
        <DialogHeader>
          <div className="flex items-center justify-between gap-3">
            <DialogTitle className="flex items-center gap-2 flex-1">
              <Activity className="h-6 w-6" />
              System Health Status
              {healthData && <div className="ml-2">{getStatusBadge(healthData.overall)}</div>}
            </DialogTitle>
          </div>
          <DialogDescription className="flex items-center gap-2">
            Detailed health checks for all system components
            {getTimeSinceCheck() && (
              <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
                <Clock className="h-3 w-3" />
                Last check: {getTimeSinceCheck()}
              </span>
            )}
          </DialogDescription>
        </DialogHeader>

        {loading && (
          <div className="flex items-center justify-center py-8">
            <Loader2 className="h-8 w-8 animate-spin text-primary" />
          </div>
        )}

        {error && (
          <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-red-800 dark:bg-red-950 dark:border-red-800 dark:text-red-200">
            <p className="font-medium">Error loading health status</p>
            <p className="text-sm">{error}</p>
          </div>
        )}

        {healthData && !loading && (
          <div className="space-y-4">
            {/* Overall Stats Summary */}
            <div className={`grid gap-3 p-4 rounded-lg bg-muted/30 border ${stats.info > 0 ? "grid-cols-5" : "grid-cols-4"}`}>
              <div className="text-center">
                <div className="text-2xl font-bold">{stats.total}</div>
                <div className="text-xs text-muted-foreground">Total</div>
              </div>
              <div className="text-center">
                <div className="text-2xl font-bold text-green-500">{stats.healthy}</div>
                <div className="text-xs text-muted-foreground">Healthy</div>
              </div>
              {stats.info > 0 && (
                <div className="text-center">
                  <div className="text-2xl font-bold text-blue-500">{stats.info}</div>
                  <div className="text-xs text-muted-foreground">Info</div>
                </div>
              )}
              <div className="text-center">
                <div className="text-2xl font-bold text-yellow-500">{stats.warnings}</div>
                <div className="text-xs text-muted-foreground">Warnings</div>
              </div>
              <div className="text-center">
                <div className="text-2xl font-bold text-red-500">{stats.critical}</div>
                <div className="text-xs text-muted-foreground">Critical</div>
              </div>
            </div>

            {healthData.summary && healthData.summary !== "All systems operational" && (
              <div className="text-sm p-3 rounded-lg bg-muted/20 border overflow-hidden max-w-full">
                <p className="font-medium text-foreground truncate" title={healthData.summary}>{healthData.summary}</p>
              </div>
            )}

            {/* Category List */}
            <div className="space-y-2">
              {CATEGORIES.map(({ key, label, Icon }) => {
                const categoryData = healthData.details[key as keyof typeof healthData.details]
                const status = categoryData?.status || "UNKNOWN"
                const reason = categoryData?.reason
                const checks = categoryData?.checks
                const isExpanded = expandedCategories.has(key)
                const hasChecks = checks && Object.keys(checks).length > 0

                return (
                  <div
                    key={key}
                    className={`rounded-lg border transition-colors overflow-hidden ${getCategoryRowStyle(status)}`}
                  >
                    {/* Clickable header row */}
                    <div
                      className="flex items-center gap-3 p-3 cursor-pointer select-none overflow-hidden"
                      onClick={() => toggleCategory(key)}
                    >
                      <div className="shrink-0 flex items-center gap-2">
                        <Icon className="h-4 w-4 text-blue-500" />
                        {getStatusIcon(status)}
                      </div>
                      <div className="flex-1 min-w-0 overflow-hidden">
                        <div className="flex items-center gap-2">
                          <p className="font-medium text-sm truncate">{label}</p>
                          {hasChecks && (
                            <span className="text-[10px] text-muted-foreground shrink-0">
                              ({Object.keys(checks).length} checks)
                            </span>
                          )}
                        </div>
                        {reason && !isExpanded && (
                          <p className="text-xs text-muted-foreground mt-0.5 truncate" title={reason}>{reason}</p>
                        )}
                      </div>
                      <div className="flex items-center gap-2 shrink-0">
                        <Badge variant="outline" className={`text-xs ${getOutlineBadgeStyle(status)}`}>
                          {status}
                        </Badge>
                        <ChevronRight
                          className={`h-4 w-4 text-muted-foreground transition-transform duration-200 ${
                            isExpanded ? "rotate-90" : ""
                          }`}
                        />
                      </div>
                    </div>

                    {/* Expandable checks section */}
                    {isExpanded && (
                      <div className="border-t border-border/50 bg-muted/5 px-2 py-1.5 overflow-hidden">
                        {reason && (
                          <p className="text-xs text-muted-foreground px-3 py-1.5 mb-1 break-words">{reason}</p>
                        )}
                        {hasChecks ? (
                          renderChecks(checks, key)
                        ) : (
                          <div className="flex items-center gap-2 text-xs text-muted-foreground px-3 py-2">
                            <CheckCircle2 className="h-3.5 w-3.5 text-green-500" />
                            No issues detected
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                )
              })}
            </div>

            {/* Dismissed Items Section */}
            {dismissedItems.length > 0 && (
              <div className="space-y-2">
                <div className="flex items-center gap-2 text-sm font-medium text-muted-foreground pt-2">
                  <BellOff className="h-4 w-4" />
                  Dismissed Items ({dismissedItems.length})
                </div>
                {dismissedItems.map((item) => (
                  <div
                    key={item.error_key}
                    className="flex items-start gap-3 p-3 rounded-lg border bg-muted/10 border-muted opacity-75"
                  >
                    <div className="mt-0.5 flex-shrink-0 flex items-center gap-2">
                      <BellOff className="h-4 w-4 text-muted-foreground" />
                      {getStatusIcon("INFO")}
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center justify-between gap-2 mb-1">
                        <p className="font-medium text-sm text-muted-foreground truncate">{item.reason}</p>
                        <div className="flex items-center gap-1.5 shrink-0">
                          <Badge variant="outline" className="text-xs border-blue-500/50 text-blue-500/70 bg-transparent">
                            Dismissed
                          </Badge>
                          <Badge variant="outline" className={`text-xs ${getOutlineBadgeStyle(item.severity)}`}>
                            was {item.severity}
                          </Badge>
                        </div>
                      </div>
                      <p className="text-xs text-muted-foreground flex items-center gap-1">
                        <Clock className="h-3 w-3" />
                        Suppressed for {item.suppression_remaining_hours < 24
                          ? `${Math.round(item.suppression_remaining_hours)}h`
                          : `${Math.round(item.suppression_remaining_hours / 24)} days`
                        } more
                      </p>
                    </div>
                  </div>
                ))}
              </div>
            )}

            {healthData.timestamp && (
              <div className="text-xs text-muted-foreground text-center pt-2">
                Last updated: {new Date(healthData.timestamp).toLocaleString()}
              </div>
            )}
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}
