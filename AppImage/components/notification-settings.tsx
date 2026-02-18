"use client"

import { useState, useEffect, useCallback } from "react"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "./ui/card"
import { Tabs, TabsList, TabsTrigger, TabsContent } from "./ui/tabs"
import { Input } from "./ui/input"
import { Label } from "./ui/label"
import { Badge } from "./ui/badge"
import { Checkbox } from "./ui/checkbox"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "./ui/select"
import { fetchApi } from "../lib/api-config"
import {
  Bell, BellOff, Send, CheckCircle2, XCircle, Loader2,
  AlertTriangle, Info, Settings2, Zap, Eye, EyeOff,
  Trash2, ChevronDown, ChevronUp, TestTube2
} from "lucide-react"

interface ChannelConfig {
  enabled: boolean
  bot_token?: string
  chat_id?: string
  url?: string
  token?: string
  webhook_url?: string
}

interface NotificationConfig {
  enabled: boolean
  channels: Record<string, ChannelConfig>
  severity_filter: string
  event_categories: Record<string, boolean>
  ai_enabled: boolean
  ai_provider: string
  ai_api_key: string
  ai_model: string
  hostname: string
}

interface ServiceStatus {
  enabled: boolean
  running: boolean
  channels: Record<string, boolean>
  queue_size: number
  last_sent: string | null
  total_sent_24h: number
}

interface HistoryEntry {
  id: number
  event_type: string
  channel: string
  title: string
  severity: string
  sent_at: string
  success: boolean
  error_message: string | null
}

const SEVERITY_OPTIONS = [
  { value: "critical", label: "Critical only" },
  { value: "warning", label: "Warning + Critical" },
  { value: "info", label: "All (Info + Warning + Critical)" },
]

const EVENT_CATEGORIES = [
  { key: "system", label: "System", desc: "Startup, shutdown, kernel events" },
  { key: "vm_ct", label: "VM / CT", desc: "Start, stop, crash, migration" },
  { key: "backup", label: "Backups", desc: "Backup start, complete, fail" },
  { key: "resources", label: "Resources", desc: "CPU, memory, temperature" },
  { key: "storage", label: "Storage", desc: "Disk space, I/O errors, SMART" },
  { key: "network", label: "Network", desc: "Connectivity, bond, latency" },
  { key: "security", label: "Security", desc: "Auth failures, fail2ban, firewall" },
  { key: "cluster", label: "Cluster", desc: "Quorum, split-brain, HA fencing" },
]

const AI_PROVIDERS = [
  { value: "openai", label: "OpenAI" },
  { value: "groq", label: "Groq" },
]

const DEFAULT_CONFIG: NotificationConfig = {
  enabled: false,
  channels: {
    telegram: { enabled: false },
    gotify: { enabled: false },
    discord: { enabled: false },
  },
  severity_filter: "warning",
  event_categories: {
    system: true, vm_ct: true, backup: true, resources: true,
    storage: true, network: true, security: true, cluster: true,
  },
  ai_enabled: false,
  ai_provider: "openai",
  ai_api_key: "",
  ai_model: "",
  hostname: "",
}

