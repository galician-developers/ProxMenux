"use client"

import { useState, useEffect } from "react"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "./ui/card"
import { Wrench, Package, Ruler, HeartPulse, Cpu, MemoryStick, HardDrive, CircleDot, Network, Server, Settings2, FileText, RefreshCw, Shield, AlertTriangle, Info, Loader2, Check } from "lucide-react"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "./ui/select"
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

export function Settings() {
  const [proxmenuxTools, setProxmenuxTools] = useState<ProxMenuxTool[]>([])
  const [loadingTools, setLoadingTools] = useState(true)
  const [networkUnitSettings, setNetworkUnitSettings] = useState<"Bytes" | "Bits">("Bytes")
  const [loadingUnitSettings, setLoadingUnitSettings] = useState(true)
  
  // Health Monitor suppression settings
  const [suppressionCategories, setSuppressionCategories] = useState<SuppressionCategory[]>([])
  const [loadingHealth, setLoadingHealth] = useState(true)
  const [savingHealth, setSavingHealth] = useState<string | null>(null)
  const [savedHealth, setSavedHealth] = useState<string | null>(null)
  const [customValues, setCustomValues] = useState<Record<string, string>>({})

  useEffect(() => {
    loadProxmenuxTools()
    getUnitsSettings()
    loadHealthSettings()
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

  const getSelectValue = (hours: number, key: string): string => {
    if (hours === -1) return "-1"
    const preset = SUPPRESSION_OPTIONS.find(o => o.value === String(hours))
    if (preset && preset.value !== "custom") return String(hours)
    return "custom"
  }

  const handleSuppressionChange = async (settingKey: string, value: string) => {
    if (value === "custom") {
      // Show custom input -- don't save yet
      const current = suppressionCategories.find(c => c.key === settingKey)
      setCustomValues(prev => ({ ...prev, [settingKey]: String(current?.hours || 48) }))
      // Temporarily mark as custom in state
      setSuppressionCategories(prev =>
        prev.map(c => c.key === settingKey ? { ...c, hours: -2 } : c)
      )
      return
    }

    const hours = parseInt(value, 10)
    if (isNaN(hours)) return
    
    await saveSuppression(settingKey, hours)
  }

  const handleCustomSave = async (settingKey: string) => {
    const raw = customValues[settingKey]
    const hours = parseInt(raw, 10)
    if (isNaN(hours) || hours < 1) return
    await saveSuppression(settingKey, hours)
  }

  const saveSuppression = async (settingKey: string, hours: number) => {
    setSavingHealth(settingKey)
    try {
      await fetchApi("/api/health/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ [settingKey]: String(hours) }),
      })
      
      setSuppressionCategories(prev =>
        prev.map(c => c.key === settingKey ? { ...c, hours } : c)
      )
      // Remove from custom values
      setCustomValues(prev => {
        const next = { ...prev }
        delete next[settingKey]
        return next
      })
      setSavedHealth(settingKey)
      setTimeout(() => setSavedHealth(null), 2000)
    } catch (err) {
      console.error("Failed to save health setting:", err)
    } finally {
      setSavingHealth(null)
    }
  }

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
          <div className="flex items-center gap-2">
            <HeartPulse className="h-5 w-5 text-red-500" />
            <CardTitle>Health Monitor</CardTitle>
          </div>
          <CardDescription>
            Configure how long dismissed alerts stay suppressed for each category.
            When you dismiss a warning, it will not reappear until the suppression period expires.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {loadingHealth ? (
            <div className="flex items-center justify-center py-8">
              <div className="animate-spin h-8 w-8 border-4 border-red-500 border-t-transparent rounded-full" />
            </div>
          ) : (
            <div className="space-y-1">
              {/* Header */}
              <div className="flex items-center justify-between mb-3 pb-2 border-b border-border">
                <span className="text-sm font-medium text-muted-foreground">Category</span>
                <span className="text-sm font-medium text-muted-foreground">Suppression Duration</span>
              </div>
              
              {/* Per-category rows */}
              {suppressionCategories.map((cat) => {
                const IconComp = CATEGORY_ICONS[cat.icon] || HeartPulse
                const isCustomMode = cat.hours === -2 || (cat.key in customValues)
                const isPermanent = cat.hours === -1
                const isLong = cat.hours >= 720 && cat.hours !== -1
                const selectVal = isCustomMode ? "custom" : getSelectValue(cat.hours, cat.key)
                
                return (
                  <div key={cat.key} className="space-y-0">
                    <div className="flex items-center justify-between gap-3 py-2.5 px-2 rounded-lg hover:bg-muted/30 transition-colors">
                      <div className="flex items-center gap-2.5 min-w-0">
                        <IconComp className="h-4 w-4 text-muted-foreground shrink-0" />
                        <span className="text-sm font-medium truncate">{cat.label}</span>
                        {savingHealth === cat.key && (
                          <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground shrink-0" />
                        )}
                        {savedHealth === cat.key && (
                          <Check className="h-3.5 w-3.5 text-green-500 shrink-0" />
                        )}
                      </div>
                      <div className="flex items-center gap-2 shrink-0">
                        {isCustomMode ? (
                          <div className="flex items-center gap-1.5">
                            <Input
                              type="number"
                              min={1}
                              className="w-20 h-8 text-xs"
                              value={customValues[cat.key] || ""}
                              onChange={(e) => setCustomValues(prev => ({ ...prev, [cat.key]: e.target.value }))}
                              placeholder="Hours"
                            />
                            <span className="text-xs text-muted-foreground">h</span>
                            <button
                              className="h-8 px-2 text-xs rounded-md border border-border bg-background hover:bg-muted transition-colors"
                              onClick={() => handleCustomSave(cat.key)}
                              disabled={savingHealth === cat.key}
                            >
                              Save
                            </button>
                            <button
                              className="h-8 px-2 text-xs rounded-md border border-border bg-background hover:bg-muted transition-colors text-muted-foreground"
                              onClick={() => {
                                setCustomValues(prev => {
                                  const next = { ...prev }
                                  delete next[cat.key]
                                  return next
                                })
                                loadHealthSettings()
                              }}
                            >
                              Cancel
                            </button>
                          </div>
                        ) : (
                          <Select value={selectVal} onValueChange={(v) => handleSuppressionChange(cat.key, v)}>
                            <SelectTrigger className="w-32 h-8 text-xs">
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
                    
                    {/* Warning for Permanent */}
                    {isPermanent && (
                      <div className="flex items-start gap-2 ml-8 mr-2 mb-2 p-2.5 rounded-md bg-amber-500/10 border border-amber-500/20">
                        <AlertTriangle className="h-4 w-4 text-amber-500 shrink-0 mt-0.5" />
                        <p className="text-xs text-amber-400/90 leading-relaxed">
                          Dismissed alerts for <span className="font-semibold">{cat.label}</span> will never reappear.
                          {cat.category === "temperature" && (
                            <span className="block mt-1 text-amber-300 font-medium">
                              Note: Critical CPU temperature alerts will still trigger for hardware safety.
                            </span>
                          )}
                        </p>
                      </div>
                    )}
                    
                    {/* Warning for long custom duration (> 1 month) */}
                    {isLong && !isPermanent && (
                      <div className="flex items-start gap-2 ml-8 mr-2 mb-2 p-2.5 rounded-md bg-amber-500/10 border border-amber-500/20">
                        <Info className="h-4 w-4 text-amber-500 shrink-0 mt-0.5" />
                        <p className="text-xs text-amber-400/90 leading-relaxed">
                          Long suppression period. Dismissed alerts for this category will not reappear for an extended time.
                        </p>
                      </div>
                    )}
                  </div>
                )
              })}
              
              {/* Info footer */}
              <div className="flex items-start gap-2 mt-4 pt-3 border-t border-border">
                <Info className="h-4 w-4 text-blue-400 shrink-0 mt-0.5" />
                <p className="text-xs text-muted-foreground leading-relaxed">
                  These settings apply when you dismiss a warning from the Health Monitor. 
                  Critical CPU temperature alerts always trigger regardless of settings to protect your hardware.
                </p>
              </div>
            </div>
          )}
        </CardContent>
      </Card>

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
