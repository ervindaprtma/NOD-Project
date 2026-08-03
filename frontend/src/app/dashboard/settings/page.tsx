"use client";

import { useState, useEffect } from "react";
import useSWR, { mutate } from "swr";
import { swrFetcher, apiFetch, hasMinRole, getErrorMessage } from "@/lib/api";
import { ThemeToggle } from "@/components/ThemeToggle";
import { useTheme } from "next-themes";
import { cn } from "@/lib/utils";
import type { NotificationChannelRead, MaintenanceWindow } from "@/types";
import { MessageTemplatesTab } from "./MessageTemplatesTab";

// All tabs are visible to all roles — but the admin-only tabs
// (notifications, maintenance) only render their forms if the
// current user has at least the admin role. Non-admins see an
// "access denied" message inside those tabs.
type Tab =
  | "password"
  | "profile"
  | "appearance"
  | "notifications"
  | "templates"
  | "maintenance";

const ALL_TABS: { id: Tab; label: string; adminOnly: boolean }[] = [
  { id: "password", label: "Change Password", adminOnly: false },
  { id: "profile", label: "Profile", adminOnly: false },
  { id: "appearance", label: "Appearance", adminOnly: false },
  { id: "notifications", label: "Notification Channels", adminOnly: true },
  { id: "templates", label: "Message Templates", adminOnly: true },
  { id: "maintenance", label: "Maintenance Windows", adminOnly: true },
];

