"use client"

import { useState } from "react"

const CHANNELS = [
  { key: "telegram", label: "Telegram", icon: "/icons/telegram.svg", color: "blue", switchOn: "bg-blue-600" },
  { key: "gotify", label: "Gotify", icon: "/icons/gotify.svg", color: "green", switchOn: "bg-green-600" },
  { key: "discord", label: "Discord", icon: "/icons/discord.svg", color: "indigo", switchOn: "bg-indigo-600" },
  { key: "email", label: "Email", icon: "/icons/mail.svg", color: "amber", switchOn: "bg-amber-600" },
]

const SELECTED_BORDER = {
  blue: "border-blue-500/60 bg-blue-500/10",
  green: "border-green-500/60 bg-green-500/10",
  indigo: "border-indigo-500/60 bg-indigo-500/10",
  amber: "border-amber-500/60 bg-amber-500/10",
}

interface ChannelGridProps {
  enabledChannels: { telegram: boolean; gotify: boolean; discord: boolean; email: boolean }
  onToggle: (channel: string, enabled: boolean) => void
  selectedChannel: string | null
  onSelect: (channel: string | null) => void
}

export function ChannelGrid({ enabledChannels, onToggle, selectedChannel, onSelect }: ChannelGridProps) {
  return (
    <div className="grid grid-cols-4 gap-3">
      {CHANNELS.map(ch => {
        const isEnabled = enabledChannels[ch.key as keyof typeof enabledChannels] || false
        const isSelected = selectedChannel === ch.key
        const selStyle = SELECTED_BORDER[ch.color as keyof typeof SELECTED_BORDER]

        return (
          <button
            key={ch.key}
            type="button"
            onClick={() => onSelect(isSelected ? null : ch.key)}
            className={
              "group relative flex flex-col items-center justify-center gap-2 rounded-lg border p-4 transition-all cursor-pointer " +
              (isSelected
                ? selStyle + " ring-1 ring-offset-0"
                : isEnabled
                  ? "border-border/60 bg-muted/30 hover:bg-muted/40"
                  : "border-border/30 bg-muted/10 hover:border-border/50 hover:bg-muted/20")
            }
          >
            {/* Status dot */}
            {isEnabled && (
              <span className={"absolute top-2 right-2 h-1.5 w-1.5 rounded-full " + ch.switchOn} />
            )}

            {/* Logo */}
            <img
              src={ch.icon}
              alt={ch.label}
              className={
                "h-7 w-7 transition-opacity " +
                (isEnabled || isSelected ? "opacity-100" : "opacity-30 group-hover:opacity-70")
              }
            />

            {/* Label */}
            <span
              className={
                "text-[11px] font-medium transition-colors " +
                (isEnabled || isSelected ? "text-foreground" : "text-muted-foreground/60")
              }
            >
              {ch.label}
            </span>

            {/* Hover overlay with switch */}
            <div
              className="absolute inset-x-0 bottom-0 flex items-center justify-center py-1.5 rounded-b-lg bg-background/80 backdrop-blur-sm opacity-0 group-hover:opacity-100 transition-opacity"
              onClick={(e) => {
                e.stopPropagation()
                onToggle(ch.key, !isEnabled)
              }}
            >
              <div
                className={
                  "relative w-8 h-4 rounded-full transition-colors " +
                  (isEnabled ? ch.switchOn : "bg-muted-foreground/30")
                }
                role="switch"
                aria-checked={isEnabled}
                aria-label={"Enable " + ch.label}
              >
                <span
                  className={
                    "absolute top-[2px] left-[2px] h-3 w-3 rounded-full bg-white shadow transition-transform " +
                    (isEnabled ? "translate-x-4" : "translate-x-0")
                  }
                />
              </div>
            </div>
          </button>
        )
      })}
    </div>
  )
}
