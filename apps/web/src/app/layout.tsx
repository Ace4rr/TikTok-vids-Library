import type { Metadata, Viewport } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "TokLib — личная библиотека TikTok",
  description: "Сохраняй ссылки из TikTok — получай карточки с тегами, саммари и поиском",
  manifest: "/manifest.webmanifest",
};

export const viewport: Viewport = {
  themeColor: "#fe2c55",
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ru">
      <body>{children}</body>
    </html>
  );
}
