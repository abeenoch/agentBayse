import { useEffect, useRef, useCallback } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { clearAccessToken, getAccessToken } from "../lib/auth";

const WS_URL = (import.meta.env.VITE_API_URL || "http://localhost:8000")
  .replace(/^http/, "ws") + "/ws/live";

export function useWebSocket() {
  const qc = useQueryClient();
  const ws = useRef<WebSocket | null>(null);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const attempt = useRef(0);

  const connect = useCallback(() => {
    const token = getAccessToken();
    if (!token) return;
    if (ws.current?.readyState === WebSocket.OPEN) return;

    const socketUrl = new URL(WS_URL);
    socketUrl.searchParams.set("token", token);
    const socket = new WebSocket(socketUrl.toString());
    ws.current = socket;

    socket.onopen = () => {
      attempt.current = 0;
    };

    socket.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data);
        switch (msg.type) {
          case "new_signal":
          case "snipe_executed":
            qc.invalidateQueries({ queryKey: ["signals"] });
            break;
          case "order_resolved":
          case "stop_loss_triggered":
            qc.invalidateQueries({ queryKey: ["signals"] });
            qc.invalidateQueries({ queryKey: ["portfolio"] });
            qc.invalidateQueries({ queryKey: ["activities"] });
            break;
          case "heartbeat":
            break;
        }
      } catch {}
    };

    socket.onclose = (event) => {
      if (event.code === 1008) {
        clearAccessToken();
        if (typeof window !== "undefined" && window.location.pathname !== "/login") {
          window.location.assign("/login");
        }
        return;
      }
      if (!getAccessToken()) return;
      const delay = Math.min(1000 * 2 ** attempt.current, 30_000);
      attempt.current++;
      reconnectTimer.current = setTimeout(connect, delay);
    };

    socket.onerror = () => socket.close();
  }, [qc]);

  useEffect(() => {
    connect();
    return () => {
      reconnectTimer.current && clearTimeout(reconnectTimer.current);
      ws.current?.close();
    };
  }, [connect]);
}
