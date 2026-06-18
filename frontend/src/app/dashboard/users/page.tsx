"use client";

import { useState } from "react";
import useSWR from "swr";
import { swrFetcher, getAccessToken, apiFetch } from "@/lib/api";
import { cn } from "@/lib/utils";

const ROLES = ["viewer", "operator", "admin", "superadmin"] as const;
type Role = (typeof ROLES)[number];

interface UserRecord {
  id: string;
  username: string;
  email: string;
  full_name: string;
  role: Role;
  is_active: boolean;
  must_change_password: boolean;
  last_login: string | null;
  created_at: string;
  updated_at: string;
}

export default function UsersPage() {
  const token = typeof window !== "undefined" ? getAccessToken() : null;
  const swrKey = token ? "/api/v1/users?limit=100" : null;

  const { data, error, isLoading, mutate } = useSWR<{ data: { users: UserRecord[]; total: number }; meta: any }>(
    swrKey,
    swrFetcher
  );

  const [showCreate, setShowCreate] = useState(false);
  const [editingUser, setEditingUser] = useState<UserRecord | null>(null);
  const [actionError, setActionError] = useState("");

  const users = data?.data?.users || [];

  async function handleToggleActive(user: UserRecord) {
    if (user.role === "superadmin") {
      setActionError("Cannot deactivate superadmin account.");
      return;
    }
    try {
      await apiFetch(`/api/v1/users/${user.id}`, {
        method: "PUT",
        body: JSON.stringify({ is_active: !user.is_active }),
      });
      mutate();
    } catch (err: any) {
      setActionError(err?.message || "Failed to update user.");
    }
  }

  async function handleDelete(user: UserRecord) {
    if (user.role === "superadmin") {
      setActionError("Cannot delete superadmin account.");
      return;
    }
    if (!confirm(`PERMANENTLY DELETE user "${user.username}"?\n\nThis action cannot be undone.`)) return;
    try {
      await apiFetch(`/api/v1/users/${user.id}`, { method: "DELETE" });
      mutate();
    } catch (err: any) {
      setActionError(err?.message || "Failed to delete user.");
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <h1 className="text-2xl font-bold tracking-tight">User Management</h1>
        <button
          onClick={() => setShowCreate(true)}
          className="px-4 py-2 text-sm bg-primary text-primary-foreground rounded-md hover:bg-primary/90"
        >
          + Create User
        </button>
      </div>

      {error && (
        <div className="p-4 rounded-lg bg-destructive/10 text-destructive text-sm">
          Failed to load users. You may not have admin privileges.
        </div>
      )}

      {actionError && (
        <div className="p-3 rounded-lg bg-destructive/10 text-destructive text-xs flex items-center justify-between">
          <span>{actionError}</span>
          <button onClick={() => setActionError("")} className="text-muted-foreground hover:text-foreground">✕</button>
        </div>
      )}

      {/* Users table */}
      <div className="bg-card border rounded-lg overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b bg-muted/50 text-muted-foreground">
                <th className="text-left py-3 px-4">Username</th>
                <th className="text-left py-3 px-4">Full Name</th>
                <th className="text-left py-3 px-4">Email</th>
                <th className="text-left py-3 px-4">Role</th>
                <th className="text-left py-3 px-4">Status</th>
                <th className="text-left py-3 px-4">Last Login</th>
                <th className="text-right py-3 px-4">Actions</th>
              </tr>
            </thead>
            <tbody>
              {isLoading ? (
                Array.from({ length: 5 }).map((_, i) => (
                  <tr key={i} className="border-b last:border-0 animate-pulse">
                    {[1, 2, 3, 4, 5, 6, 7].map((j) => (
                      <td key={j} className="py-4 px-4"><div className="h-4 bg-muted rounded" /></td>
                    ))}
                  </tr>
                ))
              ) : users.length === 0 ? (
                <tr>
                  <td colSpan={7} className="py-10 text-center text-muted-foreground">
                    No users found
                  </td>
                </tr>
              ) : (
                users.map((u) => (
                  <tr key={u.id} className="border-b last:border-0 hover:bg-muted/30 transition-colors">
                    <td className="py-3 px-4">
                      <span className="font-medium">{u.username}</span>
                      {u.must_change_password && (
                        <span className="ml-2 px-1.5 py-0.5 text-[10px] bg-warning/10 text-warning rounded">PWD reset</span>
                      )}
                    </td>
                    <td className="py-3 px-4 text-muted-foreground">{u.full_name || "—"}</td>
                    <td className="py-3 px-4 text-muted-foreground text-xs">{u.email}</td>
                    <td className="py-3 px-4">
                      <RoleBadge role={u.role} />
                    </td>
                    <td className="py-3 px-4">
                      <button
                        onClick={() => handleToggleActive(u)}
                        className={cn(
                          "inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium cursor-pointer",
                          u.is_active
                            ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-400"
                            : "bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400"
                        )}
                      >
                        <span className={cn("w-1.5 h-1.5 rounded-full", u.is_active ? "bg-emerald-500" : "bg-red-500")} />
                        {u.is_active ? "Active" : "Inactive"}
                      </button>
                    </td>
                    <td className="py-3 px-4 text-xs text-muted-foreground">
                      {u.last_login ? new Date(u.last_login).toLocaleString() : "Never"}
                    </td>
                    <td className="py-3 px-4 text-right">
                      <div className="flex items-center justify-end gap-2">
                        <button
                          onClick={() => setEditingUser(u)}
                          className="px-2.5 py-1 text-xs border rounded-md hover:bg-muted"
                        >
                          Edit
                        </button>
                        {u.is_active && u.role !== "superadmin" && (
                          <button
                            onClick={() => handleToggleActive(u)}
                            className="px-2.5 py-1 text-xs border rounded-md hover:bg-muted"
                          >
                            Deactivate
                          </button>
                        )}
                        {u.role !== "superadmin" && (
                          <button
                            onClick={() => handleDelete(u)}
                            className="px-2.5 py-1 text-xs border border-red-300 text-red-600 rounded-md hover:bg-red-50 dark:hover:bg-red-950/20"
                          >
                            Delete
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Create User Modal */}
      {showCreate && (
        <UserFormModal
          mode="create"
          onClose={() => setShowCreate(false)}
          onSuccess={() => { setShowCreate(false); mutate(); }}
        />
      )}

      {/* Edit User Modal */}
      {editingUser && (
        <UserFormModal
          mode="edit"
          user={editingUser}
          onClose={() => setEditingUser(null)}
          onSuccess={() => { setEditingUser(null); mutate(); }}
        />
      )}
    </div>
  );
}

// ── Role Badge ─────────────────────────────────────────────────

function RoleBadge({ role }: { role: Role }) {
  const colors: Record<Role, string> = {
    superadmin: "bg-purple-100 text-purple-800 dark:bg-purple-900/30 dark:text-purple-400",
    admin: "bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400",
    operator: "bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-400",
    viewer: "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-400",
  };
  return (
    <span className={cn("px-2 py-0.5 rounded text-[11px] font-medium capitalize", colors[role])}>
      {role}
    </span>
  );
}

// ── User Form Modal (Create / Edit) ────────────────────────────

function UserFormModal({
  mode,
  user,
  onClose,
  onSuccess,
}: {
  mode: "create" | "edit";
  user?: UserRecord;
  onClose: () => void;
  onSuccess: () => void;
}) {
  const [username, setUsername] = useState(user?.username || "");
  const [email, setEmail] = useState(user?.email || "");
  const [fullName, setFullName] = useState(user?.full_name || "");
  const [role, setRole] = useState<Role>(user?.role || "viewer");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");

    if (mode === "create" && password.length < 8) {
      setError("Password must be at least 8 characters.");
      return;
    }

    setLoading(true);
    try {
      if (mode === "create") {
        await apiFetch("/api/v1/users", {
          method: "POST",
          body: JSON.stringify({ username, email, full_name: fullName, role, password }),
        });
      } else {
        const body: Record<string, any> = { email, full_name: fullName, role };
        await apiFetch(`/api/v1/users/${user!.id}`, {
          method: "PUT",
          body: JSON.stringify(body),
        });
      }
      onSuccess();
    } catch (err: any) {
      setError(err?.message || "Operation failed.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <>
      <div className="fixed inset-0 z-40 bg-black/30" onClick={onClose} />
      <div className="fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 z-50 w-96 bg-card border rounded-lg shadow-xl p-6 space-y-4">
        <h3 className="text-lg font-semibold">
          {mode === "create" ? "Create User" : "Edit User"}
        </h3>

        {error && (
          <p className="text-xs text-destructive bg-destructive/10 px-3 py-2 rounded-md">{error}</p>
        )}

        <form onSubmit={handleSubmit} className="space-y-3">
          <div>
            <label className="text-xs font-medium block mb-0.5">Username</label>
            <input
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              required
              disabled={mode === "edit"}
              className="w-full px-3 py-1.5 text-sm border rounded-md bg-background focus:outline-none focus:ring-1 focus:ring-primary disabled:opacity-50"
            />
          </div>
          <div>
            <label className="text-xs font-medium block mb-0.5">Email</label>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              className="w-full px-3 py-1.5 text-sm border rounded-md bg-background focus:outline-none focus:ring-1 focus:ring-primary"
            />
          </div>
          <div>
            <label className="text-xs font-medium block mb-0.5">Full Name</label>
            <input
              type="text"
              value={fullName}
              onChange={(e) => setFullName(e.target.value)}
              className="w-full px-3 py-1.5 text-sm border rounded-md bg-background focus:outline-none focus:ring-1 focus:ring-primary"
            />
          </div>
          <div>
            <label className="text-xs font-medium block mb-0.5">Role</label>
            <select
              value={role}
              onChange={(e) => setRole(e.target.value as Role)}
              disabled={mode === "edit" && user?.role === "superadmin"}
              className="w-full px-3 py-1.5 text-sm border rounded-md bg-background focus:outline-none focus:ring-1 focus:ring-primary disabled:opacity-50"
            >
              {ROLES.map((r) => (
                <option key={r} value={r}>{r}</option>
              ))}
            </select>
          </div>
          {mode === "create" && (
            <div>
              <label className="text-xs font-medium block mb-0.5">Password</label>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                minLength={8}
                className="w-full px-3 py-1.5 text-sm border rounded-md bg-background focus:outline-none focus:ring-1 focus:ring-primary"
              />
            </div>
          )}
          <div className="flex gap-2 justify-end pt-2">
            <button
              type="button"
              onClick={onClose}
              className="px-3 py-1.5 text-xs border rounded-md hover:bg-muted"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={loading}
              className="px-3 py-1.5 text-xs bg-primary text-primary-foreground rounded-md hover:bg-primary/90 disabled:opacity-50"
            >
              {loading ? "Saving..." : mode === "create" ? "Create" : "Save"}
            </button>
          </div>
        </form>
      </div>
    </>
  );
}