export function NotificationSettings() {
  const [config, setConfig] = useState<NotificationConfig>(DEFAULT_CONFIG)
  const [status, setStatus] = useState<ServiceStatus | null>(null)
  const [history, setHistory] = useState<HistoryEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [testing, setTesting] = useState<string | null>(null)
  const [testResult, setTestResult] = useState<{ channel: string; success: boolean; message: string } | null>(null)
  const [showHistory, setShowHistory] = useState(false)
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [showSecrets, setShowSecrets] = useState<Record<string, boolean>>({})
  const [editMode, setEditMode] = useState(false)
  const [hasChanges, setHasChanges] = useState(false)
  const [originalConfig, setOriginalConfig] = useState<NotificationConfig>(DEFAULT_CONFIG)

  const loadConfig = useCallback(async () => {
    try {
      const data = await fetchApi<{ success: boolean; config: NotificationConfig }>("/api/notifications/settings")
      if (data.success && data.config) {
        setConfig(data.config)
        setOriginalConfig(data.config)
      }
    } catch (err) {
      console.error("Failed to load notification settings:", err)
    } finally {
      setLoading(false)
    }
  }, [])

  const loadStatus = useCallback(async () => {
    try {
      const data = await fetchApi<{ success: boolean } & ServiceStatus>("/api/notifications/status")
      if (data.success) {
        setStatus(data)
      }
    } catch {
      // Service may not be running yet
    }
  }, [])

  const loadHistory = useCallback(async () => {
    try {
      const data = await fetchApi<{ success: boolean; history: HistoryEntry[]; total: number }>("/api/notifications/history?limit=20")
      if (data.success) {
        setHistory(data.history || [])
      }
    } catch {
      // Ignore
    }
  }, [])

  useEffect(() => {
    loadConfig()
    loadStatus()
  }, [loadConfig, loadStatus])

  useEffect(() => {
    if (showHistory) loadHistory()
  }, [showHistory, loadHistory])

  const updateConfig = (updater: (prev: NotificationConfig) => NotificationConfig) => {
    setConfig(prev => {
      const next = updater(prev)
      setHasChanges(true)
      return next
    })
  }

  const updateChannel = (channel: string, field: string, value: string | boolean) => {
    updateConfig(prev => ({
      ...prev,
      channels: {
        ...prev.channels,
        [channel]: { ...prev.channels[channel], [field]: value },
      },
    }))
  }

  const handleSave = async () => {
    setSaving(true)
    try {
      await fetchApi("/api/notifications/settings", {
        method: "POST",
        body: JSON.stringify(config),
      })
      setOriginalConfig(config)
      setHasChanges(false)
      setEditMode(false)
      setSaved(true)
      setTimeout(() => setSaved(false), 3000)
      loadStatus()
    } catch (err) {
      console.error("Failed to save notification settings:", err)
    } finally {
      setSaving(false)
    }
  }

  const handleCancel = () => {
    setConfig(originalConfig)
    setHasChanges(false)
    setEditMode(false)
  }

  const handleTest = async (channel: string) => {
    setTesting(channel)
    setTestResult(null)
    try {
      const data = await fetchApi<{ success: boolean; message: string }>("/api/notifications/test", {
        method: "POST",
        body: JSON.stringify({ channel }),
      })
      setTestResult({ channel, success: data.success, message: data.message })
    } catch (err) {
      setTestResult({ channel, success: false, message: String(err) })
    } finally {
      setTesting(null)
      setTimeout(() => setTestResult(null), 5000)
    }
  }

  const handleClearHistory = async () => {
    try {
      await fetchApi("/api/notifications/history", { method: "DELETE" })
      setHistory([])
    } catch {
      // Ignore
    }
  }

  const toggleSecret = (key: string) => {
    setShowSecrets(prev => ({ ...prev, [key]: !prev[key] }))
  }

  if (loading) {
    return (
      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <Bell className="h-5 w-5 text-blue-500" />
            <CardTitle>Notifications</CardTitle>
          </div>
        </CardHeader>
        <CardContent>
          <div className="flex items-center justify-center py-8">
            <div className="animate-spin h-8 w-8 border-4 border-blue-500 border-t-transparent rounded-full" />
          </div>
        </CardContent>
      </Card>
    )
  }

  const activeChannels = Object.entries(config.channels).filter(([, ch]) => ch.enabled).length

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Bell className="h-5 w-5 text-blue-500" />
            <CardTitle>Notifications</CardTitle>
            {config.enabled && (
              <Badge variant="outline" className="text-[10px] border-green-500/30 text-green-500">
                Active
              </Badge>
            )}
          </div>
          <div className="flex items-center gap-2">
            {saved && (
              <span className="flex items-center gap-1 text-xs text-green-500">
                <CheckCircle2 className="h-3.5 w-3.5" />
                Saved
              </span>
            )}
            {editMode ? (
              <>
                <button
                  className="h-7 px-3 text-xs rounded-md border border-border bg-background hover:bg-muted transition-colors text-muted-foreground"
                  onClick={handleCancel}
                  disabled={saving}
                >
                  Cancel
                </button>
                <button
                  className="h-7 px-3 text-xs rounded-md bg-blue-600 hover:bg-blue-700 text-white transition-colors disabled:opacity-50 flex items-center gap-1.5"
                  onClick={handleSave}
                  disabled={saving || !hasChanges}
                >
                  {saving ? <Loader2 className="h-3 w-3 animate-spin" /> : <CheckCircle2 className="h-3 w-3" />}
                  Save
                </button>
              </>
            ) : (
              <button
                className="h-7 px-3 text-xs rounded-md border border-border bg-background hover:bg-muted transition-colors flex items-center gap-1.5"
                onClick={() => setEditMode(true)}
              >
                <Settings2 className="h-3 w-3" />
                Edit
              </button>
            )}
          </div>
        </div>
        <CardDescription>
          Configure notification channels and event filters. Receive alerts via Telegram, Gotify, or Discord.
        </CardDescription>
      </CardHeader>

      <CardContent className="space-y-5">
        {/* ── Service Status ── */}
        {status && (
          <div className="flex items-center gap-3 p-3 rounded-lg bg-muted/50 border border-border">
            <div className={`h-2.5 w-2.5 rounded-full ${status.running ? "bg-green-500" : "bg-red-500"}`} />
            <div className="flex-1 min-w-0">
              <span className="text-xs font-medium">
                {status.running ? "Service running" : "Service stopped"}
              </span>
              {status.total_sent_24h > 0 && (
                <span className="text-xs text-muted-foreground ml-2">
                  {status.total_sent_24h} sent in last 24h
                </span>
              )}
            </div>
            {activeChannels > 0 && (
              <Badge variant="outline" className="text-[10px]">
                {activeChannels} channel{activeChannels > 1 ? "s" : ""}
              </Badge>
            )}
          </div>
        )}

        {/* ── Enable/Disable ── */}
        <div className="flex items-center justify-between py-2 px-1">
          <div className="flex items-center gap-2">
            {config.enabled ? (
              <Bell className="h-4 w-4 text-blue-500" />
            ) : (
              <BellOff className="h-4 w-4 text-muted-foreground" />
            )}
            <div>
              <span className="text-sm font-medium">Enable Notifications</span>
              <p className="text-[11px] text-muted-foreground">Activate the notification service</p>
            </div>
          </div>
          <button
            className={`relative w-10 h-5 rounded-full transition-colors ${
              config.enabled ? "bg-blue-600" : "bg-muted-foreground/30"
            } ${!editMode ? "opacity-60 cursor-not-allowed" : "cursor-pointer"}`}
            onClick={() => editMode && updateConfig(p => ({ ...p, enabled: !p.enabled }))}
            disabled={!editMode}
            role="switch"
            aria-checked={config.enabled}
          >
            <span
              className={`absolute top-0.5 left-0.5 h-4 w-4 rounded-full bg-white shadow transition-transform ${
                config.enabled ? "translate-x-5" : "translate-x-0"
              }`}
            />
          </button>
        </div>

        {config.enabled && (
          <>
            {/* ── Channel Configuration ── */}
            <div className="space-y-3">
              <div className="flex items-center gap-2">
                <Zap className="h-3.5 w-3.5 text-muted-foreground" />
                <span className="text-xs font-medium text-muted-foreground uppercase tracking-wider">Channels</span>
              </div>

              <Tabs defaultValue="telegram" className="w-full">
                <TabsList className="w-full grid grid-cols-3 h-8">
                  <TabsTrigger value="telegram" className="text-xs data-[state=active]:text-blue-500">
                    Telegram
                  </TabsTrigger>
                  <TabsTrigger value="gotify" className="text-xs data-[state=active]:text-green-500">
                    Gotify
                  </TabsTrigger>
                  <TabsTrigger value="discord" className="text-xs data-[state=active]:text-indigo-500">
                    Discord
                  </TabsTrigger>
                </TabsList>

                {/* Telegram */}
                <TabsContent value="telegram" className="space-y-3 pt-2">
                  <div className="flex items-center justify-between">
                    <Label className="text-xs font-medium">Enable Telegram</Label>
                    <button
                      className={`relative w-9 h-[18px] rounded-full transition-colors ${
                        config.channels.telegram?.enabled ? "bg-blue-600" : "bg-muted-foreground/30"
                      } ${!editMode ? "opacity-60 cursor-not-allowed" : "cursor-pointer"}`}
                      onClick={() => editMode && updateChannel("telegram", "enabled", !config.channels.telegram?.enabled)}
                      disabled={!editMode}
                      role="switch"
                      aria-checked={config.channels.telegram?.enabled || false}
                    >
                      <span className={`absolute top-[1px] left-[1px] h-4 w-4 rounded-full bg-white shadow transition-transform ${
                        config.channels.telegram?.enabled ? "translate-x-[18px]" : "translate-x-0"
                      }`} />
                    </button>
                  </div>
                  {config.channels.telegram?.enabled && (
                    <>
                      <div className="space-y-1.5">
                        <Label className="text-[11px] text-muted-foreground">Bot Token</Label>
                        <div className="flex items-center gap-1.5">
                          <Input
                            type={showSecrets["tg_token"] ? "text" : "password"}
                            className="h-7 text-xs font-mono"
                            placeholder="123456:ABC-DEF1234..."
                            value={config.channels.telegram?.bot_token || ""}
                            onChange={e => updateChannel("telegram", "bot_token", e.target.value)}
                            disabled={!editMode}
                          />
                          <button
                            className="h-7 w-7 flex items-center justify-center rounded-md border border-border hover:bg-muted transition-colors shrink-0"
                            onClick={() => toggleSecret("tg_token")}
                          >
                            {showSecrets["tg_token"] ? <EyeOff className="h-3 w-3" /> : <Eye className="h-3 w-3" />}
                          </button>
                        </div>
                      </div>
                      <div className="space-y-1.5">
                        <Label className="text-[11px] text-muted-foreground">Chat ID</Label>
                        <Input
                          className="h-7 text-xs font-mono"
                          placeholder="-1001234567890"
                          value={config.channels.telegram?.chat_id || ""}
                          onChange={e => updateChannel("telegram", "chat_id", e.target.value)}
                          disabled={!editMode}
                        />
                      </div>
                      {!editMode && config.channels.telegram?.bot_token && (
                        <button
                          className="h-7 px-3 text-xs rounded-md border border-border bg-background hover:bg-muted transition-colors flex items-center gap-1.5 w-full justify-center"
                          onClick={() => handleTest("telegram")}
                          disabled={testing === "telegram"}
                        >
                          {testing === "telegram" ? (
                            <Loader2 className="h-3 w-3 animate-spin" />
                          ) : (
                            <TestTube2 className="h-3 w-3" />
                          )}
                          Test Telegram
                        </button>
                      )}
                    </>
                  )}
                </TabsContent>

                {/* Gotify */}
                <TabsContent value="gotify" className="space-y-3 pt-2">
                  <div className="flex items-center justify-between">
                    <Label className="text-xs font-medium">Enable Gotify</Label>
                    <button
                      className={`relative w-9 h-[18px] rounded-full transition-colors ${
                        config.channels.gotify?.enabled ? "bg-green-600" : "bg-muted-foreground/30"
                      } ${!editMode ? "opacity-60 cursor-not-allowed" : "cursor-pointer"}`}
                      onClick={() => editMode && updateChannel("gotify", "enabled", !config.channels.gotify?.enabled)}
                      disabled={!editMode}
                      role="switch"
                      aria-checked={config.channels.gotify?.enabled || false}
                    >
                      <span className={`absolute top-[1px] left-[1px] h-4 w-4 rounded-full bg-white shadow transition-transform ${
                        config.channels.gotify?.enabled ? "translate-x-[18px]" : "translate-x-0"
                      }`} />
                    </button>
                  </div>
                  {config.channels.gotify?.enabled && (
                    <>
                      <div className="space-y-1.5">
                        <Label className="text-[11px] text-muted-foreground">Server URL</Label>
                        <Input
                          className="h-7 text-xs font-mono"
                          placeholder="https://gotify.example.com"
                          value={config.channels.gotify?.url || ""}
                          onChange={e => updateChannel("gotify", "url", e.target.value)}
                          disabled={!editMode}
                        />
                      </div>
                      <div className="space-y-1.5">
                        <Label className="text-[11px] text-muted-foreground">App Token</Label>
                        <div className="flex items-center gap-1.5">
                          <Input
                            type={showSecrets["gt_token"] ? "text" : "password"}
                            className="h-7 text-xs font-mono"
                            placeholder="A_valid_gotify_token"
                            value={config.channels.gotify?.token || ""}
                            onChange={e => updateChannel("gotify", "token", e.target.value)}
                            disabled={!editMode}
                          />
                          <button
                            className="h-7 w-7 flex items-center justify-center rounded-md border border-border hover:bg-muted transition-colors shrink-0"
                            onClick={() => toggleSecret("gt_token")}
                          >
                            {showSecrets["gt_token"] ? <EyeOff className="h-3 w-3" /> : <Eye className="h-3 w-3" />}
                          </button>
                        </div>
                      </div>
                      {!editMode && config.channels.gotify?.url && (
                        <button
                          className="h-7 px-3 text-xs rounded-md border border-border bg-background hover:bg-muted transition-colors flex items-center gap-1.5 w-full justify-center"
                          onClick={() => handleTest("gotify")}
                          disabled={testing === "gotify"}
                        >
                          {testing === "gotify" ? (
                            <Loader2 className="h-3 w-3 animate-spin" />
                          ) : (
                            <TestTube2 className="h-3 w-3" />
                          )}
                          Test Gotify
                        </button>
                      )}
                    </>
                  )}
                </TabsContent>

                {/* Discord */}
                <TabsContent value="discord" className="space-y-3 pt-2">
                  <div className="flex items-center justify-between">
                    <Label className="text-xs font-medium">Enable Discord</Label>
                    <button
                      className={`relative w-9 h-[18px] rounded-full transition-colors ${
                        config.channels.discord?.enabled ? "bg-indigo-600" : "bg-muted-foreground/30"
                      } ${!editMode ? "opacity-60 cursor-not-allowed" : "cursor-pointer"}`}
                      onClick={() => editMode && updateChannel("discord", "enabled", !config.channels.discord?.enabled)}
                      disabled={!editMode}
                      role="switch"
                      aria-checked={config.channels.discord?.enabled || false}
                    >
                      <span className={`absolute top-[1px] left-[1px] h-4 w-4 rounded-full bg-white shadow transition-transform ${
                        config.channels.discord?.enabled ? "translate-x-[18px]" : "translate-x-0"
                      }`} />
                    </button>
                  </div>
                  {config.channels.discord?.enabled && (
                    <>
                      <div className="space-y-1.5">
                        <Label className="text-[11px] text-muted-foreground">Webhook URL</Label>
                        <div className="flex items-center gap-1.5">
                          <Input
                            type={showSecrets["dc_hook"] ? "text" : "password"}
                            className="h-7 text-xs font-mono"
                            placeholder="https://discord.com/api/webhooks/..."
                            value={config.channels.discord?.webhook_url || ""}
                            onChange={e => updateChannel("discord", "webhook_url", e.target.value)}
                            disabled={!editMode}
                          />
                          <button
                            className="h-7 w-7 flex items-center justify-center rounded-md border border-border hover:bg-muted transition-colors shrink-0"
                            onClick={() => toggleSecret("dc_hook")}
                          >
                            {showSecrets["dc_hook"] ? <EyeOff className="h-3 w-3" /> : <Eye className="h-3 w-3" />}
                          </button>
                        </div>
                      </div>
                      {!editMode && config.channels.discord?.webhook_url && (
                        <button
                          className="h-7 px-3 text-xs rounded-md border border-border bg-background hover:bg-muted transition-colors flex items-center gap-1.5 w-full justify-center"
                          onClick={() => handleTest("discord")}
                          disabled={testing === "discord"}
                        >
                          {testing === "discord" ? (
                            <Loader2 className="h-3 w-3 animate-spin" />
                          ) : (
                            <TestTube2 className="h-3 w-3" />
                          )}
                          Test Discord
                        </button>
                      )}
                    </>
                  )}
                </TabsContent>
              </Tabs>

              {/* Test Result */}
              {testResult && (
                <div className={`flex items-center gap-2 p-2.5 rounded-md text-xs ${
                  testResult.success
                    ? "bg-green-500/10 border border-green-500/20 text-green-400"
                    : "bg-red-500/10 border border-red-500/20 text-red-400"
                }`}>
                  {testResult.success ? (
                    <CheckCircle2 className="h-3.5 w-3.5 shrink-0" />
                  ) : (
                    <XCircle className="h-3.5 w-3.5 shrink-0" />
                  )}
                  <span>{testResult.message}</span>
                </div>
              )}
            </div>

            {/* ── Severity Filter ── */}
            <div className="space-y-2">
              <div className="flex items-center gap-2">
                <AlertTriangle className="h-3.5 w-3.5 text-muted-foreground" />
                <span className="text-xs font-medium text-muted-foreground uppercase tracking-wider">Severity Filter</span>
              </div>
              <Select
                value={config.severity_filter}
                onValueChange={v => updateConfig(p => ({ ...p, severity_filter: v }))}
                disabled={!editMode}
              >
                <SelectTrigger className={`h-8 text-xs ${!editMode ? "opacity-60" : ""}`}>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {SEVERITY_OPTIONS.map(opt => (
                    <SelectItem key={opt.value} value={opt.value}>{opt.label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            {/* ── Event Categories ── */}
            <div className="space-y-2">
              <div className="flex items-center gap-2">
                <Send className="h-3.5 w-3.5 text-muted-foreground" />
                <span className="text-xs font-medium text-muted-foreground uppercase tracking-wider">Event Categories</span>
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-1.5">
                {EVENT_CATEGORIES.map(cat => (
                  <label
                    key={cat.key}
                    className={`flex items-start gap-2.5 p-2 rounded-md border border-border/50 transition-colors ${
                      editMode ? "hover:bg-muted/50 cursor-pointer" : "opacity-60"
                    }`}
                  >
                    <Checkbox
                      checked={config.event_categories[cat.key] ?? true}
                      onCheckedChange={checked => {
                        if (!editMode) return
                        updateConfig(p => ({
                          ...p,
                          event_categories: { ...p.event_categories, [cat.key]: !!checked },
                        }))
                      }}
                      disabled={!editMode}
                      className="mt-0.5"
                    />
                    <div className="min-w-0">
                      <span className="text-xs font-medium block">{cat.label}</span>
                      <span className="text-[10px] text-muted-foreground">{cat.desc}</span>
                    </div>
                  </label>
                ))}
              </div>
            </div>

            {/* ── Advanced: AI Enhancement ── */}
            <div>
              <button
                className="flex items-center gap-2 text-xs text-muted-foreground hover:text-foreground transition-colors w-full py-1"
                onClick={() => setShowAdvanced(!showAdvanced)}
              >
                {showAdvanced ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
                <span className="font-medium uppercase tracking-wider">Advanced: AI Enhancement</span>
                {config.ai_enabled && (
                  <Badge variant="outline" className="text-[9px] border-purple-500/30 text-purple-400 ml-1">
                    ON
                  </Badge>
                )}
              </button>

              {showAdvanced && (
                <div className="space-y-3 mt-3 p-3 rounded-lg bg-muted/30 border border-border/50">
                  <div className="flex items-center justify-between">
                    <div>
                      <span className="text-xs font-medium">AI-Enhanced Messages</span>
                      <p className="text-[10px] text-muted-foreground">Use AI to generate contextual notification messages</p>
                    </div>
                    <button
                      className={`relative w-9 h-[18px] rounded-full transition-colors ${
                        config.ai_enabled ? "bg-purple-600" : "bg-muted-foreground/30"
                      } ${!editMode ? "opacity-60 cursor-not-allowed" : "cursor-pointer"}`}
                      onClick={() => editMode && updateConfig(p => ({ ...p, ai_enabled: !p.ai_enabled }))}
                      disabled={!editMode}
                      role="switch"
                      aria-checked={config.ai_enabled}
                    >
                      <span className={`absolute top-[1px] left-[1px] h-4 w-4 rounded-full bg-white shadow transition-transform ${
                        config.ai_enabled ? "translate-x-[18px]" : "translate-x-0"
                      }`} />
                    </button>
                  </div>

                  {config.ai_enabled && (
                    <>
                      <div className="space-y-1.5">
                        <Label className="text-[11px] text-muted-foreground">Provider</Label>
                        <Select
                          value={config.ai_provider}
                          onValueChange={v => updateConfig(p => ({ ...p, ai_provider: v }))}
                          disabled={!editMode}
                        >
                          <SelectTrigger className="h-7 text-xs">
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            {AI_PROVIDERS.map(p => (
                              <SelectItem key={p.value} value={p.value}>{p.label}</SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      </div>
                      <div className="space-y-1.5">
                        <Label className="text-[11px] text-muted-foreground">API Key</Label>
                        <div className="flex items-center gap-1.5">
                          <Input
                            type={showSecrets["ai_key"] ? "text" : "password"}
                            className="h-7 text-xs font-mono"
                            placeholder="sk-..."
                            value={config.ai_api_key}
                            onChange={e => updateConfig(p => ({ ...p, ai_api_key: e.target.value }))}
                            disabled={!editMode}
                          />
                          <button
                            className="h-7 w-7 flex items-center justify-center rounded-md border border-border hover:bg-muted transition-colors shrink-0"
                            onClick={() => toggleSecret("ai_key")}
                          >
                            {showSecrets["ai_key"] ? <EyeOff className="h-3 w-3" /> : <Eye className="h-3 w-3" />}
                          </button>
                        </div>
                      </div>
                      <div className="space-y-1.5">
                        <Label className="text-[11px] text-muted-foreground">Model (optional)</Label>
                        <Input
                          className="h-7 text-xs font-mono"
                          placeholder={config.ai_provider === "openai" ? "gpt-4o-mini" : "llama-3.3-70b-versatile"}
                          value={config.ai_model}
                          onChange={e => updateConfig(p => ({ ...p, ai_model: e.target.value }))}
                          disabled={!editMode}
                        />
                      </div>
                      <div className="flex items-start gap-2 p-2 rounded-md bg-purple-500/10 border border-purple-500/20">
                        <Info className="h-3.5 w-3.5 text-purple-400 shrink-0 mt-0.5" />
                        <p className="text-[10px] text-purple-400/90 leading-relaxed">
                          AI enhancement is optional. When enabled, notifications include contextual analysis and recommended actions. If the AI service is unavailable, standard templates are used as fallback.
                        </p>
                      </div>
                    </>
                  )}
                </div>
              )}
            </div>

            {/* ── Notification History ── */}
            <div>
              <button
                className="flex items-center gap-2 text-xs text-muted-foreground hover:text-foreground transition-colors w-full py-1"
                onClick={() => setShowHistory(!showHistory)}
              >
                {showHistory ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
                <span className="font-medium uppercase tracking-wider">Recent History</span>
                {history.length > 0 && (
                  <Badge variant="outline" className="text-[9px] ml-1">{history.length}</Badge>
                )}
              </button>

              {showHistory && (
                <div className="mt-3 space-y-2">
                  {history.length === 0 ? (
                    <p className="text-xs text-muted-foreground text-center py-4">No notifications sent yet</p>
                  ) : (
                    <>
                      <div className="flex items-center justify-end">
                        <button
                          className="h-6 px-2 text-[10px] rounded-md text-muted-foreground hover:text-red-400 transition-colors flex items-center gap-1"
                          onClick={handleClearHistory}
                        >
                          <Trash2 className="h-3 w-3" />
                          Clear
                        </button>
                      </div>
                      <div className="space-y-1 max-h-48 overflow-y-auto">
                        {history.map(entry => (
                          <div
                            key={entry.id}
                            className="flex items-center gap-2 p-2 rounded-md bg-muted/30 border border-border/50"
                          >
                            {entry.success ? (
                              <CheckCircle2 className="h-3 w-3 text-green-500 shrink-0" />
                            ) : (
                              <XCircle className="h-3 w-3 text-red-500 shrink-0" />
                            )}
                            <div className="flex-1 min-w-0">
                              <span className="text-[11px] font-medium truncate block">{entry.title || entry.event_type}</span>
                              <span className="text-[10px] text-muted-foreground">
                                {entry.channel} - {new Date(entry.sent_at).toLocaleString()}
                              </span>
                            </div>
                            <Badge
                              variant="outline"
                              className={`text-[9px] shrink-0 ${
                                entry.severity === "critical"
                                  ? "border-red-500/30 text-red-400"
                                  : entry.severity === "warning"
                                  ? "border-amber-500/30 text-amber-400"
                                  : "border-blue-500/30 text-blue-400"
                              }`}
                            >
                              {entry.severity}
                            </Badge>
                          </div>
                        ))}
                      </div>
                    </>
                  )}
                </div>
              )}
            </div>
          </>
        )}

        {/* ── Footer info ── */}
        <div className="flex items-start gap-2 pt-3 border-t border-border">
          <Info className="h-3.5 w-3.5 text-blue-400 shrink-0 mt-0.5" />
          <p className="text-[11px] text-muted-foreground leading-relaxed">
            {config.enabled
              ? "Notifications are active. Events matching your severity filter and category selection will be sent to configured channels."
              : "Enable notifications to receive alerts about system events, health status changes, and security incidents via Telegram, Gotify, or Discord."}
          </p>
        </div>
      </CardContent>
    </Card>
  )
}
