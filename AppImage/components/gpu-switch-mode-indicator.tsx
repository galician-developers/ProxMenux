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

  // Compact version for GPU card
  if (compact) {
    return (
      <div 
        className={cn(
          "flex items-center gap-3",
          isEditing && "cursor-pointer",
          className
        )}
        onClick={handleClick}
      >
        <svg
          viewBox="0 0 140 40"
          className="h-8 w-32"
          xmlns="http://www.w3.org/2000/svg"
        >
          {/* GPU Chip Icon - LARGER and always colored */}
          <g transform="translate(0, 6)">
            {/* Chip body */}
            <rect
              x="2"
              y="4"
              width="22"
              height="18"
              rx="3"
              className={cn(
                "transition-all duration-300",
                isLxcActive 
                  ? "fill-blue-500/20 stroke-blue-500" 
                  : isVmActive 
                    ? "fill-purple-500/20 stroke-purple-500" 
                    : "fill-muted-foreground/20 stroke-muted-foreground"
              )}
              strokeWidth="1.5"
            />
            {/* Chip pins top */}
            <line x1="7" y1="1" x2="7" y2="4" className={cn(isLxcActive ? "stroke-blue-500" : isVmActive ? "stroke-purple-500" : "stroke-muted-foreground")} strokeWidth="1.5" />
            <line x1="13" y1="1" x2="13" y2="4" className={cn(isLxcActive ? "stroke-blue-500" : isVmActive ? "stroke-purple-500" : "stroke-muted-foreground")} strokeWidth="1.5" />
            <line x1="19" y1="1" x2="19" y2="4" className={cn(isLxcActive ? "stroke-blue-500" : isVmActive ? "stroke-purple-500" : "stroke-muted-foreground")} strokeWidth="1.5" />
            {/* Chip pins bottom */}
            <line x1="7" y1="22" x2="7" y2="25" className={cn(isLxcActive ? "stroke-blue-500" : isVmActive ? "stroke-purple-500" : "stroke-muted-foreground")} strokeWidth="1.5" />
            <line x1="13" y1="22" x2="13" y2="25" className={cn(isLxcActive ? "stroke-blue-500" : isVmActive ? "stroke-purple-500" : "stroke-muted-foreground")} strokeWidth="1.5" />
            <line x1="19" y1="22" x2="19" y2="25" className={cn(isLxcActive ? "stroke-blue-500" : isVmActive ? "stroke-purple-500" : "stroke-muted-foreground")} strokeWidth="1.5" />
            {/* GPU text */}
            <text 
              x="13" 
              y="16" 
              textAnchor="middle" 
              className={cn(
                "text-[7px] font-bold transition-all duration-300",
                isLxcActive ? "fill-blue-500" : isVmActive ? "fill-purple-500" : "fill-muted-foreground"
              )}
            >
              GPU
            </text>
          </g>

          {/* Connection line from GPU to switch */}
          <line
            x1="26"
            y1="20"
            x2="48"
            y2="20"
            className={cn(
              "transition-all duration-300",
              isLxcActive ? "stroke-blue-500/60" : isVmActive ? "stroke-purple-500/60" : "stroke-muted-foreground/40"
            )}
            strokeWidth="2"
          />

          {/* Switch node - central junction */}
          <circle
            cx="55"
            cy="20"
            r="6"
            className={cn(
              "transition-all duration-300",
              isEditing 
                ? "fill-amber-500/30 stroke-amber-500" 
                : isLxcActive 
                  ? "fill-blue-500/30 stroke-blue-500" 
                  : isVmActive 
                    ? "fill-purple-500/30 stroke-purple-500" 
                    : "fill-muted stroke-muted-foreground/50"
            )}
            strokeWidth="2"
          />
          
          {/* Animated dot inside switch */}
          <circle
            cx="55"
            cy="20"
            r="2.5"
            className={cn(
              "transition-all duration-300",
              isEditing 
                ? "fill-amber-500" 
                : isLxcActive 
                  ? "fill-blue-500" 
                  : isVmActive 
                    ? "fill-purple-500" 
                    : "fill-muted-foreground"
            )}
          />

          {/* LXC branch line */}
          <path
            d="M 61 17 L 80 8"
            fill="none"
            className={cn(
              "transition-all duration-300",
              isLxcActive ? "stroke-blue-500" : "stroke-muted-foreground/30"
            )}
            strokeWidth={isLxcActive ? "2.5" : "1.5"}
            strokeLinecap="round"
          />
          
          {/* VM branch line */}
          <path
            d="M 61 23 L 80 32"
            fill="none"
            className={cn(
              "transition-all duration-300",
              isVmActive ? "stroke-purple-500" : "stroke-muted-foreground/30"
            )}
            strokeWidth={isVmActive ? "2.5" : "1.5"}
            strokeLinecap="round"
          />

          {/* LXC Icon - Container box */}
          <g transform="translate(83, 0)">
            <rect
              x="0"
              y="0"
              width="18"
              height="14"
              rx="2"
              className={cn(
                "transition-all duration-300",
                isLxcActive ? "fill-blue-500/25 stroke-blue-500" : "fill-muted stroke-muted-foreground/30"
              )}
              strokeWidth="1.5"
            />
            {/* Container layers */}
            <line x1="0" y1="5" x2="18" y2="5" className={cn(isLxcActive ? "stroke-blue-500" : "stroke-muted-foreground/30")} strokeWidth="1" />
            <line x1="0" y1="9" x2="18" y2="9" className={cn(isLxcActive ? "stroke-blue-500" : "stroke-muted-foreground/30")} strokeWidth="1" />
            {/* Dots */}
            <circle cx="4" cy="2.5" r="1" className={cn(isLxcActive ? "fill-blue-500" : "fill-muted-foreground/30")} />
            <circle cx="4" cy="7" r="1" className={cn(isLxcActive ? "fill-blue-500" : "fill-muted-foreground/30")} />
            <circle cx="4" cy="11.5" r="1" className={cn(isLxcActive ? "fill-blue-500" : "fill-muted-foreground/30")} />
          </g>

          {/* VM Icon - Monitor */}
          <g transform="translate(83, 24)">
            <rect
              x="1"
              y="0"
              width="16"
              height="10"
              rx="1.5"
              className={cn(
                "transition-all duration-300",
                isVmActive ? "fill-purple-500/25 stroke-purple-500" : "fill-muted stroke-muted-foreground/30"
              )}
              strokeWidth="1.5"
            />
            {/* Screen shine */}
            <rect
              x="3"
              y="2"
              width="12"
              height="6"
              rx="0.5"
              className={cn(isVmActive ? "fill-purple-500/30" : "fill-muted-foreground/10")}
            />
            {/* Stand */}
            <line x1="9" y1="10" x2="9" y2="13" className={cn(isVmActive ? "stroke-purple-500" : "stroke-muted-foreground/30")} strokeWidth="1.5" />
            <line x1="5" y1="13" x2="13" y2="13" className={cn(isVmActive ? "stroke-purple-500" : "stroke-muted-foreground/30")} strokeWidth="1.5" />
          </g>

          {/* LXC Label */}
          <text
            x="115"
            y="10"
            textAnchor="start"
            className={cn(
              "text-[8px] font-bold transition-all duration-300",
              isLxcActive ? "fill-blue-500" : "fill-muted-foreground/40"
            )}
          >
            LXC
          </text>

          {/* VM Label */}
          <text
            x="115"
            y="35"
            textAnchor="start"
            className={cn(
              "text-[8px] font-bold transition-all duration-300",
              isVmActive ? "fill-purple-500" : "fill-muted-foreground/40"
            )}
          >
            VM
          </text>
        </svg>

        {/* Status description */}
        <div className="flex flex-col items-start gap-0.5 min-w-0 flex-1">
          <span
            className={cn(
              "text-xs font-medium transition-all duration-300",
              isLxcActive ? "text-blue-500" : isVmActive ? "text-purple-500" : "text-muted-foreground"
            )}
          >
            {isLxcActive 
              ? "Ready for LXC containers" 
              : isVmActive 
                ? "Ready for VM passthrough" 
                : "Mode unknown"}
          </span>
          <span className="text-[10px] text-muted-foreground">
            {isLxcActive 
              ? "Native driver active" 
              : isVmActive 
                ? "VFIO-PCI driver active" 
                : "No driver detected"}
          </span>
          {hasChanged && (
            <span className="text-[10px] text-amber-500 font-medium animate-pulse">
              Change pending...
            </span>
          )}
        </div>
      </div>
    )
  }

  // Full version (not used in current implementation but kept for flexibility)
  return (
    <div
      className={cn(
        "relative rounded-lg border p-4 transition-all duration-300",
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
          className="h-14 w-full max-w-[200px]"
          xmlns="http://www.w3.org/2000/svg"
        >
          {/* GPU Chip Icon - LARGE and colored */}
          <g transform="translate(0, 8)">
            <rect
              x="2"
              y="6"
              width="32"
              height="24"
              rx="4"
              className={cn(
                "transition-all duration-300",
                isLxcActive 
                  ? "fill-blue-500/20 stroke-blue-500" 
                  : isVmActive 
                    ? "fill-purple-500/20 stroke-purple-500" 
                    : "fill-muted-foreground/20 stroke-muted-foreground"
              )}
              strokeWidth="2"
            />
            {/* Chip pins top */}
            <line x1="9" y1="2" x2="9" y2="6" className={cn(isLxcActive ? "stroke-blue-500" : isVmActive ? "stroke-purple-500" : "stroke-muted-foreground")} strokeWidth="2" />
            <line x1="18" y1="2" x2="18" y2="6" className={cn(isLxcActive ? "stroke-blue-500" : isVmActive ? "stroke-purple-500" : "stroke-muted-foreground")} strokeWidth="2" />
            <line x1="27" y1="2" x2="27" y2="6" className={cn(isLxcActive ? "stroke-blue-500" : isVmActive ? "stroke-purple-500" : "stroke-muted-foreground")} strokeWidth="2" />
            {/* Chip pins bottom */}
            <line x1="9" y1="30" x2="9" y2="34" className={cn(isLxcActive ? "stroke-blue-500" : isVmActive ? "stroke-purple-500" : "stroke-muted-foreground")} strokeWidth="2" />
            <line x1="18" y1="30" x2="18" y2="34" className={cn(isLxcActive ? "stroke-blue-500" : isVmActive ? "stroke-purple-500" : "stroke-muted-foreground")} strokeWidth="2" />
            <line x1="27" y1="30" x2="27" y2="34" className={cn(isLxcActive ? "stroke-blue-500" : isVmActive ? "stroke-purple-500" : "stroke-muted-foreground")} strokeWidth="2" />
            <text 
              x="18" 
              y="22" 
              textAnchor="middle" 
              className={cn(
                "text-[10px] font-bold",
                isLxcActive ? "fill-blue-500" : isVmActive ? "fill-purple-500" : "fill-muted-foreground"
              )}
            >
              GPU
            </text>
          </g>

          {/* Main connection line */}
          <line
            x1="38"
            y1="26"
            x2="70"
            y2="26"
            className={cn(
              "transition-all duration-300",
              isLxcActive ? "stroke-blue-500/60" : isVmActive ? "stroke-purple-500/60" : "stroke-muted-foreground/40"
            )}
            strokeWidth="3"
          />

          {/* Switch node */}
          <circle
            cx="82"
            cy="26"
            r="10"
            className={cn(
              "transition-all duration-300",
              isEditing 
                ? "fill-amber-500/30 stroke-amber-500" 
                : isLxcActive 
                  ? "fill-blue-500/30 stroke-blue-500" 
                  : isVmActive 
                    ? "fill-purple-500/30 stroke-purple-500"
                    : "fill-muted stroke-muted-foreground/50"
            )}
            strokeWidth="2"
          />
          
          <circle
            cx="82"
            cy="26"
            r="4"
            className={cn(
              "transition-all duration-300",
              isEditing 
                ? "fill-amber-500" 
                : isLxcActive 
                  ? "fill-blue-500" 
                  : isVmActive 
                    ? "fill-purple-500"
                    : "fill-muted-foreground"
            )}
          />

          {/* LXC branch */}
          <path
            d="M 92 20 Q 110 10, 130 10"
            fill="none"
            className={cn(
              "transition-all duration-300",
              isLxcActive ? "stroke-blue-500" : "stroke-muted-foreground/30"
            )}
            strokeWidth={isLxcActive ? "3" : "2"}
            strokeLinecap="round"
          />

          {/* VM branch */}
          <path
            d="M 92 32 Q 110 42, 130 42"
            fill="none"
            className={cn(
              "transition-all duration-300",
              isVmActive ? "stroke-purple-500" : "stroke-muted-foreground/30"
            )}
            strokeWidth={isVmActive ? "3" : "2"}
            strokeLinecap="round"
          />

          {/* LXC Container icon */}
          <g transform="translate(135, 0)">
            <rect
              x="0"
              y="0"
              width="26"
              height="20"
              rx="3"
              className={cn(
                "transition-all duration-300",
                isLxcActive ? "fill-blue-500/25 stroke-blue-500" : "fill-muted stroke-muted-foreground/30"
              )}
              strokeWidth="2"
            />
            <line x1="0" y1="7" x2="26" y2="7" className={cn(isLxcActive ? "stroke-blue-500" : "stroke-muted-foreground/30")} strokeWidth="1.5" />
            <line x1="0" y1="13" x2="26" y2="13" className={cn(isLxcActive ? "stroke-blue-500" : "stroke-muted-foreground/30")} strokeWidth="1.5" />
            <circle cx="5" cy="3.5" r="1.5" className={cn(isLxcActive ? "fill-blue-500" : "fill-muted-foreground/30")} />
            <circle cx="5" cy="10" r="1.5" className={cn(isLxcActive ? "fill-blue-500" : "fill-muted-foreground/30")} />
            <circle cx="5" cy="16.5" r="1.5" className={cn(isLxcActive ? "fill-blue-500" : "fill-muted-foreground/30")} />
          </g>
          
          <text
            x="178"
            y="14"
            textAnchor="middle"
            className={cn(
              "text-[10px] font-bold",
              isLxcActive ? "fill-blue-500" : "fill-muted-foreground/40"
            )}
          >
            LXC
          </text>

          {/* VM Monitor icon */}
          <g transform="translate(135, 32)">
            <rect
              x="2"
              y="0"
              width="22"
              height="14"
              rx="2"
              className={cn(
                "transition-all duration-300",
                isVmActive ? "fill-purple-500/25 stroke-purple-500" : "fill-muted stroke-muted-foreground/30"
              )}
              strokeWidth="2"
            />
            <rect
              x="5"
              y="3"
              width="16"
              height="8"
              rx="1"
              className={cn(isVmActive ? "fill-purple-500/30" : "fill-muted-foreground/10")}
            />
            <line x1="13" y1="14" x2="13" y2="18" className={cn(isVmActive ? "stroke-purple-500" : "stroke-muted-foreground/30")} strokeWidth="2" />
            <line x1="7" y1="18" x2="19" y2="18" className={cn(isVmActive ? "stroke-purple-500" : "stroke-muted-foreground/30")} strokeWidth="2" />
          </g>
          
          <text
            x="178"
            y="48"
            textAnchor="middle"
            className={cn(
              "text-[10px] font-bold",
              isVmActive ? "fill-purple-500" : "fill-muted-foreground/40"
            )}
          >
            VM
          </text>
        </svg>

        {/* Status */}
        <div className="flex flex-col items-end gap-1">
          <span
            className={cn(
              "text-base font-semibold transition-all duration-300",
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
