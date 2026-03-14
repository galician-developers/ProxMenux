"use client"

import { useState, useEffect } from "react"
import { Button } from "./ui/button"
import { Input } from "./ui/input"
import { Label } from "./ui/label"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "./ui/card"
import { Checkbox } from "./ui/checkbox"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "./ui/dialog"
import {
  ShieldCheck, Globe, ExternalLink, Loader2, CheckCircle, XCircle,
  Play, Square, RotateCw, Trash2, FileText, ChevronRight, ChevronDown,
  AlertTriangle, Info, Network, Eye, EyeOff, Settings, Wifi, Key,
} from "lucide-react"
import { fetchApi } from "../lib/api-config"

interface NetworkInfo {
  interface: string
  type?: string
  address?: string
  ip?: string
  subnet: string
  prefixlen?: number
  recommended?: boolean
}

interface AppStatus {
  state: "not_installed" | "running" | "stopped" | "error"
  health: string
  uptime_seconds: number
  last_check: string
}

interface ConfigSchema {
  [key: string]: {
    type: string
    label: string
    description: string
    placeholder?: string
    default?: any
    required?: boolean
    sensitive?: boolean
    env_var?: string
    help_url?: string
    help_text?: string
    options?: Array<{ value: string; label: string; description?: string }>
    depends_on?: { field: string; values: string[] }
    flag?: string
    warning?: string
    validation?: { pattern?: string; max_length?: number; message?: string }
  }
}

interface WizardStep {
  id: string
  title: string
  description: string
  fields?: string[]
}

