"use client";

import { useState } from "react";
import { apiFetch } from "@/lib/api";
import { ThemeToggle } from "@/components/ThemeToggle";
import { useTheme } from "next-themes";

export default function SettingsPage() {
  const [activeTab, setActiveTab] = useState<"password" | "profile" | "appearance">("password");

  return (
    <div className="space-y-6 max-w-lg">
      <h1 className="text-2xl font-bold tracking-tight">Settings</h1>

      {/* Tabs */}
      <div className="flex gap-1 bg-muted rounded-md p-1 w-fit">
        <button
          onClick={() => setActiveTab("password")}
          className={`px-4 py-1.5 text-sm rounded-sm transition-colors ${
            activeTab === "password"
              ? "bg-background text-foreground shadow-sm"
              : "text-muted-foreground hover:text-foreground"
          }`}
        >
          Change Password
        </button>
        <button
          onClick={() => setActiveTab("profile")}
          className={`px-4 py-1.5 text-sm rounded-sm transition-colors ${
            activeTab === "profile"
              ? "bg-background text-foreground shadow-sm"
              : "text-muted-foreground hover:text-foreground"
          }`}
        >
          Display Name
        </button>
        <button
          onClick={() => setActiveTab("appearance")}
          className={`px-4 py-1.5 text-sm rounded-sm transition-colors ${
            activeTab === "appearance"
              ? "bg-background text-foreground shadow-sm"
              : "text-muted-foreground hover:text-foreground"
          }`}
        >
          Appearance
        </button>
      </div>

      {/* Tab content */}
      <div className="bg-card border rounded-lg p-6">
        {activeTab === "password" ? <ChangePasswordForm /> : activeTab === "profile" ? <DisplayNameForm /> : <AppearanceForm />}
      </div>
    </div>
  );
}

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
    } catch (err: any) {
      setError(err?.message || "Failed to change password.");
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
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [loading, setLoading] = useState(false);

  // Load current profile
  useState(() => {
    apiFetch<{ data: { full_name: string } }>("/api/v1/users/me")
      .then((resp) => {
        setFullName(resp.data?.full_name || "");
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
        body: JSON.stringify({ full_name: fullName }),
      });
      setSuccess("Display name updated successfully.");
    } catch (err: any) {
      setError(err?.message || "Failed to update display name.");
    } finally {
      setLoading(false);
    }
  }

  if (!loaded) {
    return <div className="h-24 bg-muted animate-pulse rounded" />;
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <h2 className="text-lg font-semibold">Display Name</h2>

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
      <button
        type="submit"
        disabled={loading}
        className="px-4 py-2 text-sm bg-primary text-primary-foreground rounded-md hover:bg-primary/90 disabled:opacity-50"
      >
        {loading ? "Saving..." : "Update Display Name"}
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
          className={`flex items-center gap-3 px-4 py-3 rounded-lg border-2 transition-all ${
            !isDark ? "border-primary bg-primary/5" : "border-border hover:border-muted-foreground/30"
          }`}
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
          className={`flex items-center gap-3 px-4 py-3 rounded-lg border-2 transition-all ${
            isDark ? "border-primary bg-primary/5" : "border-border hover:border-muted-foreground/30"
          }`}
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
