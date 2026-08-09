import type { Config } from "tailwindcss";
import animate from "tailwindcss-animate";

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // §3.2 spec tokens
        bg: {
          DEFAULT: "var(--color-background-primary)",
          secondary: "var(--color-background-secondary)",
          tertiary: "var(--color-background-tertiary)",
          info: "var(--color-background-info)",
          success: "var(--color-background-success)",
          warning: "var(--color-background-warning)",
          danger: "var(--color-background-danger)",
        },
        fg: {
          DEFAULT: "var(--color-text-primary)",
          secondary: "var(--color-text-secondary)",
          tertiary: "var(--color-text-tertiary)",
          info: "var(--color-text-info)",
          success: "var(--color-text-success)",
          warning: "var(--color-text-warning)",
          danger: "var(--color-text-danger)",
        },
        border: {
          DEFAULT: "var(--color-border-primary)",
          strong: "var(--color-border-secondary)",
          subtle: "var(--color-border-tertiary)",
          info: "var(--color-border-info)",
        },
        accent: {
          DEFAULT: "var(--color-accent)",
          hover: "var(--color-accent-hover)",
          foreground: "#FFFFFF",
        },

        // shadcn/ui compatibility — mapped to our tokens
        background: "var(--color-background-primary)",
        foreground: "var(--color-text-primary)",
        card: {
          DEFAULT: "var(--color-background-primary)",
          foreground: "var(--color-text-primary)",
        },
        popover: {
          DEFAULT: "var(--color-background-primary)",
          foreground: "var(--color-text-primary)",
        },
        primary: {
          DEFAULT: "var(--color-accent)",
          foreground: "#FFFFFF",
        },
        secondary: {
          DEFAULT: "var(--color-background-secondary)",
          foreground: "var(--color-text-primary)",
        },
        muted: {
          DEFAULT: "var(--color-background-tertiary)",
          foreground: "var(--color-text-tertiary)",
        },
        destructive: {
          DEFAULT: "var(--color-text-danger)",
          foreground: "#FFFFFF",
        },
        input: "var(--color-border-primary)",
        ring: "var(--color-accent)",
      },
      fontFamily: {
        sans: ["var(--font-sans)"],
        mono: ["var(--font-mono)"],
      },
      borderRadius: {
        sm: "var(--border-radius-sm)",
        DEFAULT: "var(--border-radius-md)",
        md: "var(--border-radius-md)",
        lg: "var(--border-radius-lg)",
      },
      fontSize: {
        xs: ["12px", { lineHeight: "1.5" }],
        sm: ["13px", { lineHeight: "1.5" }],
        base: ["14px", { lineHeight: "1.7" }],
        lg: ["16px", { lineHeight: "1.5" }],
        xl: ["20px", { lineHeight: "1.4" }],
        "2xl": ["24px", { lineHeight: "1.35" }],
        "3xl": ["28px", { lineHeight: "1.3" }],
      },
      keyframes: {
        "accordion-down": {
          from: { height: "0" },
          to: { height: "var(--radix-accordion-content-height)" },
        },
        "accordion-up": {
          from: { height: "var(--radix-accordion-content-height)" },
          to: { height: "0" },
        },
      },
      animation: {
        "accordion-down": "accordion-down 0.2s ease-out",
        "accordion-up": "accordion-up 0.2s ease-out",
      },
    },
  },
  plugins: [animate],
} satisfies Config;
