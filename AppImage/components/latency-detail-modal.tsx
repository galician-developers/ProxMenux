"use client"

import { useState, useEffect } from "react"
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "./ui/dialog"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "./ui/select"
import { Activity, TrendingDown, TrendingUp, Minus } from "lucide-react"
import { AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from "recharts"
import { useIsMobile } from "../hooks/use-mobile"
import { fetchApi } from "@/lib/api-config"

const TIMEFRAME_OPTIONS = [
  { value: "hour", label: "1 Hour" },
  { value: "6hour", label: "6 Hours" },
  { value: "day", label: "24 Hours" },
  { value: "3day", label: "3 Days" },
  { value: "week", label: "7 Days" },
]

const TARGET_OPTIONS = [
  { value: "gateway", label: "Gateway (Router)" },
  { value: "cloudflare", label: "Cloudflare (1.1.1.1)" },
  { value: "google", label: "Google DNS (8.8.8.8)" },
]

interface LatencyHistoryPoint {
  timestamp: number
  value: number
  min?: number
  max?: number
  packet_loss?: number
}

interface LatencyStats {
  min: number
  max: number
  avg: number
  current: number
}

interface LatencyDetailModalProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  currentLatency?: number
}

const CustomTooltip = ({ active, payload, label }: any) => {
  if (active && payload && payload.length) {
    const entry = payload[0]
    const packetLoss = entry?.payload?.packet_loss
    return (
      <div className="bg-gray-900/95 backdrop-blur-sm border border-gray-700 rounded-lg p-3 shadow-xl">
        <p className="text-sm font-semibold text-white mb-2">{label}</p>
        <div className="space-y-1.5">
          <div className="flex items-center gap-2">
            <div className="w-2.5 h-2.5 rounded-full flex-shrink-0 bg-blue-500" />
            <span className="text-xs text-gray-300 min-w-[60px]">Latency:</span>
            <span className="text-sm font-semibold text-white">{entry.value} ms</span>
          </div>
          {packetLoss !== undefined && packetLoss > 0 && (
            <div className="flex items-center gap-2">
              <div className="w-2.5 h-2.5 rounded-full flex-shrink-0 bg-red-500" />
              <span className="text-xs text-gray-300 min-w-[60px]">Pkt Loss:</span>
              <span className="text-sm font-semibold text-red-400">{packetLoss}%</span>
            </div>
          )}
        </div>
      </div>
    )
  }
  return null
}

const getStatusColor = (latency: number) => {
  if (latency >= 200) return "#ef4444"
  if (latency >= 100) return "#f59e0b"
  return "#22c55e"
}

const getStatusInfo = (latency: number) => {
  if (latency === 0) return { status: "N/A", color: "bg-gray-500/10 text-gray-500 border-gray-500/20" }
  if (latency < 50) return { status: "Excellent", color: "bg-green-500/10 text-green-500 border-green-500/20" }
  if (latency < 100) return { status: "Good", color: "bg-green-500/10 text-green-500 border-green-500/20" }
  if (latency < 200) return { status: "Fair", color: "bg-yellow-500/10 text-yellow-500 border-yellow-500/20" }
  return { status: "Poor", color: "bg-red-500/10 text-red-500 border-red-500/20" }
}

