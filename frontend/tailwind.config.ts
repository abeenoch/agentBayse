import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        primary: "#5B4FFF",
        secondary: "#00D9A5",
        danger: "#FF4F4F",
        warning: "#F5A623",
        bg: "#0A0B0F",
        surface: "#12141A",
        border: "#1E2028",
        text: "#E8E9F0",
        muted: "#6B7280",
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "sans-serif"],
        mono: ["JetBrains Mono", "monospace"],
      },
    },
  },
  plugins: [],
};

export default config;
