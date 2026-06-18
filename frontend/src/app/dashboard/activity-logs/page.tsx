"use client";

import { useState } from "react";
import useSWR from "swr";
import { swrFetcher, getAccessToken } from "@/lib/api";
import { cn } from "@/lib/utils";

const ACTION_LABELS: Record<string, string> = {
  login: "Login",
  logout: "Logout",
  user_created: "User Created",
  user_updated: "User Updated",
  user_deleted: "User Deleted",
  alert_rule_created: "Alert Rule Created",
  alert_rule_updated: "Alert Rule Updated",
  alert_rule_deleted: "Alert Rule Deleted",
  report_generated: "Report Generated",
  report_downloaded: "Report Downloaded",
  report_distributed: "Report Distributed",
};

const ACTION_COLORS: Record<string, string> = {
  login: "bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400",
  logout: "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-400",
  user_created: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-400",
  user_updated: "bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-400",
  user_deleted: "bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400",
  alert_rule_created: "bg-purple-100 text-purple-800 dark:bg-purple-900/30 dark:text-purple-400",
  alert_rule_updated: "bg-purple-100 text-purple-800 dark:bg-purple-900/30 dark:text-purple-400",
  alert_rule_deleted: "bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400",
  report_generated: "bg-teal-100 text-teal-800 dark:bg-teal-900/30 dark:text-teal-400",
  report_downloaded: "bg-cyan-100 text-cyan-800 dark:bg-cyan-900/30 dark:text-cyan-400",
  report_distributed: "bg-indigo-100 text-indigo-800 dark:bg-indigo-900/30 dark:text-indigo-400",
};

interface ActivityLog {
  id: string;
  user_id: string;
  username: string;
  role: string;
  action: string;
  source_ip: string | null;
  details: Record<string, any> | null;
  timestamp: string;
}

export default function ActivityLogsPage() {
  const [filter, setFilter] = useState("");
  const token = typeof window !== "undefined" ? getAccessToken() : null;
  const swrKey = token ? "/api/v1/logs/user-activity?limit=100" : null;

  const { data, error, isLoading } = useSWR<{ data: ActivityLog[]; meta: { total: number } }>(
    swrKey,
    swrFetcher,
    { refreshInterval: 30000 }
  );

  const logs = data?.data || [];
  const total = data?.meta?.total || 0;

  const filtered = filter
    ? logs.filter((l) => l.action.toLowerCase().includes(filter.toLowerCase()))
    : logs;

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold tracking-tight">Activity Logs</h1>

      {error ? (
        <div className="p-6 bg-destructive/10 border border-destructive/20 rounded-lg text-center">
          <p className="text-destructive font-medium">Access Denied</p>
          <p className="text-sm text-muted-foreground mt-1">
            Only superadmin can view activity logs (FR-11).
          </p>
        </div>
      ) : (
        <>
          {/* Filter bar */}
          <div className="flex items-center gap-3 flex-wrap">
            <input
              type="text"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder="Filter by action type..."
              className="px-3 py-1.5 text-sm border rounded-md bg-background w-64 focus:outline-none focus:ring-1 focus:ring-primary"
            />
            <span className="text-xs text-muted-foreground">
              {filtered.length} of {total} entries
            </span>
          </div>

          {/* Table */}
          <div className="bg-card border rounded-lg overflow-hidden">
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b bg-muted/50 text-muted-foreground">
                    <th className="text-left py-3 px-4 w-40">Timestamp</th>
                    <th className="text-left py-3 px-4">Action</th>
                    <th className="text-left py-3 px-4">Username</th>
                    <th className="text-left py-3 px-4">Role</th>
                    <th className="text-left py-3 px-4">Source IP</th>
                    <th className="text-left py-3 px-4">Details</th>
                  </tr>
                </thead>
                <tbody>
                  {isLoading ? (
                    Array.from({ length: 8 }).map((_, i) => (
                      <tr key={i} className="border-b animate-pulse">
                        {[1, 2, 3, 4, 5, 6].map((j) => (
                          <td key={j} className="py-4 px-4"><div className="h-4 bg-muted rounded" /></td>
                        ))}
                      </tr>
                    ))
                  ) : filtered.length === 0 ? (
                    <tr>
                      <td colSpan={6} className="py-10 text-center text-muted-foreground">
                        No activity logs found
                      </td>
                    </tr>
                  ) : (
                    filtered.map((log) => (
                      <tr key={log.id} className="border-b last:border-0 hover:bg-muted/30 transition-colors">
                        <td className="py-3 px-4 text-xs font-mono whitespace-nowrap">
                          {new Date(log.timestamp).toLocaleString()}
                        </td>
                        <td className="py-3 px-4">
                          <span className={cn(
                            "px-2 py-0.5 rounded text-[11px] font-medium",
                            ACTION_COLORS[log.action] || "bg-slate-100 text-slate-600"
                          )}>
                            {ACTION_LABELS[log.action] || log.action}
                          </span>
                        </td>
                        <td className="py-3 px-4 font-medium text-sm">
                          {log.username || log.user_id.slice(0, 8) + "…"}
                        </td>
                        <td className="py-3 px-4">
                          <span className={cn(
                            "px-2 py-0.5 rounded text-[11px] font-medium capitalize",
                            log.role === "superadmin" ? "bg-purple-100 text-purple-800 dark:bg-purple-900/30 dark:text-purple-400" :
                            log.role === "admin" ? "bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400" :
                            log.role === "operator" ? "bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-400" :
                            "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-400"
                          )}>
                            {log.role}
                          </span>
                        </td>
                        <td className="py-3 px-4 text-xs text-muted-foreground">
                          {log.source_ip || "—"}
                        </td>
                        <td className="py-3 px-4 text-xs text-muted-foreground max-w-xs">
                          {log.details ? (
                            <pre className="whitespace-pre-wrap break-all font-mono text-[11px]">
                              {JSON.stringify(log.details, null, 1)}
                            </pre>
                          ) : "—"}
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
