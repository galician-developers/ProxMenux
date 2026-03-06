"use client"

import { useState, useEffect, useCallback, useRef } from "react"
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "./ui/dialog"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "./ui/select"
import { Button } from "./ui/button"
import { Badge } from "./ui/badge"
import { Activity, TrendingDown, TrendingUp, Minus, RefreshCw, Wifi, FileText, Square } from "lucide-react"
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

// Realtime test configuration
const REALTIME_TEST_DURATION = 120 // 2 minutes in seconds
const REALTIME_TEST_INTERVAL = 5 // 5 seconds between tests

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
  testDuration?: number
}

const generateLatencyReport = (report: ReportData) => {
  const now = new Date().toLocaleString()
  const logoUrl = `${window.location.origin}/images/proxmenux-logo.png`
  
  // Calculate stats for realtime results
  const realtimeStats = report.realtimeResults.length > 0 ? {
    min: Math.min(...report.realtimeResults.filter(r => r.latency_min !== null).map(r => r.latency_min!)),
    max: Math.max(...report.realtimeResults.filter(r => r.latency_max !== null).map(r => r.latency_max!)),
    avg: report.realtimeResults.reduce((acc, r) => acc + (r.latency_avg || 0), 0) / report.realtimeResults.length,
    current: report.realtimeResults[report.realtimeResults.length - 1]?.latency_avg ?? null,
    avgPacketLoss: report.realtimeResults.reduce((acc, r) => acc + (r.packet_loss || 0), 0) / report.realtimeResults.length,
  } : null

  const statusText = report.isRealtime 
    ? getStatusText(realtimeStats?.current ?? null)
    : getStatusText(report.stats.current)
  
  // Colors matching Lynis report
  const statusColorMap: Record<string, string> = {
    "Excellent": "#16a34a",
    "Good": "#16a34a", 
    "Fair": "#ca8a04",
    "Poor": "#dc2626",
    "N/A": "#64748b"
  }
  const statusColor = statusColorMap[statusText] || "#64748b"

  const timeframeLabel = TIMEFRAME_OPTIONS.find(t => t.value === report.timeframe)?.label || report.timeframe

  // Build test results table for realtime mode
  const realtimeTableRows = report.realtimeResults.map((r, i) => `
    <tr${r.packet_loss > 0 ? ' class="warn"' : ''}>
      <td>${i + 1}</td>
      <td>${new Date(r.timestamp || Date.now()).toLocaleTimeString()}</td>
      <td style="font-weight:600;color:${statusColorMap[getStatusText(r.latency_avg)] || '#64748b'}">${r.latency_avg !== null ? r.latency_avg + ' ms' : 'Failed'}</td>
      <td>${r.latency_min !== null ? r.latency_min + ' ms' : '-'}</td>
      <td>${r.latency_max !== null ? r.latency_max + ' ms' : '-'}</td>
      <td${r.packet_loss > 0 ? ' style="color:#dc2626;font-weight:600;"' : ''}>${r.packet_loss}%</td>
      <td><span class="f-tag" style="background:${statusColorMap[getStatusText(r.latency_avg)] || '#64748b'}15;color:${statusColorMap[getStatusText(r.latency_avg)] || '#64748b'}">${getStatusText(r.latency_avg)}</span></td>
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
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Network Latency Report - ${report.targetLabel}</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; color: #1a1a2e; background: #fff; font-size: 13px; line-height: 1.5; }

  @page { margin: 15mm 15mm 20mm 15mm; size: A4; }
  @media print {
    .no-print { display: none !important; }
    .page-break { page-break-before: always; }
    * { -webkit-print-color-adjust: exact !important; print-color-adjust: exact !important; }
    body { font-size: 11px; }
    .section { margin-bottom: 16px; }
    .rpt-header-left p, .rpt-header-right { color: #374151; }
    .rpt-header-right .rid { color: #4b5563; }
    .exec-text p { color: #374151; }
    .score-bar-labels { color: #4b5563; }
    .card-label { color: #4b5563; }
    .card-sub { color: #374151; }
    .chk-tbl th { color: #374151; }
    .rpt-footer { color: #4b5563; }
  }
  @media screen {
    body { max-width: 1000px; margin: 0 auto; padding: 24px 32px; padding-top: 64px; }
  }

  /* Top bar for screen only */
  .top-bar {
    position: fixed; top: 0; left: 0; right: 0; background: #0f172a; color: #e2e8f0;
    padding: 12px 24px; display: flex; align-items: center; justify-content: space-between; z-index: 100;
    font-size: 13px;
  }
  .top-bar button {
    background: #06b6d4; color: #fff; border: none; padding: 8px 20px; border-radius: 6px;
    font-size: 13px; font-weight: 600; cursor: pointer;
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
    display: flex; align-items: center; gap: 24px; padding: 20px;
    background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; margin-bottom: 16px;
  }
  .score-ring {
    width: 96px; height: 96px; border-radius: 50%; display: flex; flex-direction: column;
    align-items: center; justify-content: center; border: 4px solid; flex-shrink: 0;
  }
  .score-num { font-size: 28px; font-weight: 800; line-height: 1; }
  .score-unit { font-size: 12px; font-weight: 600; opacity: 0.8; }
  .score-lbl { font-size: 9px; font-weight: 700; letter-spacing: 0.1em; text-transform: uppercase; margin-top: 2px; }
  .exec-text { flex: 1; }
  .exec-text h3 { font-size: 16px; margin-bottom: 4px; }
  .exec-text p { font-size: 12px; color: #64748b; line-height: 1.5; }

  /* Score bar */
  .score-bar-wrap { margin: 10px 0 6px; }
  .score-bar-bg { height: 10px; background: #e2e8f0; border-radius: 5px; position: relative; overflow: hidden; }
  .score-bar-fill { height: 100%; border-radius: 5px; }
  .score-bar-labels { display: flex; justify-content: space-between; font-size: 9px; color: #94a3b8; margin-top: 3px; }

  /* Grids */
  .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-bottom: 8px; }
  .grid-3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 8px; margin-bottom: 8px; }
  .grid-4 { display: grid; grid-template-columns: 1fr 1fr 1fr 1fr; gap: 8px; margin-bottom: 8px; }
  .card { padding: 10px 12px; background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 6px; }
  .card-label { font-size: 10px; font-weight: 600; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 2px; }
  .card-value { font-size: 13px; font-weight: 600; color: #0f172a; }
  .card-c { text-align: center; }
  .card-c .card-value { font-size: 20px; font-weight: 800; }
  .card-c .card-label { margin-top: 3px; margin-bottom: 0; }
  .card-sub { font-size: 9px; color: #64748b; margin-top: 2px; }

  /* Tags */
  .f-tag { font-size: 9px; padding: 2px 6px; border-radius: 4px; font-weight: 600; }

  /* Tables */
  .chk-tbl { width: 100%; border-collapse: collapse; font-size: 11px; margin-bottom: 14px; }
  .chk-tbl th { text-align: left; padding: 6px 8px; font-size: 10px; color: #64748b; font-weight: 600; border-bottom: 1px solid #e2e8f0; background: #f1f5f9; }
  .chk-tbl td { padding: 5px 8px; border-bottom: 1px solid #f1f5f9; color: #1e293b; }
  .chk-tbl tr.warn { background: #fef2f2; }

  /* Thresholds */
  .threshold-item { 
    display: flex; align-items: center; gap: 10px; padding: 8px 12px;
    background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 4px; margin-bottom: 6px;
  }
  .threshold-dot { width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; }
  .threshold-item p { font-size: 11px; color: #374151; }
  .threshold-item strong { color: #0f172a; }

  /* Info box */
  .info-box {
    background: #ecfeff; border: 1px solid #a5f3fc; border-left: 4px solid #06b6d4;
    border-radius: 4px; padding: 12px 14px; margin-top: 12px;
  }
  .info-box h4 { font-size: 11px; font-weight: 700; color: #0891b2; margin-bottom: 4px; }
  .info-box p { font-size: 11px; color: #0e7490; }

  /* Footer */
  .rpt-footer {
    margin-top: 32px; padding-top: 12px; border-top: 1px solid #e2e8f0;
    display: flex; justify-content: space-between; font-size: 10px; color: #94a3b8;
  }
</style>
</head>
<body>

<script>
function pmxPrint(){
  try { window.print(); }
  catch(e) {
    var isMac = navigator.platform.toUpperCase().indexOf('MAC') >= 0;
    var el = document.getElementById('pmx-print-hint');
    if(el) el.textContent = isMac ? 'Use Cmd+P to save as PDF' : 'Use Ctrl+P to save as PDF';
  }
}
</script>
<div class="top-bar no-print">
  <div style="display:flex;align-items:center;gap:12px;">
    <strong>ProxMenux Network Latency Report</strong>
    <span id="pmx-print-hint" style="font-size:11px;opacity:0.7;">Review the report, then print or save as PDF</span>
  </div>
  <div style="display:flex;align-items:center;gap:8px;">
    <span style="font-size:11px;opacity:0.5;">⌘P / Ctrl+P</span>
    <button onclick="pmxPrint()">Print / Save as PDF</button>
  </div>
</div>

<!-- Header -->
<div class="rpt-header">
  <div class="rpt-header-left">
    <img src="${logoUrl}" alt="ProxMenux" onerror="this.style.display='none'" />
    <div>
      <h1>Network Latency Report</h1>
      <p>ProxMenux Monitor - Network Performance Analysis</p>
    </div>
  </div>
  <div class="rpt-header-right">
    <div><strong>Date:</strong> ${now}</div>
    <div><strong>Target:</strong> ${report.targetLabel}</div>
    <div><strong>Mode:</strong> ${report.isRealtime ? 'Real-time Test' : 'Historical Analysis'}</div>
    <div class="rid">ID: PMXL-${Date.now().toString(36).toUpperCase()}</div>
  </div>
</div>

<!-- 1. Executive Summary -->
<div class="section">
  <div class="section-title">1. Executive Summary</div>
  <div class="exec-box">
    <div class="score-ring" style="border-color:${statusColor};color:${statusColor};">
      <div class="score-num">${report.isRealtime ? (realtimeStats?.current?.toFixed(0) ?? 'N/A') : report.stats.current}</div>
      <div class="score-unit">ms</div>
      <div class="score-lbl">${statusText}</div>
    </div>
    <div class="exec-text">
      <h3>Network Latency Assessment${report.isRealtime ? ' (Real-time)' : ''}</h3>
      <p>
        ${report.isRealtime 
          ? `Real-time latency test to <strong>${report.targetLabel}</strong> with <strong>${report.realtimeResults.length} samples</strong> collected over ${report.testDuration ? Math.round(report.testDuration / 60) + ' minute(s)' : 'the test period'}. 
             Average latency: <strong style="color:${statusColor}">${realtimeStats?.avg?.toFixed(1) ?? 'N/A'} ms</strong>.
             ${realtimeStats && realtimeStats.avgPacketLoss > 0 ? `<span style="color:#dc2626">Average packet loss: ${realtimeStats.avgPacketLoss.toFixed(1)}%.</span>` : '<span style="color:#16a34a">No packet loss detected.</span>'}`
          : `Historical latency analysis to <strong>Gateway</strong> over <strong>${timeframeLabel.toLowerCase()}</strong>.
             <strong>${report.data.length} samples</strong> analyzed.
             Average latency: <strong style="color:${statusColor}">${report.stats.avg} ms</strong>.`
        }
      </p>
      <div class="score-bar-wrap">
        <div style="display:flex;justify-content:space-between;font-size:10px;margin-bottom:3px;">
          <span style="color:#64748b;">Min: ${report.isRealtime ? (realtimeStats?.min?.toFixed(1) ?? 'N/A') : report.stats.min} ms</span>
          <span style="color:#64748b;">Avg: ${report.isRealtime ? (realtimeStats?.avg?.toFixed(1) ?? 'N/A') : report.stats.avg} ms</span>
          <span style="color:${statusColor};font-weight:700;">Max: ${report.isRealtime ? (realtimeStats?.max?.toFixed(1) ?? 'N/A') : report.stats.max} ms</span>
        </div>
        <div class="score-bar-bg">
          <div class="score-bar-fill" style="width:${Math.min(100, ((report.isRealtime ? (realtimeStats?.avg ?? 0) : report.stats.avg) / 300) * 100)}%;background:${statusColor};"></div>
        </div>
        <div class="score-bar-labels"><span>0ms - Excellent</span><span>100ms - Fair</span><span>200ms - Poor</span><span>300ms+</span></div>
      </div>
    </div>
  </div>
</div>

<!-- 2. Statistics -->
<div class="section">
  <div class="section-title">2. Latency Statistics</div>
  <div class="grid-4">
    <div class="card card-c">
      <div class="card-value" style="color:${statusColor};">${report.isRealtime ? (realtimeStats?.current?.toFixed(1) ?? 'N/A') : report.stats.current}<span style="font-size:10px;color:#64748b;"> ms</span></div>
      <div class="card-label">Current</div>
    </div>
    <div class="card card-c">
      <div class="card-value" style="color:#16a34a;">${report.isRealtime ? (realtimeStats?.min?.toFixed(1) ?? 'N/A') : report.stats.min}<span style="font-size:10px;color:#64748b;"> ms</span></div>
      <div class="card-label">Minimum</div>
    </div>
    <div class="card card-c">
      <div class="card-value">${report.isRealtime ? (realtimeStats?.avg?.toFixed(1) ?? 'N/A') : report.stats.avg}<span style="font-size:10px;color:#64748b;"> ms</span></div>
      <div class="card-label">Average</div>
    </div>
    <div class="card card-c">
      <div class="card-value" style="color:#dc2626;">${report.isRealtime ? (realtimeStats?.max?.toFixed(1) ?? 'N/A') : report.stats.max}<span style="font-size:10px;color:#64748b;"> ms</span></div>
      <div class="card-label">Maximum</div>
    </div>
  </div>
  <div class="grid-3">
    <div class="card">
      <div class="card-label">Sample Count</div>
      <div class="card-value">${report.isRealtime ? report.realtimeResults.length : report.data.length}</div>
    </div>
    <div class="card">
      <div class="card-label">Packet Loss (Avg)</div>
      <div class="card-value" style="color:${(report.isRealtime ? (realtimeStats?.avgPacketLoss ?? 0) : parseFloat(historyStats?.avgPacketLoss ?? '0')) > 0 ? '#dc2626' : '#16a34a'};">
        ${report.isRealtime ? (realtimeStats?.avgPacketLoss?.toFixed(1) ?? '0') : (historyStats?.avgPacketLoss ?? '0')}%
      </div>
    </div>
    <div class="card">
      <div class="card-label">Test Period</div>
      <div class="card-value" style="font-size:11px;">
        ${report.isRealtime 
          ? (report.testDuration ? Math.round(report.testDuration / 60) + ' min' : 'Real-time')
          : timeframeLabel}
      </div>
    </div>
  </div>
</div>

${report.isRealtime && report.realtimeResults.length > 0 ? `
<!-- 3. Test Results -->
<div class="section">
  <div class="section-title">3. Detailed Test Results</div>
  <table class="chk-tbl">
    <thead>
      <tr>
        <th>#</th>
        <th>Time</th>
        <th>Latency (Avg)</th>
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
  <div class="threshold-item">
    <div class="threshold-dot" style="background:#16a34a;"></div>
    <p><strong>Excellent (&lt; 50ms):</strong> Optimal for real-time applications, gaming, and video calls.</p>
  </div>
  <div class="threshold-item">
    <div class="threshold-dot" style="background:#16a34a;"></div>
    <p><strong>Good (50-100ms):</strong> Acceptable for most applications with minimal impact.</p>
  </div>
  <div class="threshold-item">
    <div class="threshold-dot" style="background:#ca8a04;"></div>
    <p><strong>Fair (100-200ms):</strong> Noticeable delay. May affect VoIP and interactive applications.</p>
  </div>
  <div class="threshold-item">
    <div class="threshold-dot" style="background:#dc2626;"></div>
    <p><strong>Poor (&gt; 200ms):</strong> Significant latency. Investigation recommended.</p>
  </div>
</div>

<!-- Latency Chart -->
<div class="section">
  <div class="section-title">\${report.isRealtime ? '4' : '3'}. Latency Graph</div>
  <div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:16px;">
    \${(() => {
      const chartData = report.isRealtime 
        ? report.realtimeResults.map(r => r.latency_avg || 0)
        : report.data.map(d => d.value || 0);
      if (chartData.length < 2) return '<p style="text-align:center;color:#64748b;padding:20px;">Not enough data points for chart</p>';
      
      const minVal = Math.min(...chartData);
      const maxVal = Math.max(...chartData);
      const range = maxVal - minVal || 1;
      const width = 700;
      const height = 120;
      const padding = 30;
      
      const points = chartData.map((val, i) => {
        const x = padding + (i / (chartData.length - 1)) * (width - padding * 2);
        const y = height - padding - ((val - minVal) / range) * (height - padding * 2);
        return \`\${x},\${y}\`;
      }).join(' ');
      
      const areaPoints = \`\${padding},\${height - padding} \${points} \${width - padding},\${height - padding}\`;
      
      return \`
        <svg width="100%" viewBox="0 0 \${width} \${height}" style="display:block;">
          <defs>
            <linearGradient id="areaGrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stop-color="\${statusColor}" stop-opacity="0.3"/>
              <stop offset="100%" stop-color="\${statusColor}" stop-opacity="0.05"/>
            </linearGradient>
          </defs>
          <!-- Grid lines -->
          <line x1="\${padding}" y1="\${padding}" x2="\${padding}" y2="\${height - padding}" stroke="#e2e8f0" stroke-width="1"/>
          <line x1="\${padding}" y1="\${height - padding}" x2="\${width - padding}" y2="\${height - padding}" stroke="#e2e8f0" stroke-width="1"/>
          <line x1="\${padding}" y1="\${height / 2}" x2="\${width - padding}" y2="\${height / 2}" stroke="#e2e8f0" stroke-width="1" stroke-dasharray="4"/>
          <!-- Y-axis labels -->
          <text x="\${padding - 5}" y="\${padding + 4}" font-size="9" fill="#64748b" text-anchor="end">\${maxVal.toFixed(0)}ms</text>
          <text x="\${padding - 5}" y="\${height / 2 + 3}" font-size="9" fill="#64748b" text-anchor="end">\${((minVal + maxVal) / 2).toFixed(0)}ms</text>
          <text x="\${padding - 5}" y="\${height - padding + 4}" font-size="9" fill="#64748b" text-anchor="end">\${minVal.toFixed(0)}ms</text>
          <!-- Area fill -->
          <polygon points="\${areaPoints}" fill="url(#areaGrad)"/>
          <!-- Line -->
          <polyline points="\${points}" fill="none" stroke="\${statusColor}" stroke-width="2"/>
          <!-- Labels -->
          <text x="\${width / 2}" y="\${height - 5}" font-size="9" fill="#64748b" text-anchor="middle">\${chartData.length} samples</text>
        </svg>
      \`;
    })()}
  </div>
</div>

<!-- Reference Thresholds -->
<div class="section">
  <div class="section-title">\${report.isRealtime ? '5' : '4'}. Performance Thresholds</div>
  <div class="threshold-item">
    <div class="threshold-dot" style="background:#16a34a;"></div>
    <p><strong>Excellent (&lt; 50ms):</strong> Optimal for real-time applications, gaming, and video calls.</p>
  </div>
  <div class="threshold-item">
    <div class="threshold-dot" style="background:#16a34a;"></div>
    <p><strong>Good (50-100ms):</strong> Acceptable for most applications with minimal impact.</p>
  </div>
  <div class="threshold-item">
    <div class="threshold-dot" style="background:#ca8a04;"></div>
    <p><strong>Fair (100-200ms):</strong> Noticeable delay. May affect VoIP and interactive applications.</p>
  </div>
  <div class="threshold-item">
    <div class="threshold-dot" style="background:#dc2626;"></div>
    <p><strong>Poor (&gt; 200ms):</strong> Significant latency. Investigation recommended.</p>
  </div>
</div>

<!-- Methodology -->
<div class="section">
  <div class="section-title">\${report.isRealtime ? '6' : '5'}. Methodology</div>
  <div class="grid-2">
    <div class="card">
      <div class="card-label">Test Method</div>
      <div class="card-value" style="font-size:12px;">ICMP Echo Request (Ping)</div>
    </div>
    <div class="card">
      <div class="card-label">Samples per Test</div>
      <div class="card-value" style="font-size:12px;">3 consecutive pings</div>
    </div>
    <div class="card">
      <div class="card-label">Target</div>
      <div class="card-value" style="font-size:12px;">\${report.targetLabel}</div>
    </div>
    <div class="card">
      <div class="card-label">Target IP</div>
      <div class="card-value" style="font-size:12px;">\${report.target === 'gateway' ? 'Default Gateway' : report.target === 'cloudflare' ? '1.1.1.1' : '8.8.8.8'}</div>
    </div>
  </div>
  <div class="info-box">
    <h4>Performance Assessment</h4>
    <p>\${
      statusText === 'Excellent' ? 'Network latency is excellent. No action required.' :
      statusText === 'Good' ? 'Network latency is within acceptable parameters.' :
      statusText === 'Fair' ? 'Network latency is elevated. Consider investigating network congestion or routing issues.' :
      statusText === 'Poor' ? 'Network latency is critically high. Immediate investigation recommended.' :
      'Unable to determine network status.'
    }</p>
  </div>
</div>

<!-- Footer -->
<div class="rpt-footer">
  <div>
    <img src="\${logoUrl}" alt="ProxMenux" style="height:20px;vertical-align:middle;margin-right:8px;" onerror="this.style.display='none'" />
    ProxMenux Monitor - Network Performance Report
  </div>
  <div>Generated: \${now} | Report ID: PMXL-\${Date.now().toString(36).toUpperCase()}</div>
</div>
    <div class="card">
      <div class="card-label">Samples per Test</div>
      <div class="card-value" style="font-size:12px;">3 consecutive pings</div>
    </div>
    <div class="card">
      <div class="card-label">Target</div>
      <div class="card-value" style="font-size:12px;">${report.targetLabel}</div>
    </div>
    <div class="card">
      <div class="card-label">Target IP</div>
      <div class="card-value" style="font-size:12px;">${report.target === 'gateway' ? 'Default Gateway' : report.target === 'cloudflare' ? '1.1.1.1' : '8.8.8.8'}</div>
    </div>
  </div>
  <div class="info-box">
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

<!-- Footer -->
<div class="rpt-footer">
  <div>Generated by ProxMenux Monitor</div>
  <div>Network Latency Report | ${now}</div>
</div>

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
  const [testProgress, setTestProgress] = useState(0) // 0-100 percentage
  const [testStartTime, setTestStartTime] = useState<number | null>(null)
  const testIntervalRef = useRef<NodeJS.Timeout | null>(null)
  const isMobile = useIsMobile()

  const isRealtime = TARGET_OPTIONS.find(t => t.value === target)?.realtime ?? false

  // Cleanup on unmount or close
  useEffect(() => {
    if (!open) {
      stopRealtimeTest()
    }
    return () => {
      stopRealtimeTest()
    }
  }, [open])

  // Fetch history for gateway
  useEffect(() => {
    if (open && target === "gateway") {
      fetchHistory()
    }
  }, [open, timeframe, target])

  // Auto-start test when switching to realtime target
  useEffect(() => {
    if (open && isRealtime) {
      // Clear previous results and start new test
      setRealtimeResults([])
      startRealtimeTest()
    } else {
      stopRealtimeTest()
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

  const runSingleTest = useCallback(async () => {
    try {
      const result = await fetchApi<RealtimeResult>(`/api/network/latency/current?target=${target}`)
      if (result) {
        const resultWithTimestamp = { ...result, timestamp: Date.now() }
        setRealtimeResults(prev => [...prev, resultWithTimestamp])
      }
    } catch (err) {
      // Silently fail
    }
  }, [target])

  const startRealtimeTest = useCallback(() => {
    if (realtimeTesting) return
    
    setRealtimeTesting(true)
    setTestProgress(0)
    setTestStartTime(Date.now())
    
    // Run first test immediately
    runSingleTest()
    
    // Set up interval for subsequent tests
    const totalTests = REALTIME_TEST_DURATION / REALTIME_TEST_INTERVAL
    let testCount = 1
    
    testIntervalRef.current = setInterval(() => {
      testCount++
      const progress = Math.min(100, (testCount / totalTests) * 100)
      setTestProgress(progress)
      
      runSingleTest()
      
      // Stop after duration
      if (testCount >= totalTests) {
        stopRealtimeTest()
      }
    }, REALTIME_TEST_INTERVAL * 1000)
  }, [realtimeTesting, runSingleTest])

  const stopRealtimeTest = useCallback(() => {
    if (testIntervalRef.current) {
      clearInterval(testIntervalRef.current)
      testIntervalRef.current = null
    }
    setRealtimeTesting(false)
    setTestProgress(100)
  }, [])

  const restartRealtimeTest = useCallback(() => {
    // Don't clear results - add to existing data
    startRealtimeTest()
  }, [startRealtimeTest])

  // Format chart data
  const chartData = data.map(point => ({
    ...point,
    time: new Date(point.timestamp * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
  }))

  const realtimeChartData = realtimeResults.map((r, i) => ({
    time: new Date(r.timestamp || Date.now()).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }),
    value: r.latency_avg,
    packet_loss: r.packet_loss,
  }))

  // Calculate realtime stats
  const realtimeStats = realtimeResults.length > 0 ? {
    current: realtimeResults[realtimeResults.length - 1]?.latency_avg ?? 0,
    min: Math.min(...realtimeResults.filter(r => r.latency_min !== null).map(r => r.latency_min!)) || 0,
    max: Math.max(...realtimeResults.filter(r => r.latency_max !== null).map(r => r.latency_max!)) || 0,
    avg: realtimeResults.reduce((acc, r) => acc + (r.latency_avg || 0), 0) / realtimeResults.length,
    packetLoss: realtimeResults[realtimeResults.length - 1]?.packet_loss ?? 0,
  } : null

  const displayStats = isRealtime ? {
    current: realtimeStats?.current ?? 0,
    min: realtimeStats?.min ?? 0,
    max: realtimeStats?.max ?? 0,
    avg: Math.round((realtimeStats?.avg ?? 0) * 10) / 10,
  } : stats

  const statusInfo = getStatusInfo(displayStats.current)

  // Calculate test duration for report based on first and last result timestamps
  const testDuration = realtimeResults.length >= 2 
    ? Math.round(((realtimeResults[realtimeResults.length - 1].timestamp || Date.now()) - (realtimeResults[0].timestamp || Date.now())) / 1000)
    : realtimeResults.length === 1 
    ? 5 // Single sample = 5 seconds (one test)
    : 0

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-4xl max-h-[90vh] overflow-y-auto bg-background border-border">
        <DialogHeader>
          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
            <DialogTitle className="flex items-center gap-2 text-foreground">
              <Wifi className="h-5 w-5 text-blue-500" />
              Network Latency
            </DialogTitle>
            <div className="flex flex-wrap items-center gap-2">
              <Select value={target} onValueChange={setTarget}>
                <SelectTrigger className="w-[180px] h-8 text-xs">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {TARGET_OPTIONS.map(opt => (
                    <SelectItem key={opt.value} value={opt.value} className="text-xs">
                      {opt.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {!isRealtime && (
                <Select value={timeframe} onValueChange={setTimeframe}>
                  <SelectTrigger className="w-[100px] h-8 text-xs">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {TIMEFRAME_OPTIONS.map(opt => (
                      <SelectItem key={opt.value} value={opt.value} className="text-xs">
                        {opt.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              )}
              {isRealtime && (
                <>
                  {realtimeTesting ? (
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={stopRealtimeTest}
                      className="gap-2 text-red-500 border-red-500/30 hover:bg-red-500/10"
                    >
                      <Square className="h-3 w-3 fill-current" />
                      Stop
                    </Button>
                  ) : (
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={restartRealtimeTest}
                      className="gap-2"
                    >
                      <RefreshCw className="h-4 w-4" />
                      Test Again
                    </Button>
                  )}
                </>
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
                  timeframe,
                  testDuration: isRealtime ? testDuration : undefined,
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

        {/* Progress bar for realtime test */}
        {isRealtime && realtimeTesting && (
          <div className="mb-4">
            <div className="flex items-center justify-between text-xs text-muted-foreground mb-1">
              <span>Testing... {Math.round(testProgress)}%</span>
              <span>{Math.round((REALTIME_TEST_DURATION * (1 - testProgress / 100)))}s remaining</span>
            </div>
            <div className="h-1.5 bg-muted rounded-full overflow-hidden">
              <div 
                className="h-full bg-blue-500 transition-all duration-500 ease-out"
                style={{ width: `${testProgress}%` }}
              />
            </div>
          </div>
        )}

        {/* Stats Cards */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-4">
          <div className="bg-muted/30 rounded-lg p-3">
            <div className="text-xs text-muted-foreground mb-1">Current</div>
            <div className="flex items-baseline gap-1">
              <span className="text-xl font-bold" style={{ color: getStatusColor(displayStats.current || 0) }}>
                {displayStats.current || '-'}
              </span>
              <span className="text-xs text-muted-foreground">ms</span>
            </div>
          </div>
          <div className="bg-muted/30 rounded-lg p-3">
            <div className="text-xs text-muted-foreground mb-1 flex items-center gap-1">
              <TrendingDown className="h-3 w-3 text-green-500" /> Min
            </div>
            <div className="flex items-baseline gap-1">
              <span className="text-xl font-bold text-green-500">{displayStats.min || '-'}</span>
              <span className="text-xs text-muted-foreground">ms</span>
            </div>
          </div>
          <div className="bg-muted/30 rounded-lg p-3">
            <div className="text-xs text-muted-foreground mb-1 flex items-center gap-1">
              <Minus className="h-3 w-3" /> Avg
            </div>
            <div className="flex items-baseline gap-1">
              <span className="text-xl font-bold">{displayStats.avg || '-'}</span>
              <span className="text-xs text-muted-foreground">ms</span>
            </div>
          </div>
          <div className="bg-muted/30 rounded-lg p-3">
            <div className="text-xs text-muted-foreground mb-1 flex items-center gap-1">
              <TrendingUp className="h-3 w-3 text-red-500" /> Max
            </div>
            <div className="flex items-baseline gap-1">
              <span className="text-xl font-bold text-red-500">{displayStats.max || '-'}</span>
              <span className="text-xs text-muted-foreground">ms</span>
            </div>
          </div>
        </div>

        {/* Status Badge */}
        <div className="flex items-center justify-between mb-4">
          <Badge variant="outline" className={statusInfo.color}>
            {statusInfo.status}
          </Badge>
          {isRealtime && (
            <span className="text-xs text-muted-foreground">
              {realtimeResults.length} sample{realtimeResults.length !== 1 ? 's' : ''} collected
              {realtimeStats?.packetLoss ? ` | ${realtimeStats.packetLoss}% packet loss` : ''}
            </span>
          )}
        </div>

        {/* Chart */}
        <div className="h-[250px] sm:h-[300px] w-full">
          {isRealtime ? (
            realtimeChartData.length > 0 ? (
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={realtimeChartData}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#374151" opacity={0.3} />
                  <XAxis 
                    dataKey="time" 
                    stroke="#6b7280" 
                    fontSize={10}
                    tickLine={false}
                    interval="preserveStartEnd"
                  />
                  <YAxis 
                    stroke="#6b7280" 
                    fontSize={10}
                    tickLine={false}
                    domain={['dataMin - 5', 'dataMax + 10']}
                    tickFormatter={(v) => `${v}ms`}
                  />
                  <Tooltip content={<CustomTooltip />} />
                  <Line
                    type="monotone"
                    dataKey="value"
                    stroke="#3b82f6"
                    strokeWidth={2}
                    dot={{ fill: '#3b82f6', strokeWidth: 0, r: 3 }}
                    activeDot={{ r: 5, fill: '#3b82f6' }}
                  />
                </LineChart>
              </ResponsiveContainer>
            ) : (
              <div className="h-full flex flex-col items-center justify-center text-muted-foreground">
                <Activity className="h-12 w-12 mb-3 opacity-30" />
                <p className="text-sm">
                  {realtimeTesting ? 'Collecting data...' : 'No data yet. Click "Test Again" to start.'}
                </p>
              </div>
            )
          ) : loading ? (
            <div className="h-full flex items-center justify-center">
              <RefreshCw className="h-8 w-8 animate-spin text-muted-foreground" />
            </div>
          ) : chartData.length > 0 ? (
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={chartData}>
                <defs>
                  <linearGradient id="latencyGradient" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#3b82f6" stopOpacity={0.3} />
                    <stop offset="95%" stopColor="#3b82f6" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#374151" opacity={0.3} />
                <XAxis 
                  dataKey="time" 
                  stroke="#6b7280" 
                  fontSize={10}
                  tickLine={false}
                  interval="preserveStartEnd"
                />
                <YAxis 
                  stroke="#6b7280" 
                  fontSize={10}
                  tickLine={false}
                  domain={['dataMin - 5', 'dataMax + 10']}
                  tickFormatter={(v) => `${v}ms`}
                />
                <Tooltip content={<CustomTooltip />} />
                <Area
                  type="monotone"
                  dataKey="value"
                  stroke="#3b82f6"
                  strokeWidth={2}
                  fill="url(#latencyGradient)"
                />
              </AreaChart>
            </ResponsiveContainer>
          ) : (
            <div className="h-full flex flex-col items-center justify-center text-muted-foreground">
              <Activity className="h-12 w-12 mb-3 opacity-30" />
              <p className="text-sm">No latency data available for this period</p>
              <p className="text-xs mt-1">Data is collected every 60 seconds</p>
            </div>
          )}
        </div>

        {/* Info for realtime mode */}
        {isRealtime && (
          <div className="mt-4 p-3 bg-blue-500/10 border border-blue-500/20 rounded-lg">
            <p className="text-xs text-blue-400">
              <strong>Real-time Mode:</strong> Tests run for 2 minutes with readings every 5 seconds. 
              Click "Test Again" to add more samples. All data is included in the report.
            </p>
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}
