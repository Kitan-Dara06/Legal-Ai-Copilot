"use client";

import { useEffect, useState, useRef, useCallback } from "react";
import { listNotifications, markNotificationRead, markAllNotificationsRead } from "@/lib/api";
import type { NotificationItem } from "@/lib/types";

interface NotificationBellProps {
    token: string;
    orgSlug?: string;
}

export function NotificationBell({ token, orgSlug }: NotificationBellProps) {
    const [notifications, setNotifications] = useState<NotificationItem[]>([]);
    const [open, setOpen] = useState(false);
    const dropdownRef = useRef<HTMLDivElement>(null);

    const fetchNotifications = useCallback(async () => {
        try {
            const data = await listNotifications(token, orgSlug);
            setNotifications(data);
        } catch {
            // Silently fail — notifications are non-critical
        }
    }, [token, orgSlug]);

    // Poll every 30 seconds
    useEffect(() => {
        fetchNotifications();
        const interval = setInterval(fetchNotifications, 30000);
        return () => clearInterval(interval);
    }, [fetchNotifications]);

    // Close dropdown on click outside
    useEffect(() => {
        const handleClick = (e: MouseEvent) => {
            if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
                setOpen(false);
            }
        };
        document.addEventListener("mousedown", handleClick);
        return () => document.removeEventListener("mousedown", handleClick);
    }, []);

    const unreadCount = notifications.filter(n => !n.is_read).length;

    const handleMarkRead = async (id: string) => {
        await markNotificationRead(token, id, orgSlug);
        fetchNotifications();
    };

    const handleMarkAllRead = async () => {
        await markAllNotificationsRead(token, orgSlug);
        fetchNotifications();
    };

    return (
        <div className="relative" ref={dropdownRef}>
            <button
                className="relative p-2 text-slate-400 hover:text-white transition-colors"
                onClick={() => setOpen(!open)}
                aria-label="Notifications"
            >
                <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                        d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9"
                    />
                </svg>
                {unreadCount > 0 && (
                    <span className="absolute -top-0.5 -right-0.5 flex items-center justify-center w-4 h-4 text-[10px] font-bold text-white bg-red-500 rounded-full">
                        {unreadCount > 9 ? "9+" : unreadCount}
                    </span>
                )}
            </button>

            {open && (
                <div className="absolute right-0 top-full mt-2 w-80 bg-slate-800 border border-slate-700 rounded-xl shadow-xl overflow-hidden z-50">
                    <div className="flex items-center justify-between p-3 border-b border-slate-700">
                        <h3 className="text-white text-sm font-semibold">Notifications</h3>
                        {unreadCount > 0 && (
                            <button
                                className="text-xs text-blue-400 hover:text-blue-300"
                                onClick={handleMarkAllRead}
                            >
                                Mark all read
                            </button>
                        )}
                    </div>

                    <div className="max-h-80 overflow-y-auto">
                        {notifications.length === 0 ? (
                            <p className="text-slate-500 text-sm text-center py-6">
                                No notifications
                            </p>
                        ) : (
                            notifications.map((n) => (
                                <div
                                    key={n.id}
                                    className={`p-3 border-b border-slate-700/50 hover:bg-slate-700/30 transition-colors cursor-pointer ${
                                        !n.is_read ? "bg-blue-900/10" : ""
                                    }`}
                                    onClick={() => !n.is_read && handleMarkRead(n.id)}
                                >
                                    <div className="flex items-start justify-between gap-2">
                                        <div className="flex-1 min-w-0">
                                            <p className="text-white text-sm font-medium truncate">
                                                {n.title}
                                            </p>
                                            <p className="text-slate-400 text-xs mt-0.5 line-clamp-2">
                                                {n.body}
                                            </p>
                                        </div>
                                        {!n.is_read && (
                                            <span className="w-2 h-2 bg-blue-500 rounded-full flex-shrink-0 mt-1" />
                                        )}
                                    </div>
                                    {n.action_url && (
                                        <a
                                            href={n.action_url}
                                            className="text-blue-400 text-xs mt-1 inline-block hover:text-blue-300"
                                        >
                                            View details →
                                        </a>
                                    )}
                                </div>
                            ))
                        )}
                    </div>
                </div>
            )}
        </div>
    );
}