export function SecureGatewaySetup() {
  // State
  const [loading, setLoading] = useState(true)
  const [runtimeAvailable, setRuntimeAvailable] = useState(false)
  const [runtimeInfo, setRuntimeInfo] = useState<{ runtime: string; version: string } | null>(null)
  const [appStatus, setAppStatus] = useState<AppStatus>({ state: "not_installed", health: "unknown", uptime_seconds: 0, last_check: "" })
  const [configSchema, setConfigSchema] = useState<ConfigSchema | null>(null)
  const [wizardSteps, setWizardSteps] = useState<WizardStep[]>([])
  const [networks, setNetworks] = useState<NetworkInfo[]>([])
  
  // Wizard state
  const [showWizard, setShowWizard] = useState(false)
  const [currentStep, setCurrentStep] = useState(0)
  const [config, setConfig] = useState<Record<string, any>>({})
  const [deploying, setDeploying] = useState(false)
  const [deployProgress, setDeployProgress] = useState("")
  const [deployError, setDeployError] = useState("")
  
  // Installed state
  const [actionLoading, setActionLoading] = useState<string | null>(null)
  const [showLogs, setShowLogs] = useState(false)
  const [logs, setLogs] = useState("")
  const [logsLoading, setLogsLoading] = useState(false)
  const [showRemoveConfirm, setShowRemoveConfirm] = useState(false)
  const [showAuthKey, setShowAuthKey] = useState(false)
  
  // Post-deploy confirmation
  const [showPostDeployInfo, setShowPostDeployInfo] = useState(false)
  const [deployedConfig, setDeployedConfig] = useState<Record<string, any>>({})
  
  // Host IP for "Host Only" mode
  const [hostIp, setHostIp] = useState("")
  
  // Update Auth Key
  const [showUpdateAuthKey, setShowUpdateAuthKey] = useState(false)
  const [newAuthKey, setNewAuthKey] = useState("")
  const [updateAuthKeyLoading, setUpdateAuthKeyLoading] = useState(false)
  const [updateAuthKeyError, setUpdateAuthKeyError] = useState("")
  
  // Password visibility
  const [visiblePasswords, setVisiblePasswords] = useState<Set<string>>(new Set())

  useEffect(() => {
    loadInitialData()
  }, [])

  const loadInitialData = async () => {
    setLoading(true)
    try {
      // Load runtime info (checks for Proxmox 9.1+ OCI support)
      const runtimeRes = await fetchApi("/api/oci/runtime")
      if (runtimeRes.success && runtimeRes.available) {
        setRuntimeAvailable(true)
        setRuntimeInfo({ runtime: runtimeRes.runtime, version: runtimeRes.version })
      } else {
        setRuntimeInfo({ runtime: "proxmox-lxc", version: runtimeRes.version || "unknown" })
      }

      // Load app definition
      const catalogRes = await fetchApi("/api/oci/catalog/secure-gateway")
      if (catalogRes.success && catalogRes.app) {
        setConfigSchema(catalogRes.app.config_schema || {})
        setWizardSteps(catalogRes.app.ui?.wizard_steps || [])
        
        // Set defaults
        const defaults: Record<string, any> = {}
        for (const [key, field] of Object.entries(catalogRes.app.config_schema || {})) {
          if (field.default !== undefined) {
            defaults[key] = field.default
          }
        }
        setConfig(defaults)
      }

      // Load status
      await loadStatus()

      // Load networks
      const networksRes = await fetchApi("/api/oci/networks")
      if (networksRes.success) {
        setNetworks(networksRes.networks || [])
        const primaryNetwork = networksRes.networks?.find((n: NetworkInfo) => n.recommended) || networksRes.networks?.[0]
        const hostIpValue = primaryNetwork?.ip || primaryNetwork?.address
        if (hostIpValue) {
          const ip = hostIpValue.split("/")[0]
          setHostIp(ip)
        }
      }
    } catch (err) {
      console.error("Failed to load data:", err)
    } finally {
      setLoading(false)
    }
  }

  const loadStatus = async () => {
    try {
      const statusRes = await fetchApi("/api/oci/status/secure-gateway")
      if (statusRes.success) {
        setAppStatus(statusRes.status)
      }
    } catch (err) {
      // Not installed is ok
    }
  }

  const handleDeploy = async () => {
    setDeploying(true)
    setDeployError("")
    setDeployProgress("Creating LXC container...")

    try {
      // Prepare config based on access_mode
      const deployConfig = { ...config }
      
      if (config.access_mode === "host_only" && hostIp) {
        deployConfig.advertise_routes = [`${hostIp}/32`]
      } else if (config.access_mode === "proxmox_network") {
        if (!deployConfig.advertise_routes?.length) {
          const recommendedNetwork = networks.find((n) => n.recommended) || networks[0]
          if (recommendedNetwork) {
            deployConfig.advertise_routes = [recommendedNetwork.subnet]
          }
        }
      }

      // Show progress messages while deploying
      const messages = [
        "Creating LXC container...",
        "Downloading Alpine Linux template...",
        "Configuring container...",
        "Installing Tailscale...",
        "Connecting to Tailscale network..."
      ]
      let msgIndex = 0
      const progressInterval = setInterval(() => {
        msgIndex = (msgIndex + 1) % messages.length
        if (msgIndex < messages.length - 1) {
          setDeployProgress(messages[msgIndex])
        }
      }, 2000)

      const result = await fetchApi("/api/oci/deploy", {
        method: "POST",
        body: JSON.stringify({
          app_id: "secure-gateway",
          config: deployConfig
        })
      })

      clearInterval(progressInterval)

      if (!result.success) {
        setDeployError(result.message || "Failed to deploy gateway")
        setDeploying(false)
        return
      }

      setDeployProgress("Gateway deployed successfully!")

      // Show post-deploy confirmation
      const needsApproval = deployConfig.access_mode && deployConfig.access_mode !== "none"
      if (needsApproval) {
        const finalConfig = { ...deployConfig }
        if (deployConfig.access_mode === "host_only" && hostIp) {
          finalConfig.advertise_routes = [`${hostIp}/32`]
        }
        setDeployedConfig(finalConfig)
        setShowPostDeployInfo(true)
      }

      await loadStatus()
      
      setTimeout(() => {
        setShowWizard(false)
        setDeploying(false)
        setDeployPercent(0)
        setCurrentStep(0)
      }, 1000)

    } catch (err: any) {
      setDeployError(err.message || "Failed to deploy gateway")
      setDeploying(false)
      setDeployPercent(0)
    }
  }

  const handleUpdateAuthKey = async () => {
    if (!newAuthKey.trim()) {
      setUpdateAuthKeyError("Auth Key is required")
      return
    }
    
    setUpdateAuthKeyLoading(true)
    setUpdateAuthKeyError("")
    
    try {
      const result = await fetchApi("/api/oci/installed/secure-gateway/update-auth-key", {
        method: "POST",
        body: JSON.stringify({
          auth_key: newAuthKey.trim()
        })
      })
      
      if (!result.success) {
        setUpdateAuthKeyError(result.message || "Failed to update auth key")
        setUpdateAuthKeyLoading(false)
        return
      }
      
      setShowUpdateAuthKey(false)
      setNewAuthKey("")
      await loadStatus()
    } catch (err: any) {
      setUpdateAuthKeyError(err.message || "Failed to update auth key")
    } finally {
      setUpdateAuthKeyLoading(false)
    }
  }

  const handleAction = async (action: "start" | "stop" | "restart") => {
    setActionLoading(action)
    try {
      await fetchApi(`/api/oci/installed/secure-gateway/${action}`, { method: "POST" })
      await loadStatus()
    } catch (err) {
      console.error(`Failed to ${action}:`, err)
    } finally {
      setActionLoading(null)
    }
  }

  const handleRemove = async () => {
    setActionLoading("remove")
    try {
      await fetchApi("/api/oci/installed/secure-gateway", { method: "DELETE" })
      setShowRemoveConfirm(false)
      await loadStatus()
    } catch (err) {
      console.error("Failed to remove:", err)
    } finally {
      setActionLoading(null)
    }
  }

  const handleViewLogs = async () => {
    setShowLogs(true)
    setLogsLoading(true)
    try {
      const result = await fetchApi("/api/oci/installed/secure-gateway/logs")
      setLogs(result.logs || "No logs available")
    } catch (err) {
      setLogs("Failed to load logs")
    } finally {
      setLogsLoading(false)
    }
  }

  const formatUptime = (seconds: number) => {
    if (seconds < 60) return `${seconds}s`
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m`
    if (seconds < 86400) return `${Math.floor(seconds / 3600)}h`
    return `${Math.floor(seconds / 86400)}d`
  }

  const togglePasswordVisibility = (fieldName: string) => {
    setVisiblePasswords(prev => {
      const next = new Set(prev)
      if (next.has(fieldName)) {
        next.delete(fieldName)
      } else {
        next.add(fieldName)
      }
      return next
    })
  }

  // Render field based on type
  const renderField = (fieldName: string, field: ConfigSchema[string]) => {
    // Check depends_on
    if (field.depends_on) {
      const dependsValue = config[field.depends_on.field]
      if (!field.depends_on.values.includes(dependsValue)) {
        return null
      }
    }

    switch (field.type) {
      case "password":
        const isVisible = visiblePasswords.has(fieldName)
        return (
          <div key={fieldName} className="space-y-2">
            <Label className="text-sm font-medium">
              {field.label}
              {field.required && <span className="text-red-500 ml-1">*</span>}
            </Label>
            <div className="relative">
              <Input
                type={isVisible ? "text" : "password"}
                value={config[fieldName] || ""}
                onChange={(e) => setConfig({ ...config, [fieldName]: e.target.value })}
                placeholder={field.placeholder}
                className="pr-10 font-mono text-sm"
              />
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="absolute right-0 top-0 h-full px-3 hover:bg-transparent"
                onClick={() => togglePasswordVisibility(fieldName)}
              >
                {isVisible ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
              </Button>
            </div>
            {field.description && (
              <p className="text-xs text-muted-foreground">{field.description}</p>
            )}
            {field.help_url && (
              <a
                href={field.help_url}
                target="_blank"
                rel="noopener noreferrer"
                className="text-xs text-cyan-500 hover:text-cyan-400 flex items-center gap-1"
              >
                {field.help_text || "Learn more"} <ExternalLink className="h-3 w-3" />
              </a>
            )}
          </div>
        )

      case "text":
        return (
          <div key={fieldName} className="space-y-2">
            <Label className="text-sm font-medium">
              {field.label}
              {field.required && <span className="text-red-500 ml-1">*</span>}
            </Label>
            <Input
              type="text"
              value={config[fieldName] || ""}
              onChange={(e) => setConfig({ ...config, [fieldName]: e.target.value })}
              placeholder={field.placeholder}
            />
            {field.description && (
              <p className="text-xs text-muted-foreground">{field.description}</p>
            )}
          </div>
        )

      case "select":
        const handleSelectChange = (value: string) => {
          const newConfig = { ...config, [fieldName]: value }
          
          if (fieldName === "access_mode" && value === "proxmox_network") {
            const recommendedNetwork = networks.find((n) => n.recommended) || networks[0]
            if (recommendedNetwork) {
              newConfig.advertise_routes = [recommendedNetwork.subnet]
            }
          }
          if (fieldName === "access_mode" && value === "host_only") {
            newConfig.advertise_routes = []
          }
          if (fieldName === "access_mode" && value === "custom") {
            newConfig.advertise_routes = []
          }
          
          setConfig(newConfig)
        }
        
        return (
          <div key={fieldName} className="space-y-3">
            <Label className="text-sm font-medium">
              {field.label}
              {field.required && <span className="text-red-500 ml-1">*</span>}
            </Label>
            <div className="space-y-2">
              {field.options?.map((opt) => (
                <div
                  key={opt.value}
                  onClick={() => handleSelectChange(opt.value)}
                  className={`p-3 rounded-lg border cursor-pointer transition-colors ${
                    config[fieldName] === opt.value
                      ? "border-cyan-500 bg-cyan-500/10"
                      : "border-border hover:border-muted-foreground/50"
                  }`}
                >
                  <div className="flex items-start gap-3">
                    <div className={`mt-0.5 w-4 h-4 rounded-full border-2 flex items-center justify-center ${
                      config[fieldName] === opt.value ? "border-cyan-500" : "border-muted-foreground"
                    }`}>
                      {config[fieldName] === opt.value && (
                        <div className="w-2 h-2 rounded-full bg-cyan-500" />
                      )}
                    </div>
                    <div className="flex-1">
                      <p className="font-medium text-sm">{opt.label}</p>
                      {opt.description && (
                        <p className="text-xs text-muted-foreground">{opt.description}</p>
                      )}
                      {fieldName === "access_mode" && opt.value === "proxmox_network" && config[fieldName] === "proxmox_network" && (
                        <p className="text-xs text-cyan-400 mt-1 flex items-center gap-1">
                          <Network className="h-3 w-3" />
                          {networks.find((n) => n.recommended)?.subnet || networks[0]?.subnet || "No network detected"}
                        </p>
                      )}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )

      case "boolean":
        return (
          <div
            key={fieldName}
            onClick={() => setConfig({ ...config, [fieldName]: !config[fieldName] })}
            className={`p-3 rounded-lg border cursor-pointer transition-colors ${
              config[fieldName]
                ? "border-cyan-500 bg-cyan-500/10"
                : "border-border hover:border-muted-foreground/50"
            }`}
          >
            <div className="flex items-start gap-3">
              <Checkbox
                checked={config[fieldName] || false}
                onCheckedChange={(checked) => setConfig({ ...config, [fieldName]: checked })}
                className="mt-0.5"
              />
              <div className="flex-1">
                <p className="font-medium text-sm">{field.label}</p>
                {field.description && (
                  <p className="text-xs text-muted-foreground mt-1">{field.description}</p>
                )}
                {field.warning && config[fieldName] && (
                  <p className="text-xs text-cyan-400 mt-2 flex items-start gap-1.5 bg-cyan-500/10 p-2 rounded">
                    <Info className="h-3 w-3 mt-0.5 flex-shrink-0" />
                    {field.warning}
                  </p>
                )}
              </div>
            </div>
          </div>
        )

      case "networks":
        return (
          <div key={fieldName} className="space-y-3">
            <Label className="text-sm font-medium">{field.label}</Label>
            <p className="text-xs text-muted-foreground">{field.description}</p>
            <div className="space-y-2">
              {networks.map((net) => {
                const isSelected = (config[fieldName] || []).includes(net.subnet)
                return (
                  <div
                    key={net.subnet}
                    onClick={() => {
                      const current = config[fieldName] || []
                      const updated = isSelected
                        ? current.filter((s: string) => s !== net.subnet)
                        : [...current, net.subnet]
                      setConfig({ ...config, [fieldName]: updated })
                    }}
                    className={`p-3 rounded-lg border cursor-pointer transition-colors ${
                      isSelected
                        ? "border-cyan-500 bg-cyan-500/10"
                        : "border-border hover:border-muted-foreground/50"
                    }`}
                  >
                    <div className="flex items-center gap-3">
                      <Checkbox checked={isSelected} />
                      <div>
                        <p className="font-mono text-sm flex items-center gap-2">
                          <Network className="h-4 w-4 text-muted-foreground" />
                          {net.subnet}
                        </p>
                        <p className="text-xs text-muted-foreground">
                          {net.interface} {net.type ? `(${net.type})` : ""}
                        </p>
                      </div>
                    </div>
                  </div>
                )
              })}
            </div>
          </div>
        )

      default:
        return null
    }
  }

  const renderWizardContent = () => {
    if (!wizardSteps.length || !configSchema) return null
    
    const step = wizardSteps[currentStep]
    if (!step) return null

    // Review step
    if (step.id === "review") {
      return (
        <div className="space-y-4">
          <div className="text-center mb-4">
            <h3 className="text-lg font-semibold">{step.title}</h3>
            <p className="text-sm text-muted-foreground">{step.description}</p>
          </div>

          <div className="bg-muted/30 rounded-lg p-4 space-y-3">
            <div className="flex justify-between text-sm">
              <span className="text-muted-foreground">Gateway Name</span>
              <span className="font-medium">{config.hostname || "proxmox-gateway"}</span>
            </div>
            <div className="flex justify-between text-sm">
              <span className="text-muted-foreground">Access Scope</span>
              <span className="font-medium">
                {config.access_mode === "host_only" ? "Proxmox Only" :
                 config.access_mode === "proxmox_network" ? "Full Local Network" :
                 config.access_mode === "custom" ? "Custom Subnets" : config.access_mode}
              </span>
            </div>
            {config.advertise_routes?.length > 0 && (
              <div className="flex justify-between text-sm">
                <span className="text-muted-foreground">Networks</span>
                <span className="font-mono text-xs">{config.advertise_routes.join(", ")}</span>
              </div>
            )}
            {config.exit_node && (
              <div className="flex justify-between text-sm">
                <span className="text-muted-foreground">Exit Node</span>
                <span className="text-cyan-400">Enabled</span>
              </div>
            )}
          </div>

          {/* Approval notice */}
          {(config.access_mode && config.access_mode !== "none") && !deploying && (
            <div className="bg-cyan-500/10 border border-cyan-500/20 rounded-lg p-3 space-y-2">
              <p className="text-xs text-cyan-400 flex items-start gap-2">
                <Info className="h-4 w-4 flex-shrink-0 mt-0.5" />
                <span>
                  <strong>Important:</strong> After deployment, you must approve the subnet route in Tailscale Admin for remote access to work.
                  {config.exit_node && <span> You{"'"}ll also need to approve the exit node.</span>}
                </span>
              </p>
              <p className="text-xs text-muted-foreground ml-6">
                We{"'"}ll show you exactly what to do after the gateway is deployed.
              </p>
            </div>
          )}

          {deploying && (
            <div className="bg-cyan-500/10 border border-cyan-500/20 rounded-lg p-4 space-y-3">
              <div className="flex items-center gap-3">
                <Loader2 className="h-5 w-5 text-cyan-500 animate-spin" />
                <div className="flex-1">
                  <span className="text-sm font-medium">{deployProgress}</span>
                  <p className="text-xs text-muted-foreground mt-1">This may take a minute...</p>
                </div>
              </div>
            </div>
          )}

          {deployError && (
            <div className="bg-red-500/10 border border-red-500/20 rounded-lg p-3">
              <p className="text-sm text-red-500 flex items-center gap-2">
                <XCircle className="h-4 w-4" />
                {deployError}
              </p>
            </div>
          )}
        </div>
      )
    }

    // Regular step with fields
    return (
      <div className="space-y-4">
        <div className="text-center mb-4">
          <h3 className="text-lg font-semibold">{step.title}</h3>
          <p className="text-sm text-muted-foreground">{step.description}</p>
        </div>

        <div className="space-y-4">
          {step.fields?.map((fieldName) => {
            const field = configSchema[fieldName]
            if (!field) return null
            return renderField(fieldName, field)
          })}
        </div>
      </div>
    )
  }

  // Loading state
  if (loading) {
    return (
      <Card className="bg-card border-border">
        <CardContent className="p-6">
          <div className="flex items-center gap-3">
            <Loader2 className="h-5 w-5 animate-spin text-cyan-500" />
            <span>Loading Secure Gateway...</span>
          </div>
        </CardContent>
      </Card>
    )
  }

  // Installed state
  if (appStatus.state !== "not_installed") {
    const isRunning = appStatus.state === "running"
    
    return (
      <>
        <Card className="bg-card border-border">
          <CardContent className="p-6 space-y-4">
            <div className="flex items-start justify-between">
              <div className="flex items-center gap-3">
                <ShieldCheck className="h-5 w-5 text-cyan-500" />
                <div>
                  <h3 className="font-semibold">Secure Gateway</h3>
                  <p className="text-sm text-muted-foreground">Tailscale VPN Gateway</p>
                </div>
              </div>
              <div className={`flex items-center gap-2 text-sm ${isRunning ? "text-green-500" : "text-red-500"}`}>
                {isRunning ? <Wifi className="h-4 w-4" /> : <XCircle className="h-4 w-4" />}
                {isRunning ? "Connected" : "Disconnected"}
              </div>
            </div>

            {isRunning && (
              <p className="text-sm text-muted-foreground">
                Uptime: {formatUptime(appStatus.uptime_seconds)}
              </p>
            )}

            {/* Action buttons */}
            <div className="flex flex-wrap gap-2">
              {isRunning ? (
                <>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => handleAction("stop")}
                    disabled={actionLoading !== null}
                  >
                    {actionLoading === "stop" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Square className="h-4 w-4 mr-1" />}
                    Stop
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => handleAction("restart")}
                    disabled={actionLoading !== null}
                  >
                    {actionLoading === "restart" ? <Loader2 className="h-4 w-4 animate-spin" /> : <RotateCw className="h-4 w-4 mr-1" />}
                    Restart
                  </Button>
                </>
              ) : (
                <Button
                  size="sm"
                  className="bg-green-600 hover:bg-green-700 text-white"
                  onClick={() => handleAction("start")}
                  disabled={actionLoading !== null}
                >
                  {actionLoading === "start" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4 mr-1" />}
                  Start
                </Button>
              )}
              <Button
                size="sm"
                variant="outline"
                onClick={handleViewLogs}
                disabled={actionLoading !== null}
              >
                <FileText className="h-4 w-4 mr-1" />
                Logs
              </Button>
              <Button
                size="sm"
                variant="destructive"
                onClick={() => setShowRemoveConfirm(true)}
                disabled={actionLoading !== null}
              >
                <Trash2 className="h-4 w-4 mr-1" />
                Remove
              </Button>
            </div>

            {/* Update Auth Key button */}
            <div className="pt-2 border-t border-border flex items-center justify-between">
              <Button
                size="sm"
                variant="ghost"
                onClick={() => setShowUpdateAuthKey(true)}
                disabled={actionLoading !== null}
                className="text-xs h-7 px-2"
              >
                <Key className="h-3 w-3 mr-1" />
                Update Auth Key
              </Button>
              <a
                href="https://login.tailscale.com/admin/machines"
                target="_blank"
                rel="noopener noreferrer"
                className="text-xs text-cyan-500 hover:text-cyan-400 inline-flex items-center gap-1"
              >
                Open Tailscale Admin <ExternalLink className="h-3 w-3" />
              </a>
            </div>
          </CardContent>
        </Card>

        {/* Logs Dialog */}
        <Dialog open={showLogs} onOpenChange={setShowLogs}>
          <DialogContent className="max-w-2xl max-h-[80vh]">
            <DialogHeader>
              <DialogTitle>Gateway Logs</DialogTitle>
            </DialogHeader>
            <div className="bg-black rounded-lg p-4 overflow-auto max-h-[60vh]">
              {logsLoading ? (
                <Loader2 className="h-5 w-5 animate-spin text-cyan-500" />
              ) : (
                <pre className="text-xs text-green-400 font-mono whitespace-pre-wrap">{logs}</pre>
              )}
            </div>
          </DialogContent>
        </Dialog>

        {/* Remove Confirmation */}
        <Dialog open={showRemoveConfirm} onOpenChange={setShowRemoveConfirm}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Remove Secure Gateway?</DialogTitle>
              <DialogDescription>
                This will remove the gateway container and disconnect it from your Tailscale network.
              </DialogDescription>
            </DialogHeader>
            <div className="flex justify-end gap-2 pt-4">
              <Button variant="outline" onClick={() => setShowRemoveConfirm(false)}>
                Cancel
              </Button>
              <Button
                variant="destructive"
                onClick={handleRemove}
                disabled={actionLoading === "remove"}
              >
                {actionLoading === "remove" ? <Loader2 className="h-4 w-4 animate-spin mr-2" /> : null}
                Remove
              </Button>
            </div>
          </DialogContent>
        </Dialog>

        {/* Update Auth Key Dialog */}
        <Dialog open={showUpdateAuthKey} onOpenChange={(open) => {
          setShowUpdateAuthKey(open)
          if (!open) {
            setNewAuthKey("")
            setUpdateAuthKeyError("")
          }
        }}>
          <DialogContent className="max-w-md">
            <DialogHeader>
              <DialogTitle className="flex items-center gap-2">
                <Key className="h-5 w-5 text-cyan-500" />
                Update Auth Key
              </DialogTitle>
              <DialogDescription>
                Enter a new Tailscale auth key to re-authenticate the gateway. This is useful if your previous key has expired.
              </DialogDescription>
            </DialogHeader>
            
            <div className="space-y-4">
              <div className="space-y-2">
                <label className="text-sm font-medium">New Auth Key</label>
                <Input
                  type="password"
                  value={newAuthKey}
                  onChange={(e) => setNewAuthKey(e.target.value)}
                  placeholder="tskey-auth-..."
                  className="font-mono text-sm"
                />
                <p className="text-xs text-muted-foreground">
                  Generate a new key at{" "}
                  <a
                    href="https://login.tailscale.com/admin/settings/keys"
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-cyan-500 hover:text-cyan-400 underline"
                  >
                    Tailscale Admin &gt; Settings &gt; Keys
                  </a>
                </p>
              </div>
              
              {updateAuthKeyError && (
                <div className="bg-red-500/10 border border-red-500/20 rounded-lg p-3">
                  <p className="text-xs text-red-500">{updateAuthKeyError}</p>
                </div>
              )}
            </div>
            
            <div className="flex justify-end gap-2 pt-2">
              <Button variant="outline" onClick={() => setShowUpdateAuthKey(false)}>
                Cancel
              </Button>
              <Button
                onClick={handleUpdateAuthKey}
                disabled={updateAuthKeyLoading || !newAuthKey.trim()}
                className="bg-cyan-600 hover:bg-cyan-700"
              >
                {updateAuthKeyLoading ? (
                  <Loader2 className="h-4 w-4 animate-spin mr-2" />
                ) : (
                  <Key className="h-4 w-4 mr-2" />
                )}
                Update Key
              </Button>
            </div>
          </DialogContent>
        </Dialog>

        {/* Post-Deploy Info Dialog */}
        <Dialog open={showPostDeployInfo} onOpenChange={setShowPostDeployInfo}>
          <DialogContent className="max-w-lg">
            <DialogHeader>
              <DialogTitle className="flex items-center gap-2">
                <CheckCircle className="h-5 w-5 text-green-500" />
                Gateway Deployed Successfully!
              </DialogTitle>
            </DialogHeader>
            
            <div className="space-y-4">
              <p className="text-sm text-muted-foreground">
                Your gateway is connected to Tailscale. To complete setup, you need to approve the advertised routes in Tailscale Admin.
              </p>
              
              {deployedConfig.advertise_routes?.length > 0 && (
                <div className="bg-muted/30 rounded-lg p-3">
                  <p className="text-xs text-muted-foreground mb-2">Routes to approve:</p>
                  <div className="space-y-1">
                    {deployedConfig.advertise_routes.map((route: string) => (
                      <p key={route} className="font-mono text-sm flex items-center gap-2">
                        <Network className="h-4 w-4 text-cyan-500" />
                        {route}
                      </p>
                    ))}
                  </div>
                </div>
              )}

              <div className="bg-muted/30 rounded-lg p-4 space-y-2">
                <p className="text-sm font-medium">How to approve:</p>
                <ol className="text-sm text-muted-foreground space-y-2 list-decimal list-inside">
                  <li>Click the button below to open Tailscale Admin</li>
                  <li>Find <span className="font-mono text-cyan-400">{deployedConfig.hostname || "proxmox-gateway"}</span> in the machines list</li>
                  <li>Click on it to open machine details</li>
                  <li>In the <strong>Subnets</strong> section, click <strong>Edit</strong> and enable the route</li>
                  {deployedConfig.exit_node && (
                    <li>In <strong>Routing Settings</strong>, enable <strong>Exit Node</strong></li>
                  )}
                </ol>
              </div>
              
              <div className="bg-green-500/10 border border-green-500/20 rounded-lg p-3">
                <p className="text-xs text-green-400">
                  Once approved, you can access your Proxmox host at{" "}
                  <span className="font-mono">{deployedConfig.advertise_routes?.[0]?.replace("/32", "") || hostIp}:8006</span> (Proxmox UI) or{" "}
                  <span className="font-mono">{deployedConfig.advertise_routes?.[0]?.replace("/32", "") || hostIp}:8008</span> (ProxMenux Monitor) from any device with Tailscale.
                </p>
              </div>

              <div className="flex justify-end gap-2 pt-2">
                <Button variant="outline" onClick={() => setShowPostDeployInfo(false)}>
                  Done
                </Button>
                <a
                  href="https://login.tailscale.com/admin/machines"
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  <Button className="bg-cyan-600 hover:bg-cyan-700">
                    Open Tailscale Admin
                    <ExternalLink className="h-4 w-4 ml-2" />
                  </Button>
                </a>
              </div>
            </div>
          </DialogContent>
        </Dialog>
      </>
    )
  }

  // Not installed state
  return (
    <>
      <Card className="bg-card border-border">
        <CardContent className="p-6 space-y-4">
          <div className="flex items-start gap-3">
            <ShieldCheck className="h-5 w-5 text-cyan-500 mt-0.5" />
            <div>
              <h3 className="font-semibold">Secure Gateway</h3>
              <p className="text-sm text-muted-foreground">VPN access without opening ports</p>
            </div>
          </div>

          <p className="text-sm text-muted-foreground">
            Deploy a Tailscale VPN gateway for secure remote access to your Proxmox infrastructure. No port forwarding required.
          </p>

          {runtimeInfo && (
            <p className={`text-xs flex items-center gap-1 ${runtimeAvailable ? "text-green-500" : "text-yellow-500"}`}>
              {runtimeAvailable ? <CheckCircle className="h-3 w-3" /> : <AlertTriangle className="h-3 w-3" />}
              Proxmox VE {runtimeInfo.version} - {runtimeAvailable ? "OCI support available" : "Requires Proxmox 9.1+"}
            </p>
          )}

          <Button
            onClick={() => setShowWizard(true)}
            className="bg-cyan-600 hover:bg-cyan-700"
            disabled={!runtimeAvailable}
          >
            <ShieldCheck className="h-4 w-4 mr-2" />
            Deploy Secure Gateway
          </Button>
        </CardContent>
      </Card>

      {/* Wizard Dialog */}
      <Dialog open={showWizard} onOpenChange={(open) => {
        if (!deploying) {
          setShowWizard(open)
          if (!open) {
            setCurrentStep(0)
            setDeployError("")
            setDeployPercent(0)
          }
        }
      }}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <ShieldCheck className="h-5 w-5 text-cyan-500" />
              Secure Gateway Setup
            </DialogTitle>
          </DialogHeader>

          {/* Progress indicator - filter out "options" step if using Proxmox Only */}
          <div className="flex items-center gap-1 mb-4">
            {wizardSteps
              .filter((step) => !(config.access_mode === "host_only" && step.id === "options"))
              .map((step, idx) => {
                const adjustedCurrentStep = config.access_mode === "host_only" 
                  ? (currentStep > wizardSteps.findIndex((s) => s.id === "options") ? currentStep - 1 : currentStep)
                  : currentStep
                return (
                  <div
                    key={step.id}
                    className={`flex-1 h-1 rounded-full transition-colors ${
                      idx < adjustedCurrentStep ? "bg-cyan-500" :
                      idx === adjustedCurrentStep ? "bg-cyan-500" :
                      "bg-muted"
                    }`}
                  />
                )
              })}
          </div>

          {renderWizardContent()}

          {/* Navigation */}
          <div className="flex justify-between pt-4 border-t border-border">
            <Button
              variant="outline"
              onClick={() => {
                let prevStep = currentStep - 1
                if (config.access_mode === "host_only" && wizardSteps[prevStep]?.id === "options") {
                  prevStep = prevStep - 1
                }
                setCurrentStep(Math.max(0, prevStep))
              }}
              disabled={currentStep === 0 || deploying}
            >
              Back
            </Button>
            
            {currentStep < wizardSteps.length - 1 ? (
              <Button
                onClick={() => {
                  let nextStep = currentStep + 1
                  if (config.access_mode === "host_only" && wizardSteps[nextStep]?.id === "options") {
                    nextStep = nextStep + 1
                  }
                  setCurrentStep(nextStep)
                }}
                className="bg-cyan-600 hover:bg-cyan-700"
              >
                Continue
                <ChevronRight className="h-4 w-4 ml-1" />
              </Button>
            ) : (
              <Button
                onClick={handleDeploy}
                disabled={deploying}
                className="bg-cyan-600 hover:bg-cyan-700"
              >
                {deploying ? (
                  <>
                    <Loader2 className="h-4 w-4 animate-spin mr-2" />
                    Deploying...
                  </>
                ) : (
                  <>
                    <Play className="h-4 w-4 mr-2" />
                    Deploy Gateway
                  </>
                )}
              </Button>
            )}
          </div>
        </DialogContent>
      </Dialog>
    </>
  )
}
