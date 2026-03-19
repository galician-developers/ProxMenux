"use client"

import { useState, useEffect } from "react"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "./ui/card"
import { Wrench, Package, Ruler, HeartPulse, Cpu, MemoryStick, HardDrive, CircleDot, Network, Server, Settings2, FileText, RefreshCw, Shield, AlertTriangle, Info, Loader2, Check, Database, CloudOff } from "lucide-react"
import { NotificationSettings } from "./notification-settings"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "./ui/select"
import { Switch } from "./ui/switch"
import { Input } from "./ui/input"
import { Badge } from "./ui/badge"
import { getNetworkUnit } from "../lib/format-network"
import { fetchApi } from "../lib/api-config"

interface SuppressionCategory {
  key: string
  label: string
  category: string
  icon: string
  hours: number
}

const SUPPRESSION_OPTIONS = [
  { value: "24", label: "24 hours" },
  { value: "72", label: "3 days" },
  { value: "168", label: "1 week" },
  { value: "720", label: "1 month" },
  { value: "8760", label: "1 year" },
  { value: "custom", label: "Custom" },
  { value: "-1", label: "Permanent" },
]

const CATEGORY_ICONS: Record<string, React.ElementType> = {
  cpu: Cpu,
  memory: MemoryStick,
  storage: HardDrive,
  disk: CircleDot,
  network: Network,
  vms: Server,
  services: Settings2,
  logs: FileText,
  updates: RefreshCw,
  security: Shield,
}

interface ProxMenuxTool {
  key: string
  name: string
  enabled: boolean
}

interface RemoteStorage {
  name: string
  type: string
  status: string
  total: number
  used: number
  available: number
  percent: number
  exclude_health: boolean
  exclude_notifications: boolean
  excluded_at?: string
  reason?: string
}

