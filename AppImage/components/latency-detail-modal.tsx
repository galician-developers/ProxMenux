"use client"

import { useState, useEffect, useCallback } from "react"
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "./ui/dialog"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "./ui/select"
import { Button } from "./ui/button"
import { Badge } from "./ui/badge"
import { Activity, TrendingDown, TrendingUp, Minus, RefreshCw, Wifi, FileText } from "lucide-react"
import { AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, LineChart, Line } from "recharts"
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
  { value: "gateway", label: "Gateway (Router)", realtime: false },
  { value: "cloudflare", label: "Cloudflare (1.1.1.1)", realtime: true },
  { value: "google", label: "Google DNS (8.8.8.8)", realtime: true },
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

interface RealtimeResult {
  target: string
  target_ip: string
  latency_avg: number | null
  latency_min: number | null
  latency_max: number | null
  packet_loss: number
  status: string
  timestamp?: number
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

const getStatusInfo = (latency: number | null) => {
  if (latency === null || latency === 0) return { status: "N/A", color: "bg-gray-500/10 text-gray-500 border-gray-500/20" }
  if (latency < 50) return { status: "Excellent", color: "bg-green-500/10 text-green-500 border-green-500/20" }
  if (latency < 100) return { status: "Good", color: "bg-green-500/10 text-green-500 border-green-500/20" }
  if (latency < 200) return { status: "Fair", color: "bg-yellow-500/10 text-yellow-500 border-yellow-500/20" }
  return { status: "Poor", color: "bg-red-500/10 text-red-500 border-red-500/20" }
}

const getStatusText = (latency: number | null): string => {
  if (latency === null || latency === 0) return "N/A"
  if (latency < 50) return "Excellent"
  if (latency < 100) return "Good"
  if (latency < 200) return "Fair"
  return "Poor"
}

interface ReportData {
  target: string
  targetLabel: string
  isRealtime: boolean
  stats: LatencyStats
  realtimeResults: RealtimeResult[]
  data: LatencyHistoryPoint[]
  timeframe: string
}

const generateLatencyReport = (report: ReportData) => {
  const now = new Date().toLocaleString()
  const statusText = report.isRealtime 
    ? getStatusText(report.realtimeResults[report.realtimeResults.length - 1]?.latency_avg ?? null)
    : getStatusText(report.stats.current)
  
  const statusColorMap: Record<string, string> = {
    "Excellent": "#22c55e",
    "Good": "#22c55e", 
    "Fair": "#f59e0b",
    "Poor": "#ef4444",
    "N/A": "#888888"
  }
  const statusColor = statusColorMap[statusText] || "#888888"

  const timeframeLabel = TIMEFRAME_OPTIONS.find(t => t.value === report.timeframe)?.label || report.timeframe

  // Build test results table for realtime mode
  const realtimeTableRows = report.realtimeResults.map((r, i) => `
    <tr>
      <td>${i + 1}</td>
      <td>${new Date(r.timestamp || Date.now()).toLocaleTimeString()}</td>
      <td>${r.latency_avg !== null ? r.latency_avg + ' ms' : 'Failed'}</td>
      <td>${r.latency_min !== null ? r.latency_min + ' ms' : '-'}</td>
      <td>${r.latency_max !== null ? r.latency_max + ' ms' : '-'}</td>
      <td class="${r.packet_loss > 0 ? 'text-red' : ''}">${r.packet_loss}%</td>
      <td><span class="status-badge" style="background: ${statusColorMap[getStatusText(r.latency_avg)] || '#888'}20; color: ${statusColorMap[getStatusText(r.latency_avg)] || '#888'}">${getStatusText(r.latency_avg)}</span></td>
    </tr>
  `).join('')

  // Build history summary for gateway mode
  const historyStats = report.data.length > 0 ? {
    samples: report.data.length,
    avgPacketLoss: (report.data.reduce((acc, d) => acc + (d.packet_loss || 0), 0) / report.data.length).toFixed(2),
    startTime: new Date(report.data[0].timestamp * 1000).toLocaleString(),
    endTime: new Date(report.data[report.data.length - 1].timestamp * 1000).toLocaleString(),
  } : null

  const html = `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Network Latency Report - ProxMenux Monitor</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { 
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; 
      background: #fff; 
      color: #1a1a1a; 
      line-height: 1.6;
      padding: 40px;
    }
    .container { max-width: 900px; margin: 0 auto; }
    
    /* Header */
    .header { 
      display: flex; 
      justify-content: space-between; 
      align-items: flex-start; 
      border-bottom: 3px solid #2563eb;
      padding-bottom: 20px;
      margin-bottom: 30px;
    }
    .header-left h1 { font-size: 24px; color: #1a1a1a; margin-bottom: 4px; }
    .header-left p { color: #666; font-size: 14px; }
    .header-right { text-align: right; font-size: 13px; color: #666; }
    .header-right .rid { font-family: monospace; color: #2563eb; margin-top: 8px; }
    
    /* Sections */
    .section { margin-bottom: 30px; }
    .section-title { 
      font-size: 16px; 
      font-weight: 600; 
      color: #2563eb; 
      border-bottom: 1px solid #e5e7eb;
      padding-bottom: 8px;
      margin-bottom: 16px;
    }
    
    /* Cards Grid */
    .grid-4 { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; }
    .grid-3 { display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; }
    .grid-2 { display: grid; grid-template-columns: repeat(2, 1fr); gap: 16px; }
    .card { 
      background: #f9fafb; 
      border: 1px solid #e5e7eb; 
      border-radius: 8px; 
      padding: 16px;
    }
    .card-label { font-size: 12px; color: #666; margin-bottom: 4px; text-transform: uppercase; letter-spacing: 0.5px; }
    .card-value { font-size: 20px; font-weight: 600; color: #1a1a1a; }
    .card-value.green { color: #22c55e; }
    .card-value.yellow { color: #f59e0b; }
    .card-value.red { color: #ef4444; }
    
    /* Status Badge */
    .status-badge {
      display: inline-block;
      padding: 4px 12px;
      border-radius: 20px;
      font-size: 13px;
      font-weight: 500;
    }
    .status-large {
      font-size: 18px;
      padding: 8px 20px;
    }
    
    /* Table */
    table { width: 100%; border-collapse: collapse; margin-top: 12px; }
    th, td { 
      padding: 10px 12px; 
      text-align: left; 
      border-bottom: 1px solid #e5e7eb;
      font-size: 13px;
    }
    th { 
      background: #f3f4f6; 
      font-weight: 600; 
      color: #374151;
      text-transform: uppercase;
      font-size: 11px;
      letter-spacing: 0.5px;
    }
    tr:hover { background: #f9fafb; }
    .text-red { color: #ef4444; }
    
    /* Info Box */
    .info-box {
      background: #eff6ff;
      border: 1px solid #bfdbfe;
      border-radius: 8px;
      padding: 16px;
      margin-top: 16px;
    }
    .info-box h4 { color: #1e40af; margin-bottom: 8px; font-size: 14px; }
    .info-box p { color: #1e40af; font-size: 13px; }
    
    /* Thresholds */
    .thresholds { margin-top: 20px; }
    .threshold-item { 
      display: flex; 
      align-items: center; 
      gap: 12px; 
      padding: 8px 0;
      border-bottom: 1px solid #f3f4f6;
    }
    .threshold-dot { width: 12px; height: 12px; border-radius: 50%; }
    .threshold-dot.green { background: #22c55e; }
    .threshold-dot.yellow { background: #f59e0b; }
    .threshold-dot.red { background: #ef4444; }
    
    /* Footer */
    .footer { 
      margin-top: 40px; 
      padding-top: 20px; 
      border-top: 1px solid #e5e7eb;
      text-align: center;
      color: #666;
      font-size: 12px;
    }
    
    @media print {
      body { padding: 20px; }
      .no-print { display: none; }
    }
  </style>
</head>
<body>
  <div class="container">
    <!-- Header -->
    <div class="header">
      <div class="header-left">
        <h1>Network Latency Report</h1>
        <p>ProxMenux Monitor - Network Performance Analysis</p>
      </div>
      <div class="header-right">
        <div><strong>Generated:</strong> ${now}</div>
        <div><strong>Target:</strong> ${report.targetLabel}</div>
        <div><strong>Mode:</strong> ${report.isRealtime ? 'Real-time Test' : 'Historical Analysis'}</div>
        <div class="rid">ID: PMXL-${Date.now().toString(36).toUpperCase()}</div>
      </div>
    </div>

    <!-- Executive Summary -->
    <div class="section">
      <div class="section-title">1. Executive Summary</div>
      <div class="grid-2">
        <div>
          <div class="card" style="text-align: center; padding: 24px;">
            <div class="card-label">Overall Status</div>
            <div style="margin: 12px 0;">
              <span class="status-badge status-large" style="background: ${statusColor}20; color: ${statusColor}">${statusText}</span>
            </div>
            <div class="card-label" style="margin-top: 12px;">Current Latency</div>
            <div class="card-value" style="color: ${statusColor}">
              ${report.isRealtime 
                ? (report.realtimeResults[report.realtimeResults.length - 1]?.latency_avg ?? 'N/A') + (report.realtimeResults[report.realtimeResults.length - 1]?.latency_avg ? ' ms' : '')
                : report.stats.current + ' ms'}
            </div>
          </div>
        </div>
        <div class="card">
          <div class="card-label">Analysis Summary</div>
          <p style="margin-top: 8px; font-size: 14px; color: #374151;">
            ${report.isRealtime 
              ? `This report contains ${report.realtimeResults.length} real-time latency test(s) performed against ${report.targetLabel}. ${
                  report.realtimeResults.length > 0 
                    ? `The average latency across all tests is ${(report.realtimeResults.reduce((acc, r) => acc + (r.latency_avg || 0), 0) / report.realtimeResults.length).toFixed(1)} ms.`
                    : ''
                }`
              : `This report analyzes ${report.data.length} latency samples collected over ${timeframeLabel.toLowerCase()} against the network gateway. The average latency during this period was ${report.stats.avg} ms with a minimum of ${report.stats.min} ms and maximum of ${report.stats.max} ms.`
            }
          </p>
          <div class="info-box" style="margin-top: 16px;">
            <h4>Performance Rating</h4>
            <p>${
              statusText === 'Excellent' ? 'Network latency is excellent. No action required.' :
              statusText === 'Good' ? 'Network latency is within acceptable parameters.' :
              statusText === 'Fair' ? 'Network latency is elevated. Consider investigating network congestion or routing issues.' :
              statusText === 'Poor' ? 'Network latency is critically high. Immediate investigation recommended.' :
              'Unable to determine network status.'
            }</p>
          </div>
        </div>
      </div>
    </div>

    <!-- Statistics -->
    <div class="section">
      <div class="section-title">2. Latency Statistics</div>
      <div class="grid-4">
        <div class="card">
          <div class="card-label">Current</div>
          <div class="card-value" style="color: ${statusColor}">
            ${report.isRealtime 
              ? (report.realtimeResults[report.realtimeResults.length - 1]?.latency_avg ?? 'N/A') + ' ms'
              : report.stats.current + ' ms'}
          </div>
        </div>
        <div class="card">
          <div class="card-label">Minimum</div>
          <div class="card-value green">
            ${report.isRealtime 
              ? (report.realtimeResults.length > 0 ? Math.min(...report.realtimeResults.map(r => r.latency_min || Infinity)).toFixed(1) : 'N/A') + ' ms'
              : report.stats.min + ' ms'}
          </div>
        </div>
        <div class="card">
          <div class="card-label">Average</div>
          <div class="card-value">
            ${report.isRealtime 
              ? (report.realtimeResults.length > 0 ? (report.realtimeResults.reduce((acc, r) => acc + (r.latency_avg || 0), 0) / report.realtimeResults.length).toFixed(1) : 'N/A') + ' ms'
              : report.stats.avg + ' ms'}
          </div>
        </div>
        <div class="card">
          <div class="card-label">Maximum</div>
          <div class="card-value red">
            ${report.isRealtime 
              ? (report.realtimeResults.length > 0 ? Math.max(...report.realtimeResults.map(r => r.latency_max || 0)).toFixed(1) : 'N/A') + ' ms'
              : report.stats.max + ' ms'}
          </div>
        </div>
      </div>
      ${!report.isRealtime && historyStats ? `
      <div class="grid-3" style="margin-top: 16px;">
        <div class="card">
          <div class="card-label">Sample Count</div>
          <div class="card-value">${historyStats.samples}</div>
        </div>
        <div class="card">
          <div class="card-label">Period Start</div>
          <div style="font-size: 14px; color: #374151; margin-top: 4px;">${historyStats.startTime}</div>
        </div>
        <div class="card">
          <div class="card-label">Period End</div>
          <div style="font-size: 14px; color: #374151; margin-top: 4px;">${historyStats.endTime}</div>
        </div>
      </div>
      ` : ''}
    </div>

    ${report.isRealtime && report.realtimeResults.length > 0 ? `
    <!-- Test Results -->
    <div class="section">
      <div class="section-title">3. Test Results</div>
      <table>
        <thead>
          <tr>
            <th>#</th>
            <th>Time</th>
            <th>Avg Latency</th>
            <th>Min</th>
            <th>Max</th>
            <th>Packet Loss</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          ${realtimeTableRows}
        </tbody>
      </table>
    </div>
    ` : ''}

    <!-- Reference Thresholds -->
    <div class="section">
      <div class="section-title">${report.isRealtime ? '4' : '3'}. Reference Thresholds</div>
      <div class="thresholds">
        <div class="threshold-item">
          <div class="threshold-dot green"></div>
          <div><strong>Excellent (< 50ms):</strong> Optimal network performance for all applications including real-time gaming and video calls.</div>
        </div>
        <div class="threshold-item">
          <div class="threshold-dot green"></div>
          <div><strong>Good (50-100ms):</strong> Acceptable latency for most applications. Minor impact on real-time interactions.</div>
        </div>
        <div class="threshold-item">
          <div class="threshold-dot yellow"></div>
          <div><strong>Fair (100-200ms):</strong> Noticeable delay in interactive applications. May affect VoIP and gaming quality.</div>
        </div>
        <div class="threshold-item">
          <div class="threshold-dot red"></div>
          <div><strong>Poor (> 200ms):</strong> Significant latency causing degraded user experience. Investigation recommended.</div>
        </div>
      </div>
    </div>

    <!-- Methodology -->
    <div class="section">
      <div class="section-title">${report.isRealtime ? '5' : '4'}. Methodology</div>
      <div class="card">
        <p style="font-size: 14px; color: #374151; margin-bottom: 12px;">
          <strong>Test Method:</strong> ICMP Echo Request (Ping)
        </p>
        <p style="font-size: 14px; color: #374151; margin-bottom: 12px;">
          <strong>Target:</strong> ${report.targetLabel} ${report.target === 'gateway' ? '(Default network gateway)' : `(${report.target === 'cloudflare' ? '1.1.1.1' : '8.8.8.8'})`}
        </p>
        <p style="font-size: 14px; color: #374151; margin-bottom: 12px;">
          <strong>Samples per Test:</strong> 3 consecutive pings
        </p>
        <p style="font-size: 14px; color: #374151;">
          <strong>Metrics Collected:</strong> Round-trip time (RTT) minimum, average, maximum, and packet loss percentage
        </p>
      </div>
    </div>

    <!-- Footer -->
    <div class="footer">
      <p>Generated by ProxMenux Monitor | Network Latency Analysis Report</p>
      <p style="margin-top: 4px;">This report is provided for informational purposes. Results may vary based on network conditions.</p>
    </div>
  </div>

  <script>
    window.onload = function() { window.print(); }
  </script>
</body>
</html>`

  const printWindow = window.open('', '_blank')
  if (printWindow) {
    printWindow.document.write(html)
    printWindow.document.close()
  }
}

export function LatencyDetailModal({ open, onOpenChange, currentLatency }: LatencyDetailModalProps) {
  const [timeframe, setTimeframe] = useState("hour")
  const [target, setTarget] = useState("gateway")
  const [data, setData] = useState<LatencyHistoryPoint[]>([])
  const [stats, setStats] = useState<LatencyStats>({ min: 0, max: 0, avg: 0, current: 0 })
  const [loading, setLoading] = useState(true)
  const [realtimeResults, setRealtimeResults] = useState<RealtimeResult[]>([])
  const [realtimeTesting, setRealtimeTesting] = useState(false)
  const isMobile = useIsMobile()

  const isRealtime = TARGET_OPTIONS.find(t => t.value === target)?.realtime ?? false

  // Fetch history for gateway
  useEffect(() => {
    if (open && target === "gateway") {
      fetchHistory()
    }
  }, [open, timeframe, target])

  // Auto-test when switching to realtime target
  useEffect(() => {
    if (open && isRealtime) {
      // Clear previous results and run initial test
      setRealtimeResults([])
      runRealtimeTest()
    }
  }, [open, target])

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
      // Silently fail
    } finally {
      setLoading(false)
    }
  }

  const runRealtimeTest = useCallback(async () => {
    if (realtimeTesting) return
    setRealtimeTesting(true)
    try {
      const result = await fetchApi<RealtimeResult>(`/api/network/latency/current?target=${target}`)
      if (result) {
        const newResult = { ...result, timestamp: Date.now() }
        setRealtimeResults(prev => [...prev.slice(-19), newResult]) // Keep last 20 results
      }
    } catch (err) {
      // Silently fail
    } finally {
      setRealtimeTesting(false)
    }
  }, [target, realtimeTesting])

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

  const formatRealtimeTime = (timestamp: number) => {
    return new Date(timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })
  }

  // Gateway view data
  const chartData = data.map((d) => ({
    ...d,
    time: formatTime(d.timestamp),
  }))

  // Realtime view data
  const realtimeChartData = realtimeResults.map(r => ({
    time: formatRealtimeTime(r.timestamp || Date.now()),
    value: r.latency_avg || 0,
    packet_loss: r.packet_loss,
  }))

  const lastRealtimeResult = realtimeResults[realtimeResults.length - 1]
  const realtimeLatency = lastRealtimeResult?.latency_avg ?? null

  const currentLat = isRealtime 
    ? realtimeLatency 
    : (currentLatency && currentLatency > 0 ? Math.round(currentLatency * 10) / 10 : stats.current)
  
  const currentStatus = getStatusInfo(currentLat)
  const chartColor = getStatusColor(currentLat || 0)

  const values = data.map((d) => d.value).filter(v => v !== null && v !== undefined)
  const yMin = 0
  const yMax = values.length > 0 ? Math.ceil(Math.max(...values) * 1.2) : 200

  const realtimeValues = realtimeResults.map(r => r.latency_avg).filter(v => v !== null) as number[]
  const realtimeYMax = realtimeValues.length > 0 ? Math.ceil(Math.max(...realtimeValues) * 1.2) : 200

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
                <SelectTrigger className="w-[160px] bg-card border-border">
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
              {!isRealtime && (
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
              )}
              {isRealtime && (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={runRealtimeTest}
                  disabled={realtimeTesting}
                  className="gap-2"
                >
                  <RefreshCw className={`h-4 w-4 ${realtimeTesting ? 'animate-spin' : ''}`} />
                  {realtimeTesting ? 'Testing...' : 'Test Again'}
                </Button>
              )}
              <Button
                variant="outline"
                size="sm"
                onClick={() => generateLatencyReport({
                  target,
                  targetLabel: TARGET_OPTIONS.find(t => t.value === target)?.label || target,
                  isRealtime,
                  stats,
                  realtimeResults,
                  data,
                  timeframe
                })}
                disabled={isRealtime ? realtimeResults.length === 0 : data.length === 0}
                className="gap-2"
              >
                <FileText className="h-4 w-4" />
                Report
              </Button>
            </div>
          </div>
        </DialogHeader>

        {/* Realtime mode indicator */}
        {isRealtime && (
          <div className="flex items-center gap-2 text-sm text-muted-foreground bg-muted/30 rounded-lg px-3 py-2">
            <Wifi className="h-4 w-4" />
            <span>Real-time test mode - Results are not stored. Click "Test Again" for new measurements.</span>
          </div>
        )}

        {/* Stats bar */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 sm:gap-3">
          <div className={`rounded-lg p-3 text-center border ${currentStatus.color}`}>
            <div className="text-xs opacity-80 mb-1">Current</div>
            <div className="text-lg font-bold">{currentLat !== null ? `${currentLat} ms` : '---'}</div>
          </div>
          {isRealtime ? (
            <>
              <div className="bg-muted/50 rounded-lg p-3 text-center">
                <div className="text-xs text-muted-foreground mb-1 flex items-center justify-center gap-1">
                  <TrendingDown className="h-3 w-3" /> Min
                </div>
                <div className="text-lg font-bold text-green-500">
                  {lastRealtimeResult?.latency_min !== null ? `${lastRealtimeResult?.latency_min} ms` : '---'}
                </div>
              </div>
              <div className="bg-muted/50 rounded-lg p-3 text-center">
                <div className="text-xs text-muted-foreground mb-1 flex items-center justify-center gap-1">
                  <TrendingUp className="h-3 w-3" /> Max
                </div>
                <div className="text-lg font-bold text-red-500">
                  {lastRealtimeResult?.latency_max !== null ? `${lastRealtimeResult?.latency_max} ms` : '---'}
                </div>
              </div>
              <div className="bg-muted/50 rounded-lg p-3 text-center">
                <div className="text-xs text-muted-foreground mb-1">Packet Loss</div>
                <div className={`text-lg font-bold ${(lastRealtimeResult?.packet_loss || 0) > 0 ? 'text-red-500' : 'text-foreground'}`}>
                  {lastRealtimeResult?.packet_loss !== undefined ? `${lastRealtimeResult.packet_loss}%` : '---'}
                </div>
              </div>
            </>
          ) : (
            <>
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
            </>
          )}
        </div>

        {/* Chart */}
        <div className="h-[300px] lg:h-[350px]">
          {isRealtime ? (
            // Realtime chart - shows test results from this session
            realtimeChartData.length === 0 ? (
              <div className="h-full flex items-center justify-center text-muted-foreground">
                <div className="text-center">
                  {realtimeTesting ? (
                    <>
                      <RefreshCw className="h-8 w-8 mx-auto mb-2 animate-spin opacity-50" />
                      <p>Running latency test...</p>
                    </>
                  ) : (
                    <>
                      <Activity className="h-8 w-8 mx-auto mb-2 opacity-50" />
                      <p>Click "Test Again" to run a latency test</p>
                    </>
                  )}
                </div>
              </div>
            ) : (
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={realtimeChartData} margin={{ top: 10, right: 10, left: 0, bottom: 0 }}>
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
                    domain={[0, realtimeYMax]}
                    stroke="currentColor"
                    className="text-foreground"
                    tick={{ fill: "currentColor", fontSize: isMobile ? 10 : 12 }}
                    tickFormatter={(v) => `${v}ms`}
                    width={isMobile ? 45 : 50}
                  />
                  <Tooltip content={<CustomTooltip />} />
                  <Line
                    type="monotone"
                    dataKey="value"
                    name="Latency"
                    stroke={chartColor}
                    strokeWidth={2}
                    dot={{ r: 4, fill: chartColor, stroke: "#fff", strokeWidth: 2 }}
                    activeDot={{ r: 6, fill: chartColor, stroke: "#fff", strokeWidth: 2 }}
                  />
                </LineChart>
              </ResponsiveContainer>
            )
          ) : (
            // Gateway historical chart
            loading ? (
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
            )
          )}
        </div>

        {/* Test history for realtime mode */}
        {isRealtime && realtimeResults.length > 0 && (
          <div className="text-xs text-muted-foreground text-center">
            {realtimeResults.length} test{realtimeResults.length > 1 ? 's' : ''} this session
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}
