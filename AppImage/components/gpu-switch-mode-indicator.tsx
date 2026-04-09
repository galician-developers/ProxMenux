"use client"

import { cn } from "@/lib/utils"

interface GpuSwitchModeIndicatorProps {
  mode: "lxc" | "vm" | "unknown"
  isEditing?: boolean
  pendingMode?: "lxc" | "vm" | null
  onToggle?: (e: React.MouseEvent) => void
  className?: string
}

export function GpuSwitchModeIndicator({
  mode,
  isEditing = false,
  pendingMode = null,
  onToggle,
  className,
}: GpuSwitchModeIndicatorProps) {
  const displayMode = pendingMode ?? mode
  const isLxcActive = displayMode === "lxc"
  const isVmActive = displayMode === "vm"
  const hasChanged = pendingMode !== null && pendingMode !== mode

  // Colors
  const activeColor = isLxcActive ? "#3b82f6" : isVmActive ? "#a855f7" : "#6b7280"
  const inactiveColor = "#374151" // gray-700 for dark theme
  const lxcColor = isLxcActive ? "#3b82f6" : inactiveColor
  const vmColor = isVmActive ? "#a855f7" : inactiveColor

  const handleClick = (e: React.MouseEvent) => {
    // Only stop propagation and handle toggle when in editing mode
    if (isEditing) {
      e.stopPropagation()
      if (onToggle) {
        onToggle(e)
      }
    }
    // When not editing, let the click propagate to the card to open the modal
  }

  return (
    <div 
      className={cn(
        "flex items-center gap-6",
        isEditing && "cursor-pointer",
        className
      )}
      onClick={handleClick}
    >
      {/* Large SVG Diagram */}
      <svg
        viewBox="0 0 220 100"
        className="h-24 w-56 flex-shrink-0"
        xmlns="http://www.w3.org/2000/svg"
      >
        {/* GPU Chip - Large with "GPU" text */}
        <g transform="translate(0, 22)">
          {/* Main chip body */}
          <rect
            x="4"
            y="8"
            width="44"
            height="36"
            rx="6"
            fill={`${activeColor}20`}
            stroke={activeColor}
            strokeWidth="2.5"
            className="transition-all duration-300"
          />
          {/* Chip pins - top */}
          <line x1="14" y1="2" x2="14" y2="8" stroke={activeColor} strokeWidth="2.5" strokeLinecap="round" className="transition-all duration-300" />
          <line x1="26" y1="2" x2="26" y2="8" stroke={activeColor} strokeWidth="2.5" strokeLinecap="round" className="transition-all duration-300" />
          <line x1="38" y1="2" x2="38" y2="8" stroke={activeColor} strokeWidth="2.5" strokeLinecap="round" className="transition-all duration-300" />
          {/* Chip pins - bottom */}
          <line x1="14" y1="44" x2="14" y2="50" stroke={activeColor} strokeWidth="2.5" strokeLinecap="round" className="transition-all duration-300" />
          <line x1="26" y1="44" x2="26" y2="50" stroke={activeColor} strokeWidth="2.5" strokeLinecap="round" className="transition-all duration-300" />
          <line x1="38" y1="44" x2="38" y2="50" stroke={activeColor} strokeWidth="2.5" strokeLinecap="round" className="transition-all duration-300" />
          {/* GPU text */}
          <text 
            x="26" 
            y="32" 
            textAnchor="middle" 
            fill={activeColor}
            className="text-[14px] font-bold transition-all duration-300"
            style={{ fontFamily: 'system-ui, sans-serif' }}
          >
            GPU
          </text>
        </g>

        {/* Connection line from GPU to switch */}
        <line
          x1="52"
          y1="50"
          x2="78"
          y2="50"
          stroke={activeColor}
          strokeWidth="3"
          strokeLinecap="round"
          className="transition-all duration-300"
        />

        {/* Central Switch Node - Large circle with inner dot */}
        <circle
          cx="95"
          cy="50"
          r="14"
          fill={isEditing ? "#f59e0b20" : `${activeColor}20`}
          stroke={isEditing ? "#f59e0b" : activeColor}
          strokeWidth="3"
          className="transition-all duration-300"
        />
        <circle
          cx="95"
          cy="50"
          r="6"
          fill={isEditing ? "#f59e0b" : activeColor}
          className="transition-all duration-300"
        />

        {/* LXC Branch Line - going up-right */}
        <path
          d="M 109 42 L 135 20"
          fill="none"
          stroke={lxcColor}
          strokeWidth={isLxcActive ? "3.5" : "2"}
          strokeLinecap="round"
          className="transition-all duration-300"
        />

        {/* VM Branch Line - going down-right */}
        <path
          d="M 109 58 L 135 80"
          fill="none"
          stroke={vmColor}
          strokeWidth={isVmActive ? "3.5" : "2"}
          strokeLinecap="round"
          className="transition-all duration-300"
        />

        {/* LXC Container Icon - Server/Stack icon */}
        <g transform="translate(138, 2)">
          {/* Container box */}
          <rect
            x="0"
            y="0"
            width="32"
            height="28"
            rx="4"
            fill={isLxcActive ? `${lxcColor}25` : "transparent"}
            stroke={lxcColor}
            strokeWidth={isLxcActive ? "2.5" : "1.5"}
            className="transition-all duration-300"
          />
          {/* Container layers/lines */}
          <line x1="0" y1="10" x2="32" y2="10" stroke={lxcColor} strokeWidth={isLxcActive ? "1.5" : "1"} className="transition-all duration-300" />
          <line x1="0" y1="19" x2="32" y2="19" stroke={lxcColor} strokeWidth={isLxcActive ? "1.5" : "1"} className="transition-all duration-300" />
          {/* Status dots */}
          <circle cx="7" cy="5" r="2" fill={lxcColor} className="transition-all duration-300" />
          <circle cx="7" cy="14.5" r="2" fill={lxcColor} className="transition-all duration-300" />
          <circle cx="7" cy="23.5" r="2" fill={lxcColor} className="transition-all duration-300" />
        </g>

        {/* LXC Label */}
        <text
          x="188"
          y="22"
          textAnchor="start"
          fill={lxcColor}
          className={cn(
            "transition-all duration-300",
            isLxcActive ? "text-[14px] font-bold" : "text-[12px] font-medium"
          )}
          style={{ fontFamily: 'system-ui, sans-serif' }}
        >
          LXC
        </text>

        {/* VM Monitor Icon */}
        <g transform="translate(138, 65)">
          {/* Monitor screen */}
          <rect
            x="2"
            y="0"
            width="28"
            height="18"
            rx="3"
            fill={isVmActive ? `${vmColor}25` : "transparent"}
            stroke={vmColor}
            strokeWidth={isVmActive ? "2.5" : "1.5"}
            className="transition-all duration-300"
          />
          {/* Screen inner/shine */}
          <rect
            x="5"
            y="3"
            width="22"
            height="12"
            rx="1"
            fill={isVmActive ? `${vmColor}30` : `${vmColor}10`}
            className="transition-all duration-300"
          />
          {/* Monitor stand */}
          <line x1="16" y1="18" x2="16" y2="24" stroke={vmColor} strokeWidth={isVmActive ? "2.5" : "1.5"} strokeLinecap="round" className="transition-all duration-300" />
          {/* Monitor base */}
          <line x1="8" y1="24" x2="24" y2="24" stroke={vmColor} strokeWidth={isVmActive ? "2.5" : "1.5"} strokeLinecap="round" className="transition-all duration-300" />
        </g>

        {/* VM Label */}
        <text
          x="188"
          y="84"
          textAnchor="start"
          fill={vmColor}
          className={cn(
            "transition-all duration-300",
            isVmActive ? "text-[14px] font-bold" : "text-[12px] font-medium"
          )}
          style={{ fontFamily: 'system-ui, sans-serif' }}
        >
          VM
        </text>
      </svg>

      {/* Status Text - Large like GPU name */}
      <div className="flex flex-col gap-1 min-w-0 flex-1">
        <span
          className={cn(
            "text-base font-semibold transition-all duration-300",
            isLxcActive ? "text-blue-500" : isVmActive ? "text-purple-500" : "text-muted-foreground"
          )}
        >
          {isLxcActive 
            ? "Ready for LXC containers" 
            : isVmActive 
              ? "Ready for VM passthrough" 
              : "Mode unknown"}
        </span>
        <span className="text-sm text-muted-foreground">
          {isLxcActive 
            ? "Native driver active" 
            : isVmActive 
              ? "VFIO-PCI driver active" 
              : "No driver detected"}
        </span>
        {hasChanged && (
          <span className="text-sm text-amber-500 font-medium animate-pulse">
            Change pending...
          </span>
        )}
      </div>
    </div>
  )
}