export function Settings() {
  const [proxmenuxTools, setProxmenuxTools] = useState<ProxMenuxTool[]>([])
  const [loadingTools, setLoadingTools] = useState(true)
  const [networkUnitSettings, setNetworkUnitSettings] = useState<"Bytes" | "Bits">("Bytes")
  const [loadingUnitSettings, setLoadingUnitSettings] = useState(true)
  
  // Health Monitor suppression settings
  const [suppressionCategories, setSuppressionCategories] = useState<SuppressionCategory[]>([])
  const [loadingHealth, setLoadingHealth] = useState(true)
  const [healthEditMode, setHealthEditMode] = useState(false)
  const [savingAllHealth, setSavingAllHealth] = useState(false)
  const [savedAllHealth, setSavedAllHealth] = useState(false)
  const [pendingChanges, setPendingChanges] = useState<Record<string, number>>({})
  const [customValues, setCustomValues] = useState<Record<string, string>>({})
  
  // Remote Storage Exclusions
  const [remoteStorages, setRemoteStorages] = useState<RemoteStorage[]>([])
  const [loadingStorages, setLoadingStorages] = useState(true)
  const [savingStorage, setSavingStorage] = useState<string | null>(null)

  useEffect(() => {
    loadProxmenuxTools()
    getUnitsSettings()
    loadHealthSettings()
    loadRemoteStorages()
  }, [])

  const loadProxmenuxTools = async () => {
    try {
      const data = await fetchApi("/api/proxmenux/installed-tools")
      if (data.success) {
        setProxmenuxTools(data.installed_tools || [])
      }
    } catch (err) {
      console.error("Failed to load ProxMenux tools:", err)
    } finally {
      setLoadingTools(false)
    }
  }

  const changeNetworkUnit = (unit: string) => {
    const networkUnit = unit as "Bytes" | "Bits"
    localStorage.setItem("proxmenux-network-unit", networkUnit)
    setNetworkUnitSettings(networkUnit)

    window.dispatchEvent(new CustomEvent("networkUnitChanged", { detail: networkUnit }))

    window.dispatchEvent(new StorageEvent("storage", {
      key: "proxmenux-network-unit",
      newValue: networkUnit,
      url: window.location.href
    }))
  }

  const getUnitsSettings = () => {
    const networkUnit = getNetworkUnit()
    setNetworkUnitSettings(networkUnit)
    setLoadingUnitSettings(false)
  }

  const loadHealthSettings = async () => {
    try {
      const data = await fetchApi("/api/health/settings")
      if (data.categories) {
        setSuppressionCategories(data.categories)
      }
    } catch (err) {
      console.error("Failed to load health settings:", err)
    } finally {
      setLoadingHealth(false)
    }
  }

  const loadRemoteStorages = async () => {
    try {
      const data = await fetchApi("/api/health/remote-storages")
      if (data.storages) {
        setRemoteStorages(data.storages)
      }
    } catch (err) {
      console.error("Failed to load remote storages:", err)
    } finally {
      setLoadingStorages(false)
    }
  }

  const handleStorageExclusionChange = async (storageName: string, storageType: string, excludeHealth: boolean, excludeNotifications: boolean) => {
    setSavingStorage(storageName)
    try {
      // If both are false, remove the exclusion
      if (!excludeHealth && !excludeNotifications) {
        await fetchApi(`/api/health/storage-exclusions/${encodeURIComponent(storageName)}`, {
          method: "DELETE"
        })
      } else {
        await fetchApi("/api/health/storage-exclusions", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            storage_name: storageName,
            storage_type: storageType,
            exclude_health: excludeHealth,
            exclude_notifications: excludeNotifications
          })
        })
      }
      
      // Update local state
      setRemoteStorages(prev => prev.map(s => 
        s.name === storageName 
          ? { ...s, exclude_health: excludeHealth, exclude_notifications: excludeNotifications }
          : s
      ))
    } catch (err) {
      console.error("Failed to update storage exclusion:", err)
    } finally {
      setSavingStorage(null)
    }
  }

  const getSelectValue = (hours: number, key: string): string => {
    if (hours === -1) return "-1"
    const preset = SUPPRESSION_OPTIONS.find(o => o.value === String(hours))
    if (preset && preset.value !== "custom") return String(hours)
    return "custom"
  }

  const getEffectiveHours = (cat: SuppressionCategory): number => {
    if (cat.key in pendingChanges) return pendingChanges[cat.key]
    return cat.hours
  }

  const handleSuppressionChange = (settingKey: string, value: string) => {
    if (value === "custom") {
      const current = suppressionCategories.find(c => c.key === settingKey)
      const effectiveHours = current ? getEffectiveHours(current) : 48
      setCustomValues(prev => ({ ...prev, [settingKey]: String(effectiveHours > 0 ? effectiveHours : 48) }))
      // Mark as custom mode in pending
      setPendingChanges(prev => ({ ...prev, [settingKey]: -2 }))
      return
    }

    const hours = parseInt(value, 10)
    if (isNaN(hours)) return
    setPendingChanges(prev => ({ ...prev, [settingKey]: hours }))
    // Clear custom input if switching away
    setCustomValues(prev => {
      const next = { ...prev }
      delete next[settingKey]
      return next
    })
  }

  const handleCustomConfirm = (settingKey: string) => {
    const raw = customValues[settingKey]
    const hours = parseInt(raw, 10)
    if (isNaN(hours) || hours < 1) return
    setPendingChanges(prev => ({ ...prev, [settingKey]: hours }))
    setCustomValues(prev => {
      const next = { ...prev }
      delete next[settingKey]
      return next
    })
  }

  const handleCancelEdit = () => {
    setHealthEditMode(false)
    setPendingChanges({})
    setCustomValues({})
  }

  const handleSaveAllHealth = async () => {
    // Merge pending changes into a payload: only changed categories
    const payload: Record<string, string> = {}
    for (const cat of suppressionCategories) {
      if (cat.key in pendingChanges && pendingChanges[cat.key] !== -2) {
        payload[cat.key] = String(pendingChanges[cat.key])
      }
    }

    if (Object.keys(payload).length === 0) {
      setHealthEditMode(false)
      setPendingChanges({})
      return
    }

    setSavingAllHealth(true)
    try {
      await fetchApi("/api/health/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      })
      
      // Update local state with saved values
      setSuppressionCategories(prev =>
        prev.map(c => {
          if (c.key in pendingChanges && pendingChanges[c.key] !== -2) {
            return { ...c, hours: pendingChanges[c.key] }
          }
          return c
        })
      )
      setPendingChanges({})
      setCustomValues({})
      setHealthEditMode(false)
      setSavedAllHealth(true)
      setTimeout(() => setSavedAllHealth(false), 3000)
    } catch (err) {
      console.error("Failed to save health settings:", err)
    } finally {
      setSavingAllHealth(false)
    }
  }

  const hasPendingChanges = Object.keys(pendingChanges).some(
    k => pendingChanges[k] !== -2
  )

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold">Settings</h1>
        <p className="text-muted-foreground mt-2">Manage your dashboard preferences</p>
      </div>

      {/* Network Units Settings */}
      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <Ruler className="h-5 w-5 text-green-500" />
            <CardTitle>Network Units</CardTitle>
          </div>
          <CardDescription>Change how network traffic is displayed</CardDescription>
        </CardHeader>
        <CardContent>
          {loadingUnitSettings ? (
            <div className="flex items-center justify-center py-8">
              <div className="animate-spin h-8 w-8 border-4 border-green-500 border-t-transparent rounded-full" />
            </div>
          ) : (
            <div className="text-foreground flex items-center justify-between">
              <div className="flex items-center">Network Unit Display</div>
              <Select value={networkUnitSettings} onValueChange={changeNetworkUnit}>
                <SelectTrigger className="w-28 h-8 text-xs">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="Bytes">Bytes</SelectItem>
                  <SelectItem value="Bits">Bits</SelectItem>
                </SelectContent>
              </Select>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Health Monitor Settings */}
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <HeartPulse className="h-5 w-5 text-red-500" />
              <CardTitle>Health Monitor</CardTitle>
            </div>
            {!loadingHealth && (
              <div className="flex items-center gap-2">
                {savedAllHealth && (
                  <span className="flex items-center gap-1 text-xs text-green-500">
                    <Check className="h-3.5 w-3.5" />
                    Saved
                  </span>
                )}
                {healthEditMode ? (
                  <>
                    <button
                      className="h-7 px-3 text-xs rounded-md border border-border bg-background hover:bg-muted transition-colors text-muted-foreground"
                      onClick={handleCancelEdit}
                      disabled={savingAllHealth}
                    >
                      Cancel
                    </button>
                    <button
                      className="h-7 px-3 text-xs rounded-md bg-blue-600 hover:bg-blue-700 text-white transition-colors disabled:opacity-50 flex items-center gap-1.5"
                      onClick={handleSaveAllHealth}
                      disabled={savingAllHealth || !hasPendingChanges}
                    >
                      {savingAllHealth ? (
                        <Loader2 className="h-3 w-3 animate-spin" />
                      ) : (
                        <Check className="h-3 w-3" />
                      )}
                      Save
                    </button>
                  </>
                ) : (
                  <button
                    className="h-7 px-3 text-xs rounded-md border border-border bg-background hover:bg-muted transition-colors flex items-center gap-1.5"
                    onClick={() => setHealthEditMode(true)}
                  >
                    <Settings2 className="h-3 w-3" />
                    Edit
                  </button>
                )}
              </div>
            )}
          </div>
          <CardDescription>
            Configure how long dismissed alerts stay suppressed for each category.
            Changes apply immediately to both existing and future dismissed alerts.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {loadingHealth ? (
            <div className="flex items-center justify-center py-8">
              <div className="animate-spin h-8 w-8 border-4 border-red-500 border-t-transparent rounded-full" />
            </div>
          ) : (
            <div className="space-y-0">
              {/* Header */}
              <div className="flex items-center justify-between pb-2 mb-1 border-b border-border">
                <span className="text-xs font-medium text-muted-foreground">Category</span>
                <span className="text-xs font-medium text-muted-foreground">Suppression Duration</span>
              </div>
              
              {/* Per-category rows */}
              <div className="divide-y divide-border/50">
                {suppressionCategories.map((cat) => {
                  const IconComp = CATEGORY_ICONS[cat.icon] || HeartPulse
                  const effectiveHours = getEffectiveHours(cat)
                  const isCustomMode = effectiveHours === -2 || (cat.key in customValues)
                  const isPermanent = effectiveHours === -1
                  const isLong = effectiveHours >= 720 && effectiveHours !== -1 && effectiveHours !== -2
                  const hasChanged = cat.key in pendingChanges && pendingChanges[cat.key] !== cat.hours
                  const selectVal = isCustomMode ? "custom" : getSelectValue(effectiveHours, cat.key)
                  
                  return (
                    <div key={cat.key}>
                      <div className="flex items-center justify-between gap-2 py-2 sm:py-2.5 px-1 sm:px-2">
                        <div className="flex items-center gap-2 min-w-0 flex-1">
                          <IconComp className="h-4 w-4 text-muted-foreground shrink-0" />
                          <span className="text-xs sm:text-sm font-medium">{cat.label}</span>
                          {hasChanged && healthEditMode && (
                            <span className="h-1.5 w-1.5 rounded-full bg-blue-500 shrink-0" />
                          )}
                        </div>
                        <div className="shrink-0">
                          {isCustomMode && healthEditMode ? (
                            <div className="flex items-center gap-1.5">
                              <Input
                                type="number"
                                min={1}
                                className="w-16 sm:w-20 h-7 text-xs"
                                value={customValues[cat.key] || ""}
                                onChange={(e) => setCustomValues(prev => ({ ...prev, [cat.key]: e.target.value }))}
                                placeholder="Hours"
                              />
                              <span className="text-xs text-muted-foreground">h</span>
                              <button
                                className="h-7 px-2 text-xs rounded-md border border-border bg-background hover:bg-muted transition-colors"
                                onClick={() => handleCustomConfirm(cat.key)}
                              >
                                OK
                              </button>
                              <button
                                className="h-7 px-1.5 text-xs rounded-md text-muted-foreground hover:text-foreground transition-colors"
                                onClick={() => {
                                  setCustomValues(prev => {
                                    const next = { ...prev }
                                    delete next[cat.key]
                                    return next
                                  })
                                  setPendingChanges(prev => {
                                    const next = { ...prev }
                                    delete next[cat.key]
                                    return next
                                  })
                                }}
                              >
                                X
                              </button>
                            </div>
                          ) : (
                            <Select
                              value={selectVal}
                              onValueChange={(v) => handleSuppressionChange(cat.key, v)}
                              disabled={!healthEditMode}
                            >
                              <SelectTrigger className={`w-28 sm:w-32 h-7 text-xs ${!healthEditMode ? "opacity-60" : ""}`}>
                                <SelectValue />
                              </SelectTrigger>
                              <SelectContent>
                                {SUPPRESSION_OPTIONS.map((opt) => (
                                  <SelectItem key={opt.value} value={opt.value}>
                                    {opt.label}
                                  </SelectItem>
                                ))}
                              </SelectContent>
                            </Select>
                          )}
                        </div>
                      </div>
                      
                      {/* Notice for Permanent */}
                      {isPermanent && healthEditMode && (
                        <div className="flex items-start gap-2 ml-6 sm:ml-8 mr-1 mb-2 p-2 rounded-md bg-blue-500/10 border border-blue-500/20">
                          <Info className="h-3.5 w-3.5 text-blue-400 shrink-0 mt-0.5" />
                          <p className="text-[11px] text-blue-400/90 leading-relaxed">
                            Alerts for <span className="font-semibold">{cat.label}</span> will be permanently suppressed when dismissed.
                            {cat.category === "temperature" && (
                              <span className="block mt-0.5 text-blue-300/80">
                                Critical CPU temperature alerts will still trigger for hardware safety.
                              </span>
                            )}
                          </p>
                        </div>
                      )}
                      
                      {/* Notice for long duration (> 1 month) */}
                      {isLong && healthEditMode && (
                        <div className="flex items-start gap-2 ml-6 sm:ml-8 mr-1 mb-2 p-2 rounded-md bg-blue-500/10 border border-blue-500/20">
                          <Info className="h-3.5 w-3.5 text-blue-400 shrink-0 mt-0.5" />
                          <p className="text-[11px] text-blue-400/90 leading-relaxed">
                            Long suppression period. Dismissed alerts for this category will not reappear for an extended time.
                          </p>
                        </div>
                      )}
                    </div>
                  )
                })}
              </div>
              
              {/* Info footer */}
              <div className="flex items-start gap-2 mt-3 pt-3 border-t border-border">
                <Info className="h-3.5 w-3.5 text-blue-400 shrink-0 mt-0.5" />
                <p className="text-[11px] text-muted-foreground leading-relaxed">
                  These settings apply when you dismiss a warning from the Health Monitor. 
                  Critical CPU temperature alerts always trigger regardless of settings to protect your hardware.
                </p>
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Remote Storage Exclusions */}
      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <Database className="h-5 w-5 text-purple-500" />
            <CardTitle>Remote Storage Exclusions</CardTitle>
          </div>
          <CardDescription>
            Exclude remote storages (PBS, NFS, CIFS, etc.) from health monitoring and notifications.
            Use this for storages that are intentionally offline or have limited API access.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {loadingStorages ? (
            <div className="flex items-center justify-center py-8">
              <div className="animate-spin h-8 w-8 border-4 border-purple-500 border-t-transparent rounded-full" />
            </div>
          ) : remoteStorages.length === 0 ? (
            <div className="text-center py-8">
              <CloudOff className="h-12 w-12 text-muted-foreground mx-auto mb-3 opacity-50" />
              <p className="text-muted-foreground">No remote storages detected</p>
              <p className="text-sm text-muted-foreground mt-1">
                PBS, NFS, CIFS, and other remote storages will appear here when configured
              </p>
            </div>
          ) : (
            <div className="space-y-0">
              {/* Header */}
              <div className="grid grid-cols-[1fr_auto_auto] gap-4 pb-2 mb-1 border-b border-border">
                <span className="text-xs font-medium text-muted-foreground">Storage</span>
                <span className="text-xs font-medium text-muted-foreground text-center w-20">Health</span>
                <span className="text-xs font-medium text-muted-foreground text-center w-20">Alerts</span>
              </div>
              
              {/* Storage rows */}
              <div className="divide-y divide-border/50">
                {remoteStorages.map((storage) => {
                  const isExcluded = storage.exclude_health || storage.exclude_notifications
                  const isSaving = savingStorage === storage.name
                  const isOffline = storage.status === 'error' || storage.total === 0
                  
                  return (
                    <div key={storage.name} className="grid grid-cols-[1fr_auto_auto] gap-4 py-3 items-center">
                      <div className="flex items-center gap-3 min-w-0">
                        <div className={`w-2 h-2 rounded-full shrink-0 ${
                          isOffline ? 'bg-red-500' : 'bg-green-500'
                        }`} />
                        <div className="min-w-0">
                          <div className="flex items-center gap-2">
                            <span className="text-sm font-medium truncate">{storage.name}</span>
                            <Badge variant="outline" className="text-[10px] px-1.5 py-0 shrink-0">
                              {storage.type}
                            </Badge>
                          </div>
                          {isOffline && (
                            <p className="text-[11px] text-red-400 mt-0.5">Offline or unavailable</p>
                          )}
                        </div>
                      </div>
                      
                      <div className="flex items-center justify-center w-20">
                        {isSaving ? (
                          <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
                        ) : (
                          <Switch
                            checked={!storage.exclude_health}
                            onCheckedChange={(checked) => {
                              handleStorageExclusionChange(
                                storage.name,
                                storage.type,
                                !checked,
                                storage.exclude_notifications
                              )
                            }}
                          />
                        )}
                      </div>
                      
                      <div className="flex items-center justify-center w-20">
                        {isSaving ? (
                          <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
                        ) : (
                          <Switch
                            checked={!storage.exclude_notifications}
                            onCheckedChange={(checked) => {
                              handleStorageExclusionChange(
                                storage.name,
                                storage.type,
                                storage.exclude_health,
                                !checked
                              )
                            }}
                          />
                        )}
                      </div>
                    </div>
                  )
                })}
              </div>
              
              {/* Info footer */}
              <div className="flex items-start gap-2 mt-3 pt-3 border-t border-border">
                <Info className="h-3.5 w-3.5 text-purple-400 shrink-0 mt-0.5" />
                <p className="text-[11px] text-muted-foreground leading-relaxed">
                  <strong>Health:</strong> When OFF, the storage won't trigger warnings/critical alerts in the Health Monitor.
                  <br />
                  <strong>Alerts:</strong> When OFF, no notifications will be sent for this storage.
                </p>
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Notification Settings */}
      <NotificationSettings />

      {/* ProxMenux Optimizations */}
      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <Wrench className="h-5 w-5 text-orange-500" />
            <CardTitle>ProxMenux Optimizations</CardTitle>
          </div>
          <CardDescription>System optimizations and utilities installed via ProxMenux</CardDescription>
        </CardHeader>
        <CardContent>
          {loadingTools ? (
            <div className="flex items-center justify-center py-8">
              <div className="animate-spin h-8 w-8 border-4 border-orange-500 border-t-transparent rounded-full" />
            </div>
          ) : proxmenuxTools.length === 0 ? (
            <div className="text-center py-8">
              <Package className="h-12 w-12 text-muted-foreground mx-auto mb-3 opacity-50" />
              <p className="text-muted-foreground">No ProxMenux optimizations installed yet</p>
              <p className="text-sm text-muted-foreground mt-1">Run ProxMenux to configure system optimizations</p>
            </div>
          ) : (
            <div className="space-y-2">
              <div className="flex items-center justify-between mb-4 pb-2 border-b border-border">
                <span className="text-sm font-medium text-muted-foreground">Installed Tools</span>
                <span className="text-sm font-semibold text-orange-500">{proxmenuxTools.length} active</span>
              </div>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                {proxmenuxTools.map((tool) => (
                  <div
                    key={tool.key}
                    className="flex items-center gap-2 p-3 bg-muted/50 rounded-lg border border-border hover:bg-muted transition-colors"
                  >
                    <div className="w-2 h-2 rounded-full bg-green-500 flex-shrink-0" />
                    <span className="text-sm font-medium">{tool.name}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