export default function SettingsPage() {
  const [activeTab, setActiveTab] = useState<Tab>("password");
  const [toast, setToast] = useState<{ ok: boolean; msg: string } | null>(null);
  const isAdmin = hasMinRole("admin");

  // Auto-dismiss toast after 5s (matches Reports/Distribute pattern)
  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 5000);
    return () => clearTimeout(t);
  }, [toast]);

  // If a non-admin somehow lands on an admin tab, bounce them back.
  const visibleTabs = ALL_TABS.filter((t) => !t.adminOnly || isAdmin);
  const safeActive = visibleTabs.find((t) => t.id === activeTab) ? activeTab : "password";

  return (
    <div className="space-y-6 max-w-3xl">
      <h1 className="text-2xl font-bold tracking-tight">Settings</h1>

      {/* Tabs */}
      <div className="flex gap-1 bg-muted rounded-md p-1 w-fit flex-wrap">
        {visibleTabs.map((t) => (
          <button
            key={t.id}
            onClick={() => setActiveTab(t.id)}
            className={cn(
              "px-4 py-1.5 text-sm rounded-sm transition-colors",
              safeActive === t.id
                ? "bg-background text-foreground shadow-sm"
                : "text-muted-foreground hover:text-foreground"
            )}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* Tab content */}
      <div className="bg-card border rounded-lg p-6">
        {safeActive === "password" && <ChangePasswordForm />}
        {safeActive === "profile" && <DisplayNameForm />}
        {safeActive === "appearance" && <AppearanceForm />}
        {safeActive === "notifications" && isAdmin && <NotificationChannelsTab showToast={setToast} />}
        {safeActive === "templates" && isAdmin && <MessageTemplatesTab showToast={setToast} />}
        {safeActive === "maintenance" && isAdmin && <MaintenanceWindowsTab showToast={setToast} />}
      </div>

      {/* ── Status Toast (Reports/Distribute pattern) ───────── */}
      {toast && (
        <div
          className={cn(
            "fixed bottom-4 right-4 z-50 max-w-sm px-4 py-3 rounded-lg shadow-lg text-sm flex items-center gap-3",
            toast.ok
              ? "bg-green-50 border border-green-200 text-green-800 dark:bg-green-950 dark:border-green-800 dark:text-green-200"
              : "bg-red-50 border border-red-200 text-red-800 dark:bg-red-950 dark:border-red-800 dark:text-red-200"
          )}
        >
          <span>{toast.ok ? "✅" : "❌"}</span>
          <span className="flex-1">{toast.msg}</span>
          <button onClick={() => setToast(null)} className="font-bold hover:opacity-70">
            ×
          </button>
        </div>
      )}
    </div>
  );
}

// ── User Profile Tabs (unchanged) ─────────────────────────────

function ChangePasswordForm() {
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setSuccess("");

    if (newPassword.length < 8) {
      setError("Password must be at least 8 characters.");
      return;
    }
    if (newPassword !== confirmPassword) {
      setError("Passwords do not match.");
      return;
    }

    setLoading(true);
    try {
      await apiFetch("/api/v1/users/me/password", {
        method: "PUT",
        body: JSON.stringify({
          current_password: currentPassword,
          new_password: newPassword,
        }),
      });
      setSuccess("Password changed successfully.");
      setCurrentPassword("");
      setNewPassword("");
      setConfirmPassword("");
    } catch (err: unknown) {
      setError(getErrorMessage(err, "Failed to change password."));
    } finally {
      setLoading(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <h2 className="text-lg font-semibold">Change Password</h2>

      {error && <p className="text-sm text-destructive bg-destructive/10 px-3 py-2 rounded-md">{error}</p>}
      {success && <p className="text-sm text-emerald-600 bg-emerald-50 px-3 py-2 rounded-md dark:bg-emerald-950/20 dark:text-emerald-400">{success}</p>}

      <div>
        <label className="text-sm font-medium block mb-1">Current Password</label>
        <input
          type="password"
          value={currentPassword}
          onChange={(e) => setCurrentPassword(e.target.value)}
          required
          className="w-full px-3 py-2 border rounded-md text-sm bg-background focus:outline-none focus:ring-2 focus:ring-primary/30"
        />
      </div>
      <div>
        <label className="text-sm font-medium block mb-1">New Password</label>
        <input
          type="password"
          value={newPassword}
          onChange={(e) => setNewPassword(e.target.value)}
          required
          minLength={8}
          className="w-full px-3 py-2 border rounded-md text-sm bg-background focus:outline-none focus:ring-2 focus:ring-primary/30"
        />
      </div>
      <div>
        <label className="text-sm font-medium block mb-1">Confirm New Password</label>
        <input
          type="password"
          value={confirmPassword}
          onChange={(e) => setConfirmPassword(e.target.value)}
          required
          className="w-full px-3 py-2 border rounded-md text-sm bg-background focus:outline-none focus:ring-2 focus:ring-primary/30"
        />
      </div>
      <button
        type="submit"
        disabled={loading}
        className="px-4 py-2 text-sm bg-primary text-primary-foreground rounded-md hover:bg-primary/90 disabled:opacity-50"
      >
        {loading ? "Saving..." : "Update Password"}
      </button>
    </form>
  );
}

function DisplayNameForm() {
  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [loading, setLoading] = useState(false);

  // Load current profile
  useState(() => {
    apiFetch<{ data: { full_name: string; email: string } }>("/api/v1/users/me")
      .then((resp) => {
        setFullName(resp.data?.full_name || "");
        setEmail(resp.data?.email || "");
        setLoaded(true);
      })
      .catch(() => setLoaded(true));
  });

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setSuccess("");
    setLoading(true);
    try {
      await apiFetch("/api/v1/users/me", {
        method: "PUT",
        body: JSON.stringify({ full_name: fullName, email }),
      });
      setSuccess("Profile updated successfully.");
    } catch (err: unknown) {
      setError(getErrorMessage(err, "Failed to update profile."));
    } finally {
      setLoading(false);
    }
  }

  if (!loaded) {
    return <div className="h-24 bg-muted animate-pulse rounded" />;
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <h2 className="text-lg font-semibold">Profile</h2>

      {error && <p className="text-sm text-destructive bg-destructive/10 px-3 py-2 rounded-md">{error}</p>}
      {success && <p className="text-sm text-emerald-600 bg-emerald-50 px-3 py-2 rounded-md dark:bg-emerald-950/20 dark:text-emerald-400">{success}</p>}

      <div>
        <label className="text-sm font-medium block mb-1">Full Name</label>
        <input
          type="text"
          value={fullName}
          onChange={(e) => setFullName(e.target.value)}
          placeholder="Your display name"
          className="w-full px-3 py-2 border rounded-md text-sm bg-background focus:outline-none focus:ring-2 focus:ring-primary/30"
        />
      </div>
      <div>
        <label className="text-sm font-medium block mb-1" htmlFor="profile-email">Email</label>
        <input
          id="profile-email"
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          required
          placeholder="you@example.com"
          className="w-full px-3 py-2 border rounded-md text-sm bg-background focus:outline-none focus:ring-2 focus:ring-primary/30"
        />
      </div>
      <button
        type="submit"
        disabled={loading}
        className="px-4 py-2 text-sm bg-primary text-primary-foreground rounded-md hover:bg-primary/90 disabled:opacity-50"
      >
        {loading ? "Saving..." : "Update Profile"}
      </button>
    </form>
  );
}

function AppearanceForm() {
  const { theme, setTheme } = useTheme();
  const isDark = theme === "dark";

  return (
    <div className="space-y-4">
      <h2 className="text-lg font-semibold">Appearance</h2>
      <p className="text-sm text-muted-foreground">
        Choose your preferred color theme. Changes apply immediately.
      </p>

      <div className="flex items-center gap-4">
        <button
          onClick={() => setTheme("light")}
          className={cn(
            "flex items-center gap-3 px-4 py-3 rounded-lg border-2 transition-all",
            !isDark ? "border-primary bg-primary/5" : "border-border hover:border-muted-foreground/30"
          )}
        >
          <span className="text-xl">☀</span>
          <div className="text-left">
            <p className="text-sm font-medium">Light</p>
            <p className="text-xs text-muted-foreground">Light background, dark text</p>
          </div>
          {!isDark && <span className="ml-2 text-xs text-primary font-medium">Active</span>}
        </button>

        <button
          onClick={() => setTheme("dark")}
          className={cn(
            "flex items-center gap-3 px-4 py-3 rounded-lg border-2 transition-all",
            isDark ? "border-primary bg-primary/5" : "border-border hover:border-muted-foreground/30"
          )}
        >
          <span className="text-xl">🌙</span>
          <div className="text-left">
            <p className="text-sm font-medium">Dark</p>
            <p className="text-xs text-muted-foreground">Dark background, light text</p>
          </div>
          {isDark && <span className="ml-2 text-xs text-primary font-medium">Active</span>}
        </button>
      </div>
    </div>
  );
}

// ── Notification Channels Tab (v3 §3.13) — admin only ─────────

function NotificationChannelsTab({
  showToast,
}: {
  showToast: (t: { ok: boolean; msg: string }) => void;
}) {
  const { data, error, isLoading } = useSWR<{ data: Record<string, NotificationChannelRead> }>(
    "/api/v1/config/notifications",
    swrFetcher
  );
  const channelsObj = data?.data || {};
  const channels = ["whatsapp", "telegram", "smtp", "discord"] as const;
  const [editing, setEditing] = useState<string | null>(null);
  const [testStatus, setTestStatus] = useState<Record<string, "idle" | "sending" | "ok" | "error">>({});
  const [testMessage, setTestMessage] = useState<Record<string, string>>({});

  async function toggleChannel(channel: string, enabled: boolean) {
    try {
      const resp = await apiFetch<{ data: NotificationChannelRead; message?: string }>(
        `/api/v1/config/notifications/${channel}`,
        {
          method: "PUT",
          body: JSON.stringify({ enabled }),
        }
      );
      await mutate("/api/v1/config/notifications");
      const status = enabled ? "enabled" : "disabled";
      showToast({
        ok: true,
        msg: resp?.message
          ? `${channel} ${status}: ${resp.message}`
          : `Channel ${channel} ${status} successfully.`,
      });
    } catch (e: unknown) {
      showToast({ ok: false, msg: getErrorMessage(e, `Failed to ${enabled ? "enable" : "disable"} ${channel}.`) });
    }
  }

  async function sendTest(channel: string) {
    setTestStatus((s) => ({ ...s, [channel]: "sending" }));
    setTestMessage((m) => ({ ...m, [channel]: "" }));
    try {
      await apiFetch(`/api/v1/config/notifications/${channel}/test`, { method: "POST" });
      setTestStatus((s) => ({ ...s, [channel]: "ok" }));
      setTestMessage((m) => ({ ...m, [channel]: "Test sent successfully." }));
    } catch (e: unknown) {
      setTestStatus((s) => ({ ...s, [channel]: "error" }));
      setTestMessage((m) => ({ ...m, [channel]: getErrorMessage(e, "Test failed.") }));
    }
  }

  return (
    <div className="space-y-3">
      <div>
        <h2 className="text-lg font-semibold">Notification Channels</h2>
        <p className="text-sm text-muted-foreground mt-1">
          Configure the channels used to deliver alert notifications. Secrets are
          encrypted at rest. Click a channel to edit its credentials and connection settings.
        </p>
      </div>

      {error && (
        <p className="text-sm text-destructive bg-destructive/10 px-3 py-2 rounded-md">
          Failed to load channels.
        </p>
      )}

      <div className="border rounded-lg overflow-hidden">
        {isLoading ? (
          <div className="p-8 text-center text-muted-foreground">Loading…</div>
        ) : (
          channels.map((channel) => {
            const cfg = channelsObj[channel];
            const isOpen = editing === channel;
            const tStatus = testStatus[channel] || "idle";
            return (
              <div key={channel} className="border-b last:border-0">
                {/* Header row */}
                <div
                  className={cn(
                    "flex items-center justify-between p-4 cursor-pointer hover:bg-muted/30 transition-colors",
                    isOpen && "bg-muted/30"
                  )}
                  onClick={() => setEditing(isOpen ? null : channel)}
                >
                  <div className="flex items-center gap-3">
                    <ChannelIcon channel={channel} />
                    <div>
                      <p className="font-medium capitalize">{channel}</p>
                      <p className="text-xs text-muted-foreground">
                        {cfg?.enabled ? "Active" : "Disabled"}
                        {cfg?.updated_at && ` · updated ${new Date(cfg.updated_at).toLocaleString()}`}
                      </p>
                    </div>
                  </div>
                  <div className="flex items-center gap-2" onClick={(e) => e.stopPropagation()}>
                    <span
                      className={cn(
                        "inline-flex px-2 py-0.5 rounded-full text-[11px] font-medium",
                        cfg?.enabled
                          ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-400"
                          : "bg-muted text-muted-foreground"
                      )}
                    >
                      {cfg?.enabled ? "Active" : "Disabled"}
                    </span>
                    <button
                      onClick={() => toggleChannel(channel, !cfg?.enabled)}
                      className={cn(
                        "px-2 py-1 text-[11px] rounded border bg-background hover:bg-muted transition-colors",
                        cfg?.enabled
                          ? "border-red-200 text-red-600 hover:bg-red-50 dark:hover:bg-red-950/20"
                          : "border-emerald-200 text-emerald-700 hover:bg-emerald-50 dark:hover:bg-emerald-950/20"
                      )}
                    >
                      {cfg?.enabled ? "Disable" : "Enable"}
                    </button>
                    <span className="text-xs text-muted-foreground">{isOpen ? "▲" : "▼"}</span>
                  </div>
                </div>

                {/* Expanded editor */}
                {isOpen && (
                  <div className="p-4 bg-muted/10 border-t">
                    {channel === "telegram" && (
                      <TelegramConfigForm
                        config={cfg?.config}
                        onSaved={() => {
                          mutate("/api/v1/config/notifications");
                          setEditing(null);
                        }}
                        onSuccess={(msg) => showToast({ ok: true, msg })}
                        onError={(msg) => showToast({ ok: false, msg })}
                      />
                    )}
                    {channel === "smtp" && (
                      <SMTPConfigForm
                        config={cfg?.config}
                        onSaved={() => {
                          mutate("/api/v1/config/notifications");
                          setEditing(null);
                        }}
                        onSuccess={(msg) => showToast({ ok: true, msg })}
                        onError={(msg) => showToast({ ok: false, msg })}
                      />
                    )}
                    {channel === "whatsapp" && (
                      <WhatsAppConfigForm
                        config={cfg?.config}
                        onSaved={() => {
                          mutate("/api/v1/config/notifications");
                          setEditing(null);
                        }}
                        onSuccess={(msg) => showToast({ ok: true, msg })}
                        onError={(msg) => showToast({ ok: false, msg })}
                      />
                    )}
                    {channel === "discord" && (
                      <DiscordConfigForm
                        config={cfg?.config}
                        onSaved={() => {
                          mutate("/api/v1/config/notifications");
                          setEditing(null);
                        }}
                        onSuccess={(msg) => showToast({ ok: true, msg })}
                        onError={(msg) => showToast({ ok: false, msg })}
                      />
                    )}

                    {/* Test send row */}
                    <div className="mt-4 pt-3 border-t flex items-center gap-3">
                      <button
                        onClick={() => sendTest(channel)}
                        disabled={tStatus === "sending" || !cfg?.enabled}
                        className="px-3 py-1.5 text-xs rounded-md border bg-background hover:bg-muted disabled:opacity-50"
                      >
                        {tStatus === "sending" ? "Sending…" : "Send Test"}
                      </button>
                      {tStatus === "ok" && (
                        <span className="text-xs text-emerald-600 dark:text-emerald-400">
                          ✓ {testMessage[channel]}
                        </span>
                      )}
                      {tStatus === "error" && (
                        <span className="text-xs text-destructive">✗ {testMessage[channel]}</span>
                      )}
                      {!cfg?.enabled && (
                        <span className="text-xs text-muted-foreground">
                          Enable the channel to send a test.
                        </span>
                      )}
                    </div>
                  </div>
                )}
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}

function ChannelIcon({ channel }: { channel: string }) {
  const iconMap: Record<string, { emoji: string; bg: string }> = {
    whatsapp: { emoji: "💬", bg: "bg-emerald-100 dark:bg-emerald-900/30" },
    telegram: { emoji: "✈️", bg: "bg-sky-100 dark:bg-sky-900/30" },
    smtp: { emoji: "📧", bg: "bg-amber-100 dark:bg-amber-900/30" },
    discord: { emoji: "🎮", bg: "bg-indigo-100 dark:bg-indigo-900/30" },
  };
  const c = iconMap[channel] || { emoji: "•", bg: "bg-muted" };
  return (
    <div className={cn("w-9 h-9 rounded-full flex items-center justify-center text-base", c.bg)}>
      {c.emoji}
    </div>
  );
}

// ── Telegram Config Form ──────────────────────────────────────

function TelegramConfigForm({
  config,
  onSaved,
  onSuccess,
  onError,
}: {
  config?: Record<string, unknown>;
  onSaved: () => void;
  onSuccess: (msg: string) => void;
  onError: (msg: string) => void;
}) {
  const [botToken, setBotToken] = useState("");
  const [chatId, setChatId] = useState((config?.chat_id as string) || "");
  const [minSeverity, setMinSeverity] = useState<string>(
    (config?.min_severity as string) || "CRITICAL"
  );
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [tokenIsSet, setTokenIsSet] = useState(Boolean(config?.bot_token));
  const [discovering, setDiscovering] = useState(false);
  const [chats, setChats] = useState<{ id: string; type: string; title: string }[] | null>(null);

  // "Find my chat_id": ask the bot (getUpdates) which chats it can currently see.
  async function discoverChats() {
    setError("");
    setDiscovering(true);
    setChats(null);
    try {
      const resp = await apiFetch<{ data: { chats: { id: string; type: string; title: string }[] } }>(
        "/api/v1/config/notifications/telegram/chats",
        { method: "POST", body: JSON.stringify({ bot_token: botToken || undefined }) }
      );
      setChats(resp.data.chats);
    } catch (e: unknown) {
      const msg = getErrorMessage(e, "Could not fetch chats.");
      setError(msg);
      onError(msg);
    } finally {
      setDiscovering(false);
    }
  }

  async function handleSave() {
    setError("");
    // Require the token only when creating a brand-new channel (no stored config).
    // For an existing channel, a settings-only change (min_severity / enabled) must not be
    // blocked by a missing secret — that stranded remote configs whose token was never stored.
    if (!botToken && !tokenIsSet && !config) {
      setError("Bot token is required.");
      return;
    }
    if (!chatId) {
      setError("Chat ID is required.");
      return;
    }
    setSaving(true);
    try {
      const body: Record<string, unknown> = {
        enabled: true,
        min_severity: minSeverity,
        config: {
          chat_id: chatId,
          ...(botToken ? { bot_token: botToken } : {}),
        },
      };
      await apiFetch("/api/v1/config/notifications/telegram", {
        method: "PUT",
        body: JSON.stringify(body),
      });
      setBotToken("");
      onSaved();
      onSuccess("Telegram configuration saved.");
    } catch (e: unknown) {
      const msg = getErrorMessage(e, "Failed to save Telegram config.");
      setError(msg);
      onError(msg);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-3">
      <h3 className="text-sm font-semibold">Telegram Bot Configuration</h3>
      <p className="text-xs text-muted-foreground">
        Get a bot token from <span className="font-mono">@BotFather</span> on Telegram.
        The Chat ID is the target channel/group/user that should receive the alert messages.
      </p>

      {error && (
        <p className="text-sm text-destructive bg-destructive/10 px-3 py-2 rounded-md">{error}</p>
      )}

      <div>
        <label className="text-xs font-medium block mb-1">
          Bot Token {tokenIsSet && <span className="text-emerald-600">(set — leave blank to keep)</span>}
        </label>
        <input
          type="password"
          value={botToken}
          onChange={(e) => setBotToken(e.target.value)}
          placeholder={tokenIsSet ? "••••••••" : "123456789:ABCDefGhIJKlmnOPQrstUVwxYZ"}
          className="w-full px-3 py-1.5 text-sm rounded-md border bg-background focus:outline-none focus:ring-2 focus:ring-primary/30 font-mono"
        />
      </div>

      <div>
        <div className="flex items-center justify-between mb-1">
          <label className="text-xs font-medium">Chat ID / Channel ID</label>
          <button
            type="button"
            onClick={discoverChats}
            disabled={discovering}
            className="text-[11px] px-2 py-0.5 rounded border bg-background hover:bg-muted disabled:opacity-50"
            title="Ask the bot which chats it can currently message"
          >
            {discovering ? "Looking…" : "Find my chat_id"}
          </button>
        </div>
        <input
          type="text"
          value={chatId}
          onChange={(e) => setChatId(e.target.value)}
          placeholder="-1001234567890"
          className="w-full px-3 py-1.5 text-sm rounded-md border bg-background focus:outline-none focus:ring-2 focus:ring-primary/30 font-mono"
        />
        <p className="text-xs text-muted-foreground mt-1">
          Use a negative number for groups/channels (e.g. <span className="font-mono">-100…</span>).
        </p>

        {/* Discovered chats — click one to fill the field */}
        {chats !== null && (
          <div className="mt-2 border rounded-md p-2 bg-muted/30">
            {chats.length === 0 ? (
              <p className="text-[11px] text-muted-foreground">
                No chats found. Send a message to the bot (or add it to the group/channel) in the
                last 24h, then try again. For a private DM, press <span className="font-mono">/start</span> on the bot first.
              </p>
            ) : (
              <ul className="space-y-1">
                {chats.map((c) => (
                  <li key={c.id}>
                    <button
                      type="button"
                      onClick={() => setChatId(c.id)}
                      className={cn(
                        "w-full flex items-center justify-between gap-2 text-left px-2 py-1 rounded text-xs hover:bg-muted transition-colors",
                        chatId === c.id && "bg-muted"
                      )}
                    >
                      <span className="truncate">{c.title} <span className="text-[10px] text-muted-foreground">({c.type})</span></span>
                      <span className="font-mono text-[11px] shrink-0">{c.id}</span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </div>

      <div>
        <label className="text-xs font-medium block mb-1">Minimum Severity</label>
        <select
          value={minSeverity}
          onChange={(e) => setMinSeverity(e.target.value)}
          className="w-full px-3 py-1.5 text-sm rounded-md border bg-background"
        >
          <option value="INFO">INFO</option>
          <option value="WARNING">WARNING</option>
          <option value="CRITICAL">CRITICAL</option>
        </select>
      </div>

      <button
        onClick={handleSave}
        disabled={saving}
        className="px-3 py-1.5 text-xs rounded-md bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
      >
        {saving ? "Saving…" : "Save & Connect"}
      </button>
    </div>
  );
}

// ── SMTP Config Form ──────────────────────────────────────────

function SMTPConfigForm({
  config,
  onSaved,
  onSuccess,
  onError,
}: {
  config?: Record<string, unknown>;
  onSaved: () => void;
  onSuccess: (msg: string) => void;
  onError: (msg: string) => void;
}) {
  const [host, setHost] = useState((config?.host as string) || "");
  const [port, setPort] = useState<number>((config?.port as number) || 587);
  const [user, setUser] = useState("");
  const [password, setPassword] = useState("");
  const [fromAddress, setFromAddress] = useState((config?.from_address as string) || "");
  const [recipients, setRecipients] = useState(
    Array.isArray(config?.recipients) ? (config!.recipients as string[]).join(", ") : ""
  );
  const [minSeverity, setMinSeverity] = useState<string>(
    (config?.min_severity as string) || "CRITICAL"
  );
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [userSet, setUserSet] = useState(Boolean(config?.user));
  const [passSet, setPassSet] = useState(Boolean(config?.password));

  async function handleSave() {
    setError("");
    if (!host) { setError("SMTP host is required."); return; }
    if (!fromAddress) { setError("From address is required."); return; }
    if (!user && !userSet && !config) { setError("Username is required."); return; }
    if (!password && !passSet && !config) { setError("Password is required."); return; }

    setSaving(true);
    try {
      const newConfig: Record<string, unknown> = {
        host,
        port,
        from_address: fromAddress,
        recipients: recipients
          .split(",")
          .map((r) => r.trim())
          .filter(Boolean),
      };
      if (user) newConfig.user = user;
      if (password) newConfig.password = password;

      await apiFetch("/api/v1/config/notifications/smtp", {
        method: "PUT",
        body: JSON.stringify({
          enabled: true,
          min_severity: minSeverity,
          config: newConfig,
        }),
      });
      setUser("");
      setPassword("");
      onSaved();
      onSuccess("SMTP configuration saved.");
    } catch (e: unknown) {
      const msg = getErrorMessage(e, "Failed to save SMTP config.");
      setError(msg);
      onError(msg);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-3">
      <h3 className="text-sm font-semibold">SMTP / Email Configuration</h3>
      <p className="text-xs text-muted-foreground">
        Outgoing mail server for email alert delivery. Recipients receive a
        copy of every alert at or above the chosen minimum severity.
      </p>

      {error && (
        <p className="text-sm text-destructive bg-destructive/10 px-3 py-2 rounded-md">{error}</p>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <div>
          <label className="text-xs font-medium block mb-1">SMTP Host</label>
          <input
            type="text"
            value={host}
            onChange={(e) => setHost(e.target.value)}
            placeholder="smtp.gmail.com"
            className="w-full px-3 py-1.5 text-sm rounded-md border bg-background focus:outline-none focus:ring-2 focus:ring-primary/30 font-mono"
          />
        </div>
        <div>
          <label className="text-xs font-medium block mb-1">Port</label>
          <input
            type="number"
            value={port}
            onChange={(e) => setPort(Number(e.target.value))}
            min={1}
            max={65535}
            className="w-full px-3 py-1.5 text-sm rounded-md border bg-background focus:outline-none focus:ring-2 focus:ring-primary/30 font-mono"
          />
          <p className="text-[10px] text-muted-foreground mt-1">Common: 25, 465 (SSL), 587 (TLS)</p>
        </div>
      </div>

      <div>
        <label className="text-xs font-medium block mb-1">
          Username {userSet && <span className="text-emerald-600">(set — leave blank to keep)</span>}
        </label>
        <input
          type="text"
          value={user}
          onChange={(e) => setUser(e.target.value)}
          placeholder={userSet ? "••••••••" : "alerts@yourcompany.com"}
          className="w-full px-3 py-1.5 text-sm rounded-md border bg-background focus:outline-none focus:ring-2 focus:ring-primary/30 font-mono"
        />
      </div>

      <div>
        <label className="text-xs font-medium block mb-1">
          Password {passSet && <span className="text-emerald-600">(set — leave blank to keep)</span>}
        </label>
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder={passSet ? "••••••••" : "app-password-or-smtp-password"}
          className="w-full px-3 py-1.5 text-sm rounded-md border bg-background focus:outline-none focus:ring-2 focus:ring-primary/30 font-mono"
        />
      </div>

      <div>
        <label className="text-xs font-medium block mb-1">From Address</label>
        <input
          type="email"
          value={fromAddress}
          onChange={(e) => setFromAddress(e.target.value)}
          placeholder="noreply@yourcompany.com"
          className="w-full px-3 py-1.5 text-sm rounded-md border bg-background focus:outline-none focus:ring-2 focus:ring-primary/30 font-mono"
        />
      </div>

      <div>
        <label className="text-xs font-medium block mb-1">Recipients</label>
        <input
          type="text"
          value={recipients}
          onChange={(e) => setRecipients(e.target.value)}
          placeholder="noc@yourcompany.com, ops@yourcompany.com"
          className="w-full px-3 py-1.5 text-sm rounded-md border bg-background focus:outline-none focus:ring-2 focus:ring-primary/30 font-mono"
        />
        <p className="text-xs text-muted-foreground mt-1">
          Comma-separated email addresses. All receive the alert.
        </p>
      </div>

      <div>
        <label className="text-xs font-medium block mb-1">Minimum Severity</label>
        <select
          value={minSeverity}
          onChange={(e) => setMinSeverity(e.target.value)}
          className="w-full px-3 py-1.5 text-sm rounded-md border bg-background"
        >
          <option value="INFO">INFO</option>
          <option value="WARNING">WARNING</option>
          <option value="CRITICAL">CRITICAL</option>
        </select>
      </div>

      <button
        onClick={handleSave}
        disabled={saving}
        className="px-3 py-1.5 text-xs rounded-md bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
      >
        {saving ? "Saving…" : "Save & Connect"}
      </button>
    </div>
  );
}

// ── WhatsApp Config Form (preserved, same pattern) ─────────────

function WhatsAppConfigForm({
  config,
  onSaved,
  onSuccess,
  onError,
}: {
  config?: Record<string, unknown>;
  onSaved: () => void;
  onSuccess: (msg: string) => void;
  onError: (msg: string) => void;
}) {
  const [phoneNumberId, setPhoneNumberId] = useState(
    (config?.phone_number_id as string) || ""
  );
  const [businessAccountId, setBusinessAccountId] = useState(
    (config?.business_account_id as string) || ""
  );
  const [apiToken, setApiToken] = useState("");
  const [minSeverity, setMinSeverity] = useState<string>(
    (config?.min_severity as string) || "CRITICAL"
  );
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [tokenSet, setTokenSet] = useState(Boolean(config?.api_token));

  async function handleSave() {
    setError("");
    if (!phoneNumberId) { setError("Phone Number ID is required."); return; }
    if (!apiToken && !tokenSet && !config) { setError("API token is required."); return; }

    setSaving(true);
    try {
      const newConfig: Record<string, unknown> = {
        phone_number_id: phoneNumberId,
        business_account_id: businessAccountId,
        ...(apiToken ? { api_token: apiToken } : {}),
      };
      await apiFetch("/api/v1/config/notifications/whatsapp", {
        method: "PUT",
        body: JSON.stringify({
          enabled: true,
          min_severity: minSeverity,
          config: newConfig,
        }),
      });
      setApiToken("");
      onSaved();
      onSuccess("WhatsApp configuration saved.");
    } catch (e: unknown) {
      const msg = getErrorMessage(e, "Failed to save WhatsApp config.");
      setError(msg);
      onError(msg);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-3">
      <h3 className="text-sm font-semibold">WhatsApp Cloud API Configuration</h3>
      <p className="text-xs text-muted-foreground">
        Meta WhatsApp Cloud API credentials. Get them from
        <span className="font-mono"> developers.facebook.com</span>.
      </p>

      {error && (
        <p className="text-sm text-destructive bg-destructive/10 px-3 py-2 rounded-md">{error}</p>
      )}

      <div>
        <label className="text-xs font-medium block mb-1">Phone Number ID</label>
        <input
          type="text"
          value={phoneNumberId}
          onChange={(e) => setPhoneNumberId(e.target.value)}
          placeholder="123456789012345"
          className="w-full px-3 py-1.5 text-sm rounded-md border bg-background focus:outline-none focus:ring-2 focus:ring-primary/30 font-mono"
        />
      </div>

      <div>
        <label className="text-xs font-medium block mb-1">WhatsApp Business Account ID</label>
        <input
          type="text"
          value={businessAccountId}
          onChange={(e) => setBusinessAccountId(e.target.value)}
          placeholder="987654321098765"
          className="w-full px-3 py-1.5 text-sm rounded-md border bg-background focus:outline-none focus:ring-2 focus:ring-primary/30 font-mono"
        />
      </div>

      <div>
        <label className="text-xs font-medium block mb-1">
          API Token {tokenSet && <span className="text-emerald-600">(set — leave blank to keep)</span>}
        </label>
        <input
          type="password"
          value={apiToken}
          onChange={(e) => setApiToken(e.target.value)}
          placeholder={tokenSet ? "••••••••" : "EAA…"}
          className="w-full px-3 py-1.5 text-sm rounded-md border bg-background focus:outline-none focus:ring-2 focus:ring-primary/30 font-mono"
        />
      </div>

      <div>
        <label className="text-xs font-medium block mb-1">Minimum Severity</label>
        <select
          value={minSeverity}
          onChange={(e) => setMinSeverity(e.target.value)}
          className="w-full px-3 py-1.5 text-sm rounded-md border bg-background"
        >
          <option value="INFO">INFO</option>
          <option value="WARNING">WARNING</option>
          <option value="CRITICAL">CRITICAL</option>
        </select>
      </div>

      <button
        onClick={handleSave}
        disabled={saving}
        className="px-3 py-1.5 text-xs rounded-md bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
      >
        {saving ? "Saving…" : "Save & Connect"}
      </button>
    </div>
  );
}

// ── Discord Config Form (§9.2) ──────────────────────────────

function DiscordConfigForm({
  config,
  onSaved,
  onSuccess,
  onError,
}: {
  config?: Record<string, unknown>;
  onSaved: () => void;
  onSuccess: (msg: string) => void;
  onError: (msg: string) => void;
}) {
  const [webhookUrl, setWebhookUrl] = useState("");
  const [minSeverity, setMinSeverity] = useState<string>(
    (config?.min_severity as string) || "CRITICAL"
  );
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [urlSet, setUrlSet] = useState(Boolean(config?.webhook_url));

  async function handleSave() {
    setError("");
    if (!webhookUrl && !urlSet && !config) {
      setError("Webhook URL is required.");
      return;
    }
    setSaving(true);
    try {
      const newConfig: Record<string, unknown> = {
        ...(webhookUrl ? { webhook_url: webhookUrl } : {}),
      };
      await apiFetch("/api/v1/config/notifications/discord", {
        method: "PUT",
        body: JSON.stringify({
          enabled: true,
          min_severity: minSeverity,
          config: newConfig,
        }),
      });
      setWebhookUrl("");
      onSaved();
      onSuccess("Discord configuration saved.");
    } catch (e: unknown) {
      const msg = getErrorMessage(e, "Failed to save Discord config.");
      setError(msg);
      onError(msg);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-3">
      <h3 className="text-sm font-semibold">Discord Webhook Configuration</h3>
      <p className="text-xs text-muted-foreground">
        Create an incoming webhook in your Discord server (Channel Settings →
        Integrations → Webhooks) and paste the URL here. The backend will only
        POST to <span className="font-mono">discord.com</span> /{" "}
        <span className="font-mono">discordapp.com</span> hosts — any other
        host is rejected as a security safeguard.
      </p>

      {error && (
        <p className="text-sm text-destructive bg-destructive/10 px-3 py-2 rounded-md">{error}</p>
      )}

      <div>
        <label className="text-xs font-medium block mb-1">
          Webhook URL {urlSet && <span className="text-emerald-600">(set — leave blank to keep)</span>}
        </label>
        <input
          type="password"
          value={webhookUrl}
          onChange={(e) => setWebhookUrl(e.target.value)}
          placeholder={urlSet ? "••••••••" : "https://discord.com/api/webhooks/…"}
          className="w-full px-3 py-1.5 text-sm rounded-md border bg-background focus:outline-none focus:ring-2 focus:ring-primary/30 font-mono"
        />
      </div>

      <div>
        <label className="text-xs font-medium block mb-1">Minimum Severity</label>
        <select
          value={minSeverity}
          onChange={(e) => setMinSeverity(e.target.value)}
          className="w-full px-3 py-1.5 text-sm rounded-md border bg-background"
        >
          <option value="INFO">INFO</option>
          <option value="WARNING">WARNING</option>
          <option value="CRITICAL">CRITICAL</option>
        </select>
      </div>

      <button
        onClick={handleSave}
        disabled={saving}
        className="px-3 py-1.5 text-xs rounded-md bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
      >
        {saving ? "Saving…" : "Save & Connect"}
      </button>
    </div>
  );
}

// ── Maintenance Windows Tab (v3 §3.14) — admin only ──────────

function MaintenanceWindowsTab({
  showToast,
}: {
  showToast: (t: { ok: boolean; msg: string }) => void;
}) {
  const { data, isLoading } = useSWR<{ data: MaintenanceWindow[] }>(
    "/api/v1/config/maintenance",
    swrFetcher
  );
  const windows = data?.data || [];

  const [showCreate, setShowCreate] = useState(false);
  const [newSite, setNewSite] = useState("");
  const [newStart, setNewStart] = useState("");
  const [newEnd, setNewEnd] = useState("");
  const [newReason, setNewReason] = useState("");

  async function createWindow() {
    if (!newSite || !newStart || !newEnd) return;
    try {
      await apiFetch("/api/v1/config/maintenance", {
        method: "POST",
        body: JSON.stringify({
          site_name: newSite,
          starts_at: new Date(newStart).toISOString(),
          ends_at: new Date(newEnd).toISOString(),
          reason: newReason,
        }),
      });
      setShowCreate(false);
      setNewSite(""); setNewStart(""); setNewEnd(""); setNewReason("");
      mutate("/api/v1/config/maintenance");
      showToast({ ok: true, msg: `Maintenance window for ${newSite} created.` });
    } catch (e: unknown) {
      showToast({ ok: false, msg: getErrorMessage(e, "Failed to create maintenance window.") });
    }
  }

  async function deleteWindow(id: string, site: string) {
    if (!confirm("Delete this maintenance window?")) return;
    try {
      await apiFetch(`/api/v1/config/maintenance/${id}`, { method: "DELETE" });
      mutate("/api/v1/config/maintenance");
      showToast({ ok: true, msg: `Maintenance window for ${site} deleted.` });
    } catch (e: unknown) {
      showToast({ ok: false, msg: getErrorMessage(e, "Failed to delete maintenance window.") });
    }
  }

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-lg font-semibold">Maintenance Windows</h2>
        <p className="text-sm text-muted-foreground mt-1">
          Maintenance windows suppress alert evaluation for specific sites during planned outages.
        </p>
      </div>

      <div className="flex items-center justify-between">
        <span className="text-xs text-muted-foreground">
          {windows.length} window{windows.length === 1 ? "" : "s"} scheduled
        </span>
        <button
          onClick={() => setShowCreate(!showCreate)}
          className="px-3 py-1.5 text-xs rounded-md bg-primary text-primary-foreground hover:bg-primary/90 transition-colors"
        >
          {showCreate ? "Cancel" : "+ New Window"}
        </button>
      </div>

      {/* Create Form */}
      {showCreate && (
        <div className="bg-card border rounded-lg p-4 space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-xs font-medium">Site Name</label>
              <input
                type="text"
                value={newSite}
                onChange={(e) => setNewSite(e.target.value)}
                className="w-full px-3 py-1.5 text-sm rounded-md border bg-background mt-1"
                placeholder="DC, DRC, Office"
              />
            </div>
            <div>
              <label className="text-xs font-medium">Reason</label>
              <input
                type="text"
                value={newReason}
                onChange={(e) => setNewReason(e.target.value)}
                className="w-full px-3 py-1.5 text-sm rounded-md border bg-background mt-1"
                placeholder="e.g. Planned maintenance"
              />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-xs font-medium">Starts At</label>
              <input
                type="datetime-local"
                value={newStart}
                onChange={(e) => setNewStart(e.target.value)}
                className="w-full px-3 py-1.5 text-sm rounded-md border bg-background mt-1"
              />
            </div>
            <div>
              <label className="text-xs font-medium">Ends At</label>
              <input
                type="datetime-local"
                value={newEnd}
                onChange={(e) => setNewEnd(e.target.value)}
                className="w-full px-3 py-1.5 text-sm rounded-md border bg-background mt-1"
              />
            </div>
          </div>
          <button
            onClick={createWindow}
            disabled={!newSite || !newStart || !newEnd}
            className="px-3 py-1.5 text-xs rounded-md bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50 transition-colors"
          >
            Create
          </button>
        </div>
      )}

      {/* Windows Table */}
      <div className="bg-card border rounded-lg overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b bg-muted/50 text-muted-foreground">
                <th className="text-left py-3 px-3 text-xs font-medium">Site</th>
                <th className="text-left py-3 px-3 text-xs font-medium">Reason</th>
                <th className="text-left py-3 px-3 text-xs font-medium">Starts</th>
                <th className="text-left py-3 px-3 text-xs font-medium">Ends</th>
                <th className="text-center py-3 px-3 text-xs font-medium">Status</th>
                <th className="text-right py-3 px-3 text-xs font-medium">Actions</th>
              </tr>
            </thead>
            <tbody>
              {isLoading ? (
                Array.from({ length: 3 }).map((_, i) => (
                  <tr key={i} className="border-b animate-pulse">
                    <td colSpan={6}><div className="h-10 bg-muted rounded" /></td>
                  </tr>
                ))
              ) : windows.length === 0 ? (
                <tr>
                  <td colSpan={6} className="py-12 text-center text-muted-foreground">
                    No maintenance windows scheduled.
                  </td>
                </tr>
              ) : (
                windows.map((w) => {
                  const now = new Date();
                  const start = new Date(w.starts_at);
                  const end = new Date(w.ends_at);
                  const status = now < start ? "Upcoming" : now > end ? "Past" : "Active";
                  const statusColor =
                    status === "Active"
                      ? "bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-400"
                      : status === "Upcoming"
                        ? "bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400"
                        : "bg-muted text-muted-foreground";
                  return (
                    <tr key={w.id} className="border-b last:border-0 hover:bg-muted/30 transition-colors">
                      <td className="py-3 px-3 font-medium">{w.site_name}</td>
                      <td className="py-3 px-3 text-xs text-muted-foreground">{w.reason || "—"}</td>
                      <td className="py-3 px-3 text-[11px]">{start.toLocaleString()}</td>
                      <td className="py-3 px-3 text-[11px]">{end.toLocaleString()}</td>
                      <td className="py-3 px-3 text-center">
                        <span className={cn("px-2 py-0.5 rounded-full text-[11px] font-medium", statusColor)}>
                          {status}
                        </span>
                      </td>
                      <td className="py-3 px-3 text-right">
                        <button
                          onClick={() => deleteWindow(w.id, w.site_name)}
                          className="px-2 py-1 text-[11px] rounded border border-red-200 text-red-600 hover:bg-red-50 dark:hover:bg-red-950/20 transition-colors"
                        >
                          Delete
                        </button>
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