export function LatencyDetailModal({ open, onOpenChange, currentLatency }: LatencyDetailModalProps) {
  const [timeframe, setTimeframe] = useState("hour")
  const [target, setTarget] = useState("gateway")
  const [data, setData] = useState<LatencyHistoryPoint[]>([])
  const [stats, setStats] = useState<LatencyStats>({ min: 0, max: 0, avg: 0, current: 0 })
  const [loading, setLoading] = useState(true)
  const isMobile = useIsMobile()

  useEffect(() => {
    if (open) {
      fetchHistory()
    }
  }, [open, timeframe, target])

  const fetchHistory = async () => {
    setLoading(true)
    try {
      const result = await fetchApi<{ data: LatencyHistoryPoint[]; stats: LatencyStats; target: string }>(
        `/api/network/latency/history?target=${target}&timeframe=${timeframe}`
      )
      if (result && result.data) {
        setData(result.data)
        setStats(result.stats)
      }
    } catch (err) {
      // Silently fail - will show empty state
    } finally {
      setLoading(false)
    }
  }

  const formatTime = (timestamp: number) => {
    const date = new Date(timestamp * 1000)
    if (timeframe === "hour" || timeframe === "6hour") {
      return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
    } else if (timeframe === "day") {
      return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
    } else {
      return date.toLocaleDateString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })
    }
  }

  const chartData = data.map((d) => ({
    ...d,
    time: formatTime(d.timestamp),
  }))

  const currentLat = currentLatency && currentLatency > 0 ? Math.round(currentLatency * 10) / 10 : stats.current
  const currentStatus = getStatusInfo(currentLat)
  const chartColor = getStatusColor(currentLat)

  const values = data.map((d) => d.value).filter(v => v !== null && v !== undefined)
  const yMin = 0
  const yMax = values.length > 0 ? Math.ceil(Math.max(...values) * 1.2) : 200

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-3xl bg-card border-border px-3 sm:px-6">
        <DialogHeader>
          <div className="flex items-center justify-between pr-6 gap-2 flex-wrap">
            <DialogTitle className="text-foreground flex items-center gap-2">
              <Activity className="h-5 w-5" />
              Network Latency
            </DialogTitle>
            <div className="flex items-center gap-2">
              <Select value={target} onValueChange={setTarget}>
                <SelectTrigger className="w-[150px] bg-card border-border">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {TARGET_OPTIONS.map((opt) => (
                    <SelectItem key={opt.value} value={opt.value}>
                      {opt.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Select value={timeframe} onValueChange={setTimeframe}>
                <SelectTrigger className="w-[110px] bg-card border-border">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {TIMEFRAME_OPTIONS.map((opt) => (
                    <SelectItem key={opt.value} value={opt.value}>
                      {opt.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
        </DialogHeader>

        {/* Stats bar */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 sm:gap-3">
          <div className={`rounded-lg p-3 text-center ${currentStatus.color}`}>
            <div className="text-xs opacity-80 mb-1">Current</div>
            <div className="text-lg font-bold">{currentLat} ms</div>
          </div>
          <div className="bg-muted/50 rounded-lg p-3 text-center">
            <div className="text-xs text-muted-foreground mb-1 flex items-center justify-center gap-1">
              <TrendingDown className="h-3 w-3" /> Min
            </div>
            <div className="text-lg font-bold text-green-500">{stats.min} ms</div>
          </div>
          <div className="bg-muted/50 rounded-lg p-3 text-center">
            <div className="text-xs text-muted-foreground mb-1 flex items-center justify-center gap-1">
              <Minus className="h-3 w-3" /> Avg
            </div>
            <div className="text-lg font-bold text-foreground">{stats.avg} ms</div>
          </div>
          <div className="bg-muted/50 rounded-lg p-3 text-center">
            <div className="text-xs text-muted-foreground mb-1 flex items-center justify-center gap-1">
              <TrendingUp className="h-3 w-3" /> Max
            </div>
            <div className="text-lg font-bold text-red-500">{stats.max} ms</div>
          </div>
        </div>

        {/* Chart */}
        <div className="h-[300px] lg:h-[350px]">
          {loading ? (
            <div className="h-full flex items-center justify-center">
              <div className="space-y-3 w-full animate-pulse">
                <div className="h-4 bg-muted rounded w-1/4 mx-auto" />
                <div className="h-[250px] bg-muted/50 rounded" />
              </div>
            </div>
          ) : chartData.length === 0 ? (
            <div className="h-full flex items-center justify-center text-muted-foreground">
              <div className="text-center">
                <Activity className="h-8 w-8 mx-auto mb-2 opacity-50" />
                <p>No latency data available for this period</p>
                <p className="text-sm mt-1">Data is collected every 60 seconds</p>
              </div>
            </div>
          ) : (
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={chartData} margin={{ top: 10, right: 10, left: 0, bottom: 0 }}>
                <defs>
                  <linearGradient id="latencyGradient" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor={chartColor} stopOpacity={0.3} />
                    <stop offset="100%" stopColor={chartColor} stopOpacity={0.02} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="currentColor" className="text-border" />
                <XAxis
                  dataKey="time"
                  stroke="currentColor"
                  className="text-foreground"
                  tick={{ fill: "currentColor", fontSize: isMobile ? 10 : 12 }}
                  interval="preserveStartEnd"
                  minTickGap={isMobile ? 40 : 60}
                />
                <YAxis
                  domain={[yMin, yMax]}
                  stroke="currentColor"
                  className="text-foreground"
                  tick={{ fill: "currentColor", fontSize: isMobile ? 10 : 12 }}
                  tickFormatter={(v) => `${v}ms`}
                  width={isMobile ? 45 : 50}
                />
                <Tooltip content={<CustomTooltip />} />
                <Area
                  type="monotone"
                  dataKey="value"
                  name="Latency"
                  stroke={chartColor}
                  strokeWidth={2}
                  fill="url(#latencyGradient)"
                  dot={false}
                  activeDot={{ r: 4, fill: chartColor, stroke: "#fff", strokeWidth: 2 }}
                />
              </AreaChart>
            </ResponsiveContainer>
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}
