"use client"

import { cn } from "@/lib/utils"

interface GpuSwitchModeIndicatorProps {
  mode: "lxc" | "vm" | "unknown"
  isEditing?: boolean
  pendingMode?: "lxc" | "vm" | null
  onToggle?: (e: React.MouseEvent) => void
  className?: string
  compact?: boolean
}

export function GpuSwitchModeIndicator({
  mode,
  isEditing = false,
  pendingMode = null,
  onToggle,
  className,
  compact = false,
}: GpuSwitchModeIndicatorProps) {
  const displayMode = pendingMode ?? mode
  const isLxcActive = displayMode === "lxc"
  const isVmActive = displayMode === "vm"
  const hasChanged = pendingMode !== null && pendingMode !== mode

  const handleClick = (e: React.MouseEvent) => {
    e.stopPropagation() // Prevent card click propagation
    if (isEditing && onToggle) {
      onToggle(e)
    }
  }

  if (compact) {
    return (
      <div 
        className={cn(
          "flex items-center gap-2",
          isEditing && "cursor-pointer hover:opacity-80",
          className
        )}
        onClick={handleClick}
      >
        <svg
          viewBox="0 0 120 32"
          className="h-6 w-24"
          xmlns="http://www.w3.org/2000/svg"
        >
          {/* GPU Chip Icon */}
          <g transform="translate(2, 4)">
            <rect
              x="2"
              y="6"
              width="16"
              height="12"
              rx="2"
              className="fill-muted-foreground/30 stroke-muted-foreground"
              strokeWidth="1"
            />
            {/* Chip pins */}
            <line x1="5" y1="4" x2="5" y2="6" className="stroke-muted-foreground" strokeWidth="1" />
            <line x1="10" y1="4" x2="10" y2="6" className="stroke-muted-foreground" strokeWidth="1" />
            <line x1="15" y1="4" x2="15" y2="6" className="stroke-muted-foreground" strokeWidth="1" />
            <line x1="5" y1="18" x2="5" y2="20" className="stroke-muted-foreground" strokeWidth="1" />
            <line x1="10" y1="18" x2="10" y2="20" className="stroke-muted-foreground" strokeWidth="1" />
            <line x1="15" y1="18" x2="15" y2="20" className="stroke-muted-foreground" strokeWidth="1" />
            <text x="10" y="14" textAnchor="middle" className="fill-muted-foreground text-[6px] font-bold">
              GPU
            </text>
          </g>

          {/* Connection lines from GPU */}
          <g className="transition-all duration-300">
            {/* Main line from GPU */}
            <line
              x1="22"
              y1="16"
              x2="45"
              y2="16"
              className="stroke-muted-foreground/50"
              strokeWidth="2"
            />
            
            {/* Switch circle */}
            <circle
              cx="52"
              cy="16"
              r="6"
              className={cn(
                "transition-all duration-300",
                isEditing ? "fill-amber-500/20 stroke-amber-500" : "fill-muted stroke-muted-foreground/50"
              )}
              strokeWidth="1.5"
            />

            {/* LXC branch */}
            <line
              x1="58"
              y1="13"
              x2="75"
              y2="8"
              className={cn(
                "transition-all duration-300",
                isLxcActive ? "stroke-blue-500" : "stroke-muted-foreground/30"
              )}
              strokeWidth={isLxcActive ? "2.5" : "1.5"}
            />
            
            {/* VM branch */}
            <line
              x1="58"
              y1="19"
              x2="75"
              y2="24"
              className={cn(
                "transition-all duration-300",
                isVmActive ? "stroke-purple-500" : "stroke-muted-foreground/30"
              )}
              strokeWidth={isVmActive ? "2.5" : "1.5"}
            />
          </g>

          {/* LXC Icon - Container */}
          <g transform="translate(78, 2)">
            <rect
              x="0"
              y="0"
              width="14"
              height="12"
              rx="1.5"
              className={cn(
                "transition-all duration-300",
                isLxcActive ? "fill-blue-500/20 stroke-blue-500" : "fill-muted stroke-muted-foreground/40"
              )}
              strokeWidth="1.5"
            />
            <line
              x1="0"
              y1="4"
              x2="14"
              y2="4"
              className={cn(
                "transition-all duration-300",
                isLxcActive ? "stroke-blue-500" : "stroke-muted-foreground/40"
              )}
              strokeWidth="1"
            />
            <line
              x1="0"
              y1="8"
              x2="14"
              y2="8"
              className={cn(
                "transition-all duration-300",
                isLxcActive ? "stroke-blue-500" : "stroke-muted-foreground/40"
              )}
              strokeWidth="1"
            />
            <text
              x="7"
              y="21"
              textAnchor="middle"
              className={cn(
                "text-[7px] font-semibold transition-all duration-300",
                isLxcActive ? "fill-blue-500" : "fill-muted-foreground/50"
              )}
            >
              LXC
            </text>
          </g>

          {/* VM Icon - Monitor/PC */}
          <g transform="translate(78, 18)">
            <rect
              x="1"
              y="0"
              width="12"
              height="8"
              rx="1"
              className={cn(
                "transition-all duration-300",
                isVmActive ? "fill-purple-500/20 stroke-purple-500" : "fill-muted stroke-muted-foreground/40"
              )}
              strokeWidth="1.5"
            />
            <line
              x1="7"
              y1="8"
              x2="7"
              y2="10"
              className={cn(
                "transition-all duration-300",
                isVmActive ? "stroke-purple-500" : "stroke-muted-foreground/40"
              )}
              strokeWidth="1.5"
            />
            <line
              x1="3"
              y1="10"
              x2="11"
              y2="10"
              className={cn(
                "transition-all duration-300",
                isVmActive ? "stroke-purple-500" : "stroke-muted-foreground/40"
              )}
              strokeWidth="1.5"
            />
          </g>
        </svg>

        {/* Status text */}
        <span
          className={cn(
            "text-xs font-medium transition-all duration-300",
            isLxcActive ? "text-blue-500" : isVmActive ? "text-purple-500" : "text-muted-foreground"
          )}
        >
          {isLxcActive ? "LXC" : isVmActive ? "VM" : "N/A"}
        </span>

        {hasChanged && (
          <span className="text-xs text-amber-500 font-medium animate-pulse">
            (pending)
          </span>
        )}
      </div>
    )
  }

  return (
    <div
      className={cn(
        "relative rounded-lg border p-3 transition-all duration-300",
        isEditing
          ? "border-amber-500/50 bg-amber-500/5"
          : "border-border/50 bg-muted/30",
        isEditing && onToggle && "cursor-pointer hover:bg-amber-500/10",
        className
      )}
      onClick={handleClick}
    >
      <div className="flex items-center justify-between gap-4">
        <svg
          viewBox="0 0 200 60"
          className="h-12 w-full max-w-[180px]"
          xmlns="http://www.w3.org/2000/svg"
        >
          {/* GPU Chip Icon */}
          <g transform="translate(5, 12)">
            <rect
              x="0"
              y="8"
              width="28"
              height="20"
              rx="3"
              className="fill-muted-foreground/20 stroke-muted-foreground"
              strokeWidth="1.5"
            />
            {/* Chip pins top */}
            <line x1="6" y1="4" x2="6" y2="8" className="stroke-muted-foreground" strokeWidth="1.5" />
            <line x1="14" y1="4" x2="14" y2="8" className="stroke-muted-foreground" strokeWidth="1.5" />
            <line x1="22" y1="4" x2="22" y2="8" className="stroke-muted-foreground" strokeWidth="1.5" />
            {/* Chip pins bottom */}
            <line x1="6" y1="28" x2="6" y2="32" className="stroke-muted-foreground" strokeWidth="1.5" />
            <line x1="14" y1="28" x2="14" y2="32" className="stroke-muted-foreground" strokeWidth="1.5" />
            <line x1="22" y1="28" x2="22" y2="32" className="stroke-muted-foreground" strokeWidth="1.5" />
            <text x="14" y="21" textAnchor="middle" className="fill-muted-foreground text-[8px] font-bold">
              GPU
            </text>
          </g>

          {/* Connection lines */}
          <g className="transition-all duration-300">
            {/* Main line from GPU to switch */}
            <line
              x1="38"
              y1="30"
              x2="70"
              y2="30"
              className="stroke-muted-foreground/50"
              strokeWidth="2.5"
            />

            {/* Switch circle */}
            <circle
              cx="82"
              cy="30"
              r="10"
              className={cn(
                "transition-all duration-300",
                isEditing ? "fill-amber-500/30 stroke-amber-500" : "fill-muted stroke-muted-foreground/50"
              )}
              strokeWidth="2"
            />
            
            {/* Switch indicator inside circle */}
            <circle
              cx={isLxcActive ? 78 : 86}
              cy="30"
              r="4"
              className={cn(
                "transition-all duration-500",
                isLxcActive ? "fill-blue-500" : "fill-purple-500"
              )}
            />

            {/* LXC branch - top */}
            <path
              d="M 92 24 Q 105 15, 125 15"
              fill="none"
              className={cn(
                "transition-all duration-300",
                isLxcActive ? "stroke-blue-500" : "stroke-muted-foreground/30"
              )}
              strokeWidth={isLxcActive ? "3" : "2"}
            />
            
            {/* Active glow for LXC */}
            {isLxcActive && (
              <path
                d="M 92 24 Q 105 15, 125 15"
                fill="none"
                className="stroke-blue-500/30"
                strokeWidth="6"
              />
            )}

            {/* VM branch - bottom */}
            <path
              d="M 92 36 Q 105 45, 125 45"
              fill="none"
              className={cn(
                "transition-all duration-300",
                isVmActive ? "stroke-purple-500" : "stroke-muted-foreground/30"
              )}
              strokeWidth={isVmActive ? "3" : "2"}
            />
            
            {/* Active glow for VM */}
            {isVmActive && (
              <path
                d="M 92 36 Q 105 45, 125 45"
                fill="none"
                className="stroke-purple-500/30"
                strokeWidth="6"
              />
            )}
          </g>

          {/* LXC Icon - Container with layers */}
          <g transform="translate(130, 5)">
            <rect
              x="0"
              y="0"
              width="24"
              height="20"
              rx="2"
              className={cn(
                "transition-all duration-300",
                isLxcActive ? "fill-blue-500/20 stroke-blue-500" : "fill-muted stroke-muted-foreground/40"
              )}
              strokeWidth="2"
            />
            {/* Container layers */}
            <line
              x1="0"
              y1="7"
              x2="24"
              y2="7"
              className={cn(
                "transition-all duration-300",
                isLxcActive ? "stroke-blue-500" : "stroke-muted-foreground/40"
              )}
              strokeWidth="1.5"
            />
            <line
              x1="0"
              y1="13"
              x2="24"
              y2="13"
              className={cn(
                "transition-all duration-300",
                isLxcActive ? "stroke-blue-500" : "stroke-muted-foreground/40"
              )}
              strokeWidth="1.5"
            />
            {/* Small dots on layers */}
            <circle cx="5" cy="3.5" r="1.5" className={cn(isLxcActive ? "fill-blue-500" : "fill-muted-foreground/40")} />
            <circle cx="5" cy="10" r="1.5" className={cn(isLxcActive ? "fill-blue-500" : "fill-muted-foreground/40")} />
            <circle cx="5" cy="16.5" r="1.5" className={cn(isLxcActive ? "fill-blue-500" : "fill-muted-foreground/40")} />
          </g>
          
          {/* LXC label */}
          <text
            x="167"
            y="18"
            textAnchor="middle"
            className={cn(
              "text-[9px] font-bold transition-all duration-300",
              isLxcActive ? "fill-blue-500" : "fill-muted-foreground/50"
            )}
          >
            LXC
          </text>

          {/* VM Icon - Monitor/Desktop */}
          <g transform="translate(130, 35)">
            <rect
              x="2"
              y="0"
              width="20"
              height="14"
              rx="2"
              className={cn(
                "transition-all duration-300",
                isVmActive ? "fill-purple-500/20 stroke-purple-500" : "fill-muted stroke-muted-foreground/40"
              )}
              strokeWidth="2"
            />
            {/* Screen content */}
            <rect
              x="5"
              y="3"
              width="14"
              height="8"
              rx="1"
              className={cn(
                "transition-all duration-300",
                isVmActive ? "fill-purple-500/30" : "fill-muted-foreground/20"
              )}
            />
            {/* Stand */}
            <line
              x1="12"
              y1="14"
              x2="12"
              y2="18"
              className={cn(
                "transition-all duration-300",
                isVmActive ? "stroke-purple-500" : "stroke-muted-foreground/40"
              )}
              strokeWidth="2"
            />
            <line
              x1="6"
              y1="18"
              x2="18"
              y2="18"
              className={cn(
                "transition-all duration-300",
                isVmActive ? "stroke-purple-500" : "stroke-muted-foreground/40"
              )}
              strokeWidth="2"
            />
          </g>
          
          {/* VM label */}
          <text
            x="167"
            y="50"
            textAnchor="middle"
            className={cn(
              "text-[9px] font-bold transition-all duration-300",
              isVmActive ? "fill-purple-500" : "fill-muted-foreground/50"
            )}
          >
            VM
          </text>
        </svg>

        {/* Status text and edit hint */}
        <div className="flex flex-col items-end gap-1">
          <span
            className={cn(
              "text-sm font-semibold transition-all duration-300",
              isLxcActive ? "text-blue-500" : isVmActive ? "text-purple-500" : "text-muted-foreground"
            )}
          >
            {isLxcActive ? "LXC Mode" : isVmActive ? "VM Mode" : "Unknown"}
          </span>
          <span className="text-xs text-muted-foreground">
            {isLxcActive ? "Native Driver" : isVmActive ? "VFIO Passthrough" : ""}
          </span>
          {isEditing && (
            <span className="text-xs text-amber-500 font-medium">
              Click to toggle
            </span>
          )}
          {hasChanged && (
            <span className="text-xs text-amber-500 font-medium animate-pulse">
              Change pending
            </span>
          )}
        </div>
      </div>
    </div>
  )
}
