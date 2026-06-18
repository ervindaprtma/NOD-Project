"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { usePathname, useRouter } from "next/navigation";
import Link from "next/link";
import { getAccessToken, setAccessToken, apiFetch, ensureValidToken } from "@/lib/api";
import { cn } from "@/lib/utils";
import { ThemeToggle } from "@/components/ThemeToggle";
import type { Notification as NotifType } from "@/types";

const NAV_ITEMS = [
  { href: "/dashboard/overview", label: "Overview", icon: "◉" },
  { href: "/dashboard/traffic", label: "Traffic Internet", icon: "🌐" },
  { href: "/dashboard/traffic-inbound", label: "Traffic Inbound", icon: "↘" },
  { href: "/dashboard/traffic-internal", label: "Traffic Internal", icon: "⇄" },
  { href: "/dashboard/sdwan", label: "SD-WAN SLA", icon: "⏱" },
  { href: "/dashboard/resources", label: "Resources", icon: "⊞" },
  { href: "/dashboard/vpn", label: "VPN Sessions", icon: "🔒" },
  { href: "/dashboard/raw-data", label: "Raw Data", icon: "☰" },
  { href: "/dashboard/alerts", label: "Alerts", icon: "⚠" },
  { href: "/dashboard/reports", label: "Reports", icon: "📄" },
  { href: "/dashboard/users", label: "Users", icon: "👥" },
  { href: "/dashboard/activity-logs", label: "Activity Logs", icon: "📋" },
  { href: "/dashboard/settings", label: "Settings", icon: "⚙" },
];

export default function DashboardLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const pathname = usePathname();
  const router = useRouter();
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [notifications, setNotifications] = useState<NotifType[]>([]);
  const [notifOpen, setNotifOpen] = useState(false);
  const [user, setUser] = useState<{ username: string; role: string; full_name: string } | null>(null);
  const [authChecked, setAuthChecked] = useState(false);
  const [tokenPresent, setTokenPresent] = useState(false);
  const [profileOpen, setProfileOpen] = useState(false);
  const profileRef = useRef<HTMLDivElement>(null);

  const isSuperAdmin = user?.role === "superadmin";

  // Close profile dropdown on outside click
  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (profileRef.current && !profileRef.current.contains(e.target as Node)) {
        setProfileOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, []);

  const fetchUser = useCallback(async () => {
    try {
      const resp = await apiFetch<{ data: any }>("/api/v1/users/me");
      if (resp?.data) {
        setUser({
          username: resp.data.username,
          role: resp.data.role,
          full_name: resp.data.full_name,
        });
      }
    } catch { /* ignore */ }
  }, []);

  const fetchNotifications = useCallback(async () => {
    try {
      const resp = await apiFetch<{ data: NotifType[] }>("/api/v1/notifications?unread_only=true&limit=10");
      setNotifications(resp.data || []);
    } catch { /* ignore */ }
  }, []);

  useEffect(() => {
    async function initAuth() {
      const token = getAccessToken();
      if (!token) {
        setTokenPresent(false);
        setAuthChecked(true);
        router.push("/login");
        return;
      }
      // Validate token freshness to prevent 401 console errors
      const valid = await ensureValidToken();
      if (!valid) {
        setAccessToken(null);
        setTokenPresent(false);
        setAuthChecked(true);
        router.push("/login");
        return;
      }
      setTokenPresent(true);
      setAuthChecked(true);
    }
    initAuth();
  }, [router]);

  useEffect(() => {
    if (!tokenPresent) return;
    fetchUser();
    fetchNotifications();
    const interval = setInterval(fetchNotifications, 30000);
    return () => clearInterval(interval);
  }, [fetchUser, fetchNotifications, tokenPresent]);

  const unreadCount = notifications.filter((n) => !n.is_read).length;

  async function markAllRead() {
    try {
      await apiFetch("/api/v1/notifications/mark-all-read", { method: "POST" });
      setNotifications((prev) => prev.map((n) => ({ ...n, is_read: true })));
    } catch { /* ignore */ }
  }

  async function handleLogout() {
    await fetch("/auth/logout", { method: "POST", credentials: "include" });
    setAccessToken(null);
    router.push("/login");
  }

  return (
    <div className="flex h-screen overflow-hidden">
      {!authChecked ? (
        <div className="flex-1 flex items-center justify-center bg-background">
          <div className="text-center space-y-3">
            <div className="w-8 h-8 border-2 border-primary border-t-transparent rounded-full animate-spin mx-auto" />
            <p className="text-sm text-muted-foreground">Verifying session...</p>
          </div>
        </div>
      ) : !tokenPresent ? (
        <div className="flex-1 flex items-center justify-center bg-background">
          <p className="text-sm text-muted-foreground">Redirecting to login...</p>
        </div>
      ) : (
        <>
      <aside
        className={cn(
          "flex flex-col bg-card border-r transition-all duration-200",
          sidebarOpen ? "w-60" : "w-16"
        )}
      >
        <div className="flex items-center h-14 px-4 border-b">
          <button
            onClick={() => setSidebarOpen(!sidebarOpen)}
            className="text-sm font-bold tracking-tight"
          >
            {sidebarOpen ? "NOD" : "N"}
          </button>
        </div>
        <nav className="flex-1 overflow-y-auto py-2">
          {NAV_ITEMS.map((item) => {
            // Hide Activity Logs from non-superadmin
            if (item.href === "/dashboard/activity-logs" && !isSuperAdmin) return null;
            return (
            <Link
              key={item.href}
              href={item.href}
              className={cn(
                "flex items-center gap-3 px-4 py-2.5 text-sm transition-colors",
                pathname === item.href
                  ? "bg-primary/10 text-primary font-medium"
                  : "text-muted-foreground hover:bg-muted"
              )}
            >
              <span className="text-base">{item.icon}</span>
              {sidebarOpen && <span>{item.label}</span>}
            </Link>
          );
          })}
        </nav>
      </aside>

      <div className="flex-1 flex flex-col overflow-hidden">
        <header className="flex items-center justify-between h-14 px-6 border-b bg-card shrink-0">
          <div className="flex items-center gap-4">
            <button
              onClick={() => setSidebarOpen(!sidebarOpen)}
              className="text-muted-foreground hover:text-foreground"
              aria-label="Toggle sidebar"
            >
              ☰
            </button>
          </div>

          <div className="flex items-center gap-3">
            {/* Theme toggle */}
            <ThemeToggle />

            {/* Notification bell */}
            <div className="relative">
              <button
                onClick={() => { setNotifOpen(!notifOpen); setProfileOpen(false); }}
                className="relative p-1 text-muted-foreground hover:text-foreground"
                aria-label="Notifications"
              >
                🔔
                {unreadCount > 0 && (
                  <span className="absolute -top-1 -right-1 flex items-center justify-center w-5 h-5 text-xs font-bold text-white bg-destructive rounded-full">
                    {unreadCount}
                  </span>
                )}
              </button>

              {notifOpen && (
                <div className="absolute right-0 top-8 w-80 bg-card border rounded-lg shadow-lg z-50">
                  <div className="flex items-center justify-between p-3 border-b">
                    <h3 className="text-sm font-semibold">Notifications</h3>
                    <button onClick={markAllRead} className="text-xs text-primary hover:underline">
                      Mark all read
                    </button>
                  </div>
                  <div className="max-h-80 overflow-y-auto">
                    {notifications.length === 0 ? (
                      <p className="p-4 text-sm text-muted-foreground text-center">No notifications</p>
                    ) : (
                      notifications.map((n) => (
                        <div key={n.id} className={cn("p-3 border-b text-sm", !n.is_read && "bg-muted/50")}>
                          <div className="flex items-center gap-2">
                            <span className={cn("w-2 h-2 rounded-full",
                              n.severity === "CRITICAL" && "bg-destructive",
                              n.severity === "WARNING" && "bg-warning",
                              n.severity === "INFO" && "bg-primary"
                            )} />
                            <span className="font-medium">{n.alert_name}</span>
                          </div>
                          <p className="text-xs text-muted-foreground mt-1">{n.message}</p>
                        </div>
                      ))
                    )}
                  </div>
                </div>
              )}
            </div>

            {/* User profile dropdown */}
            <div className="relative" ref={profileRef}>
              <button
                onClick={() => { setProfileOpen(!profileOpen); setNotifOpen(false); }}
                className="flex items-center gap-2 p-1 text-sm rounded-lg hover:bg-muted transition-colors"
                aria-label="User menu"
              >
                <span className="w-8 h-8 rounded-full bg-primary/10 flex items-center justify-center text-sm font-medium text-primary">
                  {user?.username?.charAt(0).toUpperCase() || "?"}
                </span>
                <span className="hidden md:inline text-sm font-medium text-foreground">
                  {user?.username || "User"}
                </span>
                <span className="hidden md:inline text-xs text-muted-foreground">▾</span>
              </button>

              {profileOpen && (
                <div className="absolute right-0 top-10 w-56 bg-card border rounded-lg shadow-lg z-50 py-1">
                  <div className="px-3 py-2 border-b">
                    <p className="text-sm font-medium">{user?.full_name || user?.username}</p>
                    <p className="text-xs text-muted-foreground capitalize">{user?.role}</p>
                  </div>
                  <button
                    onClick={() => { setProfileOpen(false); router.push("/dashboard/settings"); }}
                    className="w-full text-left px-3 py-2 text-sm hover:bg-muted transition-colors"
                  >
                    ⚙ Profile Settings
                  </button>
                  <button
                    onClick={handleLogout}
                    className="w-full text-left px-3 py-2 text-sm text-destructive hover:bg-muted transition-colors"
                  >
                    ↪ Logout
                  </button>
                </div>
              )}
            </div>
          </div>
        </header>

        <main className="flex-1 overflow-y-auto p-6 bg-muted/20">
          {children}
        </main>
      </div>
    </>
      )}
    </div>
  );
}
